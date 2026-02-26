#!/usr/bin/env python3
"""
Invoice Processor v6.3 - Multi-Agent Workflow with RapidOCR
Nejpřesnější systém pro třídění faktur s 3 specializovanými AI agenty + OCR validátorem

OPTIMIZATIONS (2026-02-24):
- RapidOCR engine (PaddleOCR přes ONNX Runtime) místo Tesseractu
- Hybridní extrakce PDF (přímý text + OCR fallback pro skeny)
- Lepší přesnost OCR díky deep learning modelům
- Preskočení OCR validátoru pro digitální text z PDF (100% přesnost)
- Ignorování názvu souboru (klasifikace pouze podle obsahu)
- Optimalizovaný Extractor agent (10x rychlejší, lepší JSON compliance)
- Fix halucinovaných dat z extractoru (GQZEk9EW0AAEMi_.jpg fix)
- Fix classifier error s minimálními daty (IBM certifikát fix)
- OCR Text Validator - detekce a oprava halucinovaných slov z OCR

Workflow:
1. OCR Extrakce textu (PyMuPDF + RapidOCR)
   - PDF: Přímá extrakce textu (100% přesnost) nebo OCR fallback pro skeny
   - Obrázky: RapidOCR s konverzí barevného prostoru
2. OCR Text Validator - POUZE pro OCR text (přeskočen pro digitální PDF)
3. Pravidlový předběžný filtr
4. Paralelní AI analýza (Classifier + Extractor + Anomaly Detector)
5. Consensus Engine (vážené hlasování + veto + halucinace detection)
6. Human Review Queue pro nejisté případy

Použití:
    python invoice_gui_v6.py

Testy:
    python test_extractor_optimization.py
    python test_hallucination_fix.py
    python test_ibm_certificate_fix.py

Závislosti:
    pip install rapidocr_onnxruntime opencv-python numpy fitz
"""

# Potlačit varování Pydantic V1 s Pythonem 3.14+
import warnings
warnings.filterwarnings("ignore", message=".*Pydantic V1.*Python 3.14.*")

import os
import re
import sys
import json
import shutil
import logging
import threading
import gc
import time
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# GUI knihovny
import customtkinter as ctk
from tkinter import filedialog, messagebox, ttk

# Import agentů
try:
    from agent_workflow import (
        ClassifierAgent,
        ExtractorAgent,
        AnomalyDetectorAgent,
        ConsensusEngine,
        OCRTextValidator,
        validate_ocr_text
    )
    AGENTS_AVAILABLE = True
except ImportError as e:
    AGENTS_AVAILABLE = False
    print(f"⚠️ Warning: Agent workflow not available: {e}")

# ─────────────────────────────────────────────
# Konfigurační konstanty
# ─────────────────────────────────────────────
SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".gif"}
TEXT_MODEL = "llama3.2"  # Používáme llama3.2 (llama3.1 není nainstalován)
TEXT_MODEL_BETTER = "llama3.1:8b"  # Lepší model pro složité dokumenty (pokud je nainstalován)
LOG_LEVEL = logging.INFO

# v6.12: MIN_ZNAKU_PRO_DIGITALNI - převzato z čtečka/app.py pro lepší detekci skenů
MIN_ZNAKU_PRO_DIGITALNI = 30  # Minimální počet znaků pro digitální text (ne sken)

# v6.12: AI PARAMETRY PRO DETERMINISTICKÝ VÝSTUP (převzato z čtečka/app.py)
AI_TEMPERATURE = 0.0  # Deterministický výstup
AI_TOP_P = 0.1  # Omezený sampling pro lepší konzistenci

# v6.12: ULOŽENÍ SUROVÉHO TEXTU PRO DEBUGGING
SAVE_RAW_TEXT = True  # Ukládat surový OCR text před AI analýzou
RAW_TEXT_DIR = Path("raw_text_logs")  # Adresář pro surové texty

# Timeout configuration
# Increased timeouts for larger context window (4096 tokens)
REQUEST_TIMEOUT = 120  # Base timeout for most agents (increased from 90)
EXTRACTOR_TIMEOUT = 150  # Extra time for complex extractions (increased from 105)

# Memory management - AGRESIVNĚJŠÍ PO FIXU 2026-02-23
MAX_MEMORY_PERCENT = 60  # Snížen z 70% pro dřívější reakci
DEBUG_MEMORY = True
BATCH_SIZE = 1  # Zpracovávat po 1 souboru
MEMORY_CHECK_EVERY = 3  # Kontrolovat každé 3 soubory (bylo 5)
GC_EVERY = 5  # Spustit GC každých 5 souborů (bylo 10)

# OCR konstanty - RapidOCR (PaddleOCR přes ONNX Runtime)
USE_RAPIDOCR = True
MIN_ZNAKU_PRO_DIGITALNI = 30  # Minimální počet znaků pro detekci digitálního PDF
OCR_ZOOM = 2.0  # Zoom pro OCR skenů

# Agent thresholds - OPTIMIZED
THRESHOLD_ACCEPT = 0.7
THRESHOLD_REVIEW = 0.5
ANOMALY_VETO_THRESHOLD = 0.85
MIN_REALISTIC_AMOUNT = 1  # Minimální realistická částka (CZK/EUR/USD)
CHECK_AMOUNT_REALISTIC = False  # Kontrolovat podezřele nízké částky

# GUI nastavení
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(Path(__file__).parent / "gui_output.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Pomocné funkce pro monitoring paměti
# ─────────────────────────────────────────────
def get_memory_usage() -> dict:
    if HAS_PSUTIL:
        process = psutil.Process(os.getpid())
        memory_info = process.memory_info()
        return {
            'rss_mb': memory_info.rss / 1024 / 1024,
            'vms_mb': memory_info.vms / 1024 / 1024,
            'percent': process.memory_percent(),
        }
    return {'rss_mb': 0, 'vms_mb': 0, 'percent': 0}


def log_memory_state(context: str = ""):
    if not DEBUG_MEMORY:
        return
    mem = get_memory_usage()
    if mem['rss_mb'] > 0:
        logger.debug(f"💾 PAMĚŤ [{context}]: RSS={mem['rss_mb']:.1f}MB ({mem['percent']:.1f}%)")


# ─────────────────────────────────────────────
# Datová třída pro fakturu s agent results
# ─────────────────────────────────────────────
@dataclass
class Invoice:
    source_path: Path
    sender_name: str = "Neznamy_odesilatel"
    recipient_name: str = "Neznamy_prijemce"
    issue_date: str = "0000-00-00"
    due_date: str = "0000-00-00"
    total_amount: str = ""
    currency: str = ""
    invoice_number: str = ""
    is_invoice: bool = False
    confidence: float = 0.0
    raw_json: dict = field(default_factory=dict)
    # OCR text není uložen - šetří paměť (byl příčinou memory leak)

    # Nová pole pro v6 - agent results
    agent_results: dict = field(default_factory=dict)
    consensus_result: dict = field(default_factory=dict)
    decision_type: str = "pending"  # pending, auto_accept, human_review, auto_reject
    requires_review: bool = False
    
    @property
    def original_stem(self) -> str:
        stem = self.source_path.stem
        return _sanitize_filename(stem)

    @property
    def suffix(self) -> str:
        return self.source_path.suffix.lower()

    def matches_filter(self, filter_key: str, filter_value: str) -> bool:
        if not filter_key or not filter_value:
            return True
        value = getattr(self, filter_key, "")
        if not value:
            return False
        return filter_value.lower() in str(value).lower()


# ─────────────────────────────────────────────
# Pomocné funkce
# ─────────────────────────────────────────────
def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r'\s+', "_", name.strip())
    name = name.replace("á", "a").replace("é", "e").replace("í", "i")
    name = name.replace("ó", "o").replace("ú", "u").replace("ý", "y")
    name = name.replace("č", "c").replace("ď", "d").replace("ě", "e")
    name = name.replace("ň", "n").replace("ř", "r").replace("š", "s")
    name = name.replace("ť", "t").replace("ů", "u").replace("ž", "z")
    return name[:60]


def _extract_json_from_text(text: str) -> Optional[dict]:
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if code_block:
        try:
            return json.loads(code_block.group(1))
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


# ─────────────────────────────────────────────
# Ollama kontrola a spuštění
# ─────────────────────────────────────────────
def check_ollama_running() -> bool:
    """
    Zkontroluje zda Ollama běží na pozadí.
    Returns: True pokud Ollama běží, False jinak
    """
    import socket
    try:
        # Zkusit připojení na Ollama API (default port 11434)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', 11434))
        sock.close()
        if result == 0:
            logger.info("✓ Ollama běží na portu 11434")
            return True
        return False
    except Exception as e:
        logger.debug(f"Ollama check error: {e}")
        return False


def try_start_ollama() -> bool:
    """
    Pokusí se spustit Ollama na pozadí.
    Returns: True pokud se podařilo spustit, False jinak
    """
    # Cesty kde může být Ollama nainstalována na Windows
    ollama_paths = [
        r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe",
        r"C:\Program Files\Ollama\ollama.exe",
        r"%USERPROFILE%\AppData\Local\Programs\Ollama\ollama.exe",
    ]
    
    ollama_exe = None
    for path_template in ollama_paths:
        path = os.path.expandvars(path_template)
        if os.path.isfile(path):
            ollama_exe = path
            break
    
    # Zkusit PATH
    if not ollama_exe:
        ollama_exe = shutil.which("ollama")
    
    if not ollama_exe:
        logger.warning("⚠️ Ollama executable nenalezen")
        return False
    
    try:
        # Spustit Ollama serve na pozadí
        logger.info(f"🚀 Spouštím Ollama: {ollama_exe}")
        subprocess.Popen(
            [ollama_exe, "serve"],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # Počkat až naběhne (až 10 sekund)
        for i in range(20):
            time.sleep(0.5)
            if check_ollama_running():
                logger.info("✓ Ollama úspěšně spuštěna")
                return True
        
        logger.warning("⚠️ Ollama se nespustila včas")
        return False
        
    except Exception as e:
        logger.error(f"Chyba při spuštění Ollama: {e}")
        return False


def check_ollama_model(model_name: str = "llama3.2") -> bool:
    """
    Zkontroluje zda je požadovaný model stažený v Ollama.
    Returns: True pokud model existuje, False jinak
    """
    try:
        import ollama
        models = ollama.list()
        # Model může být v seznamu s tagem nebo bez
        for m in models.get('models', []):
            if model_name in m.get('name', ''):
                return True
        return False
    except Exception as e:
        logger.debug(f"Model check error: {e}")
        return False


# ─────────────────────────────────────────────
# Modul: OCR Text Extractor
# ─────────────────────────────────────────────
class OCRExtractor:
    """
    Extrahuje text z PDF a obrázků pomocí RapidOCR (PaddleOCR přes ONNX Runtime).
    
    Hybridní přístup:
    - PDF: Nejprve přímá extrakce textu (100% přesnost), pokud není dostupný, použije se OCR fallback
    - Obrázky: Vždy RapidOCR
    """

    INVOICE_KEYWORDS = [
        'faktura', 'invoice', 'daňový doklad', 'receipt', 'bill',
        'faktúra', 'účtenka', 'paragon'
    ]

    NON_INVOICE_KEYWORDS = [
        'upomínka', 'reminder', 'výzva', 'notice',
        'smlouva', 'contract', 'dohoda', 'agreement',
        'nabídka', 'offer', 'objednávka', 'order',
        'poznámka', 'note', 'zápis', 'minutes',
        'vizitka', 'business card', 'reklama', 'advertisement',
        'leták', 'flyer', 'prospekt', 'catalog'
    ]

    def __init__(self, ocr_memory_mb: int = 2048):
        """
        Initialize OCRExtractor.
        
        Args:
            ocr_memory_mb: OCR memory limit in MB (affects ONNX Runtime threads)
        """
        self.fitz = None
        self.rapidocr = None
        self.cv2 = None
        self.np = None
        self.ocr_memory_mb = ocr_memory_mb  # Uložit pro použití v _init_libraries
        self._init_libraries()

    def _init_libraries(self):
        """Načte potřebné knihovny pro RapidOCR s nastavením paměti."""
        try:
            import fitz
            self.fitz = fitz
            logger.debug("PyMuPDF načten")
        except ImportError as e:
            logger.warning(f"PyMuPDF není nainstalován: {e}")

        try:
            from rapidocr_onnxruntime import RapidOCR
            logger.info(f"🔍 Inicializuji RapidOCR (PaddleOCR modely přes ONNX Runtime, paměť: {self.ocr_memory_mb}MB)...")
            
            # v6.7: Nastavit limity pro ONNX Runtime podle dostupné paměti
            # Více paměti = více vláken pro paralelizaci
            import onnxruntime
            
            # Vypočítat počet vláken based on memory
            # 512MB = 1 thread, 1024MB = 2 threads, 2048MB = 4 threads, atd.
            num_threads = max(1, min(8, self.ocr_memory_mb // 512))
            
            # Nastavit environment variables pro ONNX Runtime
            os.environ['OMP_NUM_THREADS'] = str(num_threads)
            os.environ['MKL_NUM_THREADS'] = str(num_threads)
            os.environ['ONNXRUNTIME_NUM_THREADS'] = str(num_threads)
            
            logger.info(f"  → ONNX Runtime: {num_threads} vláken (z {self.ocr_memory_mb}MB)")
            
            self.rapidocr = RapidOCR()
            logger.info("✓ RapidOCR připraven")
        except ImportError as e:
            logger.error(f"RapidOCR není nainstalován: {e}")
            logger.error("💡 Instalace: pip install rapidocr_onnxruntime")
            return

        try:
            import cv2
            self.cv2 = cv2
            logger.debug("OpenCV načten")
        except ImportError as e:
            logger.warning(f"OpenCV není nainstalováno: {e}")

        try:
            import numpy as np
            self.np = np
            logger.debug("NumPy načten")
        except ImportError as e:
            logger.warning(f"NumPy není nainstalováno: {e}")

    def extract_text(self, file_path: Path) -> tuple:
        """
        Extrahuje text ze souboru (PDF nebo obrázek).
        
        Args:
            file_path: Cesta k souboru
            
        Returns:
            Tuple (text, is_digital):
                - text: Extrahovaný text nebo None
                - is_digital: True pokud byl použit digitální text z PDF (100% přesnost),
                              False pokud byl použit OCR
        """
        if self.rapidocr is None:
            logger.error("RapidOCR není inicializován")
            return (None, False)
            
        ext = file_path.suffix.lower()

        if ext == ".pdf":
            return self._extract_from_pdf(file_path)
        elif ext in {".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".gif"}:
            text = self._extract_from_image(file_path)
            return (text, False)  # Obrázky vždy přes OCR

        return (None, False)

    def _extract_from_pdf(self, file_path: Path) -> tuple:
        """
        Hybridní extrakce textu z PDF:
        1. Nejprve se pokusí o přímou extrakci textu (digitální PDF)
        2. Pokud stránka neobsahuje dostatek textu, použije se OCR fallback (sken)
        
        Returns:
            Tuple (text, is_digital):
                - text: Extrahovaný text nebo None
                - is_digital: True pokud byl použit digitální text (100% přesnost)
        """
        if self.fitz is None:
            logger.warning("PyMuPDF není dostupný")
            return (None, False)

        try:
            with self.fitz.open(str(file_path)) as doc:
                if len(doc) == 0:
                    return (None, False)

                if len(doc) > 1:
                    logger.info(f"  📄 Vícestránkové PDF ({len(doc)} stran) - analyzuji POUZE první stranu.")

                # Zpracovat POUZE první stranu
                page = doc[0]
                page_text = page.get_text("text").strip()

                # KROK 1: Zkusíme přímou extrakci textu (digitální PDF)
                if len(page_text) >= MIN_ZNAKU_PRO_DIGITALNI:
                    logger.info(f"  📄 Strana 1/{len(doc)}: ✓ Digitální text ({len(page_text)} znaků) - OCR není potřeba")
                    return (page_text, True)  # is_digital = True
                
                # KROK 2: Stránka je pravděpodobně sken - použijeme OCR fallback
                logger.info(f"  📄 Strana 1/{len(doc)}: 🖼️ Sken detekován, spouštím RapidOCR...")
                ocr_text = self._ocr_pdf_page(page)
                return (ocr_text, False)  # is_digital = False

        except Exception as e:
            logger.warning(f"Chyba při čtení PDF: {e}")
            return (None, False)

    def _ocr_pdf_page(self, page) -> Optional[str]:
        """
        Provede OCR na stránce PDF pomocí RapidOCR.
        
        Args:
            page: fitz.Page objekt
            
        Returns:
            Extrahovaný text nebo None
        """
        if self.rapidocr is None or self.fitz is None or self.cv2 is None or self.np is None:
            logger.warning("RapidOCR nebo závislosti nejsou dostupné")
            return None

        try:
            # Vykreslit stránku do pixmapu se zoomem pro lepší kvalitu OCR
            mat = self.fitz.Matrix(OCR_ZOOM, OCR_ZOOM)
            pix = page.get_pixmap(matrix=mat)
            
            # Převod PyMuPDF pixmap na numpy pole kompatibilní s OpenCV (BGR formát)
            img = self.np.frombuffer(pix.samples, dtype=self.np.uint8).reshape(pix.h, pix.w, pix.n)
            
            # Konverze barevného prostoru
            if pix.n == 4:  # RGBA
                img = self.cv2.cvtColor(img, self.cv2.COLOR_RGBA2BGR)
            elif pix.n == 3:  # RGB
                img = self.cv2.cvtColor(img, self.cv2.COLOR_RGB2BGR)
            elif pix.n == 1:  # Grayscale
                img = self.cv2.cvtColor(img, self.cv2.COLOR_GRAY2BGR)

            # Spustit RapidOCR
            result, _ = self.rapidocr(img)
            
            # Extrahovat text z výsledků
            if result:
                extracted_text = "\n".join([item[1] for item in result])
                return extracted_text if extracted_text.strip() else None
            
            return None

        except Exception as e:
            logger.warning(f"Chyba OCR PDF: {e}")
            return None

    def _extract_from_image(self, file_path: Path) -> Optional[str]:
        """
        Extrahuje text z obrázku pomocí RapidOCR.
        
        Args:
            file_path: Cesta k obrázku
            
        Returns:
            Extrahovaný text nebo None
        """
        if self.rapidocr is None or self.cv2 is None:
            logger.warning("RapidOCR nebo OpenCV nejsou dostupné")
            return None

        try:
            # Načíst obrázek pomocí OpenCV
            img = self.cv2.imread(str(file_path))
            
            if img is None:
                logger.warning(f"Nelze načíst obrázek: {file_path}")
                return None

            # Spustit RapidOCR
            result, _ = self.rapidocr(img)
            
            # Extrahovat text z výsledků
            if result:
                extracted_text = "\n".join([item[1] for item in result])
                return extracted_text if extracted_text.strip() else None
            
            return None

        except Exception as e:
            logger.warning(f"Chyba OCR obrázku: {e}")
            return None

    def pre_filter(self, text: str) -> tuple:
        """
        Rychlý pravidlový filtr před AI analýzou.
        Vrací: (classification, confidence, reason)
        classification: 'reject', 'uncertain', 'likely'
        """
        if not text or len(text.strip()) < 50:
            return 'reject', 0.95, "prázdný dokument"

        text_lower = text.lower()
        score = 0

        # Kontrola explicitních názvů
        explicit_titles = ['faktura', 'invoice', 'daňový doklad', 'zálohová faktura', 'proforma']
        has_explicit_title = any(title in text_lower for title in explicit_titles)

        # Pozitivní body
        positive = [
            ('dodavatel', 2), ('odběratel', 2), ('ičo', 2), ('dič', 2),
            ('celkem k úhradě', 3), ('datum splatnosti', 2), ('variabilní symbol', 2),
            ('faktura č', 2), ('částka', 1), ('kč', 1), ('czk', 1),
        ]

        for keyword, points in positive:
            if keyword in text_lower:
                score += points

        # Negativní body
        negative = [
            ('upomínka', -5), ('smlouva', -4), ('nabídka', -3),
            ('objednávka', -2), ('životopis', -4), ('certifikát', -4),
        ]

        for keyword, penalty in negative:
            if keyword in text_lower:
                score += penalty

        # Rozhodnutí
        if score <= -3:
            return 'reject', min(0.95, 0.7 + abs(score) * 0.05), f"negativní skóre ({score})"
        
        if has_explicit_title and score >= 6:
            return 'likely', min(0.9, 0.6 + score * 0.03), f"pozitivní skóre ({score})"
        
        return 'uncertain', 0.5, f"nejistý případ (score={score})"


# ─────────────────────────────────────────────
# Modul: Multi-Agent Processor
# ─────────────────────────────────────────────
class MultiAgentProcessor:
    """
    Hlavní processor s multi-agent workflow.
    Spouští 3 agenty paralelně a kombinuje výsledky.

    OPTIMIZATIONS (2026-02-22):
    - Extractor timeout optimalizován (10x rychlejší, průměr 3.6s)
    - Filename ignorován (klasifikace pouze podle obsahu)
    - Hallucinace detekovány (SPECIAL CASE 1B)
    - Minimal extractor data detection (IBM certifikát fix)

    v6.1 (2026-02-23):
    - OCR Text Validator integrován pro detekci halucinovaných slov z OCR

    v6.2 (2026-02-24):
    - RapidOCR engine (PaddleOCR přes ONNX Runtime) místo Tesseractu
    - Hybridní extrakce PDF (přímý text + OCR fallback)

    v6.3 (2026-02-24):
    - Přeskočení OCR validátoru pro digitální text z PDF (100% přesnost)
    """

    def __init__(self, vram_limit_gb: int = 4, num_ctx: int = 4096):
        """
        Initialize Multi-Agent Processor.
        
        Args:
            vram_limit_gb: VRAM limit for Ollama models (GB)
            num_ctx: Context window size for LLM
        """
        if not AGENTS_AVAILABLE:
            raise ImportError("Agent workflow module not available")

        # v6.7: Předat VRAM a num_ctx nastavení agentům
        self.classifier = ClassifierAgent(
            model=TEXT_MODEL,
            timeout=REQUEST_TIMEOUT,
            vram_limit_gb=vram_limit_gb,
            num_ctx=num_ctx
        )
        self.extractor = ExtractorAgent(
            model=TEXT_MODEL,
            timeout=EXTRACTOR_TIMEOUT,
            vram_limit_gb=vram_limit_gb,
            num_ctx=num_ctx
        )
        self.anomaly = AnomalyDetectorAgent(
            model=TEXT_MODEL,
            timeout=REQUEST_TIMEOUT,
            vram_limit_gb=vram_limit_gb,
            num_ctx=num_ctx
        )
        self.consensus = ConsensusEngine(
            threshold_accept=THRESHOLD_ACCEPT,
            threshold_review=THRESHOLD_REVIEW,
            anomaly_veto_threshold=ANOMALY_VETO_THRESHOLD
        )
        # OCR Text Validator - available but primarily used in GUI before calling analyze_document
        self.ocr_validator = OCRTextValidator(language="auto")

        self.stats = {
            'total': 0,
            'invoices': 0,
            'non_invoices': 0,
            'human_review': 0,
            'errors': 0,
        }

    def analyze_document(self, ocr_text: str, file_path: Path) -> Optional[Invoice]:
        """
        Analyzuje dokument pomocí 3 agentů SEKVENČNĚ (s předáním informací).
        
        v6.9: Classifier → Extractor → Anomaly (s předáním reasoningu)
        """
        if not ocr_text or len(ocr_text.strip()) < 50:
            logger.debug(f"  ⚠️ Prázdný dokument: {file_path.name}")
            return None

        start_time = time.time()
        logger.debug(f"  🚀 Start analýzy: {file_path.name}")

        # Vytvoření invoice objektu (bez ukládání OCR textu - šetří paměť)
        invoice = Invoice(source_path=file_path)

        try:
            # =====================================================
            # KROK 1: Classifier (nejrychlejší)
            # =====================================================
            logger.debug(f"  ⏳ Classifier...")
            try:
                classifier_result = self.classifier.analyze(ocr_text, None)
                logger.debug(f"  ✓ Classifier hotovo za {time.time() - start_time:.1f}s")
                logger.debug(f"    Výsledek: {'FAKTURA' if classifier_result.get('is_invoice') else 'NENÍ FAKTURA'} ({classifier_result.get('confidence', 0):.0%})")
            except Exception as e:
                logger.error(f"  ✗ Classifier ERROR: {e}")
                classifier_result = {'is_invoice': False, 'confidence': 0.0, 'reasoning': 'Error'}

            # =====================================================
            # KROK 2: Extractor (s předáním classifier reasoningu!)
            # =====================================================
            # v6.9: Předat classifier_result jako metadata aby extractor věděl co hledat
            logger.debug(f"  ⏳ Extractor (s classifier reasoningem)...")
            try:
                # Připravit metadata s classifier findings
                classifier_metadata = {
                    'classifier_reasoning': classifier_result.get('reasoning', ''),
                    'classifier_elements': classifier_result.get('elements_present', {}),
                    'classifier_is_invoice': classifier_result.get('is_invoice', False),
                    'classifier_confidence': classifier_result.get('confidence', 0),
                    'extracted_values': classifier_result.get('extracted_values', {})  # Nové: strukturované hodnoty
                }

                extractor_result = self.extractor.analyze(ocr_text, classifier_metadata)
                logger.debug(f"  ✓ Extractor hotovo za {time.time() - start_time:.1f}s")
                logger.debug(f"    Completeness: {extractor_result.get('completeness_score', 0):.0%}")
                if extractor_result.get('validation_errors'):
                    logger.debug(f"    Validation errors: {extractor_result['validation_errors'][:3]}")
            except TimeoutError:
                logger.error(f"  ⏰ Extractor TIMEOUT")
                extractor_result = {'completeness_score': 0.0, 'validation_errors': ['Timeout']}
            except Exception as e:
                logger.error(f"  ✗ Extractor ERROR: {e}")
                extractor_result = {'completeness_score': 0.0, 'validation_errors': ['Error']}

            # =====================================================
            # KROK 3: Anomaly Detector
            # =====================================================
            logger.debug(f"  ⏳ Anomaly Detector...")
            try:
                anomaly_result = self.anomaly.analyze(ocr_text, None)
                logger.debug(f"  ✓ Anomaly hotovo za {time.time() - start_time:.1f}s")
            except TimeoutError:
                logger.error(f"  ⏰ Anomaly TIMEOUT")
                anomaly_result = {'is_anomaly': False, 'confidence': 0.0}
            except Exception as e:
                logger.error(f"  ✗ Anomaly ERROR: {e}")
                anomaly_result = {'is_anomaly': False, 'confidence': 0.0}

            # Debug: log raw results
            logger.debug(f"Classifier result type: {type(classifier_result)}")
            logger.debug(f"Extractor result type: {type(extractor_result)}")
            logger.debug(f"Anomaly result type: {type(anomaly_result)}")

            # Uložení výsledků agentů
            invoice.agent_results = {
                'classifier': classifier_result,
                'extractor': extractor_result,
                'anomaly': anomaly_result,
            }

            # Výpočet konsenzu
            try:
                # v6.4: Předáme raw_text pro kontrolu invoice keywords
                consensus_result = self.consensus.calculate_consensus(
                    classifier_result,
                    extractor_result,
                    anomaly_result,
                    raw_text=ocr_text  # Nový parametr pro keyword validation
                )
                
                # Kontrola retry
                if consensus_result.get('decision_type') == 'retry':
                    logger.warning(f"  ⚠️ RETRY požadavek pro {file_path.name}: {consensus_result.get('reasoning')}")
                    # Zvýšíme pozornost - pokud classifier řekl "faktura" ale chybí keywords,
                    # je to podezřelé. Spustíme retry s vyšší opatrností.
                    # Pro tuto verzi treatujeme retry jako "human_review"
                    consensus_result['is_invoice'] = None
                    consensus_result['decision_type'] = 'human_review'
                    consensus_result['retry_info'] = consensus_result.get('keyword_analysis', {})
                    logger.info(f"  🔍 {file_path.name} přesunut do human review kvůli chybějícím invoice keywords")
            except Exception as consensus_error:
                logger.error(f"  Consensus error: {consensus_error}")
                # Fallback decision based on classifier only
                clf_is_inv = classifier_result.get('is_invoice', False)
                clf_conf = classifier_result.get('confidence', 0)
                consensus_result = {
                    'is_invoice': clf_is_inv,
                    'confidence': clf_conf,
                    'decision_type': 'auto_reject' if not clf_is_inv else 'auto_accept',
                    'reasoning': f'Fallback due to consensus error: {consensus_error}',
                    'extracted_data': {}
                }

            invoice.consensus_result = consensus_result

            # Nastavení rozhodnutí
            is_invoice = consensus_result.get('is_invoice')
            confidence = consensus_result.get('confidence', 0)
            decision_type = consensus_result.get('decision_type', 'unknown')

            invoice.is_invoice = is_invoice if is_invoice is not None else False
            invoice.confidence = confidence
            invoice.decision_type = decision_type
            invoice.requires_review = (decision_type == 'human_review')

            # Extrakce dat pro invoice objekt
            extracted = consensus_result.get('extracted_data', {})

            if extracted.get('vendor_name'):
                invoice.sender_name = _sanitize_filename(extracted['vendor_name'])
            if extracted.get('customer_name'):
                invoice.recipient_name = _sanitize_filename(extracted['customer_name'])
            if extracted.get('issue_date'):
                invoice.issue_date = extracted['issue_date']
            if extracted.get('due_date'):
                invoice.due_date = extracted['due_date']
            if extracted.get('total_amount'):
                invoice.total_amount = str(extracted['total_amount'])
            if extracted.get('currency'):
                invoice.currency = extracted['currency']
            if extracted.get('invoice_number'):
                invoice.invoice_number = extracted['invoice_number']

            # Update stats
            elapsed = time.time() - start_time
            
            # Kontrola celkového času analýzy
            if elapsed > 180:  # Více než 3 minuty
                logger.warning(f"  ⚠️ Analýza trvala velmi dlouho: {elapsed:.1f}s")
            
            self.stats['total'] += 1

            if is_invoice is True:
                self.stats['invoices'] += 1
                status = "FAKTURA"
            elif is_invoice is False:
                self.stats['non_invoices'] += 1
                status = "NENÍ FAKTURA"
            else:
                self.stats['human_review'] += 1
                status = "REVIEW"

            logger.info(f"  ✓ {file_path.name}: {status} ({confidence:.0%}, {elapsed:.1f}s)")
            logger.debug(f"     Reasoning: {consensus_result.get('reasoning', 'N/A')[:80]}")

            return invoice

        except Exception as e:
            logger.error(f"  ✗ Chyba analýzy {file_path.name}: {e}")
            self.stats['errors'] += 1

            # Vrátit alespoň základní invoice s chybou
            invoice.is_invoice = False
            invoice.confidence = 0.0
            invoice.decision_type = 'error'
            invoice.raw_json = {'error': str(e)}
            return invoice

    def get_statistics(self) -> dict:
        """Vrátí statistiky zpracování."""
        return self.stats.copy()


# ─────────────────────────────────────────────
# Modul: Rychlé vyhledávání souborů
# ─────────────────────────────────────────────
class FileDiscovery:
    def __init__(self, source_dir: Path):
        self.source_dir = source_dir

    def find_files(self) -> list[Path]:
        found: list[Path] = []
        logger.info(f"Prohledávám složku: {self.source_dir}")

        try:
            for path in self.source_dir.rglob("*"):
                if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
                    found.append(path)
        except PermissionError as e:
            logger.warning(f"Přístup odepřen: {e}")

        logger.info(f"Nalezeno {len(found)} souborů ke zpracování.")
        return found


# ─────────────────────────────────────────────
# Modul: Filtrování faktur
# ─────────────────────────────────────────────
class InvoiceFilterAgent:
    def __init__(self, invoices: list[Invoice]):
        self.invoices = invoices

    def filter_by_custom(self, filter_key: str, filter_value: str) -> list[Invoice]:
        if not filter_key or not filter_value:
            return [inv for inv in self.invoices if inv.is_invoice]

        filtered = []
        for inv in self.invoices:
            if not inv.is_invoice:
                continue
            if inv.matches_filter(filter_key, filter_value):
                filtered.append(inv)

        return filtered

    def filter_by_decision_type(self, decision_type: str) -> list[Invoice]:
        """Filtruje podle typu rozhodnutí."""
        return [inv for inv in self.invoices if inv.decision_type == decision_type]

    def get_unique_values(self, field_name: str) -> list[str]:
        values = set()
        for inv in self.invoices:
            if inv.is_invoice:
                val = getattr(inv, field_name, "")
                if val:
                    values.add(val)
        return sorted(values)


# ─────────────────────────────────────────────
# Modul: Třídění a přesun
# ─────────────────────────────────────────────
class InvoiceOrganizer:
    SORT_OPTIONS = {
        "sender_name": "Odesílatel",
        "recipient_name": "Příjemce",
        "issue_date": "Datum_vystaveni",
        "due_date": "Datum_splatnosti",
        "total_amount": "Castka",
    }

    def __init__(self, invoices: list[Invoice], target_dir: Path):
        self.invoices = invoices
        self.target_dir = target_dir

    def sort_invoices(self, sort_key: str) -> list[Invoice]:
        return sorted(
            self.invoices,
            key=lambda inv: getattr(inv, sort_key) or "ZZZZ"
        )

    def _build_new_name(self, index: int, invoice: Invoice, sort_key: str) -> str:
        criterion_value = _sanitize_filename(getattr(invoice, sort_key) or "nezname")
        seq = str(index).zfill(3)
        return f"{seq}_{criterion_value}_{invoice.original_stem}{invoice.suffix}"

    def process(self, sort_key: str) -> tuple[int, int]:
        if not self.invoices:
            return 0, 0

        sorted_invoices = self.sort_invoices(sort_key)

        try:
            self.target_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            logger.error(f"Nelze vytvořit cílovou složku: {e}")
            return 0, len(sorted_invoices)

        errors = 0
        success = 0

        for i, invoice in enumerate(sorted_invoices, start=1):
            if not invoice.is_invoice:
                logger.warning(f"⚠️ Přeskok '{invoice.source_path.name}': Není faktura")
                errors += 1
                continue

            new_name = self._build_new_name(i, invoice, sort_key)
            dest_path = self.target_dir / new_name

            if dest_path.exists():
                stem = dest_path.stem
                dest_path = self.target_dir / f"{stem}_dup{dest_path.suffix}"

            try:
                shutil.move(str(invoice.source_path), str(dest_path))
                logger.info(f"✓ Přesunuto: '{invoice.source_path.name}' → '{dest_path.name}'")
                success += 1
            except Exception as e:
                errors += 1
                logger.error(f"Chyba při přesunu '{invoice.source_path.name}': {e}")

        return success, errors


# ─────────────────────────────────────────────
# GUI Aplikace
# ─────────────────────────────────────────────
class InvoiceProcessorGUIV6(ctk.CTk):
    """GUI pro Multi-Agent Workflow systém."""

    def __init__(self):
        super().__init__()

        self.title("🧾 Invoice Processor v6.5 - Multi-Agent Workflow + Memory Settings")
        self.geometry("1200x900")
        self.minsize(1100, 800)

        # Proměnné
        self.source_dir = ctk.StringVar()
        self.target_dir = ctk.StringVar()
        self.sort_key = ctk.StringVar(value="issue_date")
        self.model_name = ctk.StringVar(value=TEXT_MODEL)

        # Filtr
        self.filter_key = ctk.StringVar(value="")
        self.filter_value = ctk.StringVar()

        # Memory settings (v6.5) - MUST BE BEFORE OCRExtractor!
        self.memory_settings = {
            'ocr_memory_mb': 2048,
            'ollama_vram_gb': 4,
            'memory_threshold': 60,
            'profile': 'balanced'
        }
        self.settings_file = Path(__file__).parent / ".memory_settings.json"
        self._load_memory_settings()

        # Initialize components
        # v6.7: Předat OCR RAM nastavení z GUI
        ocr_memory = self.memory_settings.get('ocr_memory_mb', 2048)
        self.ocr_extractor = OCRExtractor(ocr_memory_mb=ocr_memory)
        self.agent_processor = None
        self.filter_agent = None

        self.is_processing = False
        self.stop_flag = False  # Flag pro zastavení zpracování
        self.resume_index = 0  # Index pro pokračování od posledního místa
        self.processing_start_index = 0  # Startovní index pro aktuální zpracování
        self.found_files: list[Path] = []
        self.invoices: list[Invoice] = []
        self.filtered_invoices: list[Invoice] = []
        self.review_queue: list[Invoice] = []
        self.selected_invoices: list[Invoice] = []  # Seznam vybraných faktur
        self._last_selected_idx = None  # Pro Shift+Click výběr

        # Mapování
        self.sort_key_map = {
            "Datum vystavení": "issue_date",
            "Datum splatnosti": "due_date",
            "Odesílatel": "sender_name",
            "Příjemce": "recipient_name",
            "Částka": "total_amount",
        }

        self.filter_key_map = {
            "Vypnuto": "",
            "Odesílatel": "sender_name",
            "Příjemce": "recipient_name",
            "Datum vystavení": "issue_date",
            "Číslo faktury": "invoice_number",
        }

        # Rozhodnutí types
        self.decision_type_map = {
            "Všechny": "all",
            "Auto-Accept": "auto_accept",
            "Human Review": "human_review",
            "Auto-Reject": "auto_reject",
        }

        # Zkontroluj komponenty
        self.ocr_ready = self._check_ocr_status()
        self.agents_ready = AGENTS_AVAILABLE

        # Zkontroluj Ollama a pokus se ji spustit
        self.ollama_ready = self._check_and_start_ollama()

        # Agents jsou ready pouze pokud jsou dostupné agenty A Ollama běží
        self.agents_ready = self.agents_ready and self.ollama_ready

        self._setup_ui()

    def _check_ocr_status(self) -> bool:
        """Zkontroluje zda je RapidOCR připraven."""
        if self.ocr_extractor.rapidocr is None:
            logger.warning("RapidOCR není inicializován")
            return False
        
        logger.info("✓ RapidOCR připraven")
        return True

    def _check_and_start_ollama(self) -> bool:
        """
        Zkontroluje zda Ollama běží, pokud ne pokusí se ji spustit.
        Returns: True pokud Ollama běží a model je dostupný, False jinak
        """
        logger.info("🔍 Kontrola Ollama...")
        
        # 1. Zkontroluj zda Ollama běží
        if check_ollama_running():
            logger.info("✓ Ollama již běží")
            # Zkontroluj model
            if check_ollama_model(self.model_name.get()):
                logger.info(f"✓ Model {self.model_name.get()} je dostupný")
                return True
            else:
                logger.warning(f"⚠️ Model {self.model_name.get()} není stažen")
                # Pokusit se stáhnout model
                return self._pull_ollama_model(self.model_name.get())
        
        # 2. Ollama neběží - pokusit se spustit
        logger.info("⚠️ Ollama neběží - pokus o spuštění...")
        if try_start_ollama():
            # Ollama spuštěna, zkontrolovat model
            if check_ollama_model(self.model_name.get()):
                logger.info(f"✓ Model {self.model_name.get()} je dostupný")
                return True
            else:
                logger.warning(f"⚠️ Model {self.model_name.get()} není stažen")
                return self._pull_ollama_model(self.model_name.get())
        else:
            logger.error("✗ Nepodařilo se spustit Ollama")
            return False
    
    def _pull_ollama_model(self, model_name: str) -> bool:
        """
        Pokusí se stáhnout model z Ollama library.
        Returns: True pokud se podařilo stáhnout, False jinak
        """
        logger.info(f"📥 Stahuji model {model_name}...")

        # Najít ollama executable
        ollama_paths = [
            r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe",
            r"C:\Program Files\Ollama\ollama.exe",
        ]
        
        ollama_exe = None
        for path_template in ollama_paths:
            path = os.path.expandvars(path_template)
            if os.path.isfile(path):
                ollama_exe = path
                break
        
        if not ollama_exe:
            ollama_exe = shutil.which("ollama")
        
        if not ollama_exe:
            logger.error("✗ Ollama executable nenalezen")
            return False
        
        try:
            # Spustit ollama pull
            process = subprocess.Popen(
                [ollama_exe, "pull", model_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Čekat na dokončení s timeoutem
            try:
                stdout, stderr = process.communicate(timeout=300)  # 5 minut timeout
                if process.returncode == 0:
                    logger.info(f"✓ Model {model_name} úspěšně stažen")
                    return True
                else:
                    logger.error(f"✗ Chyba při stahování modelu: {stderr}")
                    return False
            except subprocess.TimeoutExpired:
                process.kill()
                logger.error(f"✗ Timeout při stahování modelu {model_name}")
                return False
                
        except Exception as e:
            logger.error(f"✗ Chyba při stahování modelu: {e}")
            return False

    def _setup_ui(self):
        # Configure main window
        self.geometry("1400x950")  # Increased height for better visibility
        self.minsize(1200, 850)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)  # Main scrollable frame
        
        # ─── Create main scrollable container ────────────────────────────
        self.main_scroll_frame = ctk.CTkScrollableFrame(self, orientation="vertical")
        self.main_scroll_frame.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        self.main_scroll_frame.grid_columnconfigure(0, weight=1)
        
        # All content will be placed in main_scroll_frame instead of self
        content_parent = self.main_scroll_frame
        
        # ─── Header ────────────────────────────
        header_frame = ctk.CTkFrame(content_parent, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 10))
        header_frame.grid_columnconfigure(0, weight=1)

        title_label = ctk.CTkLabel(
            header_frame,
            text="🧾 Invoice Processor v6.5",
            font=ctk.CTkFont(size=28, weight="bold")
        )
        title_label.grid(row=0, column=0, sticky="w")

        # Status
        status_frame = ctk.CTkFrame(header_frame, fg_color="transparent")
        status_frame.grid(row=1, column=0, sticky="w", pady=(5, 0))

        if self.agents_ready:
            agent_status = "✓ Multi-Agent připraven"
            agent_color = "#28a745"
        else:
            agent_status = "✗ Agent workflow chybí"
            agent_color = "#dc3545"

        if self.ocr_ready:
            ocr_status = "✓ RapidOCR připraven"
            ocr_color = "#28a745"
        else:
            ocr_status = "✗ RapidOCR nenalezen"
            ocr_color = "#dc3545"

        if self.ollama_ready:
            ollama_status = "✓ Ollama běží"
            ollama_color = "#28a745"
        else:
            ollama_status = "✗ Ollama neběží"
            ollama_color = "#dc3545"

        ctk.CTkLabel(
            status_frame,
            text="🤖 3 specializovaní AI agenti • Vážené hlasování • Detekce anomálií",
            font=ctk.CTkFont(size=14),
            text_color="gray"
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            status_frame,
            text=f"  {agent_status}  |  {ocr_status}  |  {ollama_status}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=agent_color if self.agents_ready else ollama_color
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))

        # ─── Nastavení složek ────────────────────
        settings_frame = ctk.CTkFrame(content_parent)
        settings_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=10)
        settings_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(settings_frame, text="📁 Zdrojová složka:").grid(
            row=0, column=0, sticky="w", padx=(15, 10), pady=10
        )
        ctk.CTkEntry(
            settings_frame,
            textvariable=self.source_dir,
            height=35
        ).grid(row=0, column=1, sticky="ew", padx=10, pady=10)
        ctk.CTkButton(
            settings_frame,
            text="Procházet",
            command=self._browse_source,
            width=100
        ).grid(row=0, column=2, padx=15, pady=10)

        ctk.CTkLabel(settings_frame, text="📂 Cílová složka:").grid(
            row=1, column=0, sticky="w", padx=(15, 10), pady=10
        )
        ctk.CTkEntry(
            settings_frame,
            textvariable=self.target_dir,
            height=35
        ).grid(row=1, column=1, sticky="ew", padx=10, pady=10)
        ctk.CTkButton(
            settings_frame,
            text="Procházet",
            command=self._browse_target,
            width=100
        ).grid(row=1, column=2, padx=15, pady=10)

        # ─── Memory/VRAM Settings Panel (v6.5) ────────────────────
        # Reduced height since we have scroll
        memory_frame = ctk.CTkFrame(content_parent)
        memory_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=10)
        memory_frame.grid_columnconfigure(1, weight=1)
        
        ctk.CTkLabel(
            memory_frame,
            text="⚙️ Nastavení Paměti a Výkonu",
            font=ctk.CTkFont(size=14, weight="bold")
        ).grid(row=0, column=0, columnspan=5, pady=(0, 10))
        
        # Profile
        ctk.CTkLabel(memory_frame, text="Profil:", width=100).grid(row=1, column=0, sticky="w", padx=10, pady=5)
        self.profile_var = ctk.StringVar(value=self.memory_settings['profile'])
        profile_combo = ctk.CTkOptionMenu(
            memory_frame, variable=self.profile_var,
            values=['conservative', 'balanced', 'aggressive', 'maximum'],
            command=self._on_profile_change, width=180
        )
        profile_combo.grid(row=1, column=1, sticky="w", padx=5, pady=5)
        
        # OCR RAM
        ctk.CTkLabel(memory_frame, text="OCR RAM (MB):", width=100).grid(row=1, column=2, sticky="w", padx=10, pady=5)
        self.ocr_memory_var = ctk.IntVar(value=self.memory_settings['ocr_memory_mb'])
        ocr_slider = ctk.CTkSlider(memory_frame, from_=512, to=8192, number_of_steps=15,
                                   variable=self.ocr_memory_var, command=self._update_memory_labels, width=200)
        ocr_slider.grid(row=1, column=3, sticky="ew", padx=5, pady=5)
        self.ocr_memory_label = ctk.CTkLabel(memory_frame, text=f"{self.memory_settings['ocr_memory_mb']} MB", width=70)
        self.ocr_memory_label.grid(row=1, column=4, padx=5, pady=5)
        
        # VRAM - POZOR: Toto nastavení NENÍ použito! Ollama si spravuje VRAM sama.
        ctk.CTkLabel(memory_frame, text="Ollama VRAM (GB):", width=100).grid(row=2, column=0, sticky="w", padx=10, pady=5)
        self.vram_var = ctk.IntVar(value=self.memory_settings['ollama_vram_gb'])
        vram_slider = ctk.CTkSlider(memory_frame, from_=2, to=24, number_of_steps=11,
                                    variable=self.vram_var, command=self._update_memory_labels, width=200)
        vram_slider.grid(row=2, column=1, sticky="ew", padx=5, pady=5, columnspan=2)
        self.vram_label = ctk.CTkLabel(memory_frame, text=f"{self.memory_settings['ollama_vram_gb']} GB", width=70)
        self.vram_label.grid(row=2, column=3, padx=5, pady=5, columnspan=2)

        # GC Threshold
        ctk.CTkLabel(memory_frame, text="GC Threshold (%):", width=100).grid(row=3, column=0, sticky="w", padx=10, pady=5)
        self.threshold_var = ctk.IntVar(value=self.memory_settings['memory_threshold'])
        threshold_slider = ctk.CTkSlider(memory_frame, from_=30, to=90, number_of_steps=12,
                                         variable=self.threshold_var, command=self._update_memory_labels, width=200)
        threshold_slider.grid(row=3, column=1, sticky="ew", padx=5, pady=5, columnspan=2)
        self.threshold_label = ctk.CTkLabel(memory_frame, text=f"{self.memory_settings['memory_threshold']}%", width=70)
        self.threshold_label.grid(row=3, column=3, padx=5, pady=5, columnspan=2)

        # Save & Display
        save_btn = ctk.CTkButton(memory_frame, text="💾 Uložit", command=self._save_memory_settings, width=120)
        save_btn.grid(row=4, column=0, padx=10, pady=10)
        self.memory_display_label = ctk.CTkLabel(memory_frame, text="💾 Paměť: -- MB (--%)", text_color="gray", width=200)
        self.memory_display_label.grid(row=4, column=1, padx=10, pady=10, columnspan=2)
        
        # Help
        help_label = ctk.CTkLabel(
            memory_frame,
            text="✅ VRAM ovlivňuje num_ctx | OCR RAM ovlivňuje počet vláken",
            text_color="gray", font=ctk.CTkFont(size=11)
        )
        help_label.grid(row=7, column=0, columnspan=5, padx=10, pady=(0, 5))
        
        self._update_memory_labels()
        self._monitor_memory()

        # ─── Filtry a třídění ────────────────────
        filter_frame = ctk.CTkFrame(content_parent)
        filter_frame.grid(row=3, column=0, sticky="ew", padx=20, pady=10)
        filter_frame.grid_columnconfigure(1, weight=1)
        filter_frame.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(filter_frame, text="🔍 Filtr:").grid(
            row=0, column=0, sticky="w", padx=(15, 5), pady=10
        )
        
        self.filter_key_combo = ctk.CTkComboBox(
            filter_frame,
            values=list(self.filter_key_map.keys()),
            variable=self.filter_key,
            width=150,
            command=self._on_filter_key_change
        )
        self.filter_key_combo.grid(row=0, column=1, sticky="w", padx=5, pady=10)
        self.filter_key_combo.set("Vypnuto")

        self.filter_value_entry = ctk.CTkEntry(
            filter_frame,
            textvariable=self.filter_value,
            width=200,
            placeholder_text="Hledaný výraz..."
        )
        self.filter_value_entry.grid(row=0, column=2, sticky="w", padx=10, pady=10)
        self.filter_value_entry.bind("<KeyRelease>", self._on_filter_change)

        # Filtr podle rozhodnutí
        ctk.CTkLabel(filter_frame, text="📋 Rozhodnutí:").grid(
            row=0, column=3, sticky="w", padx=(20, 5), pady=10
        )
        
        self.decision_type_var = ctk.StringVar(value="Všechny")
        self.decision_type_combo = ctk.CTkComboBox(
            filter_frame,
            values=list(self.decision_type_map.keys()),
            variable=self.decision_type_var,
            width=150,
            command=self._on_decision_type_change
        )
        self.decision_type_combo.grid(row=0, column=4, sticky="w", padx=5, pady=10)

        # ─── Tlačítka akcí ────────────────────
        action_frame = ctk.CTkFrame(content_parent)
        action_frame.grid(row=4, column=0, sticky="ew", padx=20, pady=10)

        self.process_btn = ctk.CTkButton(
            action_frame,
            text="▶️ Spustit analýzu",
            command=self._start_processing,
            height=45,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#28a745",
            hover_color="#218838"
        )
        self.process_btn.pack(side="left", padx=10, pady=10)

        self.stop_btn = ctk.CTkButton(
            action_frame,
            text="⏹️ Zastavit",
            command=self._stop_processing,
            height=45,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#dc3545",
            hover_color="#c82333",
            state="disabled"
        )
        self.stop_btn.pack(side="left", padx=10, pady=10)

        self.resume_btn = ctk.CTkButton(
            action_frame,
            text="▶️ Pokračovat",
            command=self._resume_processing,
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#007bff",
            hover_color="#0056b3",
            state="disabled"
        )
        self.resume_btn.pack(side="left", padx=10, pady=10)

        self.move_btn = ctk.CTkButton(
            action_frame,
            text="📂 Přesunout označené",
            command=self._move_selected_invoices,
            height=40,
            fg_color="#007bff",
            hover_color="#0056b3",
            state="disabled"
        )
        self.move_btn.pack(side="left", padx=10, pady=10)

        self.select_all_btn = ctk.CTkButton(
            action_frame,
            text="✅ Označit vše",
            command=self._select_all_invoices,
            height=40,
            fg_color="#6c757d",
            hover_color="#5a6268",
            state="disabled"
        )
        self.select_all_btn.pack(side="left", padx=10, pady=10)

        self.deselect_all_btn = ctk.CTkButton(
            action_frame,
            text="❌ Zrušit výběr",
            command=self._deselect_all_invoices,
            height=40,
            fg_color="#6c757d",
            hover_color="#5a6268",
            state="disabled"
        )
        self.deselect_all_btn.pack(side="left", padx=10, pady=10)

        # ─── Progress ────────────────────
        self.progress_frame = ctk.CTkFrame(content_parent)
        self.progress_frame.grid(row=5, column=0, sticky="ew", padx=20, pady=10)
        self.progress_frame.grid_columnconfigure(0, weight=1)

        self.progress_label = ctk.CTkLabel(
            self.progress_frame,
            text="Připraven k analýze",
            font=ctk.CTkFont(size=14)
        )
        self.progress_label.grid(row=0, column=0, sticky="w", padx=15, pady=5)

        self.progress_bar = ctk.CTkProgressBar(
            self.progress_frame,
            height=20,
            corner_radius=10
        )
        self.progress_bar.grid(row=1, column=0, sticky="ew", padx=15, pady=(0, 10))
        self.progress_bar.set(0)

        # ─── Statistiky ────────────────────
        self.stats_frame = ctk.CTkFrame(content_parent)
        self.stats_frame.grid(row=6, column=0, sticky="ew", padx=20, pady=10)
        self.stats_frame.grid_columnconfigure(0, weight=1)
        self.stats_frame.grid_columnconfigure(1, weight=1)
        self.stats_frame.grid_columnconfigure(2, weight=1)
        self.stats_frame.grid_columnconfigure(3, weight=1)
        self.stats_frame.grid_columnconfigure(4, weight=1)

        self.stat_labels = {}
        stats_config = [
            ("total", "📄 Celkem", 0),
            ("invoices", "✅ Faktury", 1),
            ("non_invoices", "❌ Odmítnuté", 2),
            ("review", "⚠️ Review", 3),
            ("errors", "⛔ Chyby", 4),
        ]

        for key, label, col in stats_config:
            frame = ctk.CTkFrame(self.stats_frame, fg_color="#2b2b2b")
            frame.grid(row=0, column=col, sticky="nsew", padx=5, pady=5)
            frame.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(
                frame,
                text=label,
                font=ctk.CTkFont(size=12),
                text_color="gray"
            ).grid(row=0, column=0, padx=10, pady=(10, 0))

            self.stat_labels[key] = ctk.CTkLabel(
                frame,
                text="0",
                font=ctk.CTkFont(size=24, weight="bold")
            )
            self.stat_labels[key].grid(row=1, column=0, padx=10, pady=(0, 10))

        # ─── Tabulka výsledků ────────────────────
        result_frame = ctk.CTkFrame(content_parent)
        result_frame.grid(row=7, column=0, sticky="nsew", padx=20, pady=(0, 20))
        result_frame.grid_columnconfigure(0, weight=1)
        result_frame.grid_rowconfigure(0, weight=1)

        # Treeview s custom style
        style = ttk.Style()
        style.theme_use("clam")

        # Konfigurace sloupců
        columns = ("file", "sender", "recipient", "date", "amount", "decision", "confidence")
        self.tree = ttk.Treeview(
            result_frame,
            columns=columns,
            show="headings",
            height=15,  # Reduced from 20 for better fit
            selectmode="extended"
        )

        # Nadpisy sloupců
        self.tree.heading("file", text="Soubor")
        self.tree.heading("sender", text="Odesílatel")
        self.tree.heading("recipient", text="Příjemce")
        self.tree.heading("date", text="Datum")
        self.tree.heading("amount", text="Částka")
        self.tree.heading("decision", text="Rozhodnutí")
        self.tree.heading("confidence", text="Jistota")

        # Šířka sloupců
        self.tree.column("file", width=200)
        self.tree.column("sender", width=150)
        self.tree.column("recipient", width=150)
        self.tree.column("date", width=100)
        self.tree.column("amount", width=100)
        self.tree.column("decision", width=100)
        self.tree.column("confidence", width=80)

        # Scrollbar
        scrollbar = ctk.CTkScrollbar(result_frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        # Bind double-click pro detail
        self.tree.bind("<Double-1>", self._show_document_detail)

        # Bind pro výběr s Shift+Click
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

    # �������─────────────���──────────────────────────────
    # Memory/VRAM Settings Methods (v6.5)
    # ───────────────────────────────���─��───────────
    def _create_memory_settings_panel(self):
        """Create memory/VRAM settings panel in GUI."""
        memory_frame = ctk.CTkScrollableFrame(self, orientation="vertical", height=320)
        memory_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=10)
        memory_frame.grid_columnconfigure(1, weight=1)
        
        # Title
        ctk.CTkLabel(memory_frame, text="⚙️ Nastavení Paměti a Výkonu", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, columnspan=5, pady=(0, 10))
        
        # Performance Profile Selector
        ctk.CTkLabel(memory_frame, text="Profil:", width=100).grid(row=1, column=0, sticky="w", padx=10, pady=5)
        self.profile_var = ctk.StringVar(value=self.memory_settings['profile'])
        profile_combo = ctk.CTkOptionMenu(memory_frame, variable=self.profile_var, values=['conservative', 'balanced', 'aggressive', 'maximum'], command=self._on_profile_change, width=180)
        profile_combo.grid(row=1, column=1, sticky="w", padx=5, pady=5)
        
        # OCR Memory Slider
        ctk.CTkLabel(memory_frame, text="OCR RAM (MB):", width=100).grid(row=1, column=2, sticky="w", padx=10, pady=5)
        self.ocr_memory_var = ctk.IntVar(value=self.memory_settings['ocr_memory_mb'])
        ocr_slider = ctk.CTkSlider(memory_frame, from_=512, to=8192, number_of_steps=15, variable=self.ocr_memory_var, command=self._update_memory_labels, width=200)
        ocr_slider.grid(row=1, column=3, sticky="ew", padx=5, pady=5)
        self.ocr_memory_label = ctk.CTkLabel(memory_frame, text=f"{self.memory_settings['ocr_memory_mb']} MB", width=70)
        self.ocr_memory_label.grid(row=1, column=4, padx=5, pady=5)
        
        # Ollama VRAM Slider
        ctk.CTkLabel(memory_frame, text="Ollama VRAM (GB):", width=100).grid(row=2, column=0, sticky="w", padx=10, pady=5)
        self.vram_var = ctk.IntVar(value=self.memory_settings['ollama_vram_gb'])
        vram_slider = ctk.CTkSlider(memory_frame, from_=2, to=24, number_of_steps=11, variable=self.vram_var, command=self._update_memory_labels, width=200)
        vram_slider.grid(row=2, column=1, sticky="ew", padx=5, pady=5, columnspan=2)
        self.vram_label = ctk.CTkLabel(memory_frame, text=f"{self.memory_settings['ollama_vram_gb']} GB", width=70)
        self.vram_label.grid(row=2, column=3, padx=5, pady=5, columnspan=2)

        # GC Threshold Slider
        ctk.CTkLabel(memory_frame, text="GC Threshold (%):", width=100).grid(row=3, column=0, sticky="w", padx=10, pady=5)
        self.threshold_var = ctk.IntVar(value=self.memory_settings['memory_threshold'])
        threshold_slider = ctk.CTkSlider(memory_frame, from_=30, to=90, number_of_steps=12, variable=self.threshold_var, command=self._update_memory_labels, width=200)
        threshold_slider.grid(row=3, column=1, sticky="ew", padx=5, pady=5, columnspan=2)
        self.threshold_label = ctk.CTkLabel(memory_frame, text=f"{self.memory_settings['memory_threshold']}%", width=70)
        self.threshold_label.grid(row=3, column=3, padx=5, pady=5, columnspan=2)

        # Save Button and Memory Display
        save_btn = ctk.CTkButton(memory_frame, text="💾 Uložit", command=self._save_memory_settings, width=120)
        save_btn.grid(row=4, column=0, padx=10, pady=10)
        self.memory_display_label = ctk.CTkLabel(memory_frame, text="💾 Paměť: -- MB (--%)", text_color="gray", width=200)
        self.memory_display_label.grid(row=4, column=1, padx=10, pady=10, columnspan=2)

        # Quick help label
        help_label = ctk.CTkLabel(memory_frame, text="✅ VRAM ovlivňuje num_ctx | OCR RAM ovlivňuje počet vláken", text_color="gray", font=ctk.CTkFont(size=11))
        help_label.grid(row=5, column=0, columnspan=5, padx=10, pady=(0, 5))
        
        # Update labels on init
        self._update_memory_labels()
        # Start memory monitoring
        self._monitor_memory()
    
    def _on_profile_change(self, profile):
        """Apply performance profile."""
        # POZOR: workers a batch_size nejsou použity - pouze pro zobrazení
        profiles = {
            'conservative': {'ocr_memory_mb': 1024, 'ollama_vram_gb': 2, 'memory_threshold': 70},
            'balanced': {'ocr_memory_mb': 2048, 'ollama_vram_gb': 4, 'memory_threshold': 60},
            'aggressive': {'ocr_memory_mb': 4096, 'ollama_vram_gb': 8, 'memory_threshold': 50},
            'maximum': {'ocr_memory_mb': 8192, 'ollama_vram_gb': 16, 'memory_threshold': 40}
        }
        if profile in profiles:
            settings = profiles[profile]
            self.ocr_memory_var.set(settings['ocr_memory_mb'])
            self.vram_var.set(settings['ollama_vram_gb'])
            self.threshold_var.set(settings['memory_threshold'])
            self._update_memory_labels()
    
    def _update_memory_labels(self, value=None):
        """Update slider value labels."""
        self.ocr_memory_label.configure(text=f"{self.ocr_memory_var.get()} MB")
        self.vram_label.configure(text=f"{self.vram_var.get()} GB")
        self.threshold_label.configure(text=f"{self.threshold_var.get()}%")
    
    def _monitor_memory(self):
        """Update memory usage display periodically."""
        try:
            mem = get_memory_usage()
            if self.memory_display_label:
                self.memory_display_label.configure(text=f"💾 Paměť: {mem['rss_mb']:.0f} MB ({mem['percent']:.1f}%)")
        except:
            pass
        if hasattr(self, 'after_id'):
            self.after_cancel(self.after_id)
        self.after_id = self.after(2000, self._monitor_memory)
    
    def _save_memory_settings(self):
        """Save memory settings to file."""
        self.memory_settings = {
            'ocr_memory_mb': self.ocr_memory_var.get(),
            'ollama_vram_gb': self.vram_var.get(),
            'memory_threshold': self.threshold_var.get(),
            'profile': self.profile_var.get()
        }
        with open(self.settings_file, 'w', encoding='utf-8') as f:
            json.dump(self.memory_settings, f, indent=2)
        logger.info(f"💾 Memory settings saved: {self.memory_settings}")
        
        # Calculate num_ctx for display
        vram_gb = self.memory_settings['ollama_vram_gb']
        num_ctx = self._calculate_num_ctx(vram_gb)
        ocr_threads = max(1, min(8, self.memory_settings['ocr_memory_mb'] // 512))
        
        messagebox.showinfo(
            "Nastavení Uloženo",
            f"✅ Nastavení bylo uloženo.\n\n"
            f"📊 Ollama VRAM: {self.memory_settings['ollama_vram_gb']} GB\n"
            f"   → num_ctx: {num_ctx} tokenů\n"
            f"   → Ovlivňuje velikost kontextu pro LLM\n\n"
            f"📊 OCR RAM: {self.memory_settings['ocr_memory_mb']} MB\n"
            f"   → Počet vláken: {ocr_threads}\n"
            f"   → Ovlivňuje rychlost OCR zpracování\n\n"
            f"📊 GC Threshold: {self.memory_settings['memory_threshold']}%\n"
            f"   → Spustí GC když paměť překročí tuto hodnotu\n\n"
            f"⚠️ Změny se projeví po restartu aplikace!"
        )
    
    def _load_memory_settings(self):
        """Load memory settings from file."""
        if self.settings_file.exists():
            try:
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    self.memory_settings = json.load(f)
                logger.info(f"📂 Memory settings loaded: {self.memory_settings}")
            except Exception as e:
                logger.warning(f"Error loading settings: {e}")
                self.memory_settings = {
                    'ocr_memory_mb': 2048,
                    'ollama_vram_gb': 4,
                    'memory_threshold': 60,
                    'profile': 'balanced'
                }

    def _calculate_num_ctx(self, vram_gb: int) -> int:
        """
        Calculate optimal context window size based on VRAM.
        
        llama3.2 needs approximately:
        - 2GB for model weights
        - ~1GB per 1024 tokens of context
        
        Args:
            vram_gb: Available VRAM in GB
            
        Returns:
            Optimal num_ctx value
        """
        # Reserve 2GB for model weights, rest for context
        available_for_context = max(0, vram_gb - 2)
        
        # 1GB ≈ 1024 tokens context
        # num_ctx must be multiple of 256 (Ollama requirement)
        num_ctx = int(available_for_context * 1024 / 1) * 256
        
        # Clamp to reasonable range
        num_ctx = max(2048, min(num_ctx, 16384))
        
        # Round down to nearest 256
        num_ctx = (num_ctx // 256) * 256
        
        logger.debug(f"Calculated num_ctx={num_ctx} for {vram_gb}GB VRAM")
        return num_ctx

    # ─────────────────────────────────────────────
    # Event Handlers
    # ─────────────────────────────────────────────
    def _browse_source(self):
        directory = filedialog.askdirectory(title="Vyberte zdrojovou složku")
        if directory:
            self.source_dir.set(directory)

    def _browse_target(self):
        directory = filedialog.askdirectory(title="Vyberte cílovou složku")
        if directory:
            self.target_dir.set(directory)

    def _on_filter_key_change(self, event=None):
        """Změna klíče filtru."""
        selected = self.filter_key_combo.get()
        self.filter_key.set(self.filter_key_map.get(selected, ""))
        self._apply_filters()

    def _on_filter_change(self, event=None):
        self._apply_filters()

    def _on_tree_select(self, event=None):
        """Obsluha změny výběru v tabulce."""
        selection = self.tree.selection()
        self.selected_invoices.clear()

        for item_id in selection:
            # Najít invoice podle jména souboru
            item = self.tree.item(item_id)
            filename = item['values'][0]

            for inv in self.filtered_invoices:
                if inv.source_path.name == filename:
                    self.selected_invoices.append(inv)
                    break

        # Aktualizovat tlačítko
        count = len(self.selected_invoices)
        if count > 0:
            self.move_btn.configure(text=f"📂 Přesunout označené ({count})")
        else:
            self.move_btn.configure(text="📂 Přesunout označené")

    def _save_raw_text(self, file_path: Path, text: str, is_digital: bool):
        """
        v6.12: Uloží surový text pro debugging (převzato z čtečka/app.py).
        
        Args:
            file_path: Path to source file
            text: Extracted OCR/digital text
            is_digital: True if text was extracted digitally from PDF (not OCR)
        """
        try:
            # Vytvořit adresář pokud neexistuje
            RAW_TEXT_DIR.mkdir(exist_ok=True)
            
            # Generovat název souboru
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            raw_filename = f"{timestamp}_{file_path.stem}_raw.txt"
            raw_path = RAW_TEXT_DIR / raw_filename
            
            # Zapsat text
            with open(raw_path, "w", encoding="utf-8") as f:
                f.write("=" * 80 + "\n")
                f.write("SUROVÝ TEXT PŘED AI ANALÝZOU\n")
                f.write("=" * 80 + "\n\n")
                f.write(f"Zdroj: {file_path.name}\n")
                f.write(f"Čas extrakce: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Typ textu: {'Digitální z PDF' if is_digital else 'OCR (sken)'}\n")
                f.write(f"Počet znaků: {len(text)}\n")
                f.write(f"Počet řádků: {text.count(chr(10)) + 1}\n")
                f.write("\n")
                f.write("=" * 80 + "\n")
                f.write("TEXT DOKUMENTU\n")
                f.write("=" * 80 + "\n\n")
                f.write(text)
            
            logger.debug(f"  📝 Uložen surový text: {raw_path}")
            
        except Exception as e:
            logger.warning(f"  ⚠️ Nepodařilo se uložit surový text: {e}")

    def _select_all_invoices(self):
        """Označit všechny faktury v aktuálním filtru."""
        # Získat všechny item_id z treeview
        all_items = self.tree.get_children()
        
        # Projít všechny faktury a najít je v tree
        for inv in self.filtered_invoices:
            if inv.is_invoice:
                for item_id in all_items:
                    item = self.tree.item(item_id)
                    if item['values'][0] == inv.source_path.name:
                        self.tree.selection_add(item_id)
                        break
        
        # Trigger selection event
        self._on_tree_select()

    def _deselect_all_invoices(self):
        """Zrušit výběr všech faktur."""
        self.tree.selection_remove(self.tree.selection())
        self.selected_invoices.clear()
        self.move_btn.configure(text="📂 Přesunout označené")

    def _on_decision_type_change(self, event=None):
        self._apply_filters()

    def _apply_filters(self):
        """Aplikuje filtry na výsledky."""
        if not self.invoices:
            return

        # Filtr podle decision type
        decision_type = self.decision_type_map.get(self.decision_type_var.get(), "all")
        
        if decision_type == "all":
            filtered = self.invoices.copy()
        else:
            filtered = [inv for inv in self.invoices if inv.decision_type == decision_type]

        # Textový filtr
        filter_key = self.filter_key.get()
        filter_value = self.filter_value.get()

        if filter_key and filter_value:
            final_filtered = []
            for inv in filtered:
                if inv.matches_filter(filter_key, filter_value):
                    final_filtered.append(inv)
            filtered = final_filtered

        self.filtered_invoices = filtered
        self._update_tree()

    def _update_tree(self):
        """Aktualizuje tabulku výsledků."""
        # Smazat všechny řádky
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Přidat filtrované výsledky
        for inv in self.filtered_invoices:
            # Barva podle rozhodnutí
            if inv.decision_type == "auto_accept":
                tags = ("accept",)
            elif inv.decision_type == "human_review":
                tags = ("review",)
            elif inv.decision_type == "auto_reject":
                tags = ("reject",)
            else:
                tags = ()

            amount_str = f"{inv.total_amount} {inv.currency}" if inv.total_amount else "-"

            decision_display = {
                "auto_accept": "✅ Accept",
                "human_review": "⚠️ Review",
                "auto_reject": "❌ Reject",
                "error": "⛔ Error",
            }.get(inv.decision_type, inv.decision_type)

            self.tree.insert(
                "",
                "end",
                values=(
                    inv.source_path.name,
                    inv.sender_name[:30] + "..." if len(inv.sender_name) > 30 else inv.sender_name,
                    inv.recipient_name[:30] + "..." if len(inv.recipient_name) > 30 else inv.recipient_name,
                    inv.issue_date if inv.issue_date != "0000-00-00" else "-",
                    amount_str,
                    decision_display,
                    f"{inv.confidence:.0%}" if inv.confidence > 0 else "-"
                ),
                tags=tags
            )

        # Konfigurace barev
        self.tree.tag_configure("accept", background="#28a745", foreground="white")
        self.tree.tag_configure("review", background="#ffc107", foreground="black")
        self.tree.tag_configure("reject", background="#dc3545", foreground="white")

        # Nastavit výběrovou barvu
        self.tree.tag_configure("selected", background="#007bff", foreground="white")

        # Enable/disable buttons based on whether there are invoices
        has_invoices = any(inv.is_invoice for inv in self.filtered_invoices)
        self.select_all_btn.configure(state="normal" if has_invoices else "disabled")
        self.deselect_all_btn.configure(state="normal" if has_invoices else "disabled")
        self.move_btn.configure(state="normal" if has_invoices else "disabled")

    def _update_stats(self, stats: dict = None):
        """Aktualizuje statistiky."""
        if stats:
            self.stat_labels["total"].configure(text=str(stats.get('total', 0)))
            self.stat_labels["invoices"].configure(text=str(stats.get('invoices', 0)))
            self.stat_labels["non_invoices"].configure(text=str(stats.get('non_invoices', 0)))
            self.stat_labels["review"].configure(text=str(stats.get('human_review', 0)))
            self.stat_labels["errors"].configure(text=str(stats.get('errors', 0)))

    def _stop_processing(self):
        """Zastaví zpracování po dokončení aktuálního souboru."""
        if self.is_processing:
            self.stop_flag = True
            logger.info("⏹️ Zastavení zpracování požadováno...")
            self.progress_label.configure(text="⏳ Zastavuji po dokončení aktuálního souboru...")

    def _resume_processing(self):
        """Pokračuje ve zpracování od posledního zastaveného místa."""
        if not self.found_files or self.resume_index >= len(self.found_files):
            return

        if self.is_processing:
            return

        # Pokračovat od resume_index
        self.is_processing = True
        self.processing_start_index = self.resume_index
        self.process_btn.configure(state="disabled", text="⏳ Zpracovávám...")
        self.stop_btn.configure(state="normal")
        self.resume_btn.configure(state="disabled")
        self.progress_label.configure(text=f"▶️ Pokračování od souboru {self.resume_index + 1}/{len(self.found_files)}...")

        # Spustit na vlákně
        thread = threading.Thread(target=self._process_files, daemon=True)
        thread.start()

    def _start_processing(self):
        """Spustí zpracování na vlákně."""
        if self.is_processing:
            return

        source = self.source_dir.get()
        if not source or not Path(source).exists():
            messagebox.showerror("Chyba", "Zadejte platnou zdrojovou složku")
            return

        if not self.ollama_ready:
            messagebox.showerror(
                "Chyba",
                "Ollama neběží a nepodařilo se ji spustit.\n\n"
                "Možná řešení:\n"
                "1. Otevřete Ollama aplikaci a nechte ji běžet na pozadí\n"
                "2. Nainstalujte Ollama z: https://ollama.ai\n"
                "3. Spusťte příkaz: ollama serve"
            )
            return

        if not self.agents_ready:
            messagebox.showerror(
                "Chyba",
                "Agent workflow není dostupný.\n\n"
                "Nainstalujte: pip install ollama\n"
                "A spusťte: ollama pull llama3.2"
            )
            return

        # Reset flags for new processing
        self.stop_flag = False
        self.resume_index = 0
        self.processing_start_index = 0
        self.invoices = []
        self.review_queue = []

        self.is_processing = True
        self.process_btn.configure(state="disabled", text="⏳ Zpracovávám...")
        self.stop_btn.configure(state="normal")
        self.resume_btn.configure(state="disabled")
        self.progress_bar.set(0)

        # Spustit na vlákně
        thread = threading.Thread(target=self._process_files, daemon=True)
        thread.start()

    def _process_files(self):
        """Hlavní zpracovací vlákno."""
        try:
            log_memory_state("start")

            # Inicializace agent processoru
            self.after(0, lambda: self.progress_label.configure(text="Inicializace agentů..."))

            # Only initialize if starting fresh (not resuming)
            if self.processing_start_index == 0:
                # v6.7: Předat VRAM a num_ctx nastavení z GUI
                vram_limit = self.vram_var.get() if hasattr(self, 'vram_var') else 4
                num_ctx = self._calculate_num_ctx(vram_limit)  # Dynamicky spočítat num_ctx podle VRAM
                
                logger.info(f"Inicializace agentů: VRAM={vram_limit}GB, num_ctx={num_ctx}")
                self.agent_processor = MultiAgentProcessor(
                    vram_limit_gb=vram_limit,
                    num_ctx=num_ctx
                )
                self.filter_agent = InvoiceFilterAgent([])

            # Objevování souborů
            self.after(0, lambda: self.progress_label.configure(text="Vyhledávání souborů..."))
            
            # Only rediscover files if starting fresh
            if not self.found_files or self.processing_start_index == 0:
                discovery = FileDiscovery(Path(self.source_dir.get()))
                self.found_files = discovery.find_files()

            total = len(self.found_files)
            if total == 0:
                self.after(0, lambda: messagebox.showinfo("Info", "Žádné soubory nenalezeny"))
                return

            # Initialize invoices list only if starting fresh
            if self.processing_start_index == 0:
                self.invoices = []
                self.review_queue = []

            # Determine starting index
            start_index = self.processing_start_index
            if start_index >= total:
                self.after(0, lambda: messagebox.showinfo("Info", "Všechny soubory již byly zpracovány"))
                return

            # Zpracování každého souboru
            for i in range(start_index, total):
                # Check stop flag before processing each file
                if self.stop_flag:
                    logger.info(f"⏹️ Zastaveno po zpracování {i} souborů z {total}")
                    self.resume_index = i  # Save current position for resume
                    self.after(
                        0,
                        lambda: (
                            self.progress_label.configure(text=f"⏹️ Zastaveno po {i}/{total} souborech"),
                            self._processing_stopped()
                        )
                    )
                    return

                file_path = self.found_files[i]
                try:
                    progress = (i + 1) / total
                    self.after(
                        0,
                        lambda p=progress, f=file_path: (
                            self.progress_bar.set(p),
                            self.progress_label.configure(text=f"[{i + 1}/{total}] {f.name}")
                        )
                    )

                    # OCR extrakce
                    # Returns: (text, is_digital)
                    ocr_text, is_digital_text = self.ocr_extractor.extract_text(file_path)

                    if not ocr_text:
                        logger.warning(f"  ⚠️ Nepodařilo se extrahovat text: {file_path.name} - přeskočeno")
                        # Vytvořit invoice s chybou pro statistiku
                        invoice = Invoice(
                            source_path=file_path,
                            is_invoice=False,
                            confidence=0.0,
                            decision_type='error'
                        )
                        invoice.raw_json = {'error': 'OCR failed'}
                        self.invoices.append(invoice)
                        continue

                    # v6.12: ULOŽENÍ SUROVÉHO TEXTU PRO DEBUGGING (převzato z čtečka/app.py)
                    if SAVE_RAW_TEXT:
                        self._save_raw_text(file_path, ocr_text, is_digital_text)

                    # OCR Text Validator - POUZE pro OCR text (ne pro digitální)
                    # Digitální text z PDF je 100% přesný, nemá smysl ho validovat
                    if is_digital_text:
                        logger.debug(f"  ✓ Digitální text detekován - OCR validátor přeskočen: {file_path.name}")
                        validation_confidence = 1.0  # Plná důvěra v digitální text
                    else:
                        logger.debug(f"  🔍 Validace OCR textu: {file_path.name}")
                        try:
                            ocr_text, validation_result = validate_ocr_text(ocr_text, language="auto")

                            # Log validation summary
                            if validation_result['hallucinated_words'] > 0:
                                logger.info(f"  ✓ OCR Validator: {validation_result['valid_words']}/{validation_result['original_words']} slov validní, "
                                           f"{validation_result['corrected_words']} opraveno, "
                                           f"⚠️ {validation_result['hallucinated_words']} halucinací odstraněno")
                                logger.debug(f"    Hallucinations: {validation_result['hallucinations'][:5]}")
                            else:
                                logger.debug(f"  ✓ OCR Validator: {validation_result['valid_words']}/{validation_result['original_words']} slov validní")

                            # Store validation result for debugging
                            validation_confidence = validation_result['confidence']

                            # If confidence is too low, warn user
                            if validation_confidence < 0.5:
                                logger.warning(f"  ⚠️ Nízká kvalita OCR textu ({validation_confidence:.0%}) - výsledek může být nepřesný")

                        except Exception as validation_error:
                            logger.warning(f"  ⚠️ OCR Validator selhal: {validation_error} - používám původní text")
                            # Continue with original OCR text

                    # Pravidlový předběžný filtr
                    pre_class, pre_conf, pre_reason = self.ocr_extractor.pre_filter(ocr_text)

                    if pre_class == 'reject' and pre_conf > 0.9:
                        # Jisté zamítnutí - přeskočit AI
                        invoice = Invoice(
                            source_path=file_path,
                            is_invoice=False,
                            confidence=pre_conf,
                            decision_type='auto_reject'
                        )
                        invoice.raw_json = {'pre_filter': True, 'reason': pre_reason}
                        self.invoices.append(invoice)
                    else:
                        # Multi-agent analýza
                        logger.debug(f"  🔄 Spouštím agenty pro: {file_path.name}")
                        invoice = self.agent_processor.analyze_document(ocr_text, file_path)

                        if invoice:
                            self.invoices.append(invoice)

                            if invoice.requires_review:
                                self.review_queue.append(invoice)
                        else:
                            logger.warning(f"  ⚠️ analyze_document vrátil None pro: {file_path.name}")

                    # Memory management - častější monitoring
                    if (i + 1) % MEMORY_CHECK_EVERY == 0:
                        mem = get_memory_usage()
                        logger.debug(f"💾 Paměť [{i + 1}/{total}]: RSS={mem['rss_mb']:.1f}MB ({mem['percent']:.1f}%)")

                        if mem['percent'] > MAX_MEMORY_PERCENT:
                            self.after(0, lambda: self.progress_label.configure(text="⚠️ Uvolňování paměti..."))
                            gc.collect()
                            log_memory_state("after_gc")

                    # Agresivní GC každých 10 souborů
                    if (i + 1) % GC_EVERY == 0:
                        gc.collect()
                        mem = get_memory_usage()
                        logger.info(f"💾 GC po {i + 1} souborech: RSS={mem['rss_mb']:.1f}MB ({mem['percent']:.1f}%)")
                        
                except Exception as file_error:
                    logger.error(f"  ✗ Chyba při zpracování {file_path.name}: {file_error}")
                    import traceback
                    logger.error(f"  Traceback: {traceback.format_exc()}")
                    # Vytvořit invoice s chybou
                    invoice = Invoice(
                        source_path=file_path,
                        is_invoice=False,
                        confidence=0.0,
                        decision_type='error'
                    )
                    invoice.raw_json = {'error': str(file_error)}
                    self.invoices.append(invoice)
                    # Pokračovat dalším souborem
                    continue

            # Final GC před dokončením
            gc.collect()
            log_memory_state("before_finalization")

            # Hotovo
            stats = self.agent_processor.get_statistics()
            self.after(0, lambda: self._update_stats(stats))

            self.filtered_invoices = self.invoices.copy()
            self.after(0, self._update_tree)

            self.after(
                0,
                lambda: (
                    self.progress_bar.set(1),
                    self.progress_label.configure(text="✅ Analýza dokončena"),
                    self._show_summary(stats)
                )
            )

        except Exception as e:
            logger.error(f"Chyba zpracování: {e}")
            import traceback
            error_msg = traceback.format_exc()
            self.after(0, lambda: messagebox.showerror("Chyba", f"Chyba zpracování:\n{error_msg}"))

        finally:
            # Only reset flags if not stopped (i.e. if completed or error)
            if not self.stop_flag:
                self.is_processing = False
                self.processing_start_index = 0  # Reset for next run
                self.resume_index = 0  # Reset resume index
                self.after(0, lambda: (
                    self.process_btn.configure(state="normal", text="▶️ Spustit analýzu"),
                    self.stop_btn.configure(state="disabled"),
                    self.resume_btn.configure(state="disabled"),
                    log_memory_state("end")
                ))

    def _processing_stopped(self):
        """Called when processing is stopped by user."""
        self.is_processing = False
        self.stop_flag = False  # Reset stop flag for next run
        
        # Update statistics from agent processor
        if self.agent_processor:
            stats = self.agent_processor.get_statistics()
            self.after(0, lambda: self._update_stats(stats))
        
        # Update table with processed invoices
        self.filtered_invoices = self.invoices.copy()
        self.after(0, self._update_tree)
        
        # Update button states
        self.process_btn.configure(state="normal", text="▶️ Spustit analýzu")
        self.stop_btn.configure(state="disabled")
        # Enable resume button if there are remaining files
        if self.resume_index < len(self.found_files):
            self.resume_btn.configure(state="normal")
        
        # Show info message
        processed = len(self.invoices)
        remaining = len(self.found_files) - self.resume_index
        self.after(
            0,
            lambda: messagebox.showinfo(
                "Zastaveno",
                f"Zpracování zastaveno.\n\n"
                f"✅ Zpracováno: {processed} souborů\n"
                f"⏳ Zbývá: {remaining} souborů\n\n"
                f"Můžete pokračovat kliknutím na '▶️ Pokračovat'"
            )
        )
        
        logger.info(f"✅ Zpracování zastaveno. Lze pokračovat od souboru {self.resume_index + 1}.")

    def _show_summary(self, stats: dict):
        """Zobrazí shrnutí výsledků."""
        total = stats.get('total', 0)
        if total == 0:
            return

        invoices = stats.get('invoices', 0)
        non_invoices = stats.get('non_invoices', 0)
        review = stats.get('human_review', 0)

        summary = (
            f"📊 Výsledky analýzy\n\n"
            f"Celkem dokumentů: {total}\n"
            f"✅ Faktury: {invoices} ({invoices/total*100:.1f}%)\n"
            f"❌ Odmítnuté: {non_invoices} ({non_invoices/total*100:.1f}%)\n"
            f"⚠️ K revizi: {review} ({review/total*100:.1f}%)\n\n"
            f"Multi-Agent workflow dokončil analýzu."
        )

        messagebox.showinfo("Shrnutí", summary)

    def _move_selected_invoices(self):
        """Přesune pouze označené faktury."""
        if not self.selected_invoices:
            messagebox.showinfo(
                "Info",
                "Nejdříve označte faktury které chcete přesunout.\n\n"
                "Výběr provedete:\n"
                "• Kliknutím na jednotlivé řádky\n"
                "• Shift+Klik pro výběr rozsahu\n"
                "• Ctrl+Klik pro výběr více řádků\n"
                "• Tlačítkem '✅ Označit vše'"
            )
            return

        target = self.target_dir.get()
        if not target:
            messagebox.showerror("Chyba", "Zadejte cílovou složku")
            return

        # Filtr pouze označené faktury
        to_move = [inv for inv in self.selected_invoices if inv.is_invoice]

        if not to_move:
            messagebox.showinfo("Info", "Žádné označené faktury k přesunu")
            return

        if messagebox.askyesno(
            "Potvrzení",
            f"Přesunout {len(to_move)} označených faktur do:\n{target}?"
        ):
            organizer = InvoiceOrganizer(to_move, Path(target))
            success, errors = organizer.process("issue_date")
            
            # Odznačit přesunuté
            for inv in to_move:
                if inv in self.selected_invoices:
                    self.selected_invoices.remove(inv)
            
            # Aktualizovat zobrazení
            self._apply_filters()
            self._on_tree_select()
            
            messagebox.showinfo(
                "Hotovo",
                f"Přesunuto: {success}\nChyby: {errors}"
            )

    def _move_invoices(self):
        """Přesune identifikované faktury (legacy funkce)."""
        # Přesměrovat na novou funkci
        self._move_selected_invoices()

    def _show_document_detail(self, event):
        """Zobrazí detail dokumentu po double-click."""
        selection = self.tree.selection()
        if not selection:
            return

        item = self.tree.item(selection[0])
        filename = item['values'][0]

        # Najít invoice
        invoice = None
        for inv in self.filtered_invoices:
            if inv.source_path.name == filename:
                invoice = inv
                break

        if not invoice:
            return

        # Detail window
        detail_window = ctk.CTkToplevel(self)
        detail_window.title(f"📄 Detail: {filename}")
        detail_window.geometry("800x600")

        # Scrollable frame
        scroll_frame = ctk.CTkScrollableFrame(detail_window)
        scroll_frame.pack(fill="both", expand=True, padx=20, pady=20)

        # Basic info
        ctk.CTkLabel(
            scroll_frame,
            text="📋 Základní informace",
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w", pady=(0, 10))

        info_text = (
            f"Soubor: {invoice.source_path.name}\n"
            f"Odesílatel: {invoice.sender_name or '-'}\n"
            f"Příjemce: {invoice.recipient_name or '-'}\n"
            f"Datum vystavení: {invoice.issue_date or '-'}\n"
            f"Datum splatnosti: {invoice.due_date or '-'}\n"
            f"Částka: {invoice.total_amount or '-'} {invoice.currency or ''}\n"
            f"Číslo faktury: {invoice.invoice_number or '-'}\n"
        )
        ctk.CTkLabel(scroll_frame, text=info_text, justify="left").pack(anchor="w", pady=5)

        # Decision
        ctk.CTkLabel(
            scroll_frame,
            text="🗳️ Rozhodnutí",
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w", pady=(20, 10))

        decision_text = (
            f"Typ: {invoice.decision_type}\n"
            f"Jistota: {invoice.confidence:.0%}\n"
            f"Je faktura: {'Ano' if invoice.is_invoice else 'Ne'}"
        )
        ctk.CTkLabel(scroll_frame, text=decision_text, justify="left").pack(anchor="w", pady=5)

        # Agent results
        if invoice.agent_results:
            ctk.CTkLabel(
                scroll_frame,
                text="🤖 Výsledky agentů",
                font=ctk.CTkFont(size=18, weight="bold")
            ).pack(anchor="w", pady=(20, 10))

            for agent_name, result in invoice.agent_results.items():
                ctk.CTkLabel(
                    scroll_frame,
                    text=f"\n{agent_name.upper()}:",
                    font=ctk.CTkFont(weight="bold")
                ).pack(anchor="w")
                
                result_json = json.dumps(result, indent=2, ensure_ascii=False)
                text_widget = ctk.CTkTextbox(scroll_frame, height=100, wrap="word")
                text_widget.insert("0.0", result_json)
                text_widget.configure(state="disabled")
                text_widget.pack(fill="x", pady=5)

            # Consensus reasoning
            if invoice.consensus_result.get('reasoning'):
                ctk.CTkLabel(
                    scroll_frame,
                    text="\n💡 Konsenzus:",
                    font=ctk.CTkFont(weight="bold")
                ).pack(anchor="w", pady=(10, 5))
                ctk.CTkLabel(
                    scroll_frame,
                    text=invoice.consensus_result['reasoning'],
                    justify="left",
                    wraplength=750
                ).pack(anchor="w", pady=5)


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    if not AGENTS_AVAILABLE:
        print("⚠️ Agent workflow module not available!")
        print("\nInstalace:")
        print("  pip install ollama")
        print("  ollama pull llama3.1")
        print("\nSpustím základní verzi bez agentů...")

    try:
        app = InvoiceProcessorGUIV6()
        app.mainloop()
    except KeyboardInterrupt:
        print("\n👋 Ukončeno uživatelem")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Fatal error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
