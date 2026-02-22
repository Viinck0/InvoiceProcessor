#!/usr/bin/env python3
"""
Invoice Processor v2 - GUI verze s Llama Vision
Moderní rozhraní pro vyhledávání, analýzu a třídění faktur
pomocí multimodálního LLM modelu přes Ollama.

Použití:
    python invoice_gui_v2.py

Závislosti:
    pip install customtkinter PyMuPDF ollama Pillow
"""

import os
import re
import sys
import json
import shutil
import logging
import threading
import base64
import io
import gc
import traceback
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Callable, List
from datetime import datetime

# Volitelný import pro monitoring paměti
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
MAX_IMAGE_SIZE = 512  # Sníženo pro úsporu paměti (512px stačí pro OCR)
LOG_LEVEL = logging.INFO
REQUEST_TIMEOUT = 120  # Timeout pro Ollama request v sekundách
MAX_MEMORY_PERCENT = 85  # Maximální využití paměti v % před pozastavením
DEBUG_MEMORY = True  # Povolit detailní logging paměti
BATCH_SIZE = 3  # Zpracovávat po 3 souborech s pauzou
MEMORY_CHECK_EVERY = 1  # Kontrolovat paměť po každém souboru

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
    """Získá aktuální využití paměti."""
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
    """Zaloguje stav paměti pro debug."""
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
    """Odstraní/nahradí znaky nevhodné pro názvy souborů."""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r'\s+', "_", name.strip())
    name = name.replace("á", "a").replace("é", "e").replace("í", "i")
    name = name.replace("ó", "o").replace("ú", "u").replace("ý", "y")
    name = name.replace("č", "c").replace("ď", "d").replace("ě", "e")
    name = name.replace("ň", "n").replace("ř", "r").replace("š", "s")
    name = name.replace("ť", "t").replace("ů", "u").replace("ž", "z")
    return name[:60]


def _extract_json_from_text(text: str) -> Optional[dict]:
    """Extrahuje JSON objekt z textu LLM."""
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
    """Rekurzivně vyhledá podporované soubory ve zdrojové složce."""

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
# Modul: Příprava obrázků pro Vision model
# ─────────────────────────────────────────────
class ImagePreprocessor:
    """Připraví obrázky z PDF a obrázkových souborů pro Vision model."""

    def __init__(self):
        # Import na úrovni třídy pro dostupnost ve všech metodách
        try:
            from PIL import Image
            self.Image = Image
        except ImportError:
            logger.error("Pillow není nainstalován.")
            self.Image = None

    def prepare_image(self, file_path: Path) -> Optional[bytes]:
        ext = file_path.suffix.lower()

        if ext == ".pdf":
            return self._pdf_to_image(file_path)
        elif ext in {".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".gif"}:
            return self._load_image(file_path)

        return None

    def _pdf_to_image(self, file_path: Path) -> Optional[bytes]:
        """Konvertuje první stránku PDF na obrázek."""
        try:
            import fitz
        except ImportError as e:
            logger.error(f"Chybí knihovna: {e}")
            return None

        if self.Image is None:
            return None

        try:
            with fitz.open(str(file_path)) as doc:
                if len(doc) == 0:
                    return None

                page = doc[0]
                mat = fitz.Matrix(2.0, 2.0)
                pix = page.get_pixmap(matrix=mat)
                img_data = pix.tobytes("png")

                img = self.Image.open(io.BytesIO(img_data))
                img = self._resize_if_needed(img, MAX_IMAGE_SIZE)

                output = io.BytesIO()
                img.save(output, format="PNG")
                return output.getvalue()

        except Exception as e:
            logger.warning(f"Chyba při konverzi PDF '{file_path.name}': {e}")
            return None

    def _load_image(self, file_path: Path) -> Optional[bytes]:
        """Načte a připraví obrázek."""
        if self.Image is None:
            logger.error("Pillow není nainstalován.")
            return None

        try:
            img = self.Image.open(str(file_path))

            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')

            img = self._resize_if_needed(img, MAX_IMAGE_SIZE)

            output = io.BytesIO()
            img.save(output, format="JPEG", quality=85)
            return output.getvalue()

        except Exception as e:
            logger.warning(f"Chyba při načítání obrázku '{file_path.name}': {e}")
            return None

    def _resize_if_needed(self, img, max_size: int):
        width, height = img.size

        if width > max_size or height > max_size:
            ratio = min(max_size / width, max_size / height)
            new_size = (int(width * ratio), int(height * ratio))
            return img.resize(new_size, self.Image.Resampling.LANCZOS)

        return img


# ─────────────────────────────────────────────
# Modul: Llama Vision Analyzer
# ─────────────────────────────────────────────
class LlamaVisionAnalyzer:
    """Analyzuje dokumenty přímo jako obrázky pomocí multimodálního LLM."""

    PROMPT_TEMPLATE = """Jsi expert na účetnictví a rozpoznávání dokumentů. 
Proanalizuj tento obrázek dokumentu a urči, zda se jedná o fakturu.

DŮLEŽITÉ: Rozlišuj mezi skutečnou fakturou a jinými dokumenty (upomínky, 
objednávky, smlouvy, poznámky s datem, vizitky, atd.).

Skutečná faktura obvykle obsahuje:
- Označení "FAKTURA" nebo "DAŇOVÝ DOKLAD"
- Identifikační údaje dodavatele a odběratele
- Částku k úhradě
- Datum vystavení a splatnosti
- Variabilní symbol nebo číslo faktury

Vrať POUZE validní JSON objekt (žádný jiný text) s těmito klíči:
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
    "reason": string
}}

Příklad pro fakturu:
{{"is_invoice": true, "confidence": 0.95, "sender_name": "ABC s.r.o.", "recipient_name": "XYZ a.s.", "issue_date": "2024-01-15", "due_date": "2024-02-15", "total_amount": "1500 Kč", "currency": "CZK", "invoice_number": "2024001", "reason": "Dokument obsahuje všechny náležitosti faktury"}}

Příklad pro ne-fakturu:
{{"is_invoice": false, "confidence": 0.9, "sender_name": "", "recipient_name": "", "issue_date": "", "due_date": "", "total_amount": "", "currency": "", "invoice_number": "", "reason": "Dokument obsahuje pouze datum a jméno, chybí náležitosti faktury"}}

Zhodnoť dokument na obrázku:"""

    def __init__(self, model: Optional[str] = None):
        self.model = model or os.environ.get("VISION_MODEL", VISION_MODEL)
        self._client = None
        self.preprocessor = ImagePreprocessor()
        self.timeout = REQUEST_TIMEOUT  # Timeout z konstanty

    def _get_client(self):
        if self._client is None:
            try:
                import ollama
                self._client = ollama
            except ImportError:
                logger.error("Knihovna 'ollama' není nainstalována.")
                raise ImportError("Nainstalujte: pip install ollama")
        return self._client

    def analyze(self, file_path: Path) -> Optional[dict]:
        """
        Analyzuje soubor pomocí Vision modelu s optimalizací paměti.
        """
        start_mem = get_memory_usage()
        log_memory_state(f"START {file_path.name}")

        # Kontrola paměti před zpracováním
        if start_mem.get('percent', 0) > MAX_MEMORY_PERCENT:
            logger.warning(f"⚠️ Vysoké využití paměti ({start_mem['percent']:.1f}%), čekám na uvolnění...")
            gc.collect()
            import time
            time.sleep(3)

        image_data = None
        max_retries = 2
        retry_count = 0
        raw_output = None

        while retry_count <= max_retries:
            try:
                # Příprava obrázku
                image_data = self.preprocessor.prepare_image(file_path)

                if image_data is None:
                    logger.warning(f"Nepodařilo se připravit obrázek: {file_path.name}")
                    return None

                log_memory_state(f"PO PŘÍPRAVĚ OBRÁZKU {file_path.name}")

                client = self._get_client()

                # Ollama request s timeoutem
                response = client.generate(
                    model=self.model,
                    prompt=self.PROMPT_TEMPLATE,
                    images=[image_data],
                    options={
                        "temperature": 0.01,
                        "num_predict": 512
                    },
                    keep_alive="1m"  # Velmi krátký keep_alive pro úsporu paměti
                )

                raw_output = response.get("response", "")

                log_memory_state(f"PO OLLAMA RESPONSE {file_path.name}")
                break  # Úspěch, ukončit retry loop

            except Exception as e:
                error_msg = str(e)

                # Specifická handling pro memory allocation errors
                if "memory layout cannot be allocated" in error_msg or "CUDA out of memory" in error_msg:
                    retry_count += 1
                    logger.warning(f"⚠️ Nedostatek paměti pro '{file_path.name}' (pokusek {retry_count}/{max_retries})")

                    # Agresivní uvolnění paměti
                    if image_data is not None:
                        del image_data
                        image_data = None
                    gc.collect()

                    if retry_count <= max_retries:
                        # Čekat na uvolnění paměti
                        import time
                        wait_time = 5 * retry_count  # Exponenciální backoff
                        logger.warning(f"   Čekám {wait_time}s na uvolnění paměti...")
                        time.sleep(wait_time)

                        # Zkusit znovu s menším obrázkem
                        if retry_count == 1:
                            logger.warning(f"   Zkouším znovu s menším rozlišením...")
                            # Další pokus bude s již uvolněnou pamětí
                        continue
                    else:
                        logger.error(f"✗ Selhalo zpracování '{file_path.name}' - nedostatek paměti")
                        return None
                else:
                    logger.warning(f"Chyba komunikace s Vision modelem '{file_path.name}': {e}")
                    logger.debug(f"Detail chyby: {traceback.format_exc()}")
                    return None

            finally:
                # Explicitní uvolnění paměti po každém pokusu
                if image_data is not None:
                    del image_data
                    image_data = None
                gc.collect()
                log_memory_state(f"PO CLEANUP {file_path.name}")

        if raw_output is None:
            return None

        parsed = _extract_json_from_text(raw_output)

        if parsed is None:
            logger.warning(f"Vision model nevrátil validní JSON pro '{file_path.name}'")
            return None

        return parsed


# ─────────────────────────────────────────────
# Modul: Filtrování faktur
# ─────────────────────────────────────────────
class InvoiceFilterAgent:
    """Filruje faktury podle uživatelem zadaných kritérií."""

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
    """Třídí, přesouvá a přejmenovává soubory faktur."""

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
class InvoiceProcessorGUIV2(ctk.CTk):
    """Hlavní GUI aplikace pro Invoice Processor v2 s Llama Vision."""

    def __init__(self):
        super().__init__()

        self.title("🧾 Invoice Processor v2 - Llama Vision")
        self.geometry("1000x750")
        self.minsize(900, 650)

        # Proměnné
        self.source_dir = ctk.StringVar()
        self.target_dir = ctk.StringVar()
        self.sort_key = ctk.StringVar(value="issue_date")
        self.model_name = ctk.StringVar(value=VISION_MODEL)
        
        # Filtr
        self.filter_key = ctk.StringVar(value="")
        self.filter_value = ctk.StringVar()

        self.analyzer = LlamaVisionAnalyzer()

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
        """Vytvoří celé uživatelské rozhraní."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        # ─── Header ────────────────────────────
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 10))
        header_frame.grid_columnconfigure(0, weight=1)

        title_label = ctk.CTkLabel(
            header_frame,
            text="🧾 Invoice Processor v2",
            font=ctk.CTkFont(size=28, weight="bold")
        )
        title_label.grid(row=0, column=0, sticky="w")

        subtitle_label = ctk.CTkLabel(
            header_frame,
            text="👁️ Llama Vision - přímá analýza dokumentů bez OCR",
            font=ctk.CTkFont(size=14),
            text_color="gray"
        )
        subtitle_label.grid(row=1, column=0, sticky="w")

        # ─── Nastavení složek ────────────────────
        settings_frame = ctk.CTkFrame(self)
        settings_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=10)
        settings_frame.grid_columnconfigure(1, weight=1)

        # Zdrojová složka
        ctk.CTkLabel(settings_frame, text="📁 Zdrojová složka:").grid(
            row=0, column=0, sticky="w", padx=(15, 10), pady=10
        )
        ctk.CTkEntry(
            settings_frame,
            textvariable=self.source_dir,
            placeholder_text="Vyberte složku k prohledání..."
        ).grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=10)
        ctk.CTkButton(
            settings_frame,
            text="Procházet",
            command=self._browse_source,
            width=100
        ).grid(row=0, column=2, padx=(0, 15), pady=10)

        # Cílová složka
        ctk.CTkLabel(settings_frame, text="📂 Cílová složka:").grid(
            row=1, column=0, sticky="w", padx=(15, 10), pady=(0, 15)
        )
        ctk.CTkEntry(
            settings_frame,
            textvariable=self.target_dir,
            placeholder_text="Vyberte nebo vytvořte cílovou složku..."
        ).grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=(0, 15))
        ctk.CTkButton(
            settings_frame,
            text="Procházet",
            command=self._browse_target,
            width=100
        ).grid(row=1, column=2, padx=(0, 15), pady=(0, 15))

        # ─── Nastavení třídění a modelu ─────────
        options_frame = ctk.CTkFrame(self)
        options_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=10)
        options_frame.grid_columnconfigure(1, weight=1)
        options_frame.grid_columnconfigure(3, weight=1)

        # Kritérium třídění
        ctk.CTkLabel(options_frame, text="🔀 Třídit podle:").grid(
            row=0, column=0, sticky="w", padx=(15, 10), pady=10
        )
        ctk.CTkOptionMenu(
            options_frame,
            variable=self.sort_key,
            values=["Datum vystavení", "Datum splatnosti", "Odesílatel", "Příjemce", "Částka"],
            command=self._on_sort_key_change,
            width=200
        ).grid(row=0, column=1, sticky="w", padx=(0, 30), pady=10)

        # Model selection
        ctk.CTkLabel(options_frame, text="🤖 Vision model:").grid(
            row=0, column=2, sticky="w", padx=(10, 10), pady=10
        )
        ctk.CTkOptionMenu(
            options_frame,
            variable=self.model_name,
            values=["llama3.2-vision", "llava", "bakllava", "phi3-vision", "moondream"],
            width=180
        ).grid(row=0, column=3, sticky="w", pady=10)

        # ─── Filtr faktur ────────────────────────
        filter_frame = ctk.CTkFrame(self, fg_color="#2b2b2b")
        filter_frame.grid(row=3, column=0, sticky="ew", padx=20, pady=10)
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

        # ─── Results preview ────────────────────
        preview_frame = ctk.CTkFrame(self)
        preview_frame.grid(row=4, column=0, sticky="nsew", padx=20, pady=10)
        preview_frame.grid_columnconfigure(0, weight=1)
        preview_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            preview_frame,
            text="📊 Nalezené faktury:",
            font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(15, 5))

        # Treeview pro výsledky
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", 
                       background="#2b2b2b",
                       foreground="white",
                       fieldbackground="#2b2b2b",
                       rowheight=28)
        style.configure("Treeview.Heading",
                       background="#3a3a3a",
                       foreground="white",
                       font=("Arial", 11, "bold"))

        columns = ("soubor", "odesilatel", "prijemce", "datum", "castka", "jistota")
        self.results_tree = ttk.Treeview(
            preview_frame,
            columns=columns,
            show="headings",
            height=12
        )

        self.results_tree.heading("soubor", text="Soubor")
        self.results_tree.heading("odesilatel", text="Odesílatel")
        self.results_tree.heading("prijemce", text="Příjemce")
        self.results_tree.heading("datum", text="Datum")
        self.results_tree.heading("castka", text="Částka")
        self.results_tree.heading("jistota", text="Jistota")

        self.results_tree.column("soubor", width=200)
        self.results_tree.column("odesilatel", width=150)
        self.results_tree.column("prijemce", width=150)
        self.results_tree.column("datum", width=100)
        self.results_tree.column("castka", width=100)
        self.results_tree.column("jistota", width=80)

        # Scrollbar
        scrollbar = ctk.CTkScrollbar(preview_frame, command=self.results_tree.yview)
        self.results_tree.configure(yscrollcommand=scrollbar.set)

        self.results_tree.grid(row=1, column=0, sticky="nsew", padx=15, pady=5)
        scrollbar.grid(row=1, column=1, sticky="ns", pady=5)

        # ─── Progress a log ─────────────────────
        output_frame = ctk.CTkFrame(self)
        output_frame.grid(row=5, column=0, sticky="ew", padx=20, pady=10)
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

        # ─── Action buttons ─────────────────────
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=6, column=0, sticky="ew", padx=20, pady=(0, 20))
        button_frame.grid_columnconfigure(0, weight=1)

        self.start_button = ctk.CTkButton(
            button_frame,
            text="▶ Spustit zpracování",
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
        self.status_var = ctk.StringVar(value="Stav: Připraveno | Model: " + VISION_MODEL)
        status_bar = ctk.CTkLabel(
            self,
            textvariable=self.status_var,
            font=ctk.CTkFont(size=11),
            text_color="gray"
        )
        status_bar.grid(row=7, column=0, sticky="ew", padx=20, pady=(0, 10))

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
        """Aktualizuje tabulku s výsledky."""
        # Vyčistit
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)

        # Přidat řádky
        for inv in invoices:
            self.results_tree.insert("", "end", values=(
                inv.source_path.name,
                inv.sender_name[:30] + "..." if len(inv.sender_name) > 30 else inv.sender_name,
                inv.recipient_name[:25] + "..." if len(inv.recipient_name) > 25 else inv.recipient_name,
                inv.issue_date,
                inv.total_amount,
                f"{inv.confidence:.0%}"
            ))

    def _start_processing(self):
        """Spustí processing ve vlákně."""
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
        self.start_button.configure(state="disabled", text="⏳ Zpracovávám...")
        self.cancel_button.configure(state="normal")
        self._clear_log()

        self.analyzer.model = self.model_name.get()
        self.status_var.set(f"Stav: Zpracovávám | Model: {self.analyzer.model}")

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
        self.start_button.configure(state="normal", text="▶ Spustit zpracování")
        self.cancel_button.configure(state="disabled")
        self._update_progress(0, "Připraveno")
        self.status_var.set(f"Stav: Připraveno | Model: {self.analyzer.model}")

    def _update_progress(self, value: float, label: str = ""):
        self.progress_bar.set(value / 100)
        if label:
            self.progress_label.configure(text=label)

    def _process_thread(self, source: str, target: str):
        """Hlavní processing thread s detailním debugem."""
        try:
            source_path = Path(source)
            target_path = Path(target)

            # Initial memory state
            log_memory_state("START PROCESSING")
            self._log(f"💾 Počáteční paměť: {get_memory_usage()['rss_mb']:.1f}MB")

            # Krok 1: Vyhledání souborů
            self._log("🔍 Vyhledávám soubory...")
            discovery = FileDiscovery(source_path)
            self.found_files = discovery.find_files()

            if not self.found_files:
                self.after(0, lambda: messagebox.showinfo(
                    "Info", "Nenalezeny žádné podporované soubory."
                ))
                self.after(0, self._reset_ui)
                return

            self._log(f"Nalezeno {len(self.found_files)} souborů")
            log_memory_state("PO FILE DISCOVERY")

            # Krok 2: Llama Vision analýza
            self._log("👁️ Spouštím Llama Vision analýzu...")
            self._log(f"   Model: {self.analyzer.model}")
            self._log(f"   MAX_IMAGE_SIZE: {MAX_IMAGE_SIZE}px")
            self._log(f"   Timeout: {REQUEST_TIMEOUT}s")
            self.invoices = []
            total = len(self.found_files)

            for idx, file_path in enumerate(self.found_files, start=1):
                if not self.is_processing:
                    self._log("❌ Zrušeno uživatelem")
                    break

                progress = (idx / total) * 100
                self._update_progress(progress, f"[{idx}/{total}] {file_path.name}")

                # Kontrola paměti po každém souboru
                if idx % MEMORY_CHECK_EVERY == 0:
                    mem = get_memory_usage()
                    self._log(f"💾 Paměť [{idx}/{total}]: {mem['rss_mb']:.1f}MB ({mem['percent']:.1f}%)")
                    
                    # Warning při vysoké paměti
                    if mem.get('percent', 0) > 70:
                        self._log(f"⚠️ Vysoká paměť! Zvažuji přerušení...")

                result = self.analyzer.analyze(file_path)

                if result is None:
                    self._log(f"  ✗ {file_path.name}: Chyba analýzy")
                    continue

                is_invoice = result.get("is_invoice", False)

                if not is_invoice:
                    reason = result.get("reason", "Neznámý důvod")[:50]
                    self._log(f"  – {file_path.name}: Není faktura ({reason})")
                    continue

                invoice = Invoice(
                    source_path=file_path,
                    sender_name=_sanitize_filename(
                        result.get("sender_name") or "Neznamy_odesilatel"
                    ),
                    recipient_name=_sanitize_filename(
                        result.get("recipient_name") or "Neznamy_prijemce"
                    ),
                    issue_date=result.get("issue_date") or "0000-00-00",
                    due_date=result.get("due_date") or "0000-00-00",
                    total_amount=result.get("total_amount") or "",
                    currency=result.get("currency") or "",
                    invoice_number=result.get("invoice_number") or "",
                    is_invoice=True,
                    confidence=result.get("confidence", 0.0),
                    raw_json=result,
                )
                self.invoices.append(invoice)
                self._log(
                    f"  ✓ {file_path.name}: Faktura od {invoice.sender_name[:25]} "
                    f"({invoice.confidence:.0%})"
                )
                
                # Pauza po každé dávce (BATCH_SIZE)
                if idx % BATCH_SIZE == 0 and idx < total:
                    self._log(f"⏸ Pauza po {BATCH_SIZE} souborech pro uvolnění paměti...")
                    gc.collect()
                    import time
                    time.sleep(3)
                    mem = get_memory_usage()
                    self._log(f"💾 Paměť po pauze: {mem['rss_mb']:.1f}MB")

            # Krok 3: Filtrování
            filter_key = self.filter_key.get()
            filter_value = self.filter_value.get().strip()

            if filter_key and filter_value:
                self._log(f"🔍 Aplikuji filtr: {filter_key} obsahuje '{filter_value}'")
                filter_agent = InvoiceFilterAgent(self.invoices)
                self.filtered_invoices = filter_agent.filter_by_custom(
                    filter_key, filter_value
                )
                self._log(f"  Nalezeno {len(self.filtered_invoices)} faktur po filtru")
            else:
                self.filtered_invoices = [inv for inv in self.invoices if inv.is_invoice]

            self._log(f"📊 Celkem faktur: {len(self.filtered_invoices)}")
            log_memory_state("PO FILTROVÁNÍ")

            # Aktualizace tabulky
            self.after(0, lambda: self._update_results_table(self.filtered_invoices))

            if not self.filtered_invoices:
                self.after(0, lambda: messagebox.showinfo(
                    "Info", "Nebyly nalezeny žádné faktury."
                ))
                self.after(0, self._reset_ui)
                return

            # Krok 4: Třídění a přesun
            self._log("📂 Třídím a přesouvám faktury...")
            sort_key = self.sort_key_map.get(self.sort_key.get(), "issue_date")

            organizer = InvoiceOrganizer(self.filtered_invoices, target_path)
            success, errors = organizer.process(sort_key)

            self._log(f"✅ Úspěšně přesunuto: {success} | Chyby: {errors}")
            
            # Final cleanup
            log_memory_state("PŘED FINAL CLEANUP")
            del self.invoices
            del self.filtered_invoices
            gc.collect()
            log_memory_state("PO FINAL CLEANUP")
            
            final_mem = get_memory_usage()
            self._log(f"💾 Konečná paměť: {final_mem['rss_mb']:.1f}MB")

            self._update_progress(100, "Dokončeno")
            self._log("✅ Hotovo!")

            self.after(0, lambda: messagebox.showinfo(
                "Dokončeno",
                f"Zpracování dokončeno!\n\n"
                f"Nalezeno faktur: {len(self.filtered_invoices)}\n"
                f"Přesunuto: {success}\n"
                f"Chyby: {errors}"
            ))

        except Exception as e:
            logger.exception(f"Chyba v processing thread: {e}")
            self.after(0, lambda: messagebox.showerror("Chyba", str(e)))
        finally:
            # Cleanup v případě chyby
            gc.collect()
            log_memory_state("FINALLY CLEANUP")
            self.after(0, self._reset_ui)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main():
    app = InvoiceProcessorGUIV2()
    app.mainloop()


if __name__ == "__main__":
    main()
