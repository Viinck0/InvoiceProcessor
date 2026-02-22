#!/usr/bin/env python3
"""
Invoice Processor v2 - Workflow s Llama Vision
Rychlé vyhledávání → Vision analýza → Filtrování → Třídění

Použití:
    python invoice_processor_v2.py

Závislosti:
    pip install PyMuPDF ollama Pillow

Ollama setup:
    ollama pull llama3.2-vision    # nebo llava, bakllava
    ollama serve
"""

import os
import re
import sys
import json
import shutil
import logging
import base64
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ─────────────────────────────────────────────
# Konfigurační konstanty
# ─────────────────────────────────────────────
SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".gif"}
VISION_MODEL = "llama3.2-vision"
MAX_IMAGE_SIZE = 768  # Sníženo z 2048 pro rychlejší analýzu
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
    total_amount: str = ""
    currency: str = ""
    is_invoice: bool = False
    confidence: float = 0.0
    raw_json: dict = field(default_factory=dict)

    @property
    def original_stem(self) -> str:
        """Původní název souboru bez přípony, sanitizovaný."""
        stem = self.source_path.stem
        return _sanitize_filename(stem)

    @property
    def suffix(self) -> str:
        return self.source_path.suffix.lower()

    def matches_filter(self, filter_key: str, filter_value: str) -> bool:
        """Zkontroluje zda faktura splňuje filtr."""
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
    # Odstraní diakritiku pro lepší kompatibilitu
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
    """
    Rychlé rekurzivní vyhledávání všech podporovaných souborů.
    Optimalizováno pro velké složky.
    """

    def __init__(self, source_dir: Path):
        self.source_dir = source_dir
        self.extensions = SUPPORTED_EXTENSIONS

    def find_files(self, show_progress: bool = True) -> list[Path]:
        """Vrátí seznam všech nalezených souborů s podporovanými příponami."""
        found: list[Path] = []
        
        if show_progress:
            logger.info(f"🔍 Prohledávám složku: {self.source_dir}")

        try:
            # Použijeme rglob pro rekurzivní hledání
            for path in self.source_dir.rglob("*"):
                if path.is_file() and path.suffix.lower() in self.extensions:
                    found.append(path)
        except PermissionError as e:
            logger.warning(f"Přístup odepřen: {e}")

        if show_progress:
            logger.info(f"✅ Nalezeno {len(found)} souborů ke zpracování.")
        
        return found

    def find_files_fast(self, max_depth: int = 10) -> list[Path]:
        """
        Rychlejší varianta s omezenou hloubkou.
        Vhodné pro velmi velké stromy souborů.
        """
        found: list[Path] = []
        max_depth = max_depth

        def walk_with_depth(path: Path, depth: int = 0):
            if depth > max_depth:
                return
            
            try:
                for item in path.iterdir():
                    if item.is_file() and item.suffix.lower() in self.extensions:
                        found.append(item)
                    elif item.is_dir() and depth < max_depth:
                        walk_with_depth(item, depth + 1)
            except PermissionError:
                pass

        walk_with_depth(self.source_dir)
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
            self.io = __import__('io')
        except ImportError:
            logger.error("Pillow není nainstalován.")
            self.Image = None
            self.io = None

    def prepare_image(self, file_path: Path) -> Optional[bytes]:
        """Vrátí binární data obrázku připravená pro Vision model."""
        ext = file_path.suffix.lower()

        if ext == ".pdf":
            return self._pdf_to_image(file_path)
        elif ext in {".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".gif"}:
            return self._load_image(file_path)

        return None

    def _pdf_to_image(self, file_path: Path) -> Optional[bytes]:
        """Konvertuje první stránku PDF na obrázek."""
        try:
            import fitz  # PyMuPDF
        except ImportError as e:
            logger.error(f"Chybí knihovna: {e}")
            return None

        if self.Image is None or self.io is None:
            return None

        try:
            with fitz.open(str(file_path)) as doc:
                if len(doc) == 0:
                    return None

                # Vezmeme první stránku
                page = doc[0]

                # Render stránky jako pixmap
                mat = fitz.Matrix(2.0, 2.0)  # 2x zoom pro lepší kvalitu
                pix = page.get_pixmap(matrix=mat)

                # Konverze na PIL Image
                img_data = pix.tobytes("png")

                # Omezení velikosti
                img = self.Image.open(self.io.BytesIO(img_data))
                img = self._resize_if_needed(img, MAX_IMAGE_SIZE)

                # Uložení do bytes
                output = self.io.BytesIO()
                img.save(output, format="PNG")
                return output.getvalue()

        except Exception as e:
            logger.warning(f"Chyba při konverzi PDF '{file_path.name}': {e}")
            return None

    def _load_image(self, file_path: Path) -> Optional[bytes]:
        """Načte a připraví obrázek."""
        if self.Image is None or self.io is None:
            logger.error("Pillow není nainstalován.")
            return None

        try:
            img = self.Image.open(str(file_path))

            # Konverze na RGB pokud je třeba (např. PNG s alfou)
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')

            # Omezení velikosti
            img = self._resize_if_needed(img, MAX_IMAGE_SIZE)

            output = self.io.BytesIO()
            img.save(output, format="JPEG", quality=85)
            return output.getvalue()

        except Exception as e:
            logger.warning(f"Chyba při načítání obrázku '{file_path.name}': {e}")
            return None

    def _resize_if_needed(self, img, max_size: int):
        """Zmenší obrázek pokud překračuje maximální rozměr."""
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
    """
    Analyzuje dokumenty přímo jako obrázky pomocí multimodálního LLM.
    Žádné OCR - model vidí rovnou pixel a rozumí struktuře faktury.
    """

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
    "confidence": number (0.0-1.0, jak jsi si jistý),
    "sender_name": string (jméno odesílatele/dodavatele),
    "recipient_name": string (jméno příjemce/odběratele),
    "issue_date": string (datum vystavení YYYY-MM-DD),
    "due_date": string (datum splatnosti YYYY-MM-DD),
    "total_amount": string (částka s měnou),
    "currency": string (CZK, EUR, USD),
    "invoice_number": string (číslo faktury),
    "reason": string (krátké vysvětlení proč je/není faktura)
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
        self.timeout = 60  # Timeout v sekundách pro každý soubor

    def _get_client(self):
        """Lazy inicializace Ollama klienta."""
        if self._client is None:
            try:
                import ollama
                self._client = ollama
            except ImportError:
                logger.error("Knihovna 'ollama' není nainstalována.")
                sys.exit(1)
        return self._client

    def analyze(self, file_path: Path) -> Optional[dict]:
        """
        Analyzuje soubor pomocí Vision modelu.
        """
        # Příprava obrázku
        image_data = self.preprocessor.prepare_image(file_path)

        if image_data is None:
            logger.warning(f"  Nepodařilo se připravit obrázek: {file_path.name}")
            return None

        client = self._get_client()

        try:
            # Ollama API pro vision modely
            response = client.generate(
                model=self.model,
                prompt=self.PROMPT_TEMPLATE,
                images=[image_data],
                options={
                    "temperature": 0.01,  # Nižší teplota = rychlejší
                    "num_predict": 512     # Omezení délky odpovědi
                },
                keep_alive="5m"  # Udrží model v paměti
            )

            raw_output = response.get("response", "")
            
        except Exception as e:
            logger.warning(f"  Chyba komunikace s Vision modelem '{file_path.name}': {e}")
            return None

        parsed = _extract_json_from_text(raw_output)
        
        if parsed is None:
            logger.warning(
                f"  Vision model nevrátil validní JSON pro '{file_path.name}'. "
                f"Výstup: {raw_output[:200]!r}"
            )
            return None

        return parsed


# ─────────────────────────────────────────────
# Modul: Filtrování faktur
# ─────────────────────────────────────────────
class InvoiceFilterAgent:
    """
    Filtruje faktury podle uživatelem zadaných kritérií.
    Např.: odesílatel obsahuje "Jana", částka > 1000, atd.
    """

    def __init__(self, invoices: list[Invoice]):
        self.invoices = invoices

    def filter_by_criteria(
        self,
        sender_contains: Optional[str] = None,
        recipient_contains: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        amount_min: Optional[float] = None,
        amount_max: Optional[float] = None,
        currency: Optional[str] = None,
    ) -> list[Invoice]:
        """
        Filtruje faktury podle zadaných kritérií.
        Všechny parametry jsou volitelné a kombinují se s AND.
        """
        filtered = []

        for inv in self.invoices:
            if not inv.is_invoice:
                continue

            # Filtr podle odesílatele
            if sender_contains and sender_contains.lower() not in inv.sender_name.lower():
                continue

            # Filtr podle příjemce
            if recipient_contains and recipient_contains.lower() not in inv.recipient_name.lower():
                continue

            # Filtr podle data
            if date_from and inv.issue_date < date_from:
                continue
            if date_to and inv.issue_date > date_to:
                continue

            # Filtr podle částky (extrahujeme číslo z textu)
            if amount_min is not None or amount_max is not None:
                amount = self._parse_amount(inv.total_amount)
                if amount is None:
                    continue
                if amount_min is not None and amount < amount_min:
                    continue
                if amount_max is not None and amount > amount_max:
                    continue

            # Filtr podle měny
            if currency and currency.upper() not in inv.currency.upper():
                continue

            filtered.append(inv)

        return filtered

    def filter_by_custom(self, filter_key: str, filter_value: str) -> list[Invoice]:
        """
        Filtruje faktury podle libovolného klíče a hodnoty.
        Podporuje částečnou shodu (case-insensitive).
        """
        if not filter_key or not filter_value:
            return [inv for inv in self.invoices if inv.is_invoice]

        filtered = []
        for inv in self.invoices:
            if inv.matches_filter(filter_key, filter_value):
                filtered.append(inv)

        return filtered

    def _parse_amount(self, amount_str: str) -> Optional[float]:
        """Extrahuje číselnou hodnotu z textu částky."""
        if not amount_str:
            return None
        
        # Najdeme první číslo v řetězci
        match = re.search(r'[\d\s]+[,.]?\d*', amount_str.replace(" ", ""))
        if match:
            num_str = match.group().replace(" ", "").replace(",", ".")
            try:
                return float(num_str)
            except ValueError:
                pass
        
        return None

    def get_unique_values(self, field_name: str) -> list[str]:
        """Vrátí seznam unikátních hodnot pro daný field."""
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
        """Seřadí faktury podle zvoleného klíče."""
        return sorted(
            self.invoices,
            key=lambda inv: getattr(inv, sort_key) or "ZZZZ"
        )

    def _build_new_name(
        self, 
        index: int, 
        invoice: Invoice, 
        sort_key: str,
        include_date: bool = True,
        include_amount: bool = False
    ) -> str:
        """
        Sestaví nový název souboru.
        Formát: 001_[Kriterium]_[Puvodni_nazev].[ext]
        """
        criterion_value = _sanitize_filename(getattr(invoice, sort_key) or "nezname")
        seq = str(index).zfill(3)
        
        parts = [seq, criterion_value, invoice.original_stem]
        
        if include_date and invoice.issue_date and invoice.issue_date != "0000-00-00":
            parts.insert(1, invoice.issue_date.replace("-", ""))
        
        if include_amount and invoice.total_amount:
            amount_clean = _sanitize_filename(invoice.total_amount.replace(" ", ""))
            parts.insert(2, amount_clean)
        
        return "_".join(parts) + invoice.suffix

    def process(self, sort_key: str = "issue_date") -> tuple[int, int]:
        """
        Hlavní metoda — třídí a přesouvá faktury.
        Vrací (úspěch, chyby).
        """
        if not self.invoices:
            logger.info("Žádné faktury k přesunutí.")
            return 0, 0

        sorted_invoices = self.sort_invoices(sort_key)

        # Vytvoříme cílovou složku
        try:
            self.target_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            logger.error(f"Nelze vytvořit cílovou složku '{self.target_dir}': {e}")
            return 0, len(sorted_invoices)

        logger.info(f"Přesouvám {len(sorted_invoices)} faktur do: {self.target_dir}")

        errors = 0
        success = 0
        
        for i, invoice in enumerate(sorted_invoices, start=1):
            new_name = self._build_new_name(i, invoice, sort_key)
            dest_path = self.target_dir / new_name

            # Konflikt názvů
            if dest_path.exists():
                stem = dest_path.stem
                dest_path = self.target_dir / f"{stem}_dup{dest_path.suffix}"

            try:
                shutil.move(str(invoice.source_path), str(dest_path))
                success += 1
                logger.info(f"  ✓ [{i:03d}] {invoice.source_path.name} → {new_name}")
            except PermissionError as e:
                logger.error(f"  ✗ Přístup odepřen: {invoice.source_path.name} — {e}")
                errors += 1
            except FileNotFoundError as e:
                logger.error(f"  ✗ Soubor nenalezen: {invoice.source_path.name} — {e}")
                errors += 1
            except Exception as e:
                logger.error(f"  ✗ Chyba u '{invoice.source_path.name}': {e}")
                errors += 1

        return success, errors


# ─────────────────────────────────────────────
# Hlavní orchestrátor
# ─────────────────────────────────────────────
class InvoiceProcessorV2:
    """
    Orchestruje nové workflow:
    1. Rychlé vyhledání všech dokumentů/obrázků
    2. Llama Vision analýza každého souboru
    3. Filtrování podle uživatelských požadavků
    4. Třídění a přesun do cílové složky
    """

    def __init__(self, model: Optional[str] = None):
        self.analyzer = LlamaVisionAnalyzer(model)
        self.filter_agent: Optional[InvoiceFilterAgent] = None

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

    def run(self) -> None:
        """Spustí celé V2 workflow."""
        self._print_banner()

        # 1. Vstup od uživatele
        print("\n📁  NASTAVENÍ SLOŽEK")
        print("─" * 55)
        source_dir = self._prompt_directory("Zdrojová složka (kde hledat soubory)")
        target_dir = self._prompt_directory("Cílová složka (kam přesunout faktury)", must_exist=False)

        # 2. Rychlé vyhledání souborů
        print("\n🔍  RYCHLÉ VYHLEDÁVÁNÍ")
        print("─" * 55)
        discovery = FileDiscovery(source_dir)
        files = discovery.find_files()

        if not files:
            print("Nenalezeny žádné podporované soubory. Ukončuji.")
            sys.exit(0)

        print(f"Nalezeno {len(files)} souborů: PDF, JPG, PNG, TIFF, BMP, GIF")

        if not self._confirm(f"\nSpustit Llama Vision analýzu {len(files)} souborů?"):
            print("Ukončeno uživatelem.")
            sys.exit(0)

        # 3. Llama Vision analýza
        print(f"\n👁️  LLAMA VISION ANALÝZA (model: {self.analyzer.model})")
        print("─" * 55)
        invoices = self._analyze_files(files)

        # 4. Výsledky analýzy
        print("\n📊  VÝSLEDKY ANALÝZY")
        print("─" * 55)
        print(f"  Celkem zpracováno souborů : {len(files)}")
        print(f"  Identifikováno faktur     : {len(invoices)}")
        
        if not invoices:
            print("\nŽádné faktury nenalezeny. Ukončuji.")
            sys.exit(0)

        # 5. Filtrování faktur
        print("\n🔍  FILTROVÁNÍ FAKTUR")
        print("─" * 55)
        self.filter_agent = InvoiceFilterAgent(invoices)
        filtered_invoices = self._apply_filters(invoices)

        if not filtered_invoices:
            print("Žádné faktury nesplňují filtr. Ukončuji.")
            sys.exit(0)

        print(f"Faktur po filtrování: {len(filtered_invoices)}")

        # 6. Třídění a přesun
        print("\n📂  TŘÍDĚNÍ A PŘESUN")
        print("─" * 55)
        organizer = InvoiceOrganizer(filtered_invoices, target_dir)
        success, errors = organizer.process()

        print("\n" + "═" * 55)
        print(f"  ✅ Úspěšně přesunuto: {success}")
        print(f"  ❌ Chyby: {errors}")
        print("═" * 55)
        print("\n✅  Hotovo!\n")

    def _analyze_files(self, files: list[Path]) -> list[Invoice]:
        """
        Analyzuje všechny soubory pomocí Llama Vision.
        """
        invoices: list[Invoice] = []
        total = len(files)

        for idx, file_path in enumerate(files, start=1):
            print(f"  [{idx:3d}/{total}] {file_path.name} ...", end=" ", flush=True)

            # Vision analýza
            result = self.analyzer.analyze(file_path)
            
            if result is None:
                print("✗ Chyba analýzy.")
                continue

            is_invoice = result.get("is_invoice", False)
            confidence = result.get("confidence", 0.0)

            if not is_invoice:
                reason = result.get("reason", "Neznámý důvod")
                print(f"– Není faktura ({reason[:40]})")
                continue

            # Sestavení Invoice objektu
            invoice = Invoice(
                source_path=file_path,
                sender_name=_sanitize_filename(result.get("sender_name") or "Neznamy_odesilatel"),
                recipient_name=_sanitize_filename(result.get("recipient_name") or "Neznamy_prijemce"),
                issue_date=result.get("issue_date") or "0000-00-00",
                due_date=result.get("due_date") or "0000-00-00",
                total_amount=result.get("total_amount") or "",
                currency=result.get("currency") or "",
                is_invoice=True,
                confidence=confidence,
                raw_json=result,
            )
            invoices.append(invoice)
            
            sender_short = invoice.sender_name[:20] if len(invoice.sender_name) > 20 else invoice.sender_name
            print(f"✓ Faktura ({confidence:.0%}) | Od: {sender_short:<20} | {invoice.issue_date}")

        return invoices

    def _apply_filters(self, invoices: list[Invoice]) -> list[Invoice]:
        """
        Aplikuje uživatelské filtry na faktury.
        """
        filter_agent = InvoiceFilterAgent(invoices)

        # Získání unikátních odesílatelů pro nápovědu
        senders = filter_agent.get_unique_values("sender_name")
        
        if senders:
            print("\nDostupní odesílatelé:")
            for i, sender in enumerate(senders[:10], 1):
                print(f"  {i}. {sender}")
            if len(senders) > 10:
                print(f"  ... a dalších {len(senders) - 10}")

        # Dotaz na filtr
        print("\n" + "─" * 55)
        filter_sender = input("Filtrovat podle odesílatele (nechat prázdné pro přeskočení): ").strip()

        if filter_sender:
            filtered = filter_agent.filter_by_custom("sender_name", filter_sender)
            print(f"Nalezeno {len(filtered)} faktur od '{filter_sender}'")
            return filtered

        return invoices

    @staticmethod
    def _print_banner() -> None:
        banner = r"""
╔═══════════════════════════════════════════════════════╗
║       🧾  Invoice Processor v2  -  Llama Vision       ║
║   Přímá analýza dokumentů pomocí multimodálního AI    ║
╚═══════════════════════════════════════════════════════╝
"""
        print(banner)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main() -> None:
    processor = InvoiceProcessorV2()
    try:
        processor.run()
    except KeyboardInterrupt:
        print("\n\nUkončeno uživatelem (Ctrl+C).")
        sys.exit(0)


if __name__ == "__main__":
    main()
