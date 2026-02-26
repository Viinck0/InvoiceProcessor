"""
OCR Text Validator
Validuje text po OCR z Tesseractu:
- Kontroluje pravopis (čeština/angličtina)
- Detekuje halucinovaná slova (nesmyslné znaky)
- Opravuje běžné OCR chyby
- Vrací pouze smysluplná slova pro další agenty

Inspired by: document_langgraph_workflow.py validator_node
"""

import re
import logging
from typing import List, Dict, Tuple, Optional
from pathlib import Path

try:
    from spellchecker import SpellChecker
    SPELLCHECKER_AVAILABLE = True
except ImportError:
    SPELLCHECKER_AVAILABLE = False
    print("⚠️ pyspellchecker not installed. Install: pip install pyspellchecker")

logger = logging.getLogger(__name__)


class OCRTextValidator:
    """
    Validuje a čistí text po OCR.
    
    Workflow:
    1. Rozdělení textu na slova
    2. Detekce jazyka (čeština/angličtina)
    3. Kontrola pravopisu
    4. Oprava běžných OCR chyb
    5. Filtrace halucinovaných slov
    6. Rekonstrukce smysluplného textu
    """
    
    # Běžné OCR chyby (Tesseract confusion matrix)
    # POZOR: Pořadí aplikací oprav je důležité! Nejdříve dlouhé patterny, pak krátké.
    OCR_CONFUSIONS = {
        # Číslo-písmeno-číslo opravy (pro adresy typu "2I6" → "216")
        '2I6': '216', '2I5': '215', '2I4': '214', '2I3': '213', '2I2': '212', '2I1': '211',
        '3I6': '316', '3I5': '315', '3I4': '314', '3I3': '313', '3I2': '312', '3I1': '311',
        '4I6': '416', '4I5': '415', '4I4': '414', '4I3': '413', '4I2': '412',
        '5I6': '516', '5I5': '515', '5I4': '514', '5I3': '513', '5I2': '512',
        'I6': '16', 'I5': '15', 'I4': '14', 'I3': '13', 'I2': '12', 'I1': '11',
        'l6': '16', 'l5': '15', 'l4': '14', 'l3': '13', 'l2': '12', 'l1': '11',
        
        # Speciální znaky
        'č': 'č', 'ć': 'č', 'ċ': 'č',  # Normalize caron
        'š': 'š', 'ś': 'š',
        'ž': 'ž', 'ź': 'ž',
        'ě': 'ě', 'e': 'e',
        'ř': 'ř', 'r': 'r',
        'ň': 'ň', 'n': 'n',
        'ť': 'ť', 't': 't',
        'ď': 'ď', 'd': 'd',
        'ů': 'ů', 'u': 'u',
        
        # Časté Tesseract chyby
        'rn': 'm', 'nv': 'm', 'cl': 'd', 'ct': 'd',
        'vv': 'w', 'Vv': 'W',
        '´': "'", '`': "'",
    }
    
    # Whitelist pro faktury - tato slova vždy považovat za platná
    INVOICE_WHITELIST = {
        # Čeština
        'faktura', 'faktúry', 'daňový', 'doklad', 'dodavatel', 'odběratel',
        'ičo', 'dič', 'splatnosti', 'vystavení', 'úhradě', 'celkem', 'bez',
        'dpH', 'DPH', 'sazba', 'množství', 'jednotka', 'cena', 'celkem',
        'variabilní', 'symbol', 'konstantní', 'specifický', 'banka', 'účet',
        'IBAN', 'BIC', 'SWIFT', 'platba', 'převodem', 'hotově', 'dobírka',
        'datum', 'měsíc', 'rok', 'číslo', 'objednávky', 'smlouvy',
        'adresou', 'sídlem', 'zastoupený', 'společnost', 'firma',
        's.r.o.', 'a.s.', 'v.o.s.', 'spol.', 'r.o.', 'z.s.',
        'Kč', 'EUR', 'USD', 'CZK', 'Sk', 'zl',
        # Měrné jednotky (často vyřazováno kvůli malému počtu znaků)
        'ks', 'kg', 'km', 'hod', 'm2', 'm3', 'cm', 'mm', 'l', 'ml', 'g', 'mg',
        # Angličtina
        'invoice', 'tax', 'document', 'supplier', 'customer', 'vendor',
        'vat', 'amount', 'total', 'quantity', 'unit', 'price',
        'payment', 'bank', 'account', 'due', 'date', 'issue', 'number', 
        'order', 'contract', 'company', 'address', 'registered', 'office', 
        'represented', 'Ltd', 'Inc', 'GmbH', 'AG', 'SA', 'NV', 'GBP',
        'pcs', 'hours', 'hr', 'hrs',
        # Česká města a regiony (častá v adresách)
        'Ústecký', 'ústecký', 'Ústí', 'ústí', 'Praha', 'pražský',
        'Brno', 'brněnský', 'Ostrava', 'Plzeň', 'Liberec', 'Zlín',
        'Sobědruhy', 'sobědruhy', 'Polní', 'polní', 'Hlavní', 'hlavní',
        'Dlouhá', 'dlouhá', 'Malá', 'malá', 'Velká', 'velká',
        # Častá příjmení
        'Novák', 'Svoboda', 'Dvořák', 'Černý', 'Procházka',
        'Krejčí', 'Němec', 'Navrátil', 'Havlíček', 'Horák',
        'Pokorný', 'Jelínek', 'Kovář', 'Adam', 'Tichý',
        'Beneš', 'Čech', 'Moravec', 'Liška', 'Růžička',
    }
    
    # OPRAVA: Zrušeny restriktivní patterny pro délku slov a souhlásky
    HALLUCINATION_PATTERNS = [
        r'(.)\1{5,}',  # Extrémně opakující se znaky (6+), např. 'xxxxxx'
        r'^[aeiouyáéíóúýěů]{6,}$',  # Pouze samohlásky (6+) - např. 'aaaaaa'
        r'[\u0400-\u04FF]', # Nalezena Azbuka (Cyrillic)
        # OPRAVA: Zmírněno - neznámé znaky ano, ale ponechány běžné formátovací znaky pro čísla a cizí měny
        r'[^a-zA-Z0-9\s.,;:!?\-_\/\\()""\'\áéíóúýčďěňřšťžůÁÉÍÓÚÝČĎĚŇŘŠŤŽŮ€$£@&%]', 
    ]
    
    VALID_WORD_PATTERNS = [
        r'^\d{1,2}\.\d{1,2}\.\d{4}$',  # Datum DD.MM.YYYY
        r'^\d{4}-\d{2}-\d{2}$',  # Datum YYYY-MM-DD
        r'^CZ\d{2,3}\d{4,6}[- ]?\d{2,4}[- ]?\d{2,4}$',  # IBAN/CZ účet
        r'^\d{6,10}$',  # IČO
        r'^[A-Z]{2}\d{8,12}$',  # DIČ
        r'^[A-Z0-9-]{3,15}$',  # Faktura číslo / Variabilní symbol / Kódy
        r'^\d+([.,]\d{1,2})?$',  # Čísla a částky s desetinnými místy
        r'^[A-Z]\.$',  # Iniciály (J., M., atd.)
    ]
    
    SHORT_WORDS_CS = {
        'a', 'i', 'k', 'o', 's', 'u', 'v', 'z', 'na', 've', 'se', 'ke',
        'ze', 'do', 'po', 'pro', 'bez', 'při', 'už', 'jen', 'tak',
        'jak', 'kdy', 'kde', 'kam', 'kým', 'mu', 'mi', 'ti', 'to', 'ta',
        'ten', 'tam', 'tu', 'té', 'tí', 'tě', 'mí', 'ní', 'jí',
        'by', 'li', 'zdali', 'což', 'jež', 'je', 'či', 'ci', 'zí', 'rí', 'ne'
    }

    SHORT_WORDS_EN = {
        'a', 'an', 'as', 'at', 'be', 'by', 'do', 'go', 'he', 'hi', 'if',
        'in', 'is', 'it', 'me', 'my', 'no', 'of', 'on', 'or', 'so',
        'to', 'up', 'us', 'we', 'am', 'id', 'en', 'ex', 'ad', 're', 
        'th', 'er', 'po'
    }

    SHORT_WORDS_COMMON = {
        'www', 'http', 'https', 'ftp', 'pdf', 'jpg', 'png', 'doc', 'xls',
        'id', 'ID', 'no', 'No', 'nr', 'Nr', 'č', 'Č',
        'IBAN', 'BIC', 'SWIFT', 'VAT', 'TAX', 'GmbH', 'AG', 'SA', 'NV', 'LLC'
    }
    
    def __init__(self, language: str = "auto"):
        self.language = language
        self.spell_cs = None
        self.spell_en = None
        
        if SPELLCHECKER_AVAILABLE:
            try:
                self.spell_cs = SpellChecker(language="cs", local_dictionary=Path(__file__).parent / "cs.json")
            except (ValueError, FileNotFoundError) as e:
                logger.debug(f"Czech spellchecker not available: {e}")
                self.spell_cs = SpellChecker()  # Fallback to English
            
            try:
                self.spell_en = SpellChecker(language="en")
            except ValueError:
                self.spell_en = SpellChecker()
        else:
            logger.warning("SpellChecker not available - using pattern-based validation only")
    
    def validate(self, text: str) -> Dict:
        """Hlavní validace OCR textu."""
        if not text or len(text.strip()) < 10:
            return {
                "valid_text": text or "",
                "original_words": 0,
                "valid_words": 0,
                "corrected_words": 0,
                "hallucinated_words": 0,
                "corrections": [],
                "hallucinations": [],
                "confidence": 0.0,
                "detected_language": "unknown"
            }
        
        # Tokenizace
        words = self._tokenize(text)
        
        # Detekce jazyka
        detected_lang = self._detect_language(words)
        
        # Validace každého slova
        corrections = []
        hallucinations = []
        valid_words = []
        
        for word in words:
            result = self._validate_word(word, detected_lang)
            
            if result["status"] == "valid":
                valid_words.append(word)
            elif result["status"] == "corrected":
                valid_words.append(result["corrected"])
                corrections.append({
                    "original": word,
                    "corrected": result["corrected"],
                    "reason": result["reason"]
                })
            elif result["status"] == "hallucination":
                hallucinations.append(word)
                corrections.append({
                    "original": word,
                    "corrected": None,
                    "reason": result["reason"]
                })
        
        # Rekonstrukce textu
        valid_text = " ".join(valid_words)
        
        total_words = len(words)
        valid_count = len(valid_words)
        hallucinated_count = len(hallucinations)
        
        confidence = (valid_count / total_words) if total_words > 0 else 0.0
        
        if hallucinated_count > 0:
            hallucination_penalty = min(0.5, hallucinated_count / total_words * 0.5)
            confidence -= hallucination_penalty
        
        confidence = max(0.0, min(1.0, confidence))
        
        return {
            "valid_text": valid_text,
            "original_words": total_words,
            "valid_words": valid_count,
            "corrected_words": len(corrections),
            "hallucinated_words": hallucinated_count,
            "corrections": corrections,
            "hallucinations": hallucinations,
            "confidence": round(confidence, 3),
            "detected_language": detected_lang
        }
    
    def _tokenize(self, text: str) -> List[str]:
        """
        OPRAVA: Šetrnější rozdělení textu na slova.
        Původní logika rozbila částku '1 500,00' na '1', '500', '00' a pak je vyhodila.
        Tento nový přístup zachovává integritu čísel a emailů.
        """
        # Zachováme nové řádky a tabulátory převedením na mezeru
        text = text.replace('\n', ' ').replace('\t', ' ')
        
        # Rozdělíme čistě jen podle mezer (nepoužijeme agresivní regex s interpunkcí)
        raw_words = text.split()
        
        cleaned_words = []
        for word in raw_words:
            word = word.strip()
            if not word:
                continue
            
            # Pokud je slovo samotná měna nebo symbol (který by se jinak smazal)
            if word in ('€', '$', '£', '%'):
                cleaned_words.append(word)
                continue
                
            # Jemné očištění od okrajové interpunkce, ale ponecháme ji uvnitř slova (pro emaily, weby a desetinná čísla)
            cleaned = word.strip('.,;:!?()[]{}"\'')
            if cleaned:
                cleaned_words.append(cleaned)
                
        return cleaned_words
    
    def _detect_language(self, words: List[str]) -> str:
        """Detekce jazyka na základě slov."""
        cs_count = 0
        en_count = 0
        
        cs_chars = {'á', 'é', 'í', 'ó', 'ú', 'ý', 'č', 'ď', 'ě', 'ň', 'ř', 'š', 'ť', 'ž', 'ů'}
        
        for word in words[:100]:
            word_lower = word.lower()
            
            if any(c in word_lower for c in cs_chars):
                cs_count += 2
                continue
            
            if word_lower in {'faktura', 'daňový', 'doklad', 'dodavatel', 'odběratel', 'částka', 'splatnosti'}:
                cs_count += 3
                continue
            
            if word_lower in {'invoice', 'supplier', 'customer', 'amount', 'vat', 'tax'}:
                en_count += 3
                continue
            
            if word_lower in self.SHORT_WORDS_CS:
                cs_count += 1
            
            if word_lower in self.SHORT_WORDS_EN:
                en_count += 1
        
        if cs_count > en_count * 1.5:
            return "cs"
        elif en_count > cs_count * 1.5:
            return "en"
        else:
            return "mixed"
    
    def _validate_word(self, word: str, language: str) -> Dict:
        """Validace jednotlivého slova."""
        word_lower = word.lower()
        
        # OPRAVA: Všechna samotná čísla (i jednociferná) a částky jsou vždy validní!
        if any(char.isdigit() for char in word):
             return {"status": "valid", "reason": "contains_number"}

        if word_lower in self.INVOICE_WHITELIST:
            return {"status": "valid", "reason": "whitelist"}
        
        for pattern in self.VALID_WORD_PATTERNS:
            if re.match(pattern, word, re.IGNORECASE):
                return {"status": "valid", "reason": f"pattern:{pattern[:30]}"}
        
        for pattern in self.HALLUCINATION_PATTERNS:
            if re.search(pattern, word):
                if len(word) <= 3 and word_lower in (self.SHORT_WORDS_CS | self.SHORT_WORDS_EN):
                    return {"status": "valid", "reason": "short_word_exception"}
                if word in self.SHORT_WORDS_COMMON or word_lower in self.SHORT_WORDS_COMMON:
                    return {"status": "valid", "reason": "common_abbreviation"}
                return {"status": "hallucination", "reason": f"pattern:{pattern[:30]}"}
        
        if language in ("cs", "mixed") and self.spell_cs:
            corrected = self.spell_cs.correction(word_lower)
            if corrected and corrected != word_lower:
                if corrected in self.INVOICE_WHITELIST:
                    return {"status": "corrected", "corrected": corrected, "reason": "spellcheck_cs"}
                if self.spell_cs.unknown([word_lower]) and not self.spell_cs.unknown([corrected]):
                    return {"status": "corrected", "corrected": corrected, "reason": "spellcheck_cs"}
        
        if language in ("en", "mixed") and self.spell_en:
            corrected = self.spell_en.correction(word_lower)
            if corrected and corrected != word_lower:
                if corrected in self.INVOICE_WHITELIST:
                    return {"status": "corrected", "corrected": corrected, "reason": "spellcheck_en"}
                if self.spell_en.unknown([word_lower]) and not self.spell_en.unknown([corrected]):
                    return {"status": "corrected", "corrected": corrected, "reason": "spellcheck_en"}
        
        # Aplikace OCR oprav
        corrected = self._apply_ocr_corrections(word)
        if corrected != word:
            return {"status": "corrected", "corrected": corrected, "reason": "ocr_confusion"}
        
        return {"status": "valid", "reason": "unknown_but_allowed"}
    
    def _apply_ocr_corrections(self, word: str) -> str:
        """Aplikace běžných OCR oprav."""
        corrected = word

        long_patterns = [
            ('2I6', '216'), ('2I5', '215'), ('2I4', '214'), ('2I3', '213'), ('2I2', '212'), ('2I1', '211'),
            ('3I6', '316'), ('3I5', '315'), ('3I4', '314'), ('3I3', '313'), ('3I2', '312'), ('3I1', '311'),
            ('4I6', '416'), ('4I5', '415'), ('4I4', '414'), ('4I3', '413'), ('4I2', '412'),
            ('5I6', '516'), ('5I5', '515'), ('5I4', '514'), ('5I3', '513'), ('5I2', '512'),
            ('I6', '16'), ('I5', '15'), ('I4', '14'), ('I3', '13'), ('I2', '12'), ('I1', '11'),
            ('l6', '16'), ('l5', '15'), ('l4', '14'), ('l3', '13'), ('l2', '12'), ('l1', '11'),
            ('rn', 'm'), ('nv', 'm'), ('cl', 'd'), ('ct', 'd'),
            ('vv', 'w'), ('Vv', 'W'),
        ]
        for wrong, right in long_patterns:
            if wrong in corrected:
                corrected = corrected.replace(wrong, right)

        # Nezahrnujeme substituci jednotlivých znaků (číslo za písmeno), pokud už slovo obsahuje čísla, 
        # zabráníme tím poškození validních dat.
        if not any(c.isdigit() for c in word):
            char_subs = {
                '´': "'", '`': "'",
            }
            for wrong, right in char_subs.items():
                corrected = corrected.replace(wrong, right)

        diacritics = {
            'č': 'č', 'ć': 'č', 'ċ': 'č',
            'š': 'š', 'ś': 'š',
            'ž': 'ž', 'ź': 'ž',
        }
        for wrong, right in diacritics.items():
            corrected = corrected.replace(wrong, right)

        return corrected
    
    def get_summary(self, validation_result: Dict) -> str:
        """Vytvoří čitelný souhrn validace."""
        lines = [
            f"📝 OCR Text Validation Summary",
            f"  Original words: {validation_result['original_words']}",
            f"  Valid words: {validation_result['valid_words']}",
            f"  Corrected: {validation_result['corrected_words']}",
            f"  Hallucinations: {validation_result['hallucinated_words']}",
            f"  Confidence: {validation_result['confidence']:.0%}",
            f"  Language: {validation_result['detected_language']}",
        ]
        
        if validation_result['hallucinations']:
            lines.append(f"  ⚠️ Hallucinated words: {', '.join(validation_result['hallucinations'][:10])}")
        
        if validation_result['corrections']:
            lines.append(f"  ✓ Corrections:")
            for corr in validation_result['corrections'][:5]:
                lines.append(f"    '{corr['original']}' → '{corr['corrected']}' ({corr['reason']})")
        
        return "\n".join(lines)


# ─────────────────────────────────────────────
# Integration helper
# ─────────────────────────────────────────────
def validate_ocr_text(text: str, language: str = "auto") -> Tuple[str, Dict]:
    """Quick helper function for OCR text validation."""
    validator = OCRTextValidator(language=language)
    result = validator.validate(text)
    
    logger.debug(f"OCR Validation: {result['valid_words']}/{result['original_words']} words valid ({result['confidence']:.0%})")
    
    if result['hallucinations']:
        logger.warning(f"  Detected {result['hallucinated_words']} hallucinated words: {result['hallucinations'][:5]}")
    
    return result["valid_text"], result


if __name__ == "__main__":
    test_text = """
    FAKTURA č. 2024001
    Dodavatel: ABC s.r.o., IČO: 12345678
    Odběratel: XYZ a.s., IČO: 87654321
    Datum vystavení: 15.01.2024
    Datum splatnosti: 15.02.2024
    Celkem k úhradě: 1 500 Kč
    """
    
    validator = OCRTextValidator()
    result = validator.validate(test_text)
    print(validator.get_summary(result))