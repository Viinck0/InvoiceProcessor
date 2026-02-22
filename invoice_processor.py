#!/usr/bin/env python3
"""
Invoice Processor - CLI aplikace pro vyhledávání, analýzu a třídění faktur
pomocí lokálního LLM modelu přes Ollama.

Použití:
    python invoice_processor.py

Závislosti:
    pip install PyMuPDF ollama pytesseract Pillow

Ollama setup:
    ollama pull llama3.1
    ollama serve  (nebo spustit Ollama Desktop)
"""

import os
import re
import sys
import json
import shutil
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ─────────────────────────────────────────────
# Konfigurační konstanty
# ─────────────────────────────────────────────
SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
OLLAMA_MODEL = "llama3.1"          # Lze přepsat env proměnnou OLLAMA_MODEL
MAX_TEXT_CHARS = 4000              # Limit znaků posílaných do LLM
LOG_LEVEL = logging.INFO

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
        """Původní název souboru bez přípony, sanitizovaný."""
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
    return name[:60]  # Omezení délky


def _extract_json_from_text(text: str) -> Optional[dict]:
    """
    Extrahuje JSON objekt z textu LLM.
    LLM občas přidá text před/za JSON — hledáme první { ... }.
    """
    # Zkusíme přímý parse
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Hledáme JSON blok ohraničený ```json ... ``` nebo ``` ... ```
    code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if code_block:
        try:
            return json.loads(code_block.group(1))
        except json.JSONDecodeError:
            pass

    # Hledáme první { ... } v textu
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
        """Vrátí seznam všech nalezených souborů s podporovanými příponami."""
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
        """Dispečer — volá správnou metodu podle přípony souboru."""
        ext = file_path.suffix.lower()
        if ext == ".pdf":
            return self._extract_pdf(file_path)
        elif ext in {".jpg", ".jpeg", ".png"}:
            return self._extract_image(file_path)
        return ""

    def _extract_pdf(self, file_path: Path) -> str:
        """Extrahuje text z PDF pomocí PyMuPDF (fitz)."""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.error("PyMuPDF není nainstalován. Spusťte: pip install PyMuPDF")
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
        """
        Placeholder pro OCR obrázků pomocí pytesseract.
        Vyžaduje instalaci Tesseract OCR na systému.
        Viz: https://github.com/tesseract-ocr/tesseract
        """
        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            logger.warning(
                "pytesseract nebo Pillow není nainstalován — obrázek přeskočen. "
                "Nainstalujte: pip install pytesseract Pillow"
            )
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
        """Lazy inicializace Ollama klienta."""
        if self._client is None:
            try:
                import ollama
                self._client = ollama
            except ImportError:
                logger.error("Knihovna 'ollama' není nainstalována. Spusťte: pip install ollama")
                sys.exit(1)
        return self._client

    def analyze(self, text: str, filename: str) -> Optional[dict]:
        """
        Pošle text LLM a vrátí parsovaný JSON nebo None při chybě.
        """
        if not text.strip():
            logger.warning(f"  Prázdný text, přeskakuji analýzu: {filename}")
            return None

        # Zkrátíme text na MAX_TEXT_CHARS, aby se vešel do kontextu
        truncated_text = text[:MAX_TEXT_CHARS]
        prompt = self.PROMPT_TEMPLATE.format(text=truncated_text)

        client = self._get_client()

        try:
            response = client.generate(
                model=self.model,
                prompt=prompt,
                options={"temperature": 0.1}  # Nízká teplota = deterministický výstup
            )
            raw_output = response.get("response", "")
        except Exception as e:
            logger.warning(f"  Chyba při komunikaci s Ollama pro '{filename}': {e}")
            return None

        parsed = _extract_json_from_text(raw_output)
        if parsed is None:
            logger.warning(
                f"  LLM nevrátil validní JSON pro '{filename}'. "
                f"Surový výstup: {raw_output[:200]!r}"
            )
            return None

        return parsed


# ─────────────────────────────────────────────
# Modul: Třídění a přesun souborů
# ─────────────────────────────────────────────
class InvoiceOrganizer:
    """Třídí, přesouvá a přejmenovává soubory faktur."""

    SORT_OPTIONS = {
        "1": ("sender_name",    "Odesílatel"),
        "2": ("recipient_name", "Příjemce"),
        "3": ("issue_date",     "Datum_vystaveni"),
        "4": ("due_date",       "Datum_splatnosti"),
    }

    def __init__(self, invoices: list[Invoice], target_dir: Path):
        self.invoices = invoices
        self.target_dir = target_dir

    def ask_sort_criterion(self) -> tuple[str, str]:
        """Zeptá se uživatele na kritérium třídění a vrátí (klíč, label)."""
        print("\n" + "═" * 55)
        print("Podle čeho chcete faktury vytřídit a přejmenovat?")
        print("─" * 55)
        for key, (attr, label) in self.SORT_OPTIONS.items():
            print(f"  {key} - {label}")
        print("═" * 55)

        while True:
            choice = input("Vaše volba [1-4]: ").strip()
            if choice in self.SORT_OPTIONS:
                attr, label = self.SORT_OPTIONS[choice]
                return attr, label
            print("  ⚠ Neplatná volba. Zadejte číslo 1 až 4.")

    def sort_invoices(self, sort_key: str) -> list[Invoice]:
        """Seřadí faktury podle zvoleného klíče."""
        return sorted(
            self.invoices,
            key=lambda inv: getattr(inv, sort_key) or "ZZZZ"  # Prázdné hodnoty na konec
        )

    def _build_new_name(self, index: int, invoice: Invoice, sort_key: str) -> str:
        """
        Sestaví nový název souboru.
        Formát: 001_[Kriterium]_[Puvodni_nazev].[ext]
        """
        criterion_value = _sanitize_filename(getattr(invoice, sort_key) or "nezname")
        seq = str(index).zfill(3)
        return f"{seq}_{criterion_value}_{invoice.original_stem}{invoice.suffix}"

    def process(self) -> None:
        """Hlavní metoda — třídí a přesouvá faktury."""
        if not self.invoices:
            print("\nŽádné faktury k přesunutí.")
            return

        sort_key, sort_label = self.ask_sort_criterion()
        sorted_invoices = self.sort_invoices(sort_key)

        # Vytvoříme cílovou složku
        try:
            self.target_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            logger.error(f"Nelze vytvořit cílovou složku '{self.target_dir}': {e}")
            sys.exit(1)

        print(f"\nPřesouvám {len(sorted_invoices)} faktur do: {self.target_dir}")
        print("─" * 55)

        errors = 0
        for i, invoice in enumerate(sorted_invoices, start=1):
            new_name = self._build_new_name(i, invoice, sort_key)
            dest_path = self.target_dir / new_name

            # Konflikt názvů — přidáme suffix
            if dest_path.exists():
                stem = dest_path.stem
                dest_path = self.target_dir / f"{stem}_dup{dest_path.suffix}"

            try:
                shutil.move(str(invoice.source_path), str(dest_path))
                print(f"  ✓ [{i:03d}] {invoice.source_path.name}")
                print(f"        → {new_name}")
            except PermissionError as e:
                logger.error(f"  ✗ Přístup odepřen: {invoice.source_path.name} — {e}")
                errors += 1
            except FileNotFoundError as e:
                logger.error(f"  ✗ Soubor nenalezen: {invoice.source_path.name} — {e}")
                errors += 1
            except Exception as e:
                logger.error(f"  ✗ Neočekávaná chyba u '{invoice.source_path.name}': {e}")
                errors += 1

        print("─" * 55)
        ok_count = len(sorted_invoices) - errors
        print(f"  Úspěšně přesunuto: {ok_count}  |  Chyby: {errors}")


# ─────────────────────────────────────────────
# Hlavní orchestrátor
# ─────────────────────────────────────────────
class InvoiceProcessor:
    """Orchestruje celý pipeline od vyhledání po přesun faktur."""

    def __init__(self):
        self.extractor = TextExtractor()
        self.analyzer = OllamaAnalyzer()

    # ── Vstupní pomocné metody ──────────────────
    def _prompt_directory(self, label: str, must_exist: bool = True) -> Path:
        """Vyzve uživatele k zadání cesty ke složce."""
        while True:
            raw = input(f"{label}: ").strip()
            if not raw:
                print("  ⚠ Cesta nesmí být prázdná.")
                continue
            path = Path(raw).expanduser().resolve()
            if must_exist and not path.is_dir():
                print(f"  ⚠ Složka '{path}' neexistuje nebo není adresář.")
                continue
            return path

    def _confirm(self, message: str) -> bool:
        """Jednoduchý Y/N dotaz."""
        answer = input(f"{message} [A/n]: ").strip().lower()
        return answer in ("", "a", "y", "ano", "yes")

    # ── Hlavní pipeline ─────────────────────────
    def run(self) -> None:
        """Spustí celý processing pipeline."""
        self._print_banner()

        # 1. Vstup od uživatele
        print("\n📁  NASTAVENÍ SLOŽEK")
        print("─" * 55)
        source_dir = self._prompt_directory("Zdrojová složka (kde hledat soubory)")
        target_dir = self._prompt_directory("Cílová složka (kam přesunout faktury)", must_exist=False)

        # 2. Vyhledávání souborů
        print("\n🔍  VYHLEDÁVÁNÍ SOUBORŮ")
        print("─" * 55)
        discovery = FileDiscovery(source_dir)
        files = discovery.find_files()

        if not files:
            print("Nenalezeny žádné PDF/JPG/PNG soubory. Ukončuji.")
            sys.exit(0)

        if not self._confirm(f"\nNalezeno {len(files)} souborů. Spustit AI analýzu?"):
            print("Ukončeno uživatelem.")
            sys.exit(0)

        # 3. Extrakce textu + AI analýza
        print(f"\n🤖  AI ANALÝZA (model: {self.analyzer.model})")
        print("─" * 55)
        invoices = self._process_files(files)

        # 4. Výsledek analýzy
        print("\n📊  VÝSLEDKY ANALÝZY")
        print("─" * 55)
        print(f"  Celkem zpracováno souborů : {len(files)}")
        print(f"  Identifikováno faktur     : {len(invoices)}")
        non_invoices = len(files) - len(invoices)
        print(f"  Přeskočeno (není faktura) : {non_invoices}")

        if not invoices:
            print("\nŽádné faktury nenalezeny. Ukončuji.")
            sys.exit(0)

        # 5. Třídění a přesun
        organizer = InvoiceOrganizer(invoices, target_dir)
        organizer.process()

        print("\n✅  Hotovo!\n")

    def _process_files(self, files: list[Path]) -> list[Invoice]:
        """
        Pro každý soubor extrahuje text, pošle do LLM a vrátí
        seznam pouze těch, které jsou faktury.
        """
        invoices: list[Invoice] = []
        total = len(files)

        for idx, file_path in enumerate(files, start=1):
            print(f"  [{idx:3d}/{total}] {file_path.name} ...", end=" ", flush=True)

            # Extrakce textu
            text = self.extractor.extract(file_path)
            if not text.strip():
                print("⚠ Prázdný text, přeskočeno.")
                continue

            # AI analýza
            result = self.analyzer.analyze(text, file_path.name)
            if result is None:
                print("✗ Chyba analýzy.")
                continue

            # Kontrola is_invoice
            is_invoice = result.get("is_invoice", False)
            if not is_invoice:
                print("– Není faktura.")
                continue

            # Sestavení Invoice objektu
            invoice = Invoice(
                source_path=file_path,
                sender_name=_sanitize_filename(result.get("sender_name") or "Neznamy_odesilatel"),
                recipient_name=_sanitize_filename(result.get("recipient_name") or "Neznamy_prijemce"),
                issue_date=result.get("issue_date") or "0000-00-00",
                due_date=result.get("due_date") or "0000-00-00",
                raw_json=result,
            )
            invoices.append(invoice)
            print(f"✓ Faktura | Od: {invoice.sender_name[:25]:<25} | Datum: {invoice.issue_date}")

        return invoices

    @staticmethod
    def _print_banner() -> None:
        banner = r"""
╔═══════════════════════════════════════════════════════╗
║          🧾  Invoice Processor  v1.0                  ║
║   AI-powered třídění faktur pomocí Ollama + LLM       ║
╚═══════════════════════════════════════════════════════╝
"""
        print(banner)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main() -> None:
    processor = InvoiceProcessor()
    try:
        processor.run()
    except KeyboardInterrupt:
        print("\n\nUkončeno uživatelem (Ctrl+C).")
        sys.exit(0)


if __name__ == "__main__":
    main()
