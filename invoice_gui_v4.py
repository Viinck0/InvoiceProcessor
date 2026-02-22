#!/usr/bin/env python3
"""
Invoice Processor v4 - Dvou-agentový systém
Rychlejší analýza díky rozdělení práce:

Agent 1: Llama Vision (nebo jiný vision model)
  - Pouze rychlá analýza obrázku
  - Odpoví: Je to faktura? + základní data
  
Agent 2: Ollama 3.2 (textový model)
  - Převezme data z Vision
  - Validuje, doplní, formatuje JSON
  - Rychlejší protože pracuje jen s textem

Použití:
    python invoice_gui_v4.py
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
from typing import Optional, Callable, List
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
VISION_MODEL = "llama3.2-vision"  # Pro analýzu obrázků
TEXT_MODEL = "llama3.1"           # Pro zpracování textu (rychlý!)
MAX_IMAGE_SIZE = 512
LOG_LEVEL = logging.INFO
REQUEST_TIMEOUT = 120
MAX_MEMORY_PERCENT = 85
DEBUG_MEMORY = True
BATCH_SIZE = 3
MEMORY_CHECK_EVERY = 1

# Nové konstanty pro dvou-agentový systém
USE_TWO_AGENT_SYSTEM = True  # Povolit dvou-agentový režim
VISION_ONLY_CHECK = True     # Vision jen kontroluje zda je faktura

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
    analysis_log: List[str] = field(default_factory=list)
    document_type: str = ""
    page_count: int = 1

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
# Agent 1: Vision Agent (rychlá analýza)
# ─────────────────────────────────────────────
class VisionAgent:
    """
    Agent 1: Pouze rychlá analýza obrázku.
    Zjistí zda je to faktura a extrahuje základní data.
    """

    VISION_PROMPT = """You are a document classifier. Analyze this image and return ONLY JSON.

CRITICAL: Return ONLY valid JSON, no other text!

{{
    "is_invoice": true/false,
    "document_type": "faktura" or "upomínka" or "smlouva" or "poznámka" or "jiné",
    "confidence": 0.0-1.0,
    "sender_name": "extract or empty string",
    "recipient_name": "extract or empty string",
    "issue_date": "YYYY-MM-DD or empty",
    "total_amount": "extract with currency or empty",
    "currency": "CZK/EUR/USD or empty",
    "invoice_number": "extract or empty",
    "due_date": "YYYY-MM-DD or empty"
}}

If NOT an invoice, set is_invoice:false and document_type appropriately.
Keep text values short and concise."""

    def __init__(self, model: str = VISION_MODEL):
        self.model = model
        self._client = None
        try:
            import fitz
            from PIL import Image
            self.fitz = fitz
            self.Image = Image
        except ImportError as e:
            logger.error(f"Chybí knihovna: {e}")
            self.fitz = None
            self.Image = None

    def _get_client(self):
        if self._client is None:
            try:
                import ollama
                self._client = ollama
            except ImportError:
                logger.error("Knihovna 'ollama' není nainstalována.")
                raise ImportError("Nainstalujte: pip install ollama")
        return self._client

    def _prepare_single_image(self, file_path: Path) -> Optional[bytes]:
        """Připraví JEDEN obrázek (první stránka PDF nebo obrázek)."""
        if self.fitz is None or self.Image is None:
            return None

        ext = file_path.suffix.lower()
        
        if ext == ".pdf":
            try:
                with self.fitz.open(str(file_path)) as doc:
                    if len(doc) == 0:
                        return None
                    page = doc[0]
                    mat = self.fitz.Matrix(1.0, 1.0)  # Nižší zoom pro menší paměť
                    pix = page.get_pixmap(matrix=mat)
                    img_data = pix.tobytes("png")
                    
                    img = self.Image.open(io.BytesIO(img_data))
                    img = self._resize_image(img, 384)  # Menší rozlišení
                    
                    output = io.BytesIO()
                    img.save(output, format="JPEG", quality=75)  # JPEG je menší
                    return output.getvalue()
            except Exception as e:
                logger.warning(f"Chyba při konverzi PDF: {e}")
                return None

        elif ext in {".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".gif"}:
            try:
                img = self.Image.open(str(file_path))
                if img.mode in ('RGBA', 'LA', 'P'):
                    img = img.convert('RGB')
                img = self._resize_image(img, 384)  # Menší rozlišení
                
                output = io.BytesIO()
                img.save(output, format="JPEG", quality=75)
                return output.getvalue()
            except Exception as e:
                logger.warning(f"Chyba při načítání obrázku: {e}")
                return None

        return None

    def _resize_image(self, img, max_size: int):
        if self.Image is None:
            return img
        width, height = img.size
        if width > max_size or height > max_size:
            ratio = min(max_size / width, max_size / height)
            new_size = (int(width * ratio), int(height * ratio))
            return img.resize(new_size, self.Image.Resampling.LANCZOS)
        return img

    def analyze(self, file_path: Path) -> Optional[dict]:
        """Rychlá analýza - jen zda je faktura + základní data."""
        logger.info(f"👁️ Vision Agent analyzuje: {file_path.name}")

        image_data = None
        try:
            image_data = self._prepare_single_image(file_path)
            if not image_data:
                logger.warning(f"Nepodařilo se připravit obrázek: {file_path.name}")
                return None

            logger.debug(f"  Velikost obrázku: {len(image_data) / 1024:.1f} KB")

            client = self._get_client()
            
            # Retry mechanismus pro chyby serveru
            max_retries = 2
            retry_count = 0
            raw_output = None

            while retry_count <= max_retries:
                try:
                    response = client.generate(
                        model=self.model,
                        prompt=self.VISION_PROMPT,
                        images=[image_data],
                        options={
                            "temperature": 0.01,
                            "num_predict": 256,
                            "top_p": 0.9,
                        },
                        keep_alive="1m"
                    )

                    raw_output = response.get("response", "")
                    break  # Úspěch

                except Exception as e:
                    error_msg = str(e)
                    retry_count += 1
                    
                    if "disconnected" in error_msg.lower() or "timeout" in error_msg.lower():
                        logger.warning(f"⚠️ Server chyba (pokusek {retry_count}/{max_retries}): {error_msg}")
                        
                        if retry_count <= max_retries:
                            # Vyčistit paměť a zkusit znovu
                            if image_data:
                                del image_data
                                image_data = None
                                image_data = self._prepare_single_image(file_path)
                            
                            gc.collect()
                            import time
                            time.sleep(3)
                            continue
                        else:
                            logger.error(f"✗ Selhalo po {max_retries} pokusech")
                            return None
                    else:
                        logger.warning(f"Chyba: {e}")
                        return None

            if raw_output is None:
                return None

            # Lepší extrakce JSON - hledáme JSON i v textu
            parsed = self._extract_json_robust(raw_output)

            if parsed is None:
                logger.warning(f"Vision nevrátil validní JSON. Output: {raw_output[:100]}...")
                # Fallback - zkusíme najít alespoň is_invoice
                return self._fallback_parse(raw_output)

            logger.info(f"  ✓ {file_path.name}: {'Faktura' if parsed.get('is_invoice') else 'Není faktura'} ({parsed.get('document_type', 'unknown')})")

            return parsed

        except Exception as e:
            logger.warning(f"Chyba Vision analýzy: {e}")
            return None

        finally:
            if image_data:
                del image_data
            gc.collect()

    def _extract_json_robust(self, text: str) -> Optional[dict]:
        """Robustní extrakce JSON z textu."""
        # Nejprve zkusíme přímo
        try:
            return json.loads(text.strip())
        except:
            pass

        # Hledáme JSON v code block
        code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if code_block:
            try:
                return json.loads(code_block.group(1))
            except:
                pass

        # Hledáme první { ... }
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except:
                pass

        # Hledáme alespoň klíčová slova
        is_invoice_match = re.search(r'"is_invoice"\s*:\s*(true|false)', text, re.IGNORECASE)
        if is_invoice_match:
            return {
                'is_invoice': is_invoice_match.group(1).lower() == 'true',
                'document_type': 'unknown',
                'confidence': 0.5,
            }

        return None

    def _fallback_parse(self, text: str) -> dict:
        """Fallback když se nepodaří extrahovat JSON."""
        text_lower = text.lower()
        
        # Detekce podle klíčových slov
        is_invoice = False
        doc_type = "jiné"
        
        if "faktura" in text_lower or "invoice" in text_lower:
            is_invoice = True
            doc_type = "faktura"
        elif "upomínka" in text_lower or "reminder" in text_lower:
            doc_type = "upomínka"
        elif "smlouva" in text_lower or "contract" in text_lower:
            doc_type = "smlouva"
        elif "poznámka" in text_lower or "note" in text_lower:
            doc_type = "poznámka"

        return {
            'is_invoice': is_invoice,
            'document_type': doc_type,
            'confidence': 0.3,
            'sender_name': '',
            'recipient_name': '',
            'issue_date': '',
            'total_amount': '',
            'currency': '',
            'invoice_number': '',
            'due_date': '',
        }


# ─────────────────────────────────────────────
# Agent 2: Text Agent (zpracování dat)
# ─────────────────────────────────────────────
class TextAgent:
    """
    Agent 2: Zpracuje data z Vision agenta.
    Validuje, doplní a formatuje JSON.
    """

    TEXT_PROMPT = """Zpracuj data z faktury. Validuj a doplň chybějící informace.

VSTUP od Vision agenta:
{vision_data}

ÚKOL:
1. Zkontroluj zda data dávají smysl
2. Doplň chybějící datumy (pokud lze odvodit)
3. Formatuj částku správně
4. Urči měnu pokud chybí

Vrať POUZE validní JSON:
{{
    "is_invoice": boolean,
    "confidence": number (0.0-1.0),
    "sender_name": string,
    "recipient_name": string,
    "issue_date": string (YYYY-MM-DD),
    "due_date": string (YYYY-MM-DD),
    "total_amount": string,
    "currency": string,
    "invoice_number": string,
    "reason": string,
    "analysis_log": ["co jsi zpracoval"]
}}"""

    def __init__(self, model: str = TEXT_MODEL):
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import ollama
                self._client = ollama
            except ImportError:
                logger.error("Knihovna 'ollama' není nainstalována.")
                raise ImportError("Nainstalujte: pip install ollama")
        return self._client

    def process(self, vision_data: dict, filename: str) -> Optional[dict]:
        """Zpracuje data z Vision agenta."""
        logger.info(f"📝 Text Agent zpracovává: {filename}")

        try:
            client = self._get_client()
            
            prompt = self.TEXT_PROMPT.format(
                vision_data=json.dumps(vision_data, ensure_ascii=False)
            )

            response = client.generate(
                model=self.model,
                prompt=prompt,
                options={
                    "temperature": 0.1,
                    "num_predict": 512
                },
                keep_alive="30s"
            )

            raw_output = response.get("response", "")
            parsed = _extract_json_from_text(raw_output)

            if parsed is None:
                logger.warning(f"Text agent nevrátil validní JSON")
                # Fallback - vrátíme vision data
                return self._fallback_result(vision_data)

            logger.info(f"  ✓ {filename}: Zpracováno (jistota: {parsed.get('confidence', 0):.0%})")

            return parsed

        except Exception as e:
            logger.warning(f"Chyba Text agenta: {e}")
            return self._fallback_result(vision_data)

    def _fallback_result(self, vision_data: dict) -> dict:
        """Fallback když Text agent selže."""
        return {
            'is_invoice': vision_data.get('is_invoice', False),
            'confidence': vision_data.get('confidence', 0.5),
            'sender_name': vision_data.get('sender_name', ''),
            'recipient_name': vision_data.get('recipient_name', ''),
            'issue_date': vision_data.get('issue_date', ''),
            'due_date': vision_data.get('due_date', ''),
            'total_amount': vision_data.get('total_amount', ''),
            'currency': vision_data.get('currency', ''),
            'invoice_number': vision_data.get('invoice_number', ''),
            'reason': 'Zpracováno Vision agentem (Text agent selhal)',
            'analysis_log': ['Data zpracována pouze Vision agentem'],
        }


# ─────────────────────────────────────────────
# Hlavní dvou-agentový systém
# ─────────────────────────────────────────────
class TwoAgentSystem:
    """
    Koordinuje spolupráci Vision a Text agenta.
    """

    def __init__(self, vision_model: str = VISION_MODEL, text_model: str = TEXT_MODEL):
        self.vision_agent = VisionAgent(vision_model)
        self.text_agent = TextAgent(text_model)
        self.use_two_agent = USE_TWO_AGENT_SYSTEM

    def analyze(self, file_path: Path) -> Optional[dict]:
        """
        Dvoufázová analýza:
        1. Vision agent: Rychlá analýza obrázku
        2. Text agent: Zpracování dat
        """
        start_mem = get_memory_usage()
        log_memory_state(f"START {file_path.name}")

        # Fáze 1: Vision analýza
        vision_result = self.vision_agent.analyze(file_path)

        if vision_result is None:
            return None

        # Rychlá cesta - pokud není faktura, končíme
        if not vision_result.get('is_invoice', False):
            return {
                **vision_result,
                'reason': vision_result.get('document_type', 'unknown'),
                'analysis_log': [f"Vision určil: {vision_result.get('document_type', 'unknown')}"],
            }

        # Fáze 2: Text zpracování (pokud je povoleno)
        if self.use_two_agent:
            text_result = self.text_agent.process(vision_result, file_path.name)
            log_memory_state(f"PO CLEANUP {file_path.name}")
            return text_result
        else:
            # Pouze Vision (režim kompatibility)
            log_memory_state(f"PO CLEANUP {file_path.name}")
            return {
                **vision_result,
                'reason': f"Vision analýza: {vision_result.get('document_type', 'unknown')}",
                'analysis_log': ['Zpracováno pouze Vision agentem'],
            }


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
            if inv.matches_filter(filter_key, filter_value):
                filtered.append(inv)

        return filtered

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
            new_name = self._build_new_name(i, invoice, sort_key)
            dest_path = self.target_dir / new_name

            if dest_path.exists():
                stem = dest_path.stem
                dest_path = self.target_dir / f"{stem}_dup{dest_path.suffix}"

            try:
                shutil.move(str(invoice.source_path), str(dest_path))
                success += 1
            except Exception as e:
                errors += 1
                logger.error(f"Chyba při přesunu '{invoice.source_path.name}': {e}")

        return success, errors


# ─────────────────────────────────────────────
# GUI Aplikace
# ─────────────────────────────────────────────
class InvoiceProcessorGUIV4(ctk.CTk):
    """GUI pro dvou-agentový systém."""

    def __init__(self):
        super().__init__()

        self.title("🧾 Invoice Processor v4 - Dvou-agentový systém")
        self.geometry("1100x800")
        self.minsize(950, 700)

        # Proměnné
        self.source_dir = ctk.StringVar()
        self.target_dir = ctk.StringVar()
        self.sort_key = ctk.StringVar(value="issue_date")
        self.vision_model_name = ctk.StringVar(value=VISION_MODEL)
        self.text_model_name = ctk.StringVar(value=TEXT_MODEL)
        self.use_two_agent = ctk.BooleanVar(value=USE_TWO_AGENT_SYSTEM)

        # Filtr
        self.filter_key = ctk.StringVar(value="")
        self.filter_value = ctk.StringVar()

        self.agent_system = TwoAgentSystem()

        self.is_processing = False
        self.found_files: list[Path] = []
        self.invoices: list[Invoice] = []
        self.filtered_invoices: list[Invoice] = []

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

        self._setup_ui()

    def _setup_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        # ─── Header ────────────────────────────
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 10))
        header_frame.grid_columnconfigure(0, weight=1)

        title_label = ctk.CTkLabel(
            header_frame,
            text="🧾 Invoice Processor v4",
            font=ctk.CTkFont(size=28, weight="bold")
        )
        title_label.grid(row=0, column=0, sticky="w")

        subtitle_label = ctk.CTkLabel(
            header_frame,
            text="⚡ Dvou-agentový systém: Vision (obrázky) + Text (JSON) = Rychlejší analýza",
            font=ctk.CTkFont(size=14),
            text_color="gray"
        )
        subtitle_label.grid(row=1, column=0, sticky="w")

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
            placeholder_text="Vyberte složku..."
        ).grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=10)
        ctk.CTkButton(
            settings_frame,
            text="Procházet",
            command=self._browse_source,
            width=100
        ).grid(row=0, column=2, padx=(0, 15), pady=10)

        ctk.CTkLabel(settings_frame, text="📂 Cílová složka:").grid(
            row=1, column=0, sticky="w", padx=(15, 10), pady=(0, 15)
        )
        ctk.CTkEntry(
            settings_frame,
            textvariable=self.target_dir,
            placeholder_text="Vyberte cílovou složku..."
        ).grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=(0, 15))
        ctk.CTkButton(
            settings_frame,
            text="Procházet",
            command=self._browse_target,
            width=100
        ).grid(row=1, column=2, padx=(0, 15), pady=(0, 15))

        # ─── Nastavení modelů ─────────
        models_frame = ctk.CTkFrame(self)
        models_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=10)
        models_frame.grid_columnconfigure(1, weight=1)
        models_frame.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(models_frame, text="👁️ Vision model:").grid(
            row=0, column=0, sticky="w", padx=(15, 10), pady=10
        )
        ctk.CTkOptionMenu(
            models_frame,
            variable=self.vision_model_name,
            values=["llama3.2-vision", "llava", "bakllava", "phi3-vision", "moondream"],
            width=180
        ).grid(row=0, column=1, sticky="w", padx=(0, 30), pady=10)

        ctk.CTkLabel(models_frame, text="📝 Text model:").grid(
            row=0, column=2, sticky="w", padx=(10, 10), pady=10
        )
        ctk.CTkOptionMenu(
            models_frame,
            variable=self.text_model_name,
            values=["llama3.1", "llama3.2", "mistral", "gemma2"],
            width=150
        ).grid(row=0, column=3, sticky="w", pady=10)

        # ─── Dvou-agentový režim ─────────
        agent_mode_frame = ctk.CTkFrame(self, fg_color="#2b2b2b")
        agent_mode_frame.grid(row=3, column=0, sticky="ew", padx=20, pady=10)

        self.agent_mode_checkbox = ctk.CTkCheckBox(
            agent_mode_frame,
            text="Povolit dvou-agentový režim (rychlejší) - Vision analyzuje, Text zpracuje",
            variable=self.use_two_agent,
            command=self._on_agent_mode_change
        )
        self.agent_mode_checkbox.grid(row=0, column=0, padx=15, pady=10, sticky="w")

        # ─── Filtr ────────────────────────
        filter_frame = ctk.CTkFrame(self, fg_color="#2b2b2b")
        filter_frame.grid(row=4, column=0, sticky="ew", padx=20, pady=10)
        filter_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            filter_frame,
            text="🔍 Filtr faktur:",
            font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=(15, 10), pady=10)

        ctk.CTkOptionMenu(
            filter_frame,
            variable=self.filter_key,
            values=["Vypnuto", "Odesílatel", "Příjemce", "Datum vystavení", "Číslo faktury"],
            command=self._on_filter_key_change,
            width=200
        ).grid(row=0, column=1, sticky="w", padx=(0, 10), pady=10)

        ctk.CTkLabel(filter_frame, text="Hledaný výraz:").grid(
            row=0, column=2, sticky="w", padx=(15, 5), pady=10
        )
        self.filter_entry = ctk.CTkEntry(
            filter_frame,
            textvariable=self.filter_value,
            placeholder_text="Např.: Jana, Alza, 2024",
            width=250
        )
        self.filter_entry.grid(row=0, column=3, sticky="w", padx=(0, 15), pady=10)
        self.filter_entry.configure(state="disabled")

        # ─── Results ────────────────────
        preview_frame = ctk.CTkFrame(self)
        preview_frame.grid(row=5, column=0, sticky="nsew", padx=20, pady=10)
        preview_frame.grid_columnconfigure(0, weight=1)
        preview_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            preview_frame,
            text="📊 Nalezené faktury:",
            font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(15, 5))

        columns = ("soubor", "odesilatel", "datum", "castka", "jistota")
        self.results_tree = ttk.Treeview(
            preview_frame,
            columns=columns,
            show="headings",
            height=12
        )

        self.results_tree.heading("soubor", text="Soubor")
        self.results_tree.heading("odesilatel", text="Odesílatel")
        self.results_tree.heading("datum", text="Datum")
        self.results_tree.heading("castka", text="Částka")
        self.results_tree.heading("jistota", text="Jistota")

        self.results_tree.column("soubor", width=200)
        self.results_tree.column("odesilatel", width=180)
        self.results_tree.column("datum", width=100)
        self.results_tree.column("castka", width=100)
        self.results_tree.column("jistota", width=80)

        scrollbar = ctk.CTkScrollbar(preview_frame, command=self.results_tree.yview)
        self.results_tree.configure(yscrollcommand=scrollbar.set)

        self.results_tree.grid(row=1, column=0, sticky="nsew", padx=15, pady=5)
        scrollbar.grid(row=1, column=1, sticky="ns", pady=5)

        # ─── Progress a log ─────────────
        output_frame = ctk.CTkFrame(self)
        output_frame.grid(row=6, column=0, sticky="ew", padx=20, pady=10)
        output_frame.grid_columnconfigure(0, weight=1)

        self.progress_label = ctk.CTkLabel(
            output_frame,
            text="Připraveno",
            font=ctk.CTkFont(size=12)
        )
        self.progress_label.grid(row=0, column=0, sticky="w", padx=15, pady=(15, 5))

        self.progress_bar = ctk.CTkProgressBar(output_frame, mode="determinate")
        self.progress_bar.grid(row=1, column=0, sticky="ew", padx=15, pady=5)
        self.progress_bar.set(0)

        self.log_text = ctk.CTkTextbox(output_frame, font=("Consolas", 11), height=80)
        self.log_text.grid(row=2, column=0, sticky="ew", padx=15, pady=(5, 15))
        self.log_text.configure(state="disabled")

        # ─── Action buttons ─────────────
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=7, column=0, sticky="ew", padx=20, pady=(0, 20))
        button_frame.grid_columnconfigure(0, weight=1)

        self.start_button = ctk.CTkButton(
            button_frame,
            text="▶ Spustit dvou-agentovou analýzu",
            command=self._start_processing,
            font=ctk.CTkFont(size=16, weight="bold"),
            height=45,
            fg_color="#28a745",
            hover_color="#218838"
        )
        self.start_button.grid(row=0, column=0, padx=15, pady=5)

        self.cancel_button = ctk.CTkButton(
            button_frame,
            text="⏹ Zrušit",
            command=self._cancel_processing,
            state="disabled",
            height=35,
            fg_color="#dc3545",
            hover_color="#c82333"
        )
        self.cancel_button.grid(row=1, column=0, padx=15, pady=5)

        # Status bar
        self.status_var = ctk.StringVar(
            value=f"Stav: Připraveno | Vision: {VISION_MODEL} | Text: {TEXT_MODEL}"
        )
        status_bar = ctk.CTkLabel(
            self,
            textvariable=self.status_var,
            font=ctk.CTkFont(size=11),
            text_color="gray"
        )
        status_bar.grid(row=8, column=0, sticky="ew", padx=20, pady=(0, 10))

    def _browse_source(self):
        folder = filedialog.askdirectory(title="Vyberte zdrojovou složku")
        if folder:
            self.source_dir.set(folder)
            self._log(f"Zdrojová složka: {folder}")

    def _browse_target(self):
        folder = filedialog.askdirectory(title="Vyberte cílovou složku")
        if folder:
            self.target_dir.set(folder)
            self._log(f"Cílová složka: {folder}")

    def _on_agent_mode_change(self):
        mode = "Dvou-agentový režim POVolen" if self.use_two_agent.get() else "Dvou-agentový režim VYPNUT"
        self._log(f"⚙️ {mode}")

    def _on_sort_key_change(self, display_value: str):
        actual_key = self.sort_key_map.get(display_value, "issue_date")
        self.sort_key.set(actual_key)

    def _on_filter_key_change(self, display_value: str):
        actual_key = self.filter_key_map.get(display_value, "")
        self.filter_key.set(actual_key)

        if display_value == "Vypnuto":
            self.filter_entry.configure(state="disabled")
        else:
            self.filter_entry.configure(state="normal")

    def _log(self, message: str):
        self.log_text.configure(state="normal")
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _update_results_table(self, invoices: list[Invoice]):
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)

        for inv in invoices:
            self.results_tree.insert("", "end", values=(
                inv.source_path.name,
                inv.sender_name[:40] + "..." if len(inv.sender_name) > 40 else inv.sender_name,
                inv.issue_date,
                inv.total_amount,
                f"{inv.confidence:.0%}"
            ))

    def _start_processing(self):
        if self.is_processing:
            return

        source = self.source_dir.get().strip()
        target = self.target_dir.get().strip()

        if not source:
            messagebox.showerror("Chyba", "Vyberte zdrojovou složku!")
            return

        if not Path(source).is_dir():
            messagebox.showerror("Chyba", f"Složka neexistuje: {source}")
            return

        if not target:
            messagebox.showerror("Chyba", "Vyberte cílovou složku!")
            return

        self.is_processing = True
        self.start_button.configure(state="disabled", text="⏳ Analyzuji...")
        self.cancel_button.configure(state="normal")
        self._clear_log()

        # Aktualizovat modely
        self.agent_system.vision_agent.model = self.vision_model_name.get()
        self.agent_system.text_agent.model = self.text_model_name.get()
        self.agent_system.use_two_agent = self.use_two_agent.get()

        mode_str = "Dvou-agentový" if self.use_two_agent.get() else "Jeden agent (Vision only)"
        self.status_var.set(
            f"Stav: {mode_str} | Vision: {self.agent_system.vision_agent.model} | "
            f"Text: {self.agent_system.text_agent.model}"
        )

        thread = threading.Thread(
            target=self._process_thread,
            args=(source, target),
            daemon=True
        )
        thread.start()

    def _cancel_processing(self):
        self.is_processing = False
        self._log("❌ Zrušeno uživatelem")
        self._reset_ui()

    def _reset_ui(self):
        self.is_processing = False
        mode_str = "▶ Spustit dvou-agentovou analýzu"
        self.start_button.configure(state="normal", text=mode_str)
        self.cancel_button.configure(state="disabled")
        self._update_progress(0, "Připraveno")
        mode_info = "Dvou-agentový" if self.use_two_agent.get() else "Vision only"
        self.status_var.set(
            f"Stav: Připraveno | {mode_info} | Vision: {self.vision_model_name.get()}"
        )

    def _update_progress(self, value: float, label: str = ""):
        self.progress_bar.set(value / 100)
        if label:
            self.progress_label.configure(text=label)

    def _process_thread(self, source: str, target: str):
        try:
            source_path = Path(source)
            target_path = Path(target)

            log_memory_state("START PROCESSING")
            initial_mem = get_memory_usage()
            self._log(f"💾 Počáteční paměť: {initial_mem['rss_mb']:.1f}MB")

            mode_str = "Dvou-agentový" if self.use_two_agent.get() else "Vision only"
            self._log(f"⚡ Režim: {mode_str}")
            self._log(f"   👁️ Vision: {self.agent_system.vision_agent.model}")
            self._log(f"   📝 Text: {self.agent_system.text_agent.model}")

            # Krok 1: Vyhledání
            self._log("🔍 Vyhledávám soubory...")
            discovery = FileDiscovery(source_path)
            self.found_files = discovery.find_files()

            if not self.found_files:
                self.after(0, lambda: messagebox.showinfo("Info", "Nenalezeny žádné soubory."))
                self.after(0, self._reset_ui)
                return

            self._log(f"Nalezeno {len(self.found_files)} souborů")

            # Krok 2: Analýza
            self._log("🤖 Spouštím analýzu...")
            self.invoices = []
            total = len(self.found_files)

            for idx, file_path in enumerate(self.found_files, start=1):
                if not self.is_processing:
                    self._log("❌ Zrušeno uživatelem")
                    break

                progress = (idx / total) * 100
                self._update_progress(progress, f"[{idx}/{total}] {file_path.name}")

                if idx % MEMORY_CHECK_EVERY == 0:
                    mem = get_memory_usage()
                    self._log(f"💾 Paměť [{idx}/{total}]: {mem['rss_mb']:.1f}MB ({mem['percent']:.1f}%)")

                result = self.agent_system.analyze(file_path)

                if result is None:
                    self._log(f"  ✗ {file_path.name}: Chyba analýzy")
                    continue

                is_invoice = result.get("is_invoice", False)

                if not is_invoice:
                    reason = result.get("reason", result.get("document_type", "Neznámý"))[:50]
                    self._log(f"  – {file_path.name}: Není faktura ({reason})")
                    continue

                invoice = Invoice(
                    source_path=file_path,
                    sender_name=_sanitize_filename(result.get("sender_name") or "Neznamy_odesilatel"),
                    recipient_name=_sanitize_filename(result.get("recipient_name") or "Neznamy_prijemce"),
                    issue_date=result.get("issue_date") or "0000-00-00",
                    due_date=result.get("due_date") or "0000-00-00",
                    total_amount=result.get("total_amount") or "",
                    currency=result.get("currency") or "",
                    invoice_number=result.get("invoice_number") or "",
                    is_invoice=True,
                    confidence=result.get("confidence", 0.0),
                    raw_json=result,
                    analysis_log=result.get("analysis_log", []),
                )
                self.invoices.append(invoice)
                self._log(
                    f"  ✓ {file_path.name}: Faktura od {invoice.sender_name[:25]} "
                    f"({invoice.confidence:.0%})"
                )

                # Pauza po dávce
                if idx % BATCH_SIZE == 0 and idx < total:
                    self._log(f"⏸ Pauza pro uvolnění paměti...")
                    gc.collect()
                    import time
                    time.sleep(2)

            # Krok 3: Filtrování
            filter_key = self.filter_key.get()
            filter_value = self.filter_value.get().strip()

            if filter_key and filter_value:
                self._log(f"🔍 Filtr: {filter_key} = '{filter_value}'")
                filter_agent = InvoiceFilterAgent(self.invoices)
                self.filtered_invoices = filter_agent.filter_by_custom(filter_key, filter_value)
                self._log(f"  Nalezeno {len(self.filtered_invoices)} faktur")
            else:
                self.filtered_invoices = [inv for inv in self.invoices if inv.is_invoice]

            self._log(f"📊 Celkem faktur: {len(self.filtered_invoices)}")

            self.after(0, lambda: self._update_results_table(self.filtered_invoices))

            if not self.filtered_invoices:
                self.after(0, lambda: messagebox.showinfo("Info", "Žádné faktury nenalezeny."))
                self.after(0, self._reset_ui)
                return

            # Krok 4: Třídění
            self._log("📂 Třídím a přesouvám...")
            sort_key = self.sort_key_map.get(self.sort_key.get(), "issue_date")

            organizer = InvoiceOrganizer(self.filtered_invoices, target_path)
            success, errors = organizer.process(sort_key)

            self._log(f"✅ Přesunuto: {success} | Chyby: {errors}")

            final_mem = get_memory_usage()
            self._log(f"💾 Konečná paměť: {final_mem['rss_mb']:.1f}MB")

            self._update_progress(100, "Dokončeno")
            self._log("✅ Hotovo!")

            self.after(0, lambda: messagebox.showinfo(
                "Dokončeno",
                f"Nalezeno: {len(self.filtered_invoices)}\n"
                f"Přesunuto: {success}\n"
                f"Chyby: {errors}"
            ))

        except Exception as e:
            logger.exception(f"Chyba: {e}")
            self.after(0, lambda: messagebox.showerror("Chyba", str(e)))
        finally:
            gc.collect()
            self.after(0, self._reset_ui)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main():
    app = InvoiceProcessorGUIV4()
    app.mainloop()


if __name__ == "__main__":
    main()
