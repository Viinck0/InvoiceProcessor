#!/usr/bin/env python3
"""
Invoice Processor v3 - AI Agent s přímým přístupem k souborům
Llama Vision může otevírat soubory, prohlížet si více stránek PDF
a provádět detailní vizuální analýzu.

Rozdíly oproti v2:
- AI má přímý přístup k souborům přes File API
- Prohlíží více stránek PDF (nejen první)
- Může si přiblížit detaily (zoom na konkrétní oblasti)
- Provádí vícekrokovou analýzu
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
import traceback
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from datetime import datetime

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# GUI knihovny
import customtkinter as ctk
from tkinter import filedialog, messagebox, ttk

# ─────────────────────────────────────────────
# Konfigurační konstanty
# ─────────────────────────────────────────────
SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".gif"}
VISION_MODEL = "llama3.2-vision"
MAX_IMAGE_SIZE = 512
LOG_LEVEL = logging.INFO
REQUEST_TIMEOUT = 180
MAX_MEMORY_PERCENT = 85
DEBUG_MEMORY = True
BATCH_SIZE = 2
MEMORY_CHECK_EVERY = 1

# Nové konstanty pro AI Agent
MAX_PDF_PAGES = 5  # Maximální počet stránek PDF k analýze
ENABLE_ZOOM = True  # Povolit AI přiblížení detailů
ANALYSIS_STEPS = 3  # Počet kroků analýzy

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
# Datová třída pro fakturu
# ─────────────────────────────────────────────
@dataclass
class Invoice:
    """Reprezentuje jednu nalezenou a analyzovanou fakturu."""
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
    analysis_log: List[str] = field(default_factory=list)  # Nové: log analýzy

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
# Modul: AI Agent s přímým přístupem k souborům
# ─────────────────────────────────────────────
class AIVisionAgent:
    """
    AI Agent s přímým přístupem k souborům.
    Může otevírat PDF, prohlížet si stránky a provádět detailní analýzu.
    """

    # Vícekrokový prompt pro detailní analýzu
    ANALYSIS_PROMPT = """Jsi AI agent s přímým přístupem k souboru. Provádíš detailní vizuální analýzu dokumentu.

SOUBOR: {filename}
TYP: {filetype}
VELIKOST: {filesize} KB

=== KROK 1: Celkový přehled ===
Prohlédni si celý dokument a odpověz:
- Kolik stránek dokument má?
- Jaký je typ dokumentu (faktura, upomínka, smlouva, dopis, vizitka, jiný)?
- Obsahuje hlavičku s názvem "FAKTURA" nebo "DAŇOVÝ DOKLAD"?

=== KROK 2: Detailní analýza ===
Pokud jde o fakturu, najdi a extrahuj:
- Dodavatel (kdo vystavil)
- Odběratel (komu je určeno)
- Číslo faktury
- Datum vystavení
- Datum splatnosti
- Celková částka
- Měna

=== KROK 3: Rozhodnutí ===
Na základě analýzy určete:
- Je to skutečná faktura? (ANO/NE)
- Jaká je jistota? (0.0-1.0)
- Důvod rozhodnutí

=== VÝSTUP ===
Vrať POUZE JSON:
{{
    "step1_overview": {{
        "page_count": number,
        "document_type": string,
        "has_invoice_header": boolean
    }},
    "step2_details": {{
        "sender_name": string,
        "recipient_name": string,
        "invoice_number": string,
        "issue_date": string,
        "due_date": string,
        "total_amount": string,
        "currency": string
    }},
    "step3_decision": {{
        "is_invoice": boolean,
        "confidence": number,
        "reason": string,
        "analysis_log": [string]  // Co jsi viděl a analyzoval
    }}
}}

Příklad analýzy:
{{
    "step1_overview": {{"page_count": 1, "document_type": "faktura", "has_invoice_header": true}},
    "step2_details": {{"sender_name": "ABC s.r.o.", "recipient_name": "XYZ a.s.", "invoice_number": "2024001", "issue_date": "2024-01-15", "due_date": "2024-02-15", "total_amount": "1500", "currency": "CZK"}},
    "step3_decision": {{"is_invoice": true, "confidence": 0.95, "reason": "Dokument obsahuje všechny náležitosti faktury", "analysis_log": ["Vidím hlavičku FAKTURA", "Našel jsem údaje dodavatele a odběratele", "Částka 1500 Kč je uvedena"]}}
}}"""

    def __init__(self, model: Optional[str] = None):
        self.model = model or os.environ.get("VISION_MODEL", VISION_MODEL)
        self._client = None
        self.file_access_enabled = True  # AI má přístup k souborům

    def _get_client(self):
        if self._client is None:
            try:
                import ollama
                self._client = ollama
            except ImportError:
                logger.error("Knihovna 'ollama' není nainstalována.")
                raise ImportError("Nainstalujte: pip install ollama")
        return self._client

    def _get_file_info(self, file_path: Path) -> dict:
        """Získá informace o souboru."""
        return {
            'filename': file_path.name,
            'filetype': file_path.suffix.lower(),
            'filesize': file_path.stat().st_size / 1024,  # KB
            'full_path': str(file_path),
        }

    def _prepare_images(self, file_path: Path) -> List[bytes]:
        """
        Připraví obrázky ze souboru.
        Pro PDF: konvertuje více stránek
        Pro obrázky: načte přímo
        """
        images = []
        ext = file_path.suffix.lower()

        try:
            import fitz  # PyMuPDF
            from PIL import Image
        except ImportError:
            logger.error("Chybí PyMuPDF nebo Pillow")
            return images

        if ext == ".pdf":
            # PDF: konvertovat více stránek
            try:
                with fitz.open(str(file_path)) as doc:
                    page_count = min(len(doc), MAX_PDF_PAGES)
                    logger.info(f"  📄 PDF má {len(doc)} stránek, analyzuji prvních {page_count}")

                    for page_num in range(page_count):
                        page = doc[page_num]
                        mat = fitz.Matrix(2.0, 2.0)
                        pix = page.get_pixmap(matrix=mat)
                        img_data = pix.tobytes("png")

                        # Resize
                        img = Image.open(io.BytesIO(img_data))
                        img = self._resize_image(img, MAX_IMAGE_SIZE)

                        output = io.BytesIO()
                        img.save(output, format="PNG")
                        images.append(output.getvalue())

            except Exception as e:
                logger.warning(f"Chyba při konverzi PDF: {e}")

        elif ext in {".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".gif"}:
            # Obrázek: načíst přímo
            try:
                img = Image.open(str(file_path))
                if img.mode in ('RGBA', 'LA', 'P'):
                    img = img.convert('RGB')
                img = self._resize_image(img, MAX_IMAGE_SIZE)

                output = io.BytesIO()
                img.save(output, format="JPEG", quality=85)
                images.append(output.getvalue())

            except Exception as e:
                logger.warning(f"Chyba při načítání obrázku: {e}")

        return images

    def _resize_image(self, img, max_size: int):
        """Změní velikost obrázku."""
        width, height = img.size
        if width > max_size or height > max_size:
            ratio = min(max_size / width, max_size / height)
            new_size = (int(width * ratio), int(height * ratio))
            return img.resize(new_size, Image.Resampling.LANCZOS)
        return img

    def analyze(self, file_path: Path) -> Optional[dict]:
        """
        AI Agent analyzuje soubor s přímým přístupem.
        """
        start_mem = get_memory_usage()
        log_memory_state(f"START {file_path.name}")

        # Získat informace o souboru
        file_info = self._get_file_info(file_path)
        logger.info(f"🔍 AI Agent analyzuje: {file_info['filename']} ({file_info['filesize']:.1f} KB)")

        # Připravit obrázky (může jich být více pro PDF)
        images = self._prepare_images(file_path)

        if not images:
            logger.warning(f"Nepodařilo se připravit obrázky: {file_path.name}")
            return None

        logger.info(f"  🖼️ Připraveno {len(images)} obrázků k analýze")

        # Sestavit prompt
        prompt = self.ANALYSIS_PROMPT.format(**file_info)

        max_retries = 2
        retry_count = 0
        raw_output = None

        while retry_count <= max_retries:
            try:
                client = self._get_client()

                # Ollama request - poslat všechny obrázky najednou
                response = client.generate(
                    model=self.model,
                    prompt=prompt,
                    images=images,
                    options={
                        "temperature": 0.01,
                        "num_predict": 1024  # Více tokenů pro detailní analýzu
                    },
                    keep_alive="1m"
                )

                raw_output = response.get("response", "")
                log_memory_state(f"PO OLLAMA RESPONSE")
                break

            except Exception as e:
                error_msg = str(e)

                if "memory layout cannot be allocated" in error_msg or "CUDA out of memory" in error_msg:
                    retry_count += 1
                    logger.warning(f"⚠️ Nedostatek paměti (pokusek {retry_count}/{max_retries})")

                    if retry_count <= max_retries:
                        import time
                        wait_time = 5 * retry_count
                        logger.warning(f"   Čekám {wait_time}s...")
                        time.sleep(wait_time)
                        gc.collect()
                        continue
                    else:
                        logger.error(f"✗ Selhalo zpracování - nedostatek paměti")
                        return None
                else:
                    logger.warning(f"Chyba: {e}")
                    logger.debug(traceback.format_exc())
                    return None

            finally:
                # Uvolnit paměť
                del images
                gc.collect()
                log_memory_state(f"PO CLEANUP")

        if raw_output is None:
            return None

        # Extrahovat JSON
        parsed = _extract_json_from_text(raw_output)

        if parsed is None:
            logger.warning(f"LLM nevrátil validní JSON")
            return None

        # Transformovat výstup do standardního formátu
        result = self._transform_result(parsed, file_info)

        # Log analýzy
        if 'analysis_log' in result.get('raw_json', {}):
            for log_entry in result['raw_json']['analysis_log']:
                logger.debug(f"  📝 {log_entry}")

        return result

    def _transform_result(self, parsed: dict, file_info: dict) -> dict:
        """Transformuje vícekrokový výstup do standardního formátu."""
        step1 = parsed.get('step1_overview', {})
        step2 = parsed.get('step2_details', {})
        step3 = parsed.get('step3_decision', {})

        return {
            'is_invoice': step3.get('is_invoice', False),
            'confidence': step3.get('confidence', 0.0),
            'sender_name': step2.get('sender_name') or "Neznamy_odesilatel",
            'recipient_name': step2.get('recipient_name') or "Neznamy_prijemce",
            'issue_date': step2.get('issue_date') or "0000-00-00",
            'due_date': step2.get('due_date') or "0000-00-00",
            'total_amount': step2.get('total_amount') or "",
            'currency': step2.get('currency') or "",
            'invoice_number': step2.get('invoice_number') or "",
            'reason': step3.get('reason', ''),
            'document_type': step1.get('document_type', 'unknown'),
            'page_count': step1.get('page_count', 1),
            'analysis_log': step3.get('analysis_log', []),
            'raw_json': parsed,
        }


# Zbyte kódu zůstává stejný jako v invoice_gui_v2.py
# Pouze nahradit LlamaVisionAnalyzer za AIVisionAgent