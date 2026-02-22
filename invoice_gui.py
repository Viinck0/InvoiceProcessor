#!/usr/bin/env python3
"""
Invoice Processor - GUI verze
Moderní uživatelské rozhraní pro vyhledávání, analýzu a třídění faktur
pomocí lokálního LLM modelu přes Ollama.

Použití:
    python invoice_gui.py

Závislosti:
    pip install customtkinter PyMuPDF ollama pytesseract Pillow
"""

import os
import re
import sys
import json
import shutil
import logging
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Callable
from datetime import datetime

# GUI knihovny
import customtkinter as ctk
from tkinter import filedialog, messagebox

# ─────────────────────────────────────────────
# Konfigurační konstanty
# ─────────────────────────────────────────────
SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
OLLAMA_MODEL = "llama3.1"
MAX_TEXT_CHARS = 4000
LOG_LEVEL = logging.INFO

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
    raw_json: dict = field(default_factory=dict)

    @property
    def original_stem(self) -> str:
        stem = self.source_path.stem
        return _sanitize_filename(stem)

    @property
    def suffix(self) -> str:
        return self.source_path.suffix.lower()


# ─────────────────────────────────────────────
# Pomocné funkce
# ─────────────────────────────────────────────
def _sanitize_filename(name: str) -> str:
    """Odstraní/nahradí znaky nevhodné pro názvy souborů."""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r'\s+', "_", name.strip())
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
# Modul: Vyhledávání souborů
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
# Modul: Extrakce textu
# ─────────────────────────────────────────────
class TextExtractor:
    """Extrahuje text z PDF a obrázkových souborů."""

    def extract(self, file_path: Path) -> str:
        ext = file_path.suffix.lower()
        if ext == ".pdf":
            return self._extract_pdf(file_path)
        elif ext in {".jpg", ".jpeg", ".png"}:
            return self._extract_image(file_path)
        return ""

    def _extract_pdf(self, file_path: Path) -> str:
        try:
            import fitz
        except ImportError:
            logger.error("PyMuPDF není nainstalován.")
            return ""

        text_parts: list[str] = []
        try:
            with fitz.open(str(file_path)) as doc:
                for page in doc:
                    text_parts.append(page.get_text())
        except Exception as e:
            logger.warning(f"Chyba při čtení PDF '{file_path.name}': {e}")
            return ""

        return "\n".join(text_parts)

    def _extract_image(self, file_path: Path) -> str:
        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            logger.warning("pytesseract nebo Pillow není nainstalován.")
            return ""

        try:
            img = Image.open(str(file_path))
            text = pytesseract.image_to_string(img, lang="ces+eng")
            return text
        except Exception as e:
            logger.warning(f"Chyba při OCR obrázku '{file_path.name}': {e}")
            return ""


# ─────────────────────────────────────────────
# Modul: AI analýza přes Ollama
# ─────────────────────────────────────────────
class OllamaAnalyzer:
    """Analyzuje text dokumentu pomocí lokálního LLM přes Ollama."""

    PROMPT_TEMPLATE = """Jsi expert na účetnictví. Analyzuj text dokumentu níže a urči, zda se jedná o fakturu.

Vrať POUZE validní JSON objekt (žádný jiný text) s těmito klíči:
- "is_invoice": boolean (true pokud je to faktura, jinak false)
- "sender_name": string (jméno odesílatele/dodavatele, nebo "")
- "recipient_name": string (jméno příjemce/odběratele, nebo "")
- "issue_date": string (datum vystavení ve formátu YYYY-MM-DD, nebo "")
- "due_date": string (datum splatnosti ve formátu YYYY-MM-DD, nebo "")

Příklad správného výstupu:
{{"is_invoice": true, "sender_name": "ABC s.r.o.", "recipient_name": "XYZ a.s.", "issue_date": "2024-01-15", "due_date": "2024-02-15"}}

TEXT DOKUMENTU:
{text}

JSON:"""

    def __init__(self, model: Optional[str] = None):
        self.model = model or os.environ.get("OLLAMA_MODEL", OLLAMA_MODEL)
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

    def analyze(self, text: str, filename: str) -> Optional[dict]:
        if not text.strip():
            logger.warning(f"Prázdný text: {filename}")
            return None

        truncated_text = text[:MAX_TEXT_CHARS]
        prompt = self.PROMPT_TEMPLATE.format(text=truncated_text)
        client = self._get_client()

        try:
            response = client.generate(
                model=self.model,
                prompt=prompt,
                options={"temperature": 0.1}
            )
            raw_output = response.get("response", "")
        except Exception as e:
            logger.warning(f"Chyba komunikace s Ollama pro '{filename}': {e}")
            return None

        parsed = _extract_json_from_text(raw_output)
        if parsed is None:
            logger.warning(f"LLM nevrátil validní JSON pro '{filename}'")
            return None

        return parsed


# ─────────────────────────────────────────────
# Modul: Třídění a přesun souborů
# ─────────────────────────────────────────────
class InvoiceOrganizer:
    """Třídí, přesouvá a přejmenovává soubory faktur."""

    SORT_OPTIONS = {
        "sender_name": "Odesílatel",
        "recipient_name": "Příjemce",
        "issue_date": "Datum_vystaveni",
        "due_date": "Datum_splatnosti",
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

    def process(self, progress_callback: Optional[Callable] = None) -> tuple[int, int]:
        """Proces přesunu s progress callbackem. Vrací (úspěch, chyby)."""
        if not self.invoices:
            return 0, 0

        sorted_invoices = self.sort_invoices(self.target_dir.parent.name)

        try:
            self.target_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            logger.error(f"Nelze vytvořit cílovou složku: {e}")
            return 0, len(sorted_invoices)

        errors = 0
        success = 0

        for i, invoice in enumerate(sorted_invoices, start=1):
            new_name = self._build_new_name(i, invoice, self.sort_key)
            dest_path = self.target_dir / new_name

            if dest_path.exists():
                stem = dest_path.stem
                dest_path = self.target_dir / f"{stem}_dup{dest_path.suffix}"

            try:
                shutil.move(str(invoice.source_path), str(dest_path))
                success += 1
                if progress_callback:
                    progress_callback(i, len(sorted_invoices), invoice.source_path.name, new_name, True)
            except Exception as e:
                errors += 1
                if progress_callback:
                    progress_callback(i, len(sorted_invoices), invoice.source_path.name, str(e), False)

        return success, errors


# ─────────────────────────────────────────────
# GUI Aplikace
# ─────────────────────────────────────────────
class InvoiceProcessorGUI(ctk.CTk):
    """Hlavní GUI aplikace pro Invoice Processor."""

    def __init__(self):
        super().__init__()

        self.title("🧾 Invoice Processor - AI Třídění Faktur")
        self.geometry("900x700")
        self.minsize(800, 600)

        # Proměnné
        self.source_dir = ctk.StringVar()
        self.target_dir = ctk.StringVar()
        self.sort_key = ctk.StringVar(value="issue_date")
        self.model_name = ctk.StringVar(value=OLLAMA_MODEL)
        
        # Filtr - nové proměnné
        self.filter_key = ctk.StringVar(value="")  # Prázdné = filtr vypnut
        self.filter_value = ctk.StringVar()

        self.extractor = TextExtractor()
        self.analyzer = OllamaAnalyzer()

        self.is_processing = False
        self.found_files: list[Path] = []
        self.invoices: list[Invoice] = []

        # Mapování zobrazených názvů na klíče
        self.sort_key_map = {
            "Datum vystavení": "issue_date",
            "Datum splatnosti": "due_date",
            "Odesílatel": "sender_name",
            "Příjemce": "recipient_name",
        }
        self.reverse_sort_key_map = {v: k for k, v in self.sort_key_map.items()}
        
        # Mapování pro filtr - přidána možnost "Vypnuto"
        self.filter_key_map = {
            "Vypnuto": "",
            "Datum vystavení": "issue_date",
            "Datum splatnosti": "due_date",
            "Odesílatel": "sender_name",
            "Příjemce": "recipient_name",
        }

        self._setup_ui()

    def _setup_ui(self):
        """Vytvoří celé uživatelské rozhraní."""
        # Hlavní layout s grid
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # ─── Header ────────────────────────────
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 10))
        header_frame.grid_columnconfigure(0, weight=1)

        title_label = ctk.CTkLabel(
            header_frame,
            text="🧾 Invoice Processor",
            font=ctk.CTkFont(size=28, weight="bold")
        )
        title_label.grid(row=0, column=0, sticky="w")

        subtitle_label = ctk.CTkLabel(
            header_frame,
            text="AI-powered třídění faktur pomocí Ollama + LLM",
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
            values=["Datum vystavení", "Datum splatnosti", "Odesílatel", "Příjemce"],
            command=self._on_sort_key_change,
            width=200
        ).grid(row=0, column=1, sticky="w", padx=(0, 30), pady=10)

        # Model selection
        ctk.CTkLabel(options_frame, text="🤖 Ollama model:").grid(
            row=0, column=2, sticky="w", padx=(10, 10), pady=10
        )
        ctk.CTkOptionMenu(
            options_frame,
            variable=self.model_name,
            values=["llama3.1", "llama3.2", "mistral", "gemma2", "llama3"],
            width=150
        ).grid(row=0, column=3, sticky="w", pady=10)

        # ─── Filtr faktur ────────────────────────
        filter_frame = ctk.CTkFrame(self, fg_color="#2b2b2b")
        filter_frame.grid(row=3, column=0, sticky="ew", padx=20, pady=10)
        filter_frame.grid_columnconfigure(1, weight=1)
        
        ctk.CTkLabel(
            filter_frame, 
            text="🔍 Vybrat faktury podle:",
            font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=(15, 10), pady=10)
        
        ctk.CTkOptionMenu(
            filter_frame,
            variable=self.filter_key,
            values=["Vypnuto", "Datum vystavení", "Datum splatnosti", "Odesílatel", "Příjemce"],
            command=self._on_filter_key_change,
            width=200
        ).grid(row=0, column=1, sticky="w", padx=(0, 10), pady=10)
        
        ctk.CTkLabel(filter_frame, text="Hledaný výraz:").grid(
            row=0, column=2, sticky="w", padx=(15, 5), pady=10
        )
        self.filter_entry = ctk.CTkEntry(
            filter_frame,
            textvariable=self.filter_value,
            placeholder_text="Filtr vypnut - vyberte kritérium",
            width=250,
            state="disabled"
        )
        self.filter_entry.grid(row=0, column=3, sticky="w", padx=(0, 15), pady=10)

        # ─── Progress a log output ──────────────
        output_frame = ctk.CTkFrame(self)
        output_frame.grid(row=4, column=0, sticky="nsew", padx=20, pady=10)
        output_frame.grid_columnconfigure(0, weight=1)
        output_frame.grid_rowconfigure(1, weight=1)

        # Progress bar
        self.progress_label = ctk.CTkLabel(
            output_frame,
            text="Připraveno",
            font=ctk.CTkFont(size=12)
        )
        self.progress_label.grid(row=0, column=0, sticky="w", padx=15, pady=(15, 5))

        self.progress_bar = ctk.CTkProgressBar(output_frame, mode="determinate")
        self.progress_bar.grid(row=1, column=0, sticky="ew", padx=15, pady=5)
        self.progress_bar.set(0)

        # Log text area
        self.log_text = ctk.CTkTextbox(output_frame, font=("Consolas", 11))
        self.log_text.grid(row=2, column=0, sticky="nsew", padx=15, pady=(5, 15))
        self.log_text.configure(state="disabled")

        # ─── Action buttons ─────────────────────
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=5, column=0, sticky="ew", padx=20, pady=(0, 20))
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
        self.status_var = ctk.StringVar(value="Stav: Připraveno | Model: " + OLLAMA_MODEL)
        status_bar = ctk.CTkLabel(
            self,
            textvariable=self.status_var,
            font=ctk.CTkFont(size=11),
            text_color="gray"
        )
        status_bar.grid(row=6, column=0, sticky="ew", padx=20, pady=(0, 10))

    def _browse_source(self):
        """Otevře dialog pro výběr zdrojové složky."""
        folder = filedialog.askdirectory(title="Vyberte zdrojovou složku")
        if folder:
            self.source_dir.set(folder)
            self._log(f"Zdrojová složka: {folder}")

    def _browse_target(self):
        """Otevře dialog pro výběr cílové složky."""
        folder = filedialog.askdirectory(title="Vyberte cílovou složku")
        if folder:
            self.target_dir.set(folder)
            self._log(f"Cílová složka: {folder}")

    def _on_sort_key_change(self, display_value: str):
        """Převede zobrazený název kritéria na interní klíč."""
        actual_key = self.sort_key_map.get(display_value, "issue_date")
        self.sort_key.set(actual_key)

    def _on_filter_key_change(self, display_value: str):
        """Převede zobrazený název filtru na interní klíč a spravuje stav entry."""
        actual_key = self.filter_key_map.get(display_value, "")
        self.filter_key.set(actual_key)
        
        # Pokud je filtr vypnut, zakážeme entry
        if display_value == "Vypnuto":
            self.filter_entry.configure(state="disabled", placeholder_text="Filtr vypnut")
        else:
            self.filter_entry.configure(state="normal")
            if display_value in ["Datum vystavení", "Datum splatnosti"]:
                self.filter_entry.configure(placeholder_text="Např.: 2024-01-15")
            elif display_value == "Odesílatel":
                self.filter_entry.configure(placeholder_text="Např.: Jana, Alza, s.r.o.")
            elif display_value == "Příjemce":
                self.filter_entry.configure(placeholder_text="Např.: XYZ a.s., Firma")

    def _validate_filter(self) -> bool:
        """
        Validuje filtr - pokud je vybrán, hodnota nesmí být prázdná.
        Vrací True pokud je filtr validní.
        """
        filter_key = self.filter_key.get().strip()
        filter_value = self.filter_value.get().strip()
        
        # Pokud je filtr vypnut, je vše v pořádku
        if not filter_key:
            return True
        
        # Pokud je filtr zapnutý, hodnota nesmí být prázdná
        if not filter_value:
            display_name = [k for k, v in self.filter_key_map.items() if v == filter_key]
            display_name = display_name[0] if display_name else filter_key
            messagebox.showerror(
                "Chyba",
                f"Je vybrán filtr '{display_name}', ale není zadána hledaná hodnota!\n\n"
                f"Prosím zadejte hodnotu do pole 'Hledaný výraz' nebo vypněte filtr."
            )
            return False
        
        return True

    def _matches_filter(self, invoice: Invoice) -> bool:
        """
        Zkontroluje zda faktura splňuje filtr.
        Pokud je filtr vypnut, vrací True pro všechny faktury.
        """
        filter_key = self.filter_key.get().strip()
        filter_value = self.filter_value.get().strip().lower()
        
        # Pokud je filtr vypnut, všechny faktury vyhovují
        if not filter_key:
            return True
        
        # Získání hodnoty z faktury podle klíče
        invoice_value = getattr(invoice, filter_key, "")
        
        if not invoice_value:
            return False
        
        # Porovnání - hledáme částečnou shodu (case-insensitive)
        return filter_value in invoice_value.lower()

    def _log(self, message: str):
        """Přidá zprávu do log outputu."""
        self.log_text.configure(state="normal")
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        """Vyčistí log output."""
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _update_progress(self, value: float, label: str = ""):
        """Aktualizuje progress bar a label."""
        self.progress_bar.set(value / 100)
        if label:
            self.progress_label.configure(text=label)

    def _start_processing(self):
        """Spustí processing ve vlákně."""
        if self.is_processing:
            return

        # Validace vstupů
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

        # Validace filtru
        if not self._validate_filter():
            return

        # Nastavení UI pro processing
        self.is_processing = True
        self.start_button.configure(state="disabled", text="⏳ Zpracovávám...")
        self.cancel_button.configure(state="normal")
        self._clear_log()

        # Aktualizace modelu
        self.analyzer.model = self.model_name.get()
        self.status_var.set(f"Stav: Zpracovávám | Model: {self.analyzer.model}")

        # Spuštění ve vlákně
        thread = threading.Thread(target=self._process_thread, args=(source, target), daemon=True)
        thread.start()

    def _cancel_processing(self):
        """Zruší processing."""
        self.is_processing = False
        self._log("❌ Zrušeno uživatelem")
        self._reset_ui()

    def _reset_ui(self):
        """Obnoví UI do původního stavu."""
        self.is_processing = False
        self.start_button.configure(state="normal", text="▶ Spustit zpracování")
        self.cancel_button.configure(state="disabled")
        self._update_progress(0, "Připraveno")
        self.status_var.set(f"Stav: Připraveno | Model: {self.analyzer.model}")

    def _process_thread(self, source: str, target: str):
        """Hlavní processing thread."""
        try:
            source_path = Path(source)
            target_path = Path(target)

            # Krok 1: Vyhledání souborů
            self._log("🔍 Vyhledávám soubory...")
            discovery = FileDiscovery(source_path)
            self.found_files = discovery.find_files()

            if not self.found_files:
                self.after(0, lambda: messagebox.showinfo("Info", "Nenalezeny žádné PDF/JPG/PNG soubory."))
                self.after(0, self._reset_ui)
                return

            self._log(f"✅ Nalezeno {len(self.found_files)} souborů")
            self._update_progress(10, f"Nalezeno {len(self.found_files)} souborů")

            if not self.is_processing:
                return

            # Krok 2: AI analýza
            self._log(f"🤖 Spouštím AI analýzu (model: {self.analyzer.model})...")
            self.invoices = []
            total = len(self.found_files)

            for idx, file_path in enumerate(self.found_files, start=1):
                if not self.is_processing:
                    break

                self._log(f"[{idx:3d}/{total}] Analyzuji: {file_path.name}")
                progress = 10 + (idx / total) * 60

                # Extrakce textu
                text = self.extractor.extract(file_path)
                if not text.strip():
                    self._log(f"  ⚠ Prázdný text, přeskočeno")
                    continue

                # AI analýza
                result = self.analyzer.analyze(text, file_path.name)
                if result is None:
                    self._log(f"  ✗ Chyba analýzy")
                    continue

                is_invoice = result.get("is_invoice", False)
                if not is_invoice:
                    self._log(f"  – Není faktura")
                    continue

                # Vytvoření Invoice objektu
                invoice = Invoice(
                    source_path=file_path,
                    sender_name=_sanitize_filename(result.get("sender_name") or "Neznamy_odesilatel"),
                    recipient_name=_sanitize_filename(result.get("recipient_name") or "Neznamy_prijemce"),
                    issue_date=result.get("issue_date") or "0000-00-00",
                    due_date=result.get("due_date") or "0000-00-00",
                    raw_json=result,
                )
                
                # Filtrování podle zadaného kritéria
                if not self._matches_filter(invoice):
                    self._log(f"  ⊘ Faktura (nesplňuje filtr)")
                    continue
                
                self.invoices.append(invoice)
                self._log(f"  ✓ Faktura | Od: {invoice.sender_name[:30]} | Datum: {invoice.issue_date}")

                self._update_progress(progress, f"Analyzuji: {idx}/{total}")

            if not self.is_processing:
                return

            # Krok 3: Výsledky
            self._log("─" * 50)
            self._log(f"📊 VÝSLEDKY ANALÝZY")
            self._log(f"  Celkem souborů: {len(self.found_files)}")
            self._log(f"  Identifikováno faktur: {len(self.invoices)}")
            self._log(f"  Přeskočeno: {len(self.found_files) - len(self.invoices)}")

            if not self.invoices:
                self.after(0, lambda: messagebox.showinfo("Info", "Žádné faktury nenalezeny."))
                self.after(0, self._reset_ui)
                return

            self._update_progress(75, f"Nalezeno {len(self.invoices)} faktur")

            # Krok 4: Třídění a přesun
            self._log("─" * 50)
            self._log("📁 Třídím a přesouvám faktury...")

            # Získání labelu pro kritérium
            sort_labels = {
                "sender_name": "Odesílatel",
                "recipient_name": "Příjemce",
                "issue_date": "Datum_vystaveni",
                "due_date": "Datum_splatnosti",
            }
            sort_label = sort_labels.get(self.sort_key.get(), "Nezname")

            organizer = InvoiceOrganizer(self.invoices, target_path)
            organizer.sort_key = self.sort_key.get()

            success_count = 0
            error_count = 0

            sorted_invoices = organizer.sort_invoices(self.sort_key.get())

            for i, invoice in enumerate(sorted_invoices, start=1):
                if not self.is_processing:
                    break

                new_name = organizer._build_new_name(i, invoice, self.sort_key.get())
                dest_path = target_path / new_name

                if dest_path.exists():
                    stem = dest_path.stem
                    dest_path = target_path / f"{stem}_dup{dest_path.suffix}"

                try:
                    shutil.move(str(invoice.source_path), str(dest_path))
                    success_count += 1
                    self._log(f"  ✓ [{i:03d}] {invoice.source_path.name}")
                    self._log(f"        → {new_name}")
                except Exception as e:
                    error_count += 1
                    self._log(f"  ✗ Chyba: {invoice.source_path.name} - {e}")

                progress = 75 + (i / len(sorted_invoices)) * 25
                self._update_progress(progress, f"Přesouvám: {i}/{len(sorted_invoices)}")

            # Závěr
            self._log("─" * 50)
            self._log(f"✅ Hotovo!")
            self._log(f"  Úspěšně přesunuto: {success_count}")
            self._log(f"  Chyby: {error_count}")
            self._log(f"  Cíl: {target_path}")

            self._update_progress(100, "Dokončeno")

            self.after(0, lambda: messagebox.showinfo(
                "Dokončeno",
                f"Zpracování dokončeno!\n\n"
                f"✅ Úspěšně: {success_count}\n"
                f"❌ Chyby: {error_count}\n"
                f"📁 Cíl: {target_path}"
            ))

        except Exception as e:
            self._log(f"❌ Kritická chyba: {e}")
            logger.exception("Critical error in processing thread")
            self.after(0, lambda: messagebox.showerror("Chyba", f"Neočekávaná chyba:\n{e}"))

        finally:
            self.after(0, self._reset_ui)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main():
    app = InvoiceProcessorGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
