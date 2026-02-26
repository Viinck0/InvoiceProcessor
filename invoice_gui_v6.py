#!/usr/bin/env python3
"""
Invoice Processor v6 - Multi-Agent Workflow
Nejpřesnější systém pro třídění faktur s 3 specializovanými AI agenty

Workflow:
1. OCR Extrakce textu (PyMuPDF + pytesseract)
2. Pravidlový předběžný filtr
3. Paralelní AI analýza (Classifier + Extractor + Anomaly Detector)
4. Consensus Engine (vážené hlasování + veto)
5. Human Review Queue pro nejisté případy

Použití:
    python invoice_gui_v6.py
"""

import os
import re
import sys
import json
import shutil
import logging
import threading
import io
import gc
import time
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
        ConsensusEngine
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
LOG_LEVEL = logging.INFO
REQUEST_TIMEOUT = 60
MAX_MEMORY_PERCENT = 70  # Snížen z 85% pro dřívější GC
DEBUG_MEMORY = True
BATCH_SIZE = 3
MEMORY_CHECK_EVERY = 5  # Kontrolovat každých 5 souborů (bylo 1)
GC_EVERY = 10  # Spustit GC každých 10 souborů

# OCR konstanty
OCR_LANG = "ces+eng"
OCR_DPI = 150
USE_TESSERACT = True

# Agent thresholds
THRESHOLD_ACCEPT = 0.7
THRESHOLD_REVIEW = 0.5
ANOMALY_VETO_THRESHOLD = 0.85
MIN_REALISTIC_AMOUNT = 10  # Minimální realistická částka (CZK/EUR/USD)
CHECK_AMOUNT_REALISTIC = False  # Kontrolovat podezřele nízké částky (False = přijmout i symbolické částky)

# Běžné instalační cesty pro Tesseract na Windows
TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe",
]

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
# Modul: OCR Text Extractor
# ─────────────────────────────────────────────
class OCRExtractor:
    """Extrahuje text z PDF a obrázků pomocí Python knihoven."""

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

    def __init__(self):
        self.fitz = None
        self.pytesseract = None
        self.Image = None
        self._init_libraries()

    def _init_libraries(self):
        try:
            import fitz
            self.fitz = fitz
            logger.debug("PyMuPDF načten")
        except ImportError as e:
            logger.warning(f"PyMuPDF není nainstalován: {e}")

        try:
            import pytesseract
            self.pytesseract = pytesseract
            logger.debug("pytesseract načten")
        except ImportError as e:
            logger.warning(f"pytesseract není nainstalován: {e}")
            return

        try:
            from PIL import Image
            self.Image = Image
            logger.debug("PIL načten")
        except ImportError as e:
            logger.warning(f"Pillow není nainstalován: {e}")

        if self.pytesseract is not None:
            self._find_tesseract()

    def _find_tesseract(self) -> bool:
        import shutil

        tesseract_path = shutil.which("tesseract")
        if tesseract_path:
            logger.debug(f"✓ Tesseract nalezen v PATH: {tesseract_path}")
            try:
                self.pytesseract.pytesseract.tesseract_cmd = tesseract_path
                self.pytesseract.get_tesseract_version()
                logger.info(f"✓ Tesseract připraven: {tesseract_path}")
                return True
            except Exception as e:
                logger.warning(f"Tesseract v PATH nefunguje: {e}")

        for path_template in TESSERACT_PATHS:
            path = os.path.expandvars(path_template)
            if os.path.isfile(path):
                logger.debug(f"✓ Tesseract nalezen: {path}")
                try:
                    self.pytesseract.pytesseract.tesseract_cmd = path
                    self.pytesseract.get_tesseract_version()
                    logger.info(f"✓ Tesseract připraven: {path}")
                    return True
                except Exception as e:
                    logger.warning(f"Tesseract nefunguje: {e}")

        default_path = TESSERACT_PATHS[0]
        self.pytesseract.pytesseract.tesseract_cmd = default_path
        logger.warning(f"⚠️ Tesseract nenalezen")
        return False

    def extract_text(self, file_path: Path) -> Optional[str]:
        ext = file_path.suffix.lower()

        if ext == ".pdf":
            return self._extract_from_pdf(file_path)
        elif ext in {".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".gif"}:
            return self._extract_from_image(file_path)

        return None

    def _extract_from_pdf(self, file_path: Path) -> Optional[str]:
        if self.fitz is None:
            logger.warning("PyMuPDF není dostupný")
            return None

        try:
            text_parts = []

            with self.fitz.open(str(file_path)) as doc:
                for page in doc:
                    page_text = page.get_text()
                    if page_text.strip():
                        text_parts.append(page_text)

            if text_parts:
                return "\n".join(text_parts)

            logger.info(f"  📄 PDF nemá textovou vrstvu, zkouším OCR...")
            return self._ocr_pdf_page(file_path, 0)

        except Exception as e:
            logger.warning(f"Chyba při čtení PDF: {e}")
            return None

    def _ocr_pdf_page(self, file_path: Path, page_num: int) -> Optional[str]:
        if not USE_TESSERACT or self.fitz is None or self.pytesseract is None or self.Image is None:
            return None

        tesseract_cmd = self.pytesseract.pytesseract.tesseract_cmd
        if not os.path.isfile(tesseract_cmd):
            return None

        try:
            with self.fitz.open(str(file_path)) as doc:
                if page_num >= len(doc):
                    return None

                page = doc[page_num]
                mat = self.fitz.Matrix(2.0, 2.0)
                pix = page.get_pixmap(matrix=mat)
                img_data = pix.tobytes("png")

                img = self.Image.open(io.BytesIO(img_data))
                text = self.pytesseract.image_to_string(img, lang=OCR_LANG)

                return text if text.strip() else None

        except Exception as e:
            logger.warning(f"Chyba OCR PDF: {e}")
            return None

    def _extract_from_image(self, file_path: Path) -> Optional[str]:
        if not USE_TESSERACT or self.pytesseract is None or self.Image is None:
            return None

        tesseract_cmd = self.pytesseract.pytesseract.tesseract_cmd
        if not os.path.isfile(tesseract_cmd):
            return None

        try:
            img = self.Image.open(str(file_path))

            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')

            text = self.pytesseract.image_to_string(img, lang=OCR_LANG)

            return text if text.strip() else None

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
    """

    def __init__(self):
        if not AGENTS_AVAILABLE:
            raise ImportError("Agent workflow module not available")
        
        self.classifier = ClassifierAgent(model=TEXT_MODEL, timeout=REQUEST_TIMEOUT)
        self.extractor = ExtractorAgent(model=TEXT_MODEL, timeout=REQUEST_TIMEOUT + 15)
        self.anomaly = AnomalyDetectorAgent(model=TEXT_MODEL, timeout=REQUEST_TIMEOUT)
        self.consensus = ConsensusEngine(
            threshold_accept=THRESHOLD_ACCEPT,
            threshold_review=THRESHOLD_REVIEW,
            anomaly_veto_threshold=ANOMALY_VETO_THRESHOLD
        )
        
        self.stats = {
            'total': 0,
            'invoices': 0,
            'non_invoices': 0,
            'human_review': 0,
            'errors': 0,
        }

    def analyze_document(self, ocr_text: str, file_path: Path) -> Optional[Invoice]:
        """
        Analyzuje dokument pomocí 3 agentů paralelně.
        """
        if not ocr_text or len(ocr_text.strip()) < 50:
            logger.debug(f"  ⚠️ Prázdný dokument: {file_path.name}")
            return None

        start_time = time.time()

        # Vytvoření invoice objektu (bez ukládání OCR textu - šetří paměť)
        invoice = Invoice(source_path=file_path)

        executor = None
        try:
            # Paralelní spuštění agentů
            executor = ThreadPoolExecutor(max_workers=3)
            
            # Submit all tasks
            future_classifier = executor.submit(
                self.classifier.analyze,
                ocr_text,
                {'filename': file_path.name}
            )
            future_extractor = executor.submit(
                self.extractor.analyze,
                ocr_text,
                {'filename': file_path.name}
            )
            future_anomaly = executor.submit(
                self.anomaly.analyze,
                ocr_text,
                {'filename': file_path.name}
            )

            # Wait for results with timeout
            try:
                classifier_result = future_classifier.result(timeout=REQUEST_TIMEOUT + 10)
                extractor_result = future_extractor.result(timeout=REQUEST_TIMEOUT + 25)
                anomaly_result = future_anomaly.result(timeout=REQUEST_TIMEOUT + 10)

                # Debug: log raw results
                logger.debug(f"Classifier result type: {type(classifier_result)}")
                logger.debug(f"Extractor result type: {type(extractor_result)}")
                logger.debug(f"Anomaly result type: {type(anomaly_result)}")

            except Exception as agent_error:
                logger.error(f"  Agent execution error: {agent_error}")
                logger.error(f"  Error type: {type(agent_error)}")
                import traceback
                logger.error(f"  Traceback: {traceback.format_exc()}")
                # Use fallback results
                classifier_result = {'is_invoice': False, 'confidence': 0.0, 'reasoning': 'Agent error'}
                extractor_result = {'completeness_score': 0.0, 'validation_errors': ['Error']}
                anomaly_result = {'is_anomaly': False, 'confidence': 0.0}

            # Explicitně shutdown executor
            executor.shutdown(wait=True)
            executor = None

            # Validate results are dicts
            if not isinstance(classifier_result, dict):
                logger.error(f"  Invalid classifier result type: {type(classifier_result)}")
                classifier_result = {'is_invoice': False, 'confidence': 0.0}
            
            if not isinstance(extractor_result, dict):
                logger.error(f"  Invalid extractor result type: {type(extractor_result)}")
                extractor_result = {'completeness_score': 0.0, 'validation_errors': ['Invalid result']}
            
            if not isinstance(anomaly_result, dict):
                logger.error(f"  Invalid anomaly result type: {type(anomaly_result)}")
                anomaly_result = {'is_anomaly': False, 'confidence': 0.0}

            # Uložení výsledků agentů
            invoice.agent_results = {
                'classifier': classifier_result,
                'extractor': extractor_result,
                'anomaly': anomaly_result,
            }

            # Výpočet konsenzu
            try:
                consensus_result = self.consensus.calculate_consensus(
                    classifier_result,
                    extractor_result,
                    anomaly_result
                )
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
        
        finally:
            # Vždy uvolnit executor
            if executor is not None:
                executor.shutdown(wait=True)
                executor = None

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

        self.title("🧾 Invoice Processor v6 - Multi-Agent Workflow")
        self.geometry("1200x850")
        self.minsize(1100, 750)

        # Proměnné
        self.source_dir = ctk.StringVar()
        self.target_dir = ctk.StringVar()
        self.sort_key = ctk.StringVar(value="issue_date")
        self.model_name = ctk.StringVar(value=TEXT_MODEL)

        # Filtr
        self.filter_key = ctk.StringVar(value="")
        self.filter_value = ctk.StringVar()

        # Initialize components
        self.ocr_extractor = OCRExtractor()
        self.agent_processor = None
        self.filter_agent = None

        self.is_processing = False
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
        self.tesseract_ready = self._check_tesseract_status()
        self.agents_ready = AGENTS_AVAILABLE

        self._setup_ui()

    def _check_tesseract_status(self) -> bool:
        if self.ocr_extractor.pytesseract is None:
            return False

        tesseract_cmd = self.ocr_extractor.pytesseract.pytesseract.tesseract_cmd
        if os.path.isfile(tesseract_cmd):
            try:
                self.ocr_extractor.pytesseract.get_tesseract_version()
                logger.info(f"✓ Tesseract připraven: {tesseract_cmd}")
                return True
            except Exception:
                pass
        return False

    def _setup_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(6, weight=1)

        # ─── Header ────────────────────────────
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 10))
        header_frame.grid_columnconfigure(0, weight=1)

        title_label = ctk.CTkLabel(
            header_frame,
            text="🧾 Invoice Processor v6",
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

        if self.tesseract_ready:
            ocr_status = "✓ OCR připraven"
            ocr_color = "#28a745"
        else:
            ocr_status = "✗ OCR nenalezen"
            ocr_color = "#dc3545"

        ctk.CTkLabel(
            status_frame,
            text="🤖 3 specializovaní AI agenti • Vážené hlasování • Detekce anomálií",
            font=ctk.CTkFont(size=14),
            text_color="gray"
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            status_frame,
            text=f"  {agent_status}  |  {ocr_status}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=agent_color if self.agents_ready else ocr_color
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))

        # ─── Nastavení složek ────────────────────
        settings_frame = ctk.CTkFrame(self)
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

        # ─── Filtry a třídění ────────────────────
        filter_frame = ctk.CTkFrame(self)
        filter_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=10)
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
        action_frame = ctk.CTkFrame(self)
        action_frame.grid(row=3, column=0, sticky="ew", padx=20, pady=10)

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

        self.move_btn = ctk.CTkButton(
            action_frame,
            text="📂 Přesunout označené",
            command=self._move_selected_invoices,
            height=40,
            fg_color="#007bff",
            hover_color="#0056b3"
        )
        self.move_btn.pack(side="left", padx=10, pady=10)

        self.select_all_btn = ctk.CTkButton(
            action_frame,
            text="✅ Označit vše",
            command=self._select_all_invoices,
            height=40,
            fg_color="#6c757d",
            hover_color="#5a6268"
        )
        self.select_all_btn.pack(side="left", padx=10, pady=10)

        self.deselect_all_btn = ctk.CTkButton(
            action_frame,
            text="❌ Zrušit výběr",
            command=self._deselect_all_invoices,
            height=40,
            fg_color="#6c757d",
            hover_color="#5a6268"
        )
        self.deselect_all_btn.pack(side="left", padx=10, pady=10)

        self.review_btn = ctk.CTkButton(
            action_frame,
            text="👁️ Human Review",
            command=self._show_review_queue,
            height=40,
            fg_color="#ffc107",
            hover_color="#e0a800",
            text_color="#000000"
        )
        self.review_btn.pack(side="left", padx=10, pady=10)

        # ─── Progress ────────────────────
        self.progress_frame = ctk.CTkFrame(self)
        self.progress_frame.grid(row=4, column=0, sticky="ew", padx=20, pady=10)
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
        self.stats_frame = ctk.CTkFrame(self)
        self.stats_frame.grid(row=5, column=0, sticky="ew", padx=20, pady=10)
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
        result_frame = ctk.CTkFrame(self)
        result_frame.grid(row=6, column=0, sticky="nsew", padx=20, pady=(0, 20))
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
            height=20,
            selectmode="extended"  # Podpora více výběrů
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

    def _update_stats(self, stats: dict = None):
        """Aktualizuje statistiky."""
        if stats:
            self.stat_labels["total"].configure(text=str(stats.get('total', 0)))
            self.stat_labels["invoices"].configure(text=str(stats.get('invoices', 0)))
            self.stat_labels["non_invoices"].configure(text=str(stats.get('non_invoices', 0)))
            self.stat_labels["review"].configure(text=str(stats.get('human_review', 0)))
            self.stat_labels["errors"].configure(text=str(stats.get('errors', 0)))

    def _start_processing(self):
        """Spustí zpracování na vlákně."""
        if self.is_processing:
            return

        source = self.source_dir.get()
        if not source or not Path(source).exists():
            messagebox.showerror("Chyba", "Zadejte platnou zdrojovou složku")
            return

        if not self.agents_ready:
            messagebox.showerror(
                "Chyba",
                "Agent workflow není dostupný.\n\n"
                "Nainstalujte: pip install ollama\n"
                "A spusťte: ollama pull llama3.1"
            )
            return

        self.is_processing = True
        self.process_btn.configure(state="disabled", text="⏳ Zpracovávám...")
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
            self.agent_processor = MultiAgentProcessor()
            self.filter_agent = InvoiceFilterAgent([])

            # Objevování souborů
            self.after(0, lambda: self.progress_label.configure(text="Vyhledávání souborů..."))
            discovery = FileDiscovery(Path(self.source_dir.get()))
            self.found_files = discovery.find_files()

            total = len(self.found_files)
            if total == 0:
                self.after(0, lambda: messagebox.showinfo("Info", "Žádné soubory nenalezeny"))
                return

            self.invoices = []
            self.review_queue = []

            # Zpracování každého souboru
            for i, file_path in enumerate(self.found_files, start=1):
                progress = i / total
                self.after(
                    0,
                    lambda p=progress, f=file_path: (
                        self.progress_bar.set(p),
                        self.progress_label.configure(text=f"[{i}/{total}] {f.name}")
                    )
                )

                # OCR extrakce
                ocr_text = self.ocr_extractor.extract_text(file_path)

                if not ocr_text:
                    logger.warning(f"  ⚠️ Nepodařilo se extrahovat text: {file_path.name}")
                    continue

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
                    invoice = self.agent_processor.analyze_document(ocr_text, file_path)

                    if invoice:
                        self.invoices.append(invoice)

                        if invoice.requires_review:
                            self.review_queue.append(invoice)

                # Memory management - častější monitoring
                if i % MEMORY_CHECK_EVERY == 0:
                    mem = get_memory_usage()
                    logger.debug(f"💾 Paměť [{i}/{total}]: RSS={mem['rss_mb']:.1f}MB ({mem['percent']:.1f}%)")
                    
                    if mem['percent'] > MAX_MEMORY_PERCENT:
                        self.after(0, lambda: self.progress_label.configure(text="⚠️ Uvolňování paměti..."))
                        gc.collect()
                        log_memory_state("after_gc")
                
                # Agresivní GC každých 10 souborů
                if i % GC_EVERY == 0:
                    gc.collect()
                    mem = get_memory_usage()
                    logger.info(f"💾 GC po {i} souborech: RSS={mem['rss_mb']:.1f}MB ({mem['percent']:.1f}%)")

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
            self.is_processing = False
            self.after(0, lambda: (
                self.process_btn.configure(state="normal", text="▶️ Spustit analýzu"),
                log_memory_state("end")
            ))

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

    def _show_review_queue(self):
        """Zobrazí frontu k human review."""
        if not self.review_queue:
            messagebox.showinfo("Info", "Žádné dokumenty k revizi")
            return

        # Vytvořit okno pro review
        review_window = ctk.CTkToplevel(self)
        review_window.title(f"👁️ Human Review ({len(self.review_queue)} dokumentů)")
        review_window.geometry("1000x600")

        # Frame pro tlačítka
        btn_frame = ctk.CTkFrame(review_window)
        btn_frame.pack(fill="x", padx=20, pady=10)

        current_idx = [0]  # Mutable index

        def show_item():
            if current_idx[0] >= len(self.review_queue):
                return

            inv = self.review_queue[current_idx[0]]
            
            # Clear existing
            for widget in review_window.winfo_children():
                if widget != btn_frame:
                    widget.destroy()

            # Content frame
            content = ctk.CTkFrame(review_window)
            content.pack(fill="both", expand=True, padx=20, pady=10)
            content.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(content, text="📄 Soubor:", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w", pady=5)
            ctk.CTkLabel(content, text=inv.source_path.name).grid(row=0, column=1, sticky="w", pady=5)

            ctk.CTkLabel(content, text="🏢 Odesílatel:").grid(row=1, column=0, sticky="w", pady=5)
            ctk.CTkLabel(content, text=inv.sender_name or "-").grid(row=1, column=1, sticky="w", pady=5)

            ctk.CTkLabel(content, text="👤 Příjemce:").grid(row=2, column=0, sticky="w", pady=5)
            ctk.CTkLabel(content, text=inv.recipient_name or "-").grid(row=2, column=1, sticky="w", pady=5)

            ctk.CTkLabel(content, text="📅 Datum:").grid(row=3, column=0, sticky="w", pady=5)
            ctk.CTkLabel(content, text=inv.issue_date or "-").grid(row=3, column=1, sticky="w", pady=5)

            ctk.CTkLabel(content, text="💰 Částka:").grid(row=4, column=0, sticky="w", pady=5)
            ctk.CTkLabel(content, text=f"{inv.total_amount} {inv.currency}" if inv.total_amount else "-").grid(row=4, column=1, sticky="w", pady=5)

            # Agent results
            ctk.CTkLabel(content, text="\n🤖 Výsledky agentů:", font=ctk.CTkFont(weight="bold")).grid(row=5, column=0, columnspan=2, sticky="w", pady=(20, 5))

            agent_results = inv.agent_results
            
            if agent_results:
                # Classifier
                clf = agent_results.get('classifier', {})
                ctk.CTkLabel(
                    content,
                    text=f"📋 Classifier: {'Faktura' if clf.get('is_invoice') else 'Není faktura'} ({clf.get('confidence', 0):.0%})",
                    text_color="#28a745" if clf.get('is_invoice') else "#dc3545"
                ).grid(row=6, column=0, columnspan=2, sticky="w", pady=2)

                # Extractor
                ext = agent_results.get('extractor', {})
                ctk.CTkLabel(
                    content,
                    text=f"📊 Extractor: Completeness {ext.get('completeness_score', 0):.0%}"
                ).grid(row=7, column=0, columnspan=2, sticky="w", pady=2)

                # Anomaly
                anom = agent_results.get('anomaly', {})
                if anom.get('is_anomaly'):
                    ctk.CTkLabel(
                        content,
                        text=f"⚠️ Anomaly Detector: {anom.get('anomaly_type', 'N/A')} ({anom.get('confidence', 0):.0%})",
                        text_color="#dc3545"
                    ).grid(row=8, column=0, columnspan=2, sticky="w", pady=2)
                else:
                    ctk.CTkLabel(
                        content,
                        text="✓ Anomaly Detector: Žádné anomálie",
                        text_color="#28a745"
                    ).grid(row=8, column=0, columnspan=2, sticky="w", pady=2)

            # Consensus
            ctk.CTkLabel(content, text="\n🗳️ Konsenzus:", font=ctk.CTkFont(weight="bold")).grid(row=9, column=0, columnspan=2, sticky="w", pady=(20, 5))
            ctk.CTkLabel(
                content,
                text=f"Rozhodnutí: {inv.decision_type} (jistota: {inv.confidence:.0%})",
                font=ctk.CTkFont(size=14, weight="bold")
            ).grid(row=10, column=0, columnspan=2, sticky="w", pady=5)
            
            if inv.consensus_result.get('reasoning'):
                ctk.CTkLabel(
                    content,
                    text=f"Důvod: {inv.consensus_result['reasoning']}",
                    text_color="gray",
                    wraplength=800
                ).grid(row=11, column=0, columnspan=2, sticky="w", pady=5)

            # Navigation
            nav_frame = ctk.CTkFrame(content)
            nav_frame.grid(row=12, column=0, columnspan=2, pady=20)

            ctk.CTkLabel(
                nav_frame,
                text=f"Dokument {current_idx[0] + 1} z {len(self.review_queue)}"
            ).pack(side="left", padx=20)

            if current_idx[0] > 0:
                ctk.CTkButton(
                    nav_frame,
                    text="← Předchozí",
                    command=lambda: (dec_idx(), show_item())
                ).pack(side="left", padx=10)

            if current_idx[0] < len(self.review_queue) - 1:
                ctk.CTkButton(
                    nav_frame,
                    text="Další →",
                    command=lambda: (inc_idx(), show_item())
                ).pack(side="left", padx=10)

            # Action buttons
            action_frame = ctk.CTkFrame(content)
            action_frame.grid(row=13, column=0, columnspan=2, pady=20)

            def accept_invoice():
                inv.is_invoice = True
                inv.decision_type = 'auto_accept'
                next_or_close()

            def reject_invoice():
                inv.is_invoice = False
                inv.decision_type = 'auto_reject'
                next_or_close()

            def next_or_close():
                if current_idx[0] < len(self.review_queue) - 1:
                    current_idx[0] += 1
                    show_item()
                else:
                    review_window.destroy()
                    self._apply_filters()
                    self._update_tree()

            ctk.CTkButton(
                action_frame,
                text="✅ Přijmout jako fakturu",
                command=accept_invoice,
                fg_color="#28a745",
                hover_color="#218838"
            ).pack(side="left", padx=10)

            ctk.CTkButton(
                action_frame,
                text="❌ Odmítnout",
                command=reject_invoice,
                fg_color="#dc3545",
                hover_color="#c82333"
            ).pack(side="left", padx=10)

        def dec_idx():
            current_idx[0] = max(0, current_idx[0] - 1)

        def inc_idx():
            current_idx[0] = min(len(self.review_queue) - 1, current_idx[0] + 1)

        show_item()

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

    app = InvoiceProcessorGUIV6()
    app.mainloop()


if __name__ == "__main__":
    main()
