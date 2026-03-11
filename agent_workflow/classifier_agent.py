"""
Classifier Agent
Binary invoice/non-invoice decision with confidence scoring

v7.4 (2026-03-04):
- ARCHITECTURAL CHANGE: Agents receive ONLY Markdown data, NO raw text!
- MARKDOWN-FIRST: Changed from JSON to Markdown output format
- Agents work exclusively with:
  1. Markdown table (structured data from text_blocks)
  2. Master Instruction (fixed reference framework for validation)
- Striktní anti-papouškovací pravidla pro extrakci hodnot
- Zaměření na prostorové rozložení (souřadnice X0, Y0)

Uses external configuration from config/rules.yaml for keywords and patterns.
Edit the YAML file to tune accuracy without modifying this code.
"""

from typing import Optional
import logging
import re
from .base_agent import BaseAgent

# Import configuration loader
try:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from config_loader import get_config
    CONFIG = get_config()
except Exception as e:
    logging.warning(f"Config loader not available: {e}. Using built-in defaults.")
    CONFIG = None

# Import security sanitizer
try:
    from core.security import sanitize_llm_output
    HAS_SECURITY = True
except ImportError:
    HAS_SECURITY = False

logger = logging.getLogger(__name__)


class ClassifierAgent(BaseAgent):
    """
    Specialized agent for binary invoice classification.
    
    Uses strict 5-element definition:
    1. Document identification
    2. Subjects (vendor + customer)
    3. Dates (issue + due)
    4. Financial data
    5. Payment instructions
    """
    
    CLASSIFICATION_PROMPT = """Jsi expertní systém pro klasifikaci dokumentů. Tvým JEDINÝM úkolem je na základě obsahu rozhodnout, zda se jedná o fakturu.

=== KRITICKÉ PRAVIDLO - FORMÁT ODPOVĚDI ===
⚠️ TVÁ ODPOVĚĎ MUSÍ ZAČÍNAT OKAMŽITĚ: "## 📊 Klasifikace dokumentu"
⚠️ NIKDY nepopisuj vzhled dokumentu, fonty, barvy nebo layout!
⚠️ NIKDY nepiš úvodní věty jako "Vzhled dokumentu...", "Tento dokument...", "Analýza dokumentu..."!
⚠️ Pokud začneš popisovat dokument místo klasifikace, selhal jsi!

=== KRITICKÉ PRAVIDLO - ROZPOZNÁNÍ FAKTURY ===
⚠️ POKUD dokument obsahuje "faktura", "daňová faktura", "invoice", "tax document" → is_invoice = TRUE
⚠️ POKUD dokument obsahuje dodavatele (firma) a odběratele (osoba/firma) → is_invoice = TRUE
⚠️ POKUD dokument obsahuje částku (i když je 0,00) → is_invoice = TRUE

=== KRITICKÉ PRAVIDLO - CO NENÍ FAKTURA ===
Pokud dokument obsahuje typické znaky jiných dokumentů (např. {negative_keywords}, nebo jiný nesouvisející text), MUSÍŠ vrátit is_invoice: false a všechny hodnoty v tabulce hodnot musí být null. NIKDY neoznačuj tyto dokumenty jako fakturu.

=== KRITICKÉ PRAVIDLO - EXTRAKCE HODNOT ===
Pokud JE dokument skutečně faktura, tvým úkolem je EXTRAKOVAT konkrétní hodnoty z textu.
Všechny hodnoty MUSÍ být doslovně převzaty z analyzovaného dokumentu.
NIKDY si nevymýšlej data. NIKDY nepoužívej hodnoty které nejsou v dokumentu.

ABSOLUTNÍ ZÁKAZ KOPÍROVÁNÍ ZADÁNÍ: Do tabulky "Extrahované hodnoty" NIKDY neopisuj text ze zadání (např. NIKDY nepiš "hodnota z textu nebo null").
Pokud hodnotu najdeš, vypiš její SKUTEČNÉ ZNĚNÍ (např. "Alza s.r.o." nebo "2500 Kč").
Pokud hodnotu nenajdeš, napiš POUZE slovo: null

=== ABSOLUTNÍ ZÁKAZ PRO HODNOTY - ŠTÍTKY ===
⚠️ NIKDY neextrahuj ŠTÍTKY jako hodnoty! Štítky poznáš podle těchto slov:

ZAKÁZANÉ ŠTÍTKY PRO DODAVATELE/ODBĚRATELE:
- "Datum účinnosti", "Datum transakce", "ID transakce" → To jsou DATUMY/ID, ne firmy!
- "Číslo faktury", "Číslo zákazníka", "ID zákazníka" → To jsou ČÍSLA, ne firmy!
- "Daňové číslo", "DIČ", "IČO", "VAT" → To jsou ČÍSLA, ne firmy!
- "Variabilní symbol", "Konstantní symbol" → To jsou ČÍSLA, ne firmy!
- "E-mail kupujícího", "E-mail odběratele" → To jsou EMAILY, ne firmy!
- "Platební metoda", "Frekvence účtování" → To jsou POPISY, ne firmy!

SPRÁVNÁ EXTRAKCE:
- "[vlevo] Daňová faktura z LinkedIn Ireland Unlimited Company" → supplier = "LinkedIn Ireland Unlimited Company"
- "[vlevo] Václav Krajkář" → customer = "Václav Krajkář"
- "[vlevo] Polní 216, Teplice" → address = "Polní 216, Teplice"
- "[vpravo] 26. 2. 2026" → date = "26. 2. 2026"
- "[vpravo] 0,00 Kč" → amount = "0,00 Kč"

Pokud za slovem "Odběratel:" nebo "Dodavatel:" následuje slovo jako "ze dne", "IČO", "DIČ", "Datum" nebo "Faktura", znamená to, že jméno na daném řádku chybí. V takovém případě VRAŤ null. Nikdy neextrahuj "ze dne" nebo podobné štítky jako jméno firmy!

=== PRIMÁRNÍ ZDROJ DAT: MASTER INSTRUCTION (PLNÝ TEXT PODLE SOUŘADNIC) ===
Tvou nejdůležitější sekcí je "🎯 PLNÝ TEXT PODLE SOUŘADNIC (Master Instruction)". Tato sekce obsahuje text již seřazený do logických bloků s označením pozice [vlevo] nebo [vpravo].
* **[vlevo]**: Informace v levém sloupci (často odesílatel, detaily firmy).
* **[vpravo]**: Informace v pravém sloupci (často odběratel, datum, částka).
* **Pořadí**: Bloky jdou za sebou tak, jak jsou fyzicky na dokumentu odshora dolů.

=== POSTUP ANALÝZY ===
1. PŘEDNOSTNĚ si prohlédni sekci "🎯 PLNÝ TEXT PODLE SOUŘADNIC". Na základě [vlevo]/[vpravo] pochop strukturu dokumentu.
2. HLEDEJ KLÍČOVÁ SLOVA:
   - "faktura", "invoice", "daňová faktura" → is_invoice = TRUE
   - "[vlevo] Firma" nebo "[vlevo] Jméno" → potential supplier/customer
   - "[vpravo] Částka" nebo "[vpravo] Cena" → amount
   - "[vlevo/vpravo] Datum" → date
3. Pokud to není faktura (např. obsahuje slova ze zakázaného seznamu), rovnou označ "Je faktura: ❌ NE" a všechny hodnoty dej jako null.
4. Pokud je to faktura, hledej hodnoty primárně v seřazených bloccích:
   - Dodavatel bývá často v bloku [vlevo] nahoře (název firmy).
   - Odběratel bývá často v bloku [vlevo] nebo [vpravo] (jméno nebo firma).
   - Částka a datum jsou v odpovídajících řádcích [vpravo] nebo [vlevo].
5. Pokud hodnotu nenajdeš → vrať null.

=== PRAVIDLA PRO REASONING A FORMATOVANI ===
- Reasoning MUSÍ být STRUČNÝ (max 3-4 věty). Uveď klíčové poznatky z Master Instrukce (co leží vlevo/vpravo).
- ODPOVÍDEJ POUZE VE FORMÁTU MARKDOWN. Naprostý zákaz JSONu (nepoužívej {{}}).
- **ZÁKAZ JSONu i v Reasoning:** Nikdy nepiš technický JSON kód do vysvětlení!
- **ŽÁDNÉ ÚVODNÍ VĚTY:** Okamžitě začni s "## 📊 Klasifikace dokumentu"

Použij PŘESNĚ následující strukturu:

## 📊 Klasifikace dokumentu
**Je faktura:** ✅ ANO / ❌ NE
**Confidence:** 0.85

## 🔍 Elementy
| Element | Přítomen |
|---------|----------|
| Document Type Identified | ✅ / ❌ |
| Supplier and Buyer Present | ✅ / ❌ |
| Total Amount Present | ✅ / ❌ |
| Date Present | ✅ / ❌ |
| Payment Instructions Present | ✅ / ❌ |

## 📋 Extrahované hodnoty
| Pole | Hodnota |
|------|---------|
| Document Type | text / null |
| Supplier | text / null |
| Customer | text / null |
| Amount | text / null |
| Date | text / null |
| Payment Info | text / null |

## 🧠 Reasoning
[Stručný rozbor na základě Master Instrukce - co jsi našel v blocích [vlevo]/[vpravo]]

=== STRUKTURA VSTUPU ===
Vstup obsahuje tyto části:
1. 🎯 PLNÝ TEXT PODLE SOUŘADNIC (Master Instruction): Tvůj HLAVNÍ zdroj dat pro pochopení struktury.
2. 📋 Strukturovaná data dokumentu: Přehledová tabulka (Cislo, Pozice, Text).
3. 📜 VIZUALIZACE DOKUMENTU: Pouze pro informaci - nepoužívej pro klasifikaci!
4. SUROVÝ TEXT: Surová data (mohou být zpřeházená).

=== TEXT DOKUMENTU ===
{input_data}"""

    def __init__(self, model: str = "llama3.2", timeout: int = 30, vram_limit_gb: int = None, num_ctx: int = None):
        super().__init__(model, timeout, vram_limit_gb, num_ctx)
        
        # Load keywords and patterns from external config
        if CONFIG:
            self.strong_negative_indicators = CONFIG.get_classifier_strong_negatives()
            self.context_negative_indicators = CONFIG.get_classifier_context_negatives()
            self.fallback_positive, self.fallback_negative = CONFIG.get_classifier_fallback_keywords()
        else:
            # Fallback to built-in defaults if config not available
            self.strong_negative_indicators = [
                'plná moc', 'plnomocenství', 'dluhopis', 'cenný papír', 'akcie',
                'životopis', 'životopisy', 'curriculum vitae', 'resume',
                'pracovní zkušenosti', 'vzdělání', 'dovednosti', 'jazykové znalosti',
                'work experience', 'education', 'skills',
                'certifikát o absolvování', 'diplom', 'osvědčení o absolvování',
                'cenová nabídka', 'nabídka ceny', 'rozpočet',
                'upomínka', 'reminder', 'výzva k úhradě',
            ]
            self.context_negative_indicators = {
                'narozen': ['rodné číslo', 'datum narození', 'narozen v'],
                'born': ['birth date', 'date of birth', 'born in'],
                'smlouva': ['fakturujeme za', 'faktura za', 'úhrada za', 'platba za'],
                'certifikát': ['fakturujeme za', 'faktura za', 'úhrada za', 'platba za'],
                'licence': ['fakturujeme za', 'faktura za', 'úhrada za', 'platba za'],
                'objednávka': ['faktura za', 'úhrada za', 'platba za', 'fakturujeme za', 'daňový doklad'],
                'purchase order': ['invoice for', 'payment for', 'billing for'],
            }
            self.fallback_positive = ['faktura', 'daňový doklad', 'celkem k úhradě']
            self.fallback_negative = ['upomínka', 'smlouva', 'nabídka', 'životopis']

    @property
    def STRONG_NEGATIVE_INDICATORS(self) -> list:
        return self.strong_negative_indicators

    @property
    def CONTEXT_NEGATIVE_INDICATORS(self) -> dict:
        return self.context_negative_indicators

    def _check_strong_negatives(self, text: str, text_blocks: list = None) -> Optional[dict]:
        """Check for strong negative indicators and reject immediately if found."""
        text_lower = text.lower()

        # Check raw text first
        for indicator in self.strong_negative_indicators:
            if indicator in text_lower:
                logger.info(f"✓ Rychlé zamítnutí: nalezeno '{indicator}'")
                return {
                    'is_invoice': False,
                    'confidence': 0.95,
                    'elements_present': {
                        'identification': False,
                        'subjects': False,
                        'dates': False,
                        'financial': False,
                        'payment_info': False
                    },
                    'reasoning': f"Dokument obsahuje '{indicator}' - není to faktura"
                }

        # Check text blocks with positions (more reliable for structured documents)
        if text_blocks:
            block_texts = []
            for block in text_blocks:
                if isinstance(block, dict) and 'text' in block:
                    block_texts.append(block['text'].lower())
                elif isinstance(block, str):
                    block_texts.append(block.lower())
            
            combined_blocks = ' '.join(block_texts)
            
            for indicator in self.strong_negative_indicators:
                if indicator in combined_blocks and indicator not in text_lower:
                    logger.info(f"✓ Rychlé zamítnutí z textových bloků: nalezeno '{indicator}'")
                    return {
                        'is_invoice': False,
                        'confidence': 0.95,
                        'elements_present': {
                            'identification': False,
                            'subjects': False,
                            'dates': False,
                            'financial': False,
                            'payment_info': False
                        },
                        'reasoning': f"Dokument obsahuje '{indicator}' v textových blocích - není to faktura"
                    }

        for indicator, invoice_contexts in self.context_negative_indicators.items():
            if indicator in text_lower:
                has_invoice_context = any(ctx in text_lower for ctx in invoice_contexts)

                if has_invoice_context:
                    logger.debug(f"  Ignorováno '{indicator}' - výskyt ve fakturačním kontextu")
                    continue

                if indicator in ['objednávka', 'purchase order']:
                    title_patterns = [
                        rf'^.*{indicator}.*$',
                        rf'\n.*{indicator}.*\n',
                        rf'{indicator}\s*č\.',
                        rf'{indicator}\s*#',
                    ]
                    is_title = any(re.search(pattern, text_lower, re.MULTILINE) for pattern in title_patterns)

                    if is_title:
                        logger.info(f"✓ Rychlé zamítnutí: nalezeno '{indicator}' jako typ dokumentu")
                        return {
                            'is_invoice': False,
                            'confidence': 0.85,
                            'elements_present': {
                                'identification': False,
                                'subjects': False,
                                'dates': False,
                                'financial': False,
                                'payment_info': False
                            },
                            'reasoning': f"Dokument je '{indicator}' (typ dokumentu) - není to faktura"
                        }
                    else:
                        logger.debug(f"  Ignorováno '{indicator}' - výskyt v textu (reference)")
                        continue

                logger.info(f"✓ Rychlé zamítnutí: nalezeno '{indicator}' bez fakturačního kontextu")
                return {
                    'is_invoice': False,
                    'confidence': 0.85,
                    'elements_present': {
                        'identification': False,
                        'subjects': False,
                        'dates': False,
                        'financial': False,
                        'payment_info': False
                    },
                    'reasoning': f"Dokument obsahuje '{indicator}' bez fakturačního kontextu - není to faktura"
                }

        return None

    def analyze(self, markdown_input: str, metadata: Optional[dict] = None, text_blocks: list = None, master_instruction: Optional[str] = None) -> dict:
        """
        Analyzuje dokument a klasifikuje jako fakturu nebo ne-fakturu.

        ARCHITEKTURA: Agent dostává POUZE strukturovaná data:
        - markdown_input: Markdown tabulka + layout (žádný surový text!)
        - master_instruction: Pevný referenční rámec pro validaci
        - text_blocks: Prostorová data pro lepší analýzu struktury

        Args:
            markdown_input: Strukturovaná Markdown data (tabulka + layout)
            metadata: Dodatečná metadata (nepoužívá se pro raw text)
            text_blocks: List textových bloků s bbox pozicemi
            master_instruction: Pevná instrukce pro validaci
        """
        if not markdown_input or len(markdown_input.strip()) < 50:
            return self._empty_result("Prázdný nebo velmi krátký text")

        # Strong negatives se kontrolují v Markdown datech (ne v surovém textu)
        strong_reject = self._check_strong_negatives(markdown_input, text_blocks)
        if strong_reject:
            return strong_reject

        # Inteligentní krácení textu - zachovat důležité Markdown sekce
        truncated = self._truncate_text_smart(markdown_input, max_chars=8000)

        try:
            # Injektování VŠECH slov z konfigurace do promptu (odstraněn limit [:15])
            negatives_str = ", ".join(self.strong_negative_indicators)
            positives_str = ", ".join(self.fallback_positive)

            master_instruction_section = f"=== MASTER INSTRUCTION (IMPORTANT) ===\n{master_instruction}\n\n" if master_instruction else ""

            prompt = self.CLASSIFICATION_PROMPT.replace(
                "=== STRUKTURA VSTUPU ===",
                master_instruction_section + "=== STRUKTURA VSTUPU ==="
            ).replace(
                "{negative_keywords}", negatives_str
            ).replace(
                "{positive_keywords}", positives_str
            ).replace(
                "{input_data}", truncated
            )
        except (KeyError, IndexError) as e:
            logger.warning(f"Prompt format error: {e}")
            return self._fallback_classification(markdown_input)

        try:
            client = self._get_client()
            response = client.generate(
                model=self.model,
                prompt=prompt,
                options={
                    "temperature": 0.0,
                    "num_predict": 512,  # Sníženo pro stručnější reasoning
                    "num_ctx": self.num_ctx,
                    "top_p": 0.1,
                    "repeat_penalty": 1.1,
                },
                keep_alive="0s"
            )

            raw_output = response.get("response", "")
            logger.debug(f"Raw classifier output: {raw_output[:500]}...")

            parsed = self._parse_markdown_output(raw_output)

            if parsed is None:
                logger.warning(f"Classifier nevrátil platný Markdown: {raw_output[:200]}")
                return self._fallback_classification(markdown_input)

            # Normalize confidence
            confidence = parsed.get('confidence', 0.5)
            if isinstance(confidence, str):
                try:
                    confidence = float(confidence)
                except ValueError:
                    confidence = 0.5
            if confidence > 1:
                confidence = confidence / 100.0
            parsed['confidence'] = min(1.0, max(0.0, confidence))

            extracted = parsed.get('extracted_values', {})
            reasoning = parsed.get('reasoning', '').lower()

            # --- OCHRANA PROTI PSČ (Sanity check pro částku) ---
            if extracted.get('amount'):
                amt_str = str(extracted['amount']).lower().replace(" ", "")
                if re.search(r'\d{5}[a-z]{3,}', amt_str) or 'praha' in amt_str or 'brno' in amt_str:
                    logger.warning(f"Sanity Check: Model zkusil jako částku podstrčit PSČ/Adresu '{extracted['amount']}'. Nuluji.")
                    extracted['amount'] = None

            # --- TVRDÁ REGEX ZÁCHRANA PŘÍMO ZDE ---
            if not extracted.get('amount'):
                regex_match = re.search(r'(?i)(?:celkem\s*k\s*úhradě|celkemkuhrade|celkem|total|grand\s*total|amount\s*due|k\s*úhradě|k\s*zaplacení|částka\s*:)[\s]*(\d+(?:[\s,.]\d+)*)(?:\s*(Kč|EUR|USD|CZK|GBP|€|\$|£))?', markdown_input)
                if regex_match:
                    num_part = regex_match.group(1).strip()
                    curr_part = regex_match.group(2)
                    extracted['amount'] = f"{num_part} {curr_part.strip()}" if curr_part else num_part
                    logger.info(f"Regex Záchrana: Částka úspěšně nalezena Pythonem: {extracted['amount']}")

            # OCHRANA PROTI ZÁMĚNĚ ŠTÍTKU ZA HODNOTU
            forbidden_labels = ['ze dne', 'ičo', 'dič', 'datum', 'splatnost', 'faktura', 'banka', 'účet', 'číslo']
            for field in ['supplier', 'customer']:
                val = extracted.get(field)
                if val:
                    val_lower = str(val).lower().strip()
                    if any(label in val_lower for label in forbidden_labels) or len(val_lower) < 3:
                        logger.warning(f"Sanity Check: AI zkusilo jako '{field}' podstrčit jiný štítek: '{val}'. Nuluji.")
                        extracted[field] = None

            parsed['extracted_values'] = extracted

            # --- SANITY CHECK PRO FAKTURU (PŘÍSNÁ KONTROLA DLE YAML) ---
            if parsed.get('is_invoice'):
                text_lower_check = markdown_input.lower()

                # Používáme výhradně povolená slova z tvého config/rules.yaml
                invoice_mandatory_words = [kw.lower() for kw in self.fallback_positive]

                has_invoice_word = any(word in text_lower_check for word in invoice_mandatory_words)

                if not has_invoice_word:
                    # Zamítneme to, i když má AI jistotu 100%!
                    logger.warning("Sanity Check: Model označil dokument jako fakturu, ale chybí jakékoliv povolené slovo z rules.yaml! Tvrdě zamítám.")
                    parsed['is_invoice'] = False
                    parsed['confidence'] = 0.95
                    parsed['reasoning'] = "Systémová korekce: Dokument neobsahuje žádná klíčová fakturační slova definovaná v config/rules.yaml (např. faktura, invoice). Jedná se o halucinaci."

                    # VYNULOVÁNÍ VŠECH ELEMENTŮ - dříve zde chybělo a způsobovalo false-positives
                    parsed['elements_present'] = {
                        'identification': False,
                        'subjects': False,
                        'dates': False,
                        'financial': False,
                        'payment_info': False
                    }

                    for key in parsed['extracted_values']:
                        parsed['extracted_values'][key] = None

            logger.debug(f"✓ Klasifikace: {'Faktura' if parsed['is_invoice'] else 'Není faktura'} (jistota: {parsed['confidence']:.0%})")

            return parsed

        except Exception as e:
            logger.error(f"Classifier chyba: {e}")
            return self._empty_result(f"Chyba analýzy: {str(e)}")
    
    def _parse_markdown_output(self, markdown_text: str) -> dict:
        """
        Robustní parser Markdown output z classifier agenta.
        """
        if not markdown_text or not markdown_text.strip():
            return None

        # Detekce kdy model opakuje prompt místo generování odpovědi
        if markdown_text.strip().startswith("Jsem expertní systém") or \
           markdown_text.strip().startswith("Jsi expertní systém") or \
           ("můj úkol" in markdown_text.lower() and "rozhodnout" in markdown_text.lower() and "fakturu" in markdown_text.lower()):
            logger.debug("Model opakuje prompt - pokus o extrakci zbytku odpovědi")
            # Pokus najít skutečnou odpověď dál v textu
            answer_start = re.search(r'##\s*📊\s*Klasifikace', markdown_text)
            if answer_start:
                markdown_text = markdown_text[answer_start.start():]
            else:
                # Žádná strukturovaná odpověď nenalezena
                return None

        # 🔧 FIX: Model začal popisovat dokument místo strukturované odpovědi
        # Hledáme začátek odpovědi kdekoli v textu
        answer_patterns = [
            r'##\s*📊\s*Klasifikace',
            r'##\s*📊\s*Klasifikace dokumentu',
            r'##\s*📋\s*Extrahované hodnoty',
            r'##\s*🔍\s*Elementy',
            r'\*\*Je faktura:\*\*',
            r'Je faktura:',
        ]

        # Detekce kdy model popisuje dokument místo klasifikace
        forbidden_starts = [
            'Vzhled dokumentu',
            'Tento dokument',
            'Analýza dokumentu',
            'Dokument obsahuje',
            'Dokument je',
            'Na základě analýzy',
            'Po prostudování',
        ]

        for forbidden in forbidden_starts:
            if markdown_text.strip().startswith(forbidden):
                logger.warning(f"Model začal popisovat dokument: '{forbidden}'")
                # Pokus najít skutečnou odpověď dál v textu
                for pattern in answer_patterns:
                    match = re.search(pattern, markdown_text, re.IGNORECASE)
                    if match:
                        markdown_text = markdown_text[match.start():]
                        logger.debug(f"Found answer after forbidden start with pattern: {pattern}")
                        break
                else:
                    # Žádná strukturovaná odpověď nenalezena
                    return None
                break

        found_answer_start = False
        for pattern in answer_patterns:
            match = re.search(pattern, markdown_text, re.IGNORECASE)
            if match:
                # Najdeme první výskyt jakékoliv strukturované sekce
                markdown_text = markdown_text[match.start():]
                found_answer_start = True
                logger.debug(f"Found answer start with pattern: {pattern}")
                break

        if not found_answer_start:
            # Model nevrátil žádnou strukturovanou odpověď
            logger.warning(f"Classifier nevrátil platný Markdown: {markdown_text[:100]}...")
            return None

        result = {
            'is_invoice': False,
            'confidence': 0.0,
            'elements_present': {
                'identification': False,
                'subjects': False,
                'dates': False,
                'financial': False,
                'payment_info': False
            },
            'extracted_values': {
                'document_type': None,
                'supplier': None,
                'customer': None,
                'amount': None,
                'date': None,
                'payment_info': None
            },
            'reasoning': ''
        }

        # 1. Je faktura
        is_invoice_match = re.search(r'(?i)(?:\*\*Je faktura:\*\*|je faktura:)\s*(✅\s*ANO|❌\s*NE|ANO|NE|TRUE|FALSE)', markdown_text)
        if is_invoice_match:
            val = is_invoice_match.group(1).upper()
            result['is_invoice'] = 'ANO' in val or 'TRUE' in val
            
        # 2. Confidence
        conf_match = re.search(r'(?i)(?:\*\*Confidence:\*\*|confidence:)\s*([\d.]+)', markdown_text)
        if conf_match:
            try:
                result['confidence'] = float(conf_match.group(1))
            except ValueError:
                pass

        # 3. Tabulky (pomocí Regex)
        table_rows = re.findall(r'\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|', markdown_text)
        
        for col1, col2 in table_rows:
            key = col1.strip().lower()
            val = col2.strip()
            
            # Přeskočíme hlavičky a formátování tabulky
            if '---' in key or key in ['element', 'pole', 'field', 'klíč']:
                continue

            # A) Zpracování sekce Elementy (vždy True/False podle zaškrtnutí)
            if 'identified' in key or 'present' in key:
                is_present = '✅' in val or 'ano' in val.lower() or 'true' in val.lower()
                if 'type identified' in key: result['elements_present']['identification'] = is_present
                elif 'supplier' in key or 'buyer' in key: result['elements_present']['subjects'] = is_present
                elif 'amount' in key: result['elements_present']['financial'] = is_present
                elif 'date' in key: result['elements_present']['dates'] = is_present
                elif 'payment' in key: result['elements_present']['payment_info'] = is_present
            
            # B) Zpracování sekce Extrahované hodnoty
            else:
                # Ochrana před halucinací "hodnota z textu"
                if "hodnota z textu" in val.lower() or val.lower() in ['null', 'none', '—', '-', '', 'n/a']:
                    continue
                    
                if 'type' in key: result['extracted_values']['document_type'] = val
                elif 'supplier' in key or 'dodavatel' in key: result['extracted_values']['supplier'] = val
                elif 'customer' in key or 'odběratel' in key: result['extracted_values']['customer'] = val
                elif 'amount' in key or 'částka' in key: result['extracted_values']['amount'] = val
                elif 'date' in key or 'datum' in key: result['extracted_values']['date'] = val
                elif 'payment' in key or 'info' in key: result['extracted_values']['payment_info'] = val

        # 4. Reasoning
        reasoning_match = re.split(r'(?i)##\s*🧠\s*Reasoning|##\s*Reasoning', markdown_text)
        if len(reasoning_match) > 1:
            result['reasoning'] = reasoning_match[1].strip()
            # 🔒 SECURITY: Sanitize reasoning to prevent prompt injection
            if HAS_SECURITY:
                result['reasoning'] = sanitize_llm_output(result['reasoning'])

        # Ochrana: Pokud z tabulek nevypadlo vůbec nic, parser selhal
        if not is_invoice_match and not result['extracted_values'].get('amount') and result['confidence'] == 0.0:
             return None

        # 🔧 FIX: Synchronize elements_present with extracted_values
        # Elements must reflect what was ACTUALLY extracted, not what LLM claimed in Elements table
        # This prevents inconsistency where element is marked present but value is null
        extracted = result['extracted_values']
        elements = result['elements_present']

        # 🔒 SANITY CHECK: Blokování extrakce štítků místo hodnot
        # Zakázané patterny pro supplier/customer - to jsou ŠTÍTKY ne hodnoty!
        forbidden_label_patterns = [
            r'^datum ', r'^datum$',  # "Datum účinnosti", "Datum transakce"
            r'^číslo ', r'^číslo$',  # "Číslo faktury", "Číslo zákazníka"
            r'^id ', r'^id$',        # "ID transakce", "ID zákazníka"
            r'^daň', r'^dic', r'^ičo', r'^vat',  # "Daňové číslo", "DIČ", "IČO"
            r'^variabilní', r'^konstantní', r'^symbol',  # "Variabilní symbol"
            r'^e-mail', r'^email',   # "E-mail kupujícího"
            r'^platební', r'^frekvence', r'^metoda',  # "Platební metoda"
            r'^účinnosti', r'^transakce', r'^zákazníka', r'^kupujícího',  # Různé genitivy
        ]

        for field in ['supplier', 'customer', 'document_type']:
            value = extracted.get(field)
            if value:
                value_lower = str(value).lower().strip()
                # Check if value matches any forbidden pattern
                for pattern in forbidden_label_patterns:
                    if re.search(pattern, value_lower):
                        logger.warning(f"Sanity Check: Blokována extrakce štítku '{value}' jako {field}")
                        extracted[field] = None
                        break
                # Also check for very short values (likely not a company name)
                if extracted.get(field) and len(str(extracted[field])) < 3:
                    extracted[field] = None

        # Document type identification - only true if we have actual value
        has_doc_type = extracted.get('document_type') and str(extracted['document_type']).lower() not in ['null', 'none', 'n/a', '']
        elements['identification'] = bool(has_doc_type)

        # Subjects (supplier + customer) - only true if at least one has actual value
        has_supplier = extracted.get('supplier') and str(extracted['supplier']).lower() not in ['null', 'none', 'n/a', '']
        has_customer = extracted.get('customer') and str(extracted['customer']).lower() not in ['null', 'none', 'n/a', '']
        elements['subjects'] = bool(has_supplier or has_customer)

        # Financial (amount) - only true if we have actual amount
        has_amount = extracted.get('amount') and str(extracted['amount']).lower() not in ['null', 'none', 'n/a', '']
        elements['financial'] = bool(has_amount)

        # Dates - only true if we have actual date
        has_date = extracted.get('date') and str(extracted['date']).lower() not in ['null', 'none', 'n/a', '']
        elements['dates'] = bool(has_date)

        # Payment info - only true if we have actual payment info
        has_payment = extracted.get('payment_info') and str(extracted['payment_info']).lower() not in ['null', 'none', 'n/a', '']
        elements['payment_info'] = bool(has_payment)

        return result
    
    def _empty_result(self, reason: str) -> dict:
        return {
            'is_invoice': False,
            'confidence': 0.0,
            'elements_present': {
                'identification': False,
                'subjects': False,
                'dates': False,
                'financial': False,
                'payment_info': False
            },
            'extracted_values': {
                'document_type': None,
                'supplier': None,
                'customer': None,
                'amount': None,
                'date': None,
                'payment_info': None
            },
            'reasoning': reason
        }

    def _fallback_classification(self, text: str) -> dict:
        """
        Záchranná klasifikace když LLM selže.

        PŘÍSNÁ PRAVIDLA:
        - Musí být alespoň 2 pozitivní slova NEBO 1 pozitivní + částka
        - Nesmí být žádné negativní slovo
        """
        text_lower = text.lower()
        positive_keywords = self.fallback_positive
        negative_keywords = self.fallback_negative

        positive_count = sum(1 for kw in positive_keywords if kw in text_lower)
        negative_count = sum(1 for kw in negative_keywords if kw in text_lower)

        # Detekce částky - důležitý indikátor faktury
        has_amount = bool(re.search(r'(?i)(?:celkem\s*k\s*úhradě|celkemkuhrade|celkem|total|grand\s*total|amount\s*due|balance\s*due|k\s*úhradě|k\s*zaplacení|částka\s*:)[\s]*(\d+(?:[\s,.]\d+)*)', text))
        if not has_amount:
            has_amount = bool(re.search(r'(\d{1,3}(?:[\s,.]\d{3})*(?:,\d+)?)\s*(Kč|EUR|USD|CZK|GBP|€|\$|£)', text, re.IGNORECASE))

        # PŘÍSNĚJŠÍ PRAVIDLA:
        # 1. Musí být alespoň 2 pozitivní slova NEBO 1 pozitivní + částka
        # 2. Nesmí být žádné negativní slovo
        has_positive = positive_count >= 2 or (positive_count >= 1 and has_amount)
        is_invoice = has_positive and negative_count == 0

        confidence = min(0.7, 0.5 + (positive_count - negative_count) * 0.1) if has_positive else 0.3

        extracted_values = {k: None for k in ['document_type', 'supplier', 'customer', 'amount', 'date', 'payment_info']}

        date_match = re.search(r'(\d{1,2}[-./]\d{1,2}[-./]\d{2,4})', text)
        if date_match:
            extracted_values['date'] = date_match.group(0)

        amount_match = re.search(r'(?i)(?:celkem\s*k\s*úhradě|celkemkuhrade|celkem|total|grand\s*total|amount\s*due|balance\s*due|k\s*úhradě|k\s*zaplacení|částka\s*:)[\s]*(\d+(?:[\s,.]\d+)*)(?:\s*(Kč|EUR|USD|CZK|GBP|€|\$|£))?', text)
        if amount_match:
            num_part = amount_match.group(1).strip()
            curr_part = amount_match.group(2)
            extracted_values['amount'] = f"{num_part} {curr_part.strip()}" if curr_part else num_part
        else:
            amount_match = re.search(r'(\d{1,3}(?:[\s,.]\d{3})*(?:,\d+)?)\s*(Kč|EUR|USD|CZK|GBP|€|\$|£)', text, re.IGNORECASE)
            if amount_match:
                extracted_values['amount'] = f"{amount_match.group(1).strip()} {amount_match.group(2).strip()}"

        return {
            'is_invoice': is_invoice,
            'confidence': confidence,
            'elements_present': {
                'identification': any(x in text_lower for x in ['faktura', 'invoice', 'tax document']),
                'subjects': any(x in text_lower for x in ['dodavatel', 'odběratel', 'supplier', 'customer', 's.r.o.']),
                'dates': any(x in text_lower for x in ['datum', 'splatnost', 'date', 'due date']),
                'financial': any(x in text_lower for x in ['celkem', 'částka', 'total', 'amount']),
                'payment_info': any(x in text_lower for x in ['účet', 'iban', 'bank account'])
            },
            'extracted_values': extracted_values,
            'reasoning': f"Fallback: {positive_count} pozitiv, {negative_count} negativ, {'částka nalezena' if has_amount else 'žádná částka'}"
        }