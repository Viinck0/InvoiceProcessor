#!/usr/bin/env python3
"""
Invoice Processor v5 - OCR + Text Agent
Místo Vision modelu používáme:
1. Python (PyMuPDF + pytesseract) na extrakci textu z obrázků/PDF
2. Ollama textový model (llama3.1) na zpracování textu a JSON

Výhody:
- Rychlejší (žádný vision model)
- Méně paměti (textový model je menší)
- Spolehlivější JSON výstup
- Funguje i bez GPU

Použití:
    python invoice_gui_v5.py
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
from typing import Optional, List
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
TEXT_MODEL = "llama3.1"  # Pouze textový model!
MAX_IMAGE_SIZE = 1024  # Pro OCR
LOG_LEVEL = logging.INFO
REQUEST_TIMEOUT = 90
MAX_MEMORY_PERCENT = 85
DEBUG_MEMORY = True
BATCH_SIZE = 5
MEMORY_CHECK_EVERY = 1

# OCR konstanty
OCR_LANG = "ces+eng"  # Čeština + Angličtina
OCR_DPI = 150  # DPI pro OCR (vyšší = přesnější ale pomalejší)
USE_TESSERACT = True  # Nastavit False pokud nemáte Tesseract

# Běžné instalační cesty pro Tesseract na Windows
TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe",
    r"%PROGRAMFILES%\Tesseract-OCR\tesseract.exe",
    r"%PROGRAMFILES(X86)%\Tesseract-OCR\tesseract.exe",
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
    ocr_text: str = ""

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
# Modul: OCR Text Extractor + Pravidla pro detekci faktur
# ─────────────────────────────────────────────
class OCRExtractor:
    """
    Extrahuje text z PDF a obrázků pomocí Python knihoven.
    Provádí první kontrolu zda se jedná o fakturu pomocí pravidel.
    Žádný AI model - čistý Python!
    """

    # Klíčová slova pro detekci faktury
    INVOICE_KEYWORDS = [
        'faktura', 'invoice', 'daňový doklad', 'receipt', 'bill',
        'faktúra', 'účtenka', 'paragon'
    ]
    
    # Klíčová slova pro detekci NE-faktur
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
        """Načte potřebné knihovny a najde Tesseract executable."""
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
            return  # Bez pytesseract nemá smysl pokračovat

        try:
            from PIL import Image
            self.Image = Image
            logger.debug("PIL načten")
        except ImportError as e:
            logger.warning(f"Pillow není nainstalován: {e}")

        # Pokus najít Tesseract executable
        if self.pytesseract is not None:
            self._find_tesseract()

    def _find_tesseract(self) -> bool:
        """
        Vyhledá tesseract.exe v běžných instalačních cestách.
        Pokud nenajde, vrátí False a doporučí instalaci.
        """
        import shutil

        # 1. Zkus najít v PATH
        tesseract_path = shutil.which("tesseract")
        if tesseract_path:
            logger.debug(f"✓ Tesseract nalezen v PATH: {tesseract_path}")
            try:
                self.pytesseract.pytesseract.tesseract_cmd = tesseract_path
                # Ověř že skutečně funguje
                self.pytesseract.get_tesseract_version()
                logger.info(f"✓ Tesseract připraven: {tesseract_path}")
                return True
            except Exception as e:
                logger.warning(f"Tesseract v PATH nefunguje: {e}")

        # 2. Prohledej známé instalační cesty
        for path_template in TESSERACT_PATHS:
            # Rozviň environment proměnné
            path = os.path.expandvars(path_template)
            if os.path.isfile(path):
                logger.debug(f"✓ Tesseract nalezen: {path}")
                try:
                    self.pytesseract.pytesseract.tesseract_cmd = path
                    # Ověř že skutečně funguje
                    self.pytesseract.get_tesseract_version()
                    logger.info(f"✓ Tesseract připraven: {path}")
                    return True
                except Exception as e:
                    logger.warning(f"Tesseract nefunguje: {e}")

        # 3. Nenalezen - nastav výchozí cestu a vrať False
        default_path = TESSERACT_PATHS[0]
        self.pytesseract.pytesseract.tesseract_cmd = default_path
        logger.warning(f"⚠️ Tesseract nenalezen v žádném známém umístění")
        return False

    def extract_text(self, file_path: Path) -> Optional[str]:
        """Extrahuje text ze souboru."""
        ext = file_path.suffix.lower()

        if ext == ".pdf":
            return self._extract_from_pdf(file_path)
        elif ext in {".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".gif"}:
            return self._extract_from_image(file_path)
        
        return None

    def _extract_from_pdf(self, file_path: Path) -> Optional[str]:
        """Extrahuje text z PDF."""
        if self.fitz is None:
            logger.warning("PyMuPDF není dostupný")
            return None

        try:
            text_parts = []
            
            with self.fitz.open(str(file_path)) as doc:
                # Zkusíme nejdřív textovou vrstvu
                for page in doc:
                    page_text = page.get_text()
                    if page_text.strip():
                        text_parts.append(page_text)
            
            # Pokud jsme našli text, vrátíme ho
            if text_parts:
                return "\n".join(text_parts)
            
            # Pokud ne, zkusíme OCR první stránky
            logger.info(f"  📄 PDF nemá textovou vrstvu, zkouším OCR...")
            return self._ocr_pdf_page(file_path, 0)

        except Exception as e:
            logger.warning(f"Chyba při čtení PDF: {e}")
            return None

    def _tesseract_not_found_msg(self) -> str:
        """Vrátí doporučení pro instalaci Tesseractu."""
        return (
            "❌ TESSERACT NENALEZEN\n\n"
            "Pro OCR obrázků a skenů je třeba nainstalovat Tesseract OCR:\n\n"
            "1. Stáhněte instalátor:\n"
            "   https://github.com/UB-Mannheim/tesseract/wiki\n\n"
            "2. Nainstalujte (doporučeno s češtinou a angličtinou):\n"
            "   - Zaškrtněte 'Additional language data'\n"
            "   - Vyberte 'Czech' a 'English'\n\n"
            "3. Výchozí instalační cesta:\n"
            "   C:\\Program Files\\Tesseract-OCR\\\n\n"
            "4. Po instalaci restartujte aplikaci.\n\n"
            "💡 Alternativně můžete přidat Tesseract do PATH:\n"
            "   Ovládací panely → Systém → Proměnné prostředí → Path\n"
            "   Přidat: C:\\Program Files\\Tesseract-OCR"
        )

    def _ocr_pdf_page(self, file_path: Path, page_num: int) -> Optional[str]:
        """Provede OCR konkrétní stránky PDF."""
        if not USE_TESSERACT or self.fitz is None or self.pytesseract is None or self.Image is None:
            logger.warning("  ⚠️ OCR není dostupná (Tesseract není nainstalován)")
            logger.warning("  💡 Pro OCR instalujte: https://github.com/UB-Mannheim/tesseract/wiki")
            logger.warning("  📝 Používám pouze textovou vrstvu PDF")
            return None

        # Ověř že tesseract.exe skutečně existuje
        tesseract_cmd = self.pytesseract.pytesseract.tesseract_cmd
        if not os.path.isfile(tesseract_cmd):
            logger.warning(f"  ⚠️ Tesseract executable nenalezen: {tesseract_cmd}")
            logger.warning(self._tesseract_not_found_msg())
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
        """Extrahuje text z obrázku pomocí OCR."""
        if not USE_TESSERACT or self.pytesseract is None or self.Image is None:
            if not USE_TESSERACT:
                logger.warning("  ⚠️ OCR vypnuto (USE_TESSERACT = False)")
            else:
                logger.warning("  ⚠️ pytesseract nebo Pillow není dostupný")
            logger.warning("  💡 Pro OCR obrázků je třeba Tesseract")
            return None

        # Ověř že tesseract.exe skutečně existuje
        tesseract_cmd = self.pytesseract.pytesseract.tesseract_cmd
        if not os.path.isfile(tesseract_cmd):
            logger.warning(f"  ⚠️ Tesseract executable nenalezen: {tesseract_cmd}")
            logger.warning(self._tesseract_not_found_msg())
            return None

        try:
            img = self.Image.open(str(file_path))

            # Konverze na RGB pokud je třeba
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')

            # OCR
            text = self.pytesseract.image_to_string(img, lang=OCR_LANG)

            return text if text.strip() else None

        except Exception as e:
            logger.warning(f"Chyba OCR obrázku: {e}")
            return None

    def is_invoice_by_rules(self, text: str) -> tuple[bool, str, float]:
        """
        Pravidly založená detekce faktury - PŘÍSNÁ KRITÉRIA.
        Vrací: (je_faktura, typ_dokumentu, jistota)
        
        Aby byl dokument uznán jako faktura, MUSÍ splňovat:
        1. Obsahovat explicitní název faktury NEBO
        2. Mít vysoké skóre z kombinace známek (subjekty + peníze + platba)
        """
        if not text or len(text.strip()) < 50:
            return False, "prázdný dokument", 0.95

        text_lower = text.lower()

        # Bodové hodnocení
        score = 0
        reasons = []
        critical_hits = 0  # Počet kritických zásahů

        # === KROK 1: Kontrola explicitních názvů faktury ===
        explicit_invoice_titles = [
            'faktura', 'invoice', 'daňový doklad', 'tax document',
            'zálohová faktura', 'proforma faktura', 'pro-forma',
            'konečná faktura', 'opravný daňový doklad', 'dobropis', 'vrubopis'
        ]
        
        has_explicit_title = False
        for title in explicit_invoice_titles:
            if title in text_lower:
                has_explicit_title = True
                critical_hits += 2
                reasons.append(f"explicitní název: '{title}'")
                break

        # === KROK 2: Kontrola pozitivních znaků faktury ===
        positive_keywords = [
            # Subjekty
            ('dodavatel', 2), ('odběratel', 2), ('příjemce', 1), ('plátce', 1),
            ('zhotovitel', 2), ('objednatel', 2),
            # Peníze
            ('celkem k úhradě', 3), ('celková částka', 2), ('k úhradě', 2),
            ('základ daně', 2), ('dph', 2), ('daň', 1),
            ('celkem', 1), ('suma', 1), ('částka', 1),
            # Identifikátory
            ('ičo', 2), ('dič', 2), ('ič dph', 2),
            ('faktura č', 2), ('číslo faktury', 2), ('invoice number', 2),
            # Platba
            ('datum splatnosti', 2), ('splatnost', 1), ('variabilní symbol', 2),
            ('číslo účtu', 2), ('iban', 2), ('bankovní spojení', 2),
        ]
        
        for keyword, points in positive_keywords:
            if keyword in text_lower:
                score += points
                reasons.append(f"nalezeno '{keyword}' (+{points})")

        # === KROK 3: Kontrola negativních znaků (NENÍ faktura) ===
        negative_keywords = [
            # Jiné typy dokumentů
            ('upomínka', -5), ('reminder', -4), ('výzva k úhradě', -3),
            ('smlouva', -4), ('contract', -4), ('dohoda', -3),
            ('nabídka', -3), ('offer', -3), ('cenová kalkulace', -2),
            ('objednávka', -2), ('purchase order', -2),
            ('specifikace', -1), ('poptávka', -2),
            # Komunikační dokumenty
            ('poznámka', -2), ('note', -2), ('zápis', -2), ('minutes', -2),
            ('e-mail', -1), ('dopis', -1), ('korespondence', -2),
            ('vizitka', -3), ('business card', -3),
            ('reklama', -2), ('advertisement', -2), ('leták', -2), ('flyer', -2),
            ('prospekt', -2), ('katalog', -2), ('catalogue', -2),
            # Interní dokumenty
            ('interní', -1), ('internal', -1), ('pracovní', -1),
            ('koncept', -1), ('draft', -1), ('návrh', -1),
        ]
        
        for keyword, penalty in negative_keywords:
            if keyword in text_lower:
                score += penalty
                reasons.append(f"nalezeno '{keyword}' ({penalty})")

        # === KROK 4: Strukturální prvky (bonusové body) ===
        structural_patterns = [
            # Částky s měnou
            (r'\d+[\s,.]\d{2,3}\s*(kč|czk|eur|€|\$|usd)', 2, "částka s měnou"),
            # Datum ve formátu DD.MM.YYYY
            (r'\d{1,2}\.\d{1,2}\.\d{4}', 1, "datum"),
            # IČO (8 číslic)
            (r'ičo\s*[:.]?\s*\d{8}', 2, "IČO formát"),
            # DIČ formáty
            (r'dič\s*[:.]?\s*[a-z]{2}\d{8,12}', 2, "DIČ formát"),
            # Bankovní účet / IBAN
            (r'(cz|sk)?\d{4,6}[- ]?\d{6,10}[- ]?\d{2,4}', 1, "bankovní účet"),
            (r'iban:\s*[a-z]{2}\d', 2, "IBAN"),
            # Variabilní symbol
            (r'var\.?\s*symbol\s*[:.]?\s*\d{8,10}', 2, "variabilní symbol"),
        ]
        
        for pattern, points, description in structural_patterns:
            if re.search(pattern, text_lower):
                score += points
                reasons.append(description)

        # === ROZHODOVACÍ LOGIKA ===
        
        # Kritická pravidla pro zamítnutí
        if score <= -5:
            return False, "není faktura (silné negativní znaky)", 0.95
        
        # Absolutní zákazy - tyto dokumenty NIKDY jako faktura
        if 'upomínka' in text_lower and 'faktura' not in text_lower:
            return False, "upomínka (není faktura)", 0.98
        
        if 'smlouva' in text_lower and 'faktura' not in text_lower:
            return False, "smlouva (není faktura)", 0.95
        
        if 'nabídka' in text_lower and 'faktura' not in text_lower:
            return False, "nabídka (není faktura)", 0.92
        
        if 'objednávka' in text_lower and 'faktura' not in text_lower:
            return False, "objednávka (není faktura)", 0.90
        
        if 'cenová kalkulace' in text_lower and 'faktura' not in text_lower:
            return False, "cenová kalkulace (není faktura)", 0.90
        
        if 'specifikace' in text_lower and 'faktura' not in text_lower and score < 5:
            return False, "specifikace (není faktura)", 0.85

        # === PŘÍSNÁ PRAVIDLA PRO SCHVÁLENÍ ===
        
        # Pouze explicitní název + vysoké skóre = jistá faktura
        if has_explicit_title and score >= 6:
            confidence = min(0.98, 0.75 + (score - 6) * 0.03)
            return True, "faktura (explicitní název + dostatek znaků)", confidence
        
        # Vysoké skóre BEZ explicitního názvu = nutná AI kontrola
        if score >= 10:
            # Velmi vysoké skóre ale žádný explicitní název - podezřelé
            return None, "vysoké skóre bez názvu - nutná AI", 0.5
        
        # Střední skóre s názvem = pravděpodobná faktura ale ověřit AI
        if has_explicit_title and score >= 4:
            return None, "název + střední skóre - nutná AI", 0.6
        
        # Nízké skóre = zamítnout nebo AI
        if score >= 6 and not has_explicit_title:
            return None, "střední skóre bez názvu - nutná AI", 0.4
        
        # Vše ostatní = zamítnout
        if score <= 0:
            return False, "není faktura (nízké skóre)", max(0.7, 0.5 + abs(score) * 0.1)
        
        # Šedá zóna - potřebuje AI
        return None, "nejistý dokument - nutná AI kontrola", 0.3


# ─────────────────────────────────────────────
# Modul: Text Agent (pomocník pro extrakci dat)
# ─────────────────────────────────────────────
class TextAgent:
    """
    Textový model jako POMOCNÍK pro extrakci dat.
    Hlavní rozhodnutí dělá Python skript pravidly.
    AI se používá pouze pro:
    1. Kontrolu nejistých případů
    2. Extrakci strukturovaných dat z textu
    """

    INVOICE_CLASSIFICATION_PROMPT = """Jsi expertní systém pro klasifikaci firemních dokumentů a extrakci dat z faktur. Tvým úkolem je:
1. Rozhodnout zda se jedná o fakturu (včetně daňového dokladu, zálohové nebo proforma faktury)
2. Extrahovat klíčová data z faktury

=== DEFINICE FAKTURY ===
Faktura je OBCHODNÍ DOKLANT který MUSÍ obsahovat VŠECHNY tyto 5 elementů:
1. Identifikace dokladu: Název "Faktura", "Daňový doklad", "Invoice", "Proforma"
2. Subjekty: DODAVATEL (kdo vystavil) + ODBĚRATEL (komu je určeno) + IČO/DIČ
3. Časové údaje: Datum vystavení + Datum splatnosti
4. Finanční jádro: Položky/služby + Cena bez DPH + DPH + CELKEM K ÚHRADĚ
5. Platební instrukce: Číslo účtu/IBAN + Variabilní symbol + Datum splatnosti

=== DŮLEŽITÉ PRAVIDLO ===
Pokud dokument NEMÁ všech 5 elementů, NENÍ to faktura!

=== CO NENÍ FAKTURA ===
Následující dokumenty NIKDY neklasifikuj jako fakturu (i když obsahují jména, adresy nebo částky):
- Životopisy, CV, curriculum vitae - obsahují jméno, vzdělání, pracovní zkušenosti
- Certifikáty, osvědčení, licence - obsahují jméno, kurz, datum absolvování
- Upomínky, reminder, výzvy k úhradě
- Smlouvy, contracts, dohody, dodatky
- Nabídky, offers, cenové kalkulace, rozpočty
- Objednávky, purchase orders, poptávky
- Poznámky, notes, zápisy z porad, minutes
- E-maily, dopisy, korespondence
- Vizitky, business cards
- Reklamy, advertisements, letáky, flyers, prospekty
- Katalogy, ceníky
- Interní dokumenty, koncepty, návrhy, drafty
- Účtenky, paragon (pokud není explicitně "daňový doklad")
- Výpisy z bankovního účtu
- Přiznání k dani, daňová přiznání

=== ROZPOZNÁVÁNÍ NE-FKTUR ===
Pokud text obsahuje tyto vzorce, pravděpodobně NENÍ faktura:
- "životopis", "CV", "curriculum", "vzdělání", "praxe", "zaměstnání"
- "certifikát", "osvědčení", "licence", "absolvoval", "kurz", "školení"
- "narozen", "rodné číslo", "bydliště" (v kontextu osoby, ne firmy)
- "pracovní pozice", "popis práce", "reference"

Pravidla pro výstup:
- Odpovídej POUZE ve formátu validního JSON.
- Nevypisuj žádný vysvětlující text mimo JSON strukturu.
- Pokud si nejsi jistý, nastav is_invoice na false.
- Pokud je dokument faktura, vyplň i extrahovaná data (i když některá pole mohou být null).
- Vysvětli v "reasoning" které z 5 elementů faktury jsou přítomny.

Požadovaná struktura JSON:
{{
  "is_invoice": true/false,
  "confidence": <číslo od 0 do 100 vyjadřující tvou jistotu>,
  "document_type": "<např. Standardní faktura, Zálohová faktura, Jiný dokument>",
  "reasoning": "<stručné odůvodnění - které elementy 1-5 jsou přítomny>",
  "elements_present": {{
    "identification": true/false,
    "subjects": true/false,
    "dates": true/false,
    "financial": true/false,
    "payment_info": true/false
  }},
  "invoice_number": "<číslo faktury nebo null>",
  "vendor_name": "<název dodavatele/vendora nebo null>",
  "customer_name": "<název odběratele/zákazníka nebo null>",
  "issue_date": "<datum vystavení YYYY-MM-DD nebo null>",
  "due_date": "<datum splatnosti YYYY-MM-DD nebo null>",
  "total_amount": "<číslo celkové částky nebo null>",
  "currency": "<CZK/EUR/USD nebo null>"
}}

TEXT K ANALÝZE:
{{input_data}}"""

    SIMPLE_PROMPT = """Analyze this text and extract invoice data as JSON.

Output ONLY valid JSON with this structure:
{{
  "is_invoice": true/false,
  "invoice_number": "string or null",
  "vendor_name": "string or null",
  "customer_name": "string or null",
  "issue_date": "YYYY-MM-DD or null",
  "due_date": "YYYY-MM-DD or null",
  "total_amount": number or null,
  "currency": "CZK/EUR/USD or null",
  "reason": "brief explanation"
}}

TEXT:
{{input_data}}"""

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

    def check_uncertain(self, ocr_text: str, filename: str) -> Optional[dict]:
        """Použije AI pro klasifikaci nejistých dokumentů."""
        if not ocr_text or not ocr_text.strip():
            return None

        # Zkrátíme text
        max_chars = 5000
        if len(ocr_text) > max_chars:
            ocr_text = ocr_text[:max_chars]

        # Použijeme specializovaný klasifikační prompt
        prompt = self.INVOICE_CLASSIFICATION_PROMPT.format(input_data=ocr_text)

        try:
            client = self._get_client()

            response = client.generate(
                model=self.model,
                prompt=prompt,
                options={
                    "temperature": 0.01,
                    "num_predict": 512,
                },
                keep_alive="1m"
            )

            raw_output = response.get("response", "")
            parsed = _extract_json_from_text(raw_output)

            if parsed is None:
                logger.debug(f"  AI nevrátila JSON pro {filename}")
                return None

            # Validace výstupu
            if 'is_invoice' not in parsed:
                logger.debug(f"  AI nevrátila is_invoice pro {filename}")
                return None

            confidence = parsed.get('confidence', 50) / 100.0
            is_invoice = parsed.get('is_invoice', False)
            
            # Kontrola 5 elementů faktury
            elements = parsed.get('elements_present', {})
            element_count = sum([
                elements.get('identification', False),
                elements.get('subjects', False),
                elements.get('dates', False),
                elements.get('financial', False),
                elements.get('payment_info', False)
            ])
            
            logger.debug(f"  ✓ AI klasifikovala {filename}: {'Faktura' if is_invoice else 'Není faktura'} (jistota: {confidence:.0%})")
            logger.debug(f"     Elementy: {element_count}/5 | Důvod: {parsed.get('reasoning', 'N/A')[:60]}")

            return parsed

        except Exception as e:
            logger.debug(f"  AI chyba pro {filename}: {e}")
            return None

    def validate_invoice_elements(self, result: dict) -> tuple[bool, str]:
        """
        Validuje že výsledek obsahuje všech 5 elementů faktury.
        Vrací: (platná_faktura, důvod)
        """
        if not result:
            return False, "Žádný výsledek k validaci"
        
        # Získat elementy
        elements = result.get('elements_present', {})
        element_count = sum([
            elements.get('identification', False),
            elements.get('subjects', False),
            elements.get('dates', False),
            elements.get('financial', False),
            elements.get('payment_info', False)
        ])
        
        # Pokud AI řekla že není faktura
        if not result.get('is_invoice', False):
            return False, "AI zamítla - není faktura"
        
        # Kontrola počtu elementů
        if element_count < 5:
            missing = []
            if not elements.get('identification', False):
                missing.append("identifikace dokladu")
            if not elements.get('subjects', False):
                missing.append("subjekty (dodavatel+odběratel)")
            if not elements.get('dates', False):
                missing.append("časové údaje")
            if not elements.get('financial', False):
                missing.append("finanční jádro")
            if not elements.get('payment_info', False):
                missing.append("platební instrukce")
            
            return False, f"Chybí elementy: {', '.join(missing)}"
        
        # Kontrola že jsou vyplněná klíčová data
        has_vendor = bool(result.get('vendor_name'))
        has_customer = bool(result.get('customer_name'))
        has_amount = result.get('total_amount') not in (None, '', 'None', 0)
        has_issue_date = bool(result.get('issue_date') and result.get('issue_date') != '0000-00-00')
        
        if not (has_vendor and has_customer and has_amount and has_issue_date):
            missing_data = []
            if not has_vendor:
                missing_data.append("dodavatel")
            if not has_customer:
                missing_data.append("odběratel")
            if not has_amount:
                missing_data.append("částka")
            if not has_issue_date:
                missing_data.append("datum")
            
            return False, f"Chybí data: {', '.join(missing_data)}"
        
        return True, "Všechny elementy přítomny"

    def extract_data(self, ocr_text: str, filename: str) -> dict:
        """Extrahuje data z textu (AI jako pomocník)."""
        if not ocr_text:
            return self._empty_result()

        # Zkrátíme text
        max_chars = 6000
        if len(ocr_text) > max_chars:
            ocr_text = ocr_text[:max_chars]

        prompt = self.SIMPLE_PROMPT.format(input_data=ocr_text)

        try:
            client = self._get_client()
            
            response = client.generate(
                model=self.model,
                prompt=prompt,
                options={
                    "temperature": 0.01,
                    "num_predict": 512,
                },
                keep_alive="1m"
            )

            raw_output = response.get("response", "")
            parsed = _extract_json_from_text(raw_output)
            
            if parsed:
                return parsed

        except Exception as e:
            logger.debug(f"  AI extrakce selhala: {e}")

        # Fallback - pravidla
        return self._fallback_extract(ocr_text)

    def _empty_result(self) -> dict:
        return {
            'is_invoice': False,
            'invoice_number': None,
            'vendor_name': None,
            'customer_name': None,
            'issue_date': None,
            'due_date': None,
            'total_amount': None,
            'currency': None,
            'reason': 'Prázdný text',
        }

    def _fallback_extract(self, text: str) -> dict:
        """Extrakce pomocí regulárních výrazů - POUZE pro dokumenty které již byly označeny jako faktury."""
        text_lower = text.lower()

        # Detekce měny
        currency = "CZK"
        if "€" in text or "eur" in text_lower:
            currency = "EUR"
        elif "$" in text or "usd" in text_lower:
            currency = "USD"

        # Extrakce částky
        amount_match = re.search(r'(\d{1,3}(?:[\s\.,]\d{3})*(?:[\s\.,]\d{1,2})?)\s*(?:kč|czk|eur|€|\$)?', text_lower)
        amount = None
        if amount_match:
            amount_str = amount_match.group(1).replace(' ', '').replace('.', '').replace(',', '.')
            try:
                amount = float(amount_str)
            except:
                pass

        # Extrakce data
        date_match = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', text)
        issue_date = None
        if date_match:
            issue_date = f"{date_match.group(3)}-{date_match.group(2).zfill(2)}-{date_match.group(1).zfill(2)}"

        # DŮLEŽITÉ: is_invoice nastavujeme na True POUZE pokud text obsahuje známky faktury
        # Tato metoda se volá až po úspěšné klasifikaci, ale přesto zkontrolujeme
        has_invoice_keywords = any(kw in text_lower for kw in ['faktura', 'invoice', 'daňový doklad', 'celkem k úhradě'])
        
        return {
            'is_invoice': has_invoice_keywords,  # Nastavíme podle přítomnosti klíčových slov
            'invoice_number': None,
            'vendor_name': self._extract_first_capitalized(text),
            'customer_name': None,
            'issue_date': issue_date,
            'due_date': None,
            'total_amount': amount,
            'currency': currency,
            'reason': 'Extrakce pravidly (AI selhala)',
        }

    def _extract_first_capitalized(self, text: str) -> Optional[str]:
        match = re.search(r'\b([A-ZČŠŽŘĎŤŇĚÁÉÍÓÚÝ][a-zčšžřďťňěáéíóúý]+\s+(?:s\.r\.o\.|a\.s\.|spol\.\s+r\.o\.))', text)
        if match:
            return match.group(1)
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
# Modul: Filtrování faktur
# ─────────────────────────────────────────────
class InvoiceFilterAgent:
    def __init__(self, invoices: list[Invoice]):
        self.invoices = invoices

    def filter_by_custom(self, filter_key: str, filter_value: str) -> list[Invoice]:
        """
        Filtruje faktury podle zadaného klíče a hodnoty.
        DŮLEŽITÉ: Vrací POUZE dokumenty kde is_invoice=True!
        """
        if not filter_key or not filter_value:
            return [inv for inv in self.invoices if inv.is_invoice]

        filtered = []
        for inv in self.invoices:
            # Nejdřív zkontrolujeme že je to faktura
            if not inv.is_invoice:
                continue
            # Pak aplikujeme filtr
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
            # DŮLEŽITÉ: Kontrola že se jedná o fakturu před přesunem
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
class InvoiceProcessorGUIV5(ctk.CTk):
    """GUI pro OCR + Text Agent systém."""

    def __init__(self):
        super().__init__()

        self.title("🧾 Invoice Processor v5 - OCR + Text Agent")
        self.geometry("1000x750")
        self.minsize(900, 650)

        # Proměnné
        self.source_dir = ctk.StringVar()
        self.target_dir = ctk.StringVar()
        self.sort_key = ctk.StringVar(value="issue_date")
        self.model_name = ctk.StringVar(value=TEXT_MODEL)

        # Filtr
        self.filter_key = ctk.StringVar(value="")
        self.filter_value = ctk.StringVar()

        self.ocr_extractor = OCRExtractor()
        self.text_agent = TextAgent()

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

        # Zkontroluj stav Tesseractu před vytvořením GUI
        self.tesseract_ready = self._check_tesseract_status()

        self._setup_ui()

    def _check_tesseract_status(self) -> bool:
        """Zkontroluje zda je Tesseract připraven a vrátí bool."""
        if self.ocr_extractor.pytesseract is None:
            logger.warning("pytesseract knihovna není nainstalována")
            return False
        
        tesseract_cmd = self.ocr_extractor.pytesseract.pytesseract.tesseract_cmd
        if os.path.isfile(tesseract_cmd):
            try:
                self.ocr_extractor.pytesseract.get_tesseract_version()
                logger.info(f"✓ Tesseract připraven: {tesseract_cmd}")
                return True
            except Exception:
                pass
        
        logger.warning(f"✗ Tesseract nenalezen: {tesseract_cmd}")
        return False

    def _contains_non_invoice_keywords(self, text: str) -> bool:
        """
        Kontroluje zda text neobsahuje znaky typické pro životopisy, certifikáty a jiné ne-faktury.
        """
        text_lower = text.lower()
        
        # Životopisy
        cv_keywords = ['životopis', 'curriculum vitae', 'curriculum', 'cv ', 'narozen', 
                       'rodné číslo', 'bydliště', 'vzdělání', 'pracovní zkušenosti',
                       'praxe', 'zaměstnání', 'pracovní pozice', 'popis práce',
                       'reference', 'dovednosti', 'kompetence', 'kariéra']
        
        # Certifikáty
        cert_keywords = ['certifikát', 'osvědčení', 'licence', 'akreditace',
                         'absolvoval', 'ukončil', 'úspěšně složil', 'zkoušku',
                         'kurz', 'školení', 'seminář', 'vzdělávací program',
                         'profesní kvalifikace']
        
        # Ostatní dokumenty
        other_keywords = ['smlouva', 'dohoda', 'dodatek', 'nájemní smlouva',
                          'pracovní smlouva', 'kupní smlouva']
        
        # Spočítat kolik keyword se našlo
        cv_count = sum(1 for kw in cv_keywords if kw in text_lower)
        cert_count = sum(1 for kw in cert_keywords if kw in text_lower)
        other_count = sum(1 for kw in other_keywords if kw in text_lower)
        
        # Pokud se našlo více než 2 CV nebo certifikát keyword, je to podezřelé
        if cv_count >= 2:
            return True
        if cert_count >= 2:
            return True
        if other_count >= 2:
            return True
        
        # Speciální kontrola pro kombinace
        if cv_count >= 1 and cert_count >= 1:
            return True
        
        return False

    def _setup_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        # ─── Header ────────────────────────────
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 10))
        header_frame.grid_columnconfigure(0, weight=1)

        title_label = ctk.CTkLabel(
            header_frame,
            text="🧾 Invoice Processor v5",
            font=ctk.CTkFont(size=28, weight="bold")
        )
        title_label.grid(row=0, column=0, sticky="w")

        # Status Tesseractu
        if self.tesseract_ready:
            tesseract_status = "✓ OCR připraven"
            tesseract_color = "#28a745"
        else:
            tesseract_status = "✗ OCR nenalezena - instalujte Tesseract"
            tesseract_color = "#dc3545"

        subtitle_label = ctk.CTkLabel(
            header_frame,
            text="📝 OCR extrakce + Textový AI agent = Rychlé a spolehlivé (žádný Vision model)",
            font=ctk.CTkFont(size=14),
            text_color="gray"
        )
        subtitle_label.grid(row=1, column=0, sticky="w")

        tesseract_label = ctk.CTkLabel(
            header_frame,
            text=tesseract_status,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=tesseract_color
        )
        tesseract_label.grid(row=2, column=0, sticky="w", pady=(5, 0))

        # ─── Nastavení složek ────────────────────
        settings_frame = ctk.CTkFrame(self)
        settings_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=10)
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

        # ─── Nastavení modelu ─────────
        model_frame = ctk.CTkFrame(self)
        model_frame.grid(row=3, column=0, sticky="ew", padx=20, pady=10)
        model_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(model_frame, text="🤖 Textový model:").grid(
            row=0, column=0, sticky="w", padx=(15, 10), pady=10
        )
        ctk.CTkOptionMenu(
            model_frame,
            variable=self.model_name,
            values=["llama3.1", "llama3.2", "mistral", "gemma2"],
            width=150
        ).grid(row=0, column=1, sticky="w", padx=(0, 30), pady=10)

        ctk.CTkLabel(
            model_frame,
            text="⚡ Pouze textový model - žádné Vision, méně paměti, rychlejší!",
            font=ctk.CTkFont(size=12),
            text_color="gray"
        ).grid(row=0, column=2, sticky="w", pady=10)

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
            text="▶ Spustit OCR + AI analýzu",
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
            value=f"Stav: Připraveno | OCR + Text Agent | Model: {TEXT_MODEL}"
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

        # Upozornění pokud Tesseract není nalezen
        if not self.tesseract_ready:
            user_wants_continue = messagebox.askyesno(
                "⚠️ Tesseract nenalezen",
                "Tesseract OCR nebyl nalezen!\n\n"
                "Bez Tesseractu nebude fungovat OCR obrázků a skenovaných PDF.\n"
                "Pokud chcete pokračovat pouze s textovými PDF, klikněte ANO.\n\n"
                "Chcete zobrazit návod k instalaci?",
                icon=messagebox.WARNING
            )
            if user_wants_continue:
                messagebox.showinfo(
                    "📥 Instalace Tesseractu",
                    "1. Stáhněte instalátor:\n"
                    "   https://github.com/UB-Mannheim/tesseract/wiki\n\n"
                    "2. Nainstalujte s češtinou a angličtinou\n\n"
                    "3. Restartujte aplikaci"
                )
            # Pokračujeme i když Tesseract není připraven (pro textová PDF)

        self.is_processing = True
        self.start_button.configure(state="disabled", text="⏳ OCR + AI analýza...")
        self.cancel_button.configure(state="normal")
        self._clear_log()

        self.text_agent.model = self.model_name.get()
        self.status_var.set(f"Stav: Zpracovávám | OCR + Text Agent | Model: {self.text_agent.model}")

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
        self.start_button.configure(state="normal", text="▶ Spustit OCR + AI analýzu")
        self.cancel_button.configure(state="disabled")
        self._update_progress(0, "Připraveno")
        self.status_var.set(f"Stav: Připraveno | OCR + Text Agent | Model: {self.text_agent.model}")

    def _update_progress(self, value: float, label: str = ""):
        self.progress_bar.set(value / 100)
        if label:
            self.progress_label.configure(text=label)

    def _process_thread(self, source: str, target: str):
        """
        Hybridní zpracování:
        1. Python (OCR + pravidla) - hlavní rozhodnutí
        2. AI (Ollama) - pouze pomocník pro extrakci dat
        """
        try:
            source_path = Path(source)
            target_path = Path(target)

            log_memory_state("START PROCESSING")
            initial_mem = get_memory_usage()
            self._log(f"💾 Počáteční paměť: {initial_mem['rss_mb']:.1f}MB")
            self._log(f"📝 Hybridní systém: Python (pravidla) + AI (pomocník)")
            
            # Status Tesseractu
            if self.tesseract_ready:
                self._log("✓ OCR (Tesseract) připraven")
            else:
                self._log("⚠️ OCR (Tesseract) nenalezen - pouze textová PDF")
                self._log("💡 Stáhněte: https://github.com/UB-Mannheim/tesseract/wiki")

            # Krok 1: Vyhledání
            self._log("🔍 Vyhledávám soubory...")
            discovery = FileDiscovery(source_path)
            self.found_files = discovery.find_files()

            if not self.found_files:
                self.after(0, lambda: messagebox.showinfo("Info", "Nenalezeny žádné soubory."))
                self.after(0, self._reset_ui)
                return

            self._log(f"Nalezeno {len(self.found_files)} souborů")

            # Krok 2: OCR + hybridní analýza
            self._log("📝 Spouštím OCR + hybridní analýzu...")
            self._log(f"   1. Python pravidla pro detekci faktur")
            self._log(f"   2. AI pouze pro extrakci dat")
            self._log(f"   Model: {self.text_agent.model}")
            self._log(f"   OCR jazyk: {OCR_LANG}")
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

                # Krok 2a: OCR extrakce
                ocr_text = self.ocr_extractor.extract_text(file_path)
                
                if not ocr_text:
                    self._log(f"  ✗ {file_path.name}: Nepodařilo se extrahovat text")
                    continue

                self._log(f"  📝 {file_path.name}: Extrahováno {len(ocr_text)} znaků")

                # Krok 2b: Python pravidla - hlavní rozhodnutí
                is_invoice, doc_type, confidence = self.ocr_extractor.is_invoice_by_rules(ocr_text)

                self._log(f"  🔍 Python detekce: {doc_type} (jistota: {confidence:.0%})")

                # === ROZHODOVÁNÍ ===
                # Pravidlo: Všechny dokumenty musí projít AI kontrolou pokud:
                # 1. Python vrátil None (nejistý)
                # 2. Python vrátil True ale s jistotou < 0.85
                # 3. Python vrátil False (ale pak rovnou zahodíme)
                
                ai_result = None
                skip_file = False
                
                # Pokud Python řekl že NENÍ faktura s vysokou jistotou
                if is_invoice is False and confidence >= 0.80:
                    self._log(f"  ✗ {file_path.name}: {doc_type} (Python zamítl)")
                    continue
                
                # Pokud Python schválil s vysokou jistotou (>0.85), můžeme přeskočit AI
                if is_invoice is True and confidence >= 0.85:
                    self._log(f"  ✓ {file_path.name}: {doc_type} (Python vysoká jistota)")
                    # Pokračujeme bez AI
                else:
                    # Všechny ostatní případy → AI kontrola
                    self._log(f"  🤖 Žádám AI o klasifikaci...")
                    ai_result = self.text_agent.check_uncertain(ocr_text, file_path.name)

                    if ai_result:
                        ai_conf = ai_result.get('confidence', 50) / 100.0
                        if not ai_result.get('is_invoice', False):
                            self._log(f"  ✗ {file_path.name}: AI zamítla - {ai_result.get('reasoning', 'N/A')[:70]}")
                            continue
                        else:
                            self._log(f"  ✓ {file_path.name}: AI potvrdila ({ai_conf:.0%}) - {ai_result.get('reasoning', 'N/A')[:50]}")
                    else:
                        # AI nepomohla
                        if is_invoice is True:
                            self._log(f"  ⚠️ {file_path.name}: AI selhala, Python schválil ({confidence:.0%})")
                            # Pokračujeme s Python výsledkem
                        else:
                            self._log(f"  ✗ {file_path.name}: Nedostatek informací pro klasifikaci")
                            continue

                # === FINÁLNÍ VALIDACE PŘED VYTVOŘENÍM INVOICE ===
                final_is_invoice = False
                final_confidence = confidence

                if ai_result and ai_result.get('is_invoice'):
                    # AI potvrdila - zkontrolujeme 5 elementů faktury
                    is_valid, reason = self.text_agent.validate_invoice_elements(ai_result)
                    if not is_valid:
                        self._log(f"  ✗ {file_path.name}: Neplatná faktura - {reason}")
                        continue
                    
                    # AI potvrdila + všech 5 elementů přítomno
                    final_is_invoice = True
                    final_confidence = ai_result.get('confidence', 50) / 100.0
                    self._log(f"  ✓ {file_path.name}: AI potvrdila + 5 elementů (jistota: {final_confidence:.0%})")
                    
                elif is_invoice is True and confidence >= 0.85:
                    # Python schválil s vysokou jistotou, AI neproběhla
                    # Musíme alespoň zkontrolovat zda nejsou známky životopisu/certifikátu
                    if self._contains_non_invoice_keywords(ocr_text):
                        self._log(f"  ✗ {file_path.name}: Obsahuje znaky životopisu/certifikátu")
                        continue
                    
                    final_is_invoice = True
                    final_confidence = confidence
                    self._log(f"  ✓ {file_path.name}: Python potvrdil (jistota: {final_confidence:.0%})")
                else:
                    # Nízká jistota nebo AI selhala
                    self._log(f"  ✗ {file_path.name}: Neprošlo finální validací")
                    continue

                if not final_is_invoice:
                    self._log(f"  ✗ {file_path.name}: Neprošlo finální validací")
                    continue

                # Krok 2c: Extrakce dat (AI jako pomocník)
                ai_confirmed_invoice = False
                if ai_result and ai_result.get('is_invoice'):
                    # AI již extrahovala data
                    result = ai_result
                    ai_confirmed_invoice = True
                    self._log(f"  ✓ {file_path.name}: Faktura (AI klasifikace + extrakce)")
                else:
                    # AI nepoužita nebo selhala - extrahujeme pravidly
                    result = self.text_agent.extract_data(ocr_text, file_path.name)

                    # DŮLEŽITÉ: Zkontrolujeme výsledek extrakce
                    if not result.get('is_invoice', False):
                        self._log(f"  ✗ {file_path.name}: Extrakce pravidly zamítla - chybí známky faktury")
                        continue

                    self._log(f"  ✓ {file_path.name}: Faktura (Python + extrakce)")

                # === VALIDACE EXTRAKOVANÝCH DAT ===
                # Různá úrovně validace podle toho jak byla faktura potvrzena
                
                # Zkontrolujeme která data jsou k dispozici
                has_vendor = bool(result.get('vendor_name') or result.get('sender_name'))
                has_amount = result.get('total_amount') not in (None, '', 'None')
                has_date = bool(result.get('issue_date') and result.get('issue_date') not in ('', '0000-00-00', 'None'))
                has_invoice_number = bool(result.get('invoice_number'))
                has_customer = bool(result.get('customer_name') or result.get('recipient_name'))
                
                # Počet dostupných polí
                data_fields_count = sum([has_vendor, has_amount, has_date, has_invoice_number, has_customer])
                
                # Validace podle zdroje potvrzení
                if ai_confirmed_invoice:
                    # AI potvrdila fakturu - kontrolujeme že má alespoň nějaká data
                    # Pro obrázky s fakturami může být OCR nepřesná, takže tolerujeme i méně dat
                    ai_conf = ai_result.get('confidence', 50) / 100.0
                    
                    if ai_conf >= 0.85:
                        # Vysoká jistota AI - stačí 1 pole nebo název faktury v textu
                        if data_fields_count == 0 and 'faktura' not in ocr_text.lower():
                            self._log(f"  ✗ {file_path.name}: Podezřelá - AI vysoká jistota ale žádná data a žádný název")
                            continue
                        else:
                            self._log(f"  ✓ {file_path.name}: AI potvrdila (jistota: {ai_conf:.0%}, data: {data_fields_count}/5)")
                    elif ai_conf >= 0.60:
                        # Střední jistota - potřebujeme alespoň 1 pole
                        if data_fields_count == 0:
                            self._log(f"  ✗ {file_path.name}: Podezřelá - AI střední jistota ({ai_conf:.0%}) ale žádná data")
                            continue
                        else:
                            self._log(f"  ✓ {file_path.name}: AI potvrdila (jistota: {ai_conf:.0%}, data: {data_fields_count}/5)")
                    else:
                        # Nízká jistota AI - potřebujeme alespoň 2 pole
                        if data_fields_count < 2:
                            self._log(f"  ✗ {file_path.name}: Podezřelá - AI nízká jistota ({ai_conf:.0%}) a málo dat ({data_fields_count}/5)")
                            continue
                        else:
                            self._log(f"  ✓ {file_path.name}: AI potvrdila (jistota: {ai_conf:.0%}, data: {data_fields_count}/5)")
                else:
                    # Python potvrdil bez AI - potřebujeme alespoň 1 pole
                    if data_fields_count == 0:
                        self._log(f"  ��� {file_path.name}: Podezřelá - žádná extrahovatelná data")
                        continue
                    else:
                        self._log(f"  ✓ {file_path.name}: Python potvrdil (data: {data_fields_count}/5)")

                # Vytvoření Invoice objektu
                invoice = Invoice(
                    source_path=file_path,
                    sender_name=_sanitize_filename(result.get('vendor_name') or result.get('sender_name') or "Neznamy_odesilatel"),
                    recipient_name=_sanitize_filename(result.get('customer_name') or result.get('recipient_name') or "Neznamy_prijemce"),
                    issue_date=result.get('issue_date') or "0000-00-00",
                    due_date=result.get('due_date') or "0000-00-00",
                    total_amount=str(result.get('total_amount') or ""),
                    currency=result.get('currency') or "",
                    invoice_number=str(result.get('invoice_number') or ""),
                    is_invoice=True,
                    confidence=final_confidence,
                    raw_json=result,
                    ocr_text=ocr_text[:500],  # Uložíme začátek OCR textu
                )
                self.invoices.append(invoice)
                self._log(
                    f"     Od: {invoice.sender_name[:25]} | "
                    f"Datum: {invoice.issue_date} | "
                    f"Částka: {invoice.total_amount} {invoice.currency}"
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

        except Exception as ex:
            logger.exception(f"Chyba: {ex}")
            error_msg = str(ex)
            self.after(0, lambda: messagebox.showerror("Chyba", error_msg))
        finally:
            gc.collect()
            self.after(0, self._reset_ui)


# ────────────���────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main():
    app = InvoiceProcessorGUIV5()
    app.mainloop()


if __name__ == "__main__":
    main()
