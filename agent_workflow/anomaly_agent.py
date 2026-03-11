"""
Anomaly Detector Agent
Detect non-invoice documents (CVs, certificates, contracts, etc.)

v7.1 (2026-03-04):
- ARCHITECTURAL CHANGE: Agents receive ONLY Markdown data, NO raw text!
- MARKDOWN-FIRST: Přepsáno z JSON formátu na Markdown (kompatibilita s BaseAgent v7)
- Agents work exclusively with:
  1. Markdown table (structured data from text_blocks)
  2. Master Instruction (fixed reference framework for validation)
- Odstraněny legacy JSON parsery
"""

from typing import Optional
import json  # Ponecháno pouze pro načítání lokálních config souborů (není pro LLM)
import logging
import re
from pathlib import Path
from .base_agent import BaseAgent

# Import security sanitizer
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent.parent))
    from core.security import sanitize_llm_output
    HAS_SECURITY = True
except ImportError:
    HAS_SECURITY = False

logger = logging.getLogger(__name__)


class AnomalyDetectorAgent(BaseAgent):
    """
    Specialized agent for detecting non-invoice documents.
    
    Detects:
    - CVs and resumes (CZ + EN)
    - Certificates and licenses (CZ + EN)
    - Specific Contracts and agreements
    - Reminders and offers
    - Inquiries and requests
    - Internal documents
    """
    
    DETECTION_PROMPT = """Jsi specialista na detekci anomálií v dokumentech. Tvým úkolem je identifikovat dokumenty, které NENÍ faktura.

=== HLAVNÍ ZDROJ DAT: MASTER INSTRUCTION ===
Vždy PŘEDNOSTNĚ používej sekci "🎯 PLNÝ TEXT PODLE SOUŘADNIC (Master Instruction)".
Obsahuje text seřazený do logických bloků s označením pozice [vlevo] a [vpravo].

=== TYPY ANOMÁLIÍ ===
1. cv_resume: životopis (curriculum vitae, resume, vzdělání, praxe, dovednosti).
2. certificate: diplom, osvědčení, certifikát.
3. contract: smlouva (pracovní, nájemní). POZOR: fakturace "dle smlouvy" je OK.
4. reminder: upomínka (výzva k úhradě).
5. offer: cenová nabídka, kalkulace, rozpočet.
6. inquiry: poptávka.
7. internal: koncept, draft.
8. research_report: výzkumná zpráva, strategický dokument.
9. logistics_report: seznam palet, nákladní list (bez cen).

=== PRAVIDLA PRO ROZHODOVÁNÍ ===
⚠️ DŮLEŽITÉ: Pokud dokument obsahuje invoice keywords → NENÍ to anomálie!

INVOICE KEYWORDS (pokud jsou nalezeny → is_anomaly = FALSE):
- "faktura", "invoice", "daňový doklad"
- "total", "celkem", "amount", "částka", "cena", "price"
- "vat", "dpH", "tax", "daň"
- "net", "subtotal", "mezisoučet"
- "supplier", "dodavatel", "customer", "odběratel"
- "iban", "bic", "bank account", "účet"
- "invoice number", "číslo faktury"

PRAVIDLA:
1. Pokud vidíš invoice keywords → is_anomaly = FALSE, anomaly_type = null
2. Pokud vidíš invoice keywords A classifier zamítl → refutes_classifier = TRUE
3. Pokud dokument má částky, dodavatele, odběratele → is_anomaly = FALSE
4. Používej POUZE platné typy anomálií ze seznamu výše (žádné "life_story"!)

=== PRAVIDLA A FORMÁT (STRIKTNÍ MARKDOWN) ===
- Pokud dokument vypadá jako běžná faktura (obsahuje částky, dodavatele, odběratele) → Nejde o anomálii.
- ODPOVÍDEJ POUZE VE FORMÁTU MARKDOWN.
- **ZÁKAZ JSONu:** Naprostý zákaz použití složených závorek {{}} nebo formátu JSON (včetně reasoning).
- **DŮLEŽITÉ:** Pokud do textu potřebuješ napsat znak svislítka `|`, MUSÍŠ ho zapsat s lomítkem jako `\\|`.
- **ZÁKAZ KONTRADIKCÍ:** is_anomaly a anomaly_type musí být konzistentní!

Použij PŘESNĚ následující strukturu (neměň nadpisy):

## 🚨 Detekce anomálií
**Je anomálie:** ✅ ANO / ❌ NE
**Typ anomálie:** [vlož jeden z typů z nabídky nahoře nebo null]
**Confidence:** 0.85
**Vyvrácení Classifieru (Nalezena Faktura):** ✅ ANO / ❌ NE

## 🔑 Nalezená klíčová slova
- [slovo 1, pokud žádné není, napiš "žádné"]
- [slovo 2]

## ⚠️ Varovné signály (Flags)
- [signál 1, pokud žádné nejsou, napiš "žádné"]

## 🧠 Reasoning
[vysvětlení proč to je/není anomálie na základě Master Instrukce a proč případně vyvracíš rozhodnutí Classifieru]

=== STRUKTURA VSTUPU ===
0. 🤖 ROZHODNUTÍ CLASSIFIERU: Informace o tom, zda Classifier zamítl dokument. Tvým úkolem je ověřit, zda se nemýlil! (Vyvrácení = ANO, pokud tu vidíš fakturu)
1. 🎯 PLNÝ TEXT PODLE SOUŘADNIC (Master Instruction): Tvůj HLAVNÍ zdroj.
2. 📋 Tabulka textových bloků: Přehled všech částí.
3. 📜 VIZUALIZACE DOKUMENTU: Grafický náhled.
4. SUROVÝ TEXT: Surová data dokumentu.

=== TEXT DOKUMENTU ===
{input_data}"""

    def __init__(self, model: str = "llama3.2", timeout: int = 30, patterns_file: Optional[str] = None, vram_limit_gb: int = None, num_ctx: int = None):
        super().__init__(model, timeout, vram_limit_gb, num_ctx)

        # Load patterns EXCLUSIVELY from config/rules.yaml
        # NO hardcoded fallbacks - rules.yaml is the single source of truth
        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from config_loader import get_config
            config = get_config()
            self.patterns = config.get_anomaly_rules()
            logger.info("✓ Anomaly patterns loaded from config/rules.yaml")
        except Exception as e:
            logger.error(f"⚠️ Config loader failed: {e}. Using EMPTY patterns - anomaly detection will be disabled!")
            logger.error("⚠️ To enable anomaly detection, ensure config/rules.yaml exists and is valid.")
            self.patterns = {}  # NO fallback patterns - force rules.yaml usage

    def analyze(self, markdown_input: str, metadata: Optional[dict] = None, master_instruction: Optional[str] = None) -> dict:
        """
        Analyzuje dokument pro detekci anomálií.

        ARCHITEKTURA: Agent dostává POUZE strukturovaná data:
        - markdown_input: Markdown tabulka + layout (žádný surový text!)
        - master_instruction: Pevný referenční rámec pro validaci

        Args:
            markdown_input: Strukturovaná Markdown data (tabulka + layout)
            metadata: Dodatečná metadata (obsahuje text_blocks pro prostorovou analýzu)
            master_instruction: Pevná instrukce pro validaci
        """
        # First, do fast rule-based detection (na Markdown datech)
        rule_result = self._rule_based_detection(markdown_input, metadata)

        # If rule-based found clear anomaly with high confidence, return immediately
        if rule_result.get('confidence', 0) > 0.9 and rule_result.get('is_anomaly'):
            logger.debug(f"✓ Anomalie detekována pravidly: {rule_result['anomaly_type']}")
            return rule_result

        # Inteligentní krácení textu - zachovat důležité Markdown sekce
        truncated = self._truncate_text_smart(markdown_input, max_chars=8000)

        try:
            master_instruction_section = f"=== MASTER INSTRUCTION (IMPORTANT) ===\n{master_instruction}\n\n" if master_instruction else ""
            
            classifier_info = ""
            if metadata and "classifier_is_invoice" in metadata:
                is_inv = "ANO" if metadata["classifier_is_invoice"] else "NE"
                reasoning = metadata.get("classifier_reasoning", "Neznámý")
                classifier_info = f"0. 🤖 ROZHODNUTÍ CLASSIFIERU:\nRozhodnutí (Je faktura?): {is_inv}\nDůvod klasifikátoru: {reasoning}\n\nToto rozhodnutí zkontroluj. Pokud Classifier zamítl fakturu (NE), ale ty v tabulce vidíš jasné prvky faktury, musíš ho vyvrátit (Vyvrácení Classifieru: ANO).\n\n"
            
            prompt = self.DETECTION_PROMPT.replace(
                "=== STRUKTURA VSTUPU ===",
                master_instruction_section + classifier_info + "=== STRUKTURA VSTUPU ==="
            ).format(input_data=truncated)
        except (KeyError, IndexError) as e:
            logger.warning(f"Prompt format error: {e}")
            return rule_result
        
        try:
            client = self._get_client()
            response = client.generate(
                model=self.model,
                prompt=prompt,
                options={
                    "temperature": 0.0,
                    "num_predict": 512,
                    "num_ctx": self.num_ctx,
                    "top_p": 0.1,
                    "repeat_penalty": 1.1,
                },
                keep_alive="0s"
            )
            
            raw_output = response.get("response", "")
            
            # v7.0: Volání vlastního parseru místo starého JSONu
            parsed = self._parse_markdown_output(raw_output)
            
            if parsed is None:
                logger.warning(f"Anomaly detector nevrátil platný Markdown: {raw_output[:150]}...")
                return rule_result
            
            # Merge with rule-based results
            result = self._merge_results(rule_result, parsed)

            # 🔧 FIX: Auto-set refutes_classifier when invoice elements are detected
            # but classifier rejected the document. This prevents false rejections when
            # the classifier is confused but extracted values exist.
            if metadata and "classifier_is_invoice" in metadata:
                classifier_rejected = not metadata["classifier_is_invoice"]

                # Check if anomaly agent detected invoice-related keywords
                invoice_keywords = ['faktura', 'invoice', 'dodavatel', 'odběratel', 'supplier',
                                   'customer', 'částka', 'amount', 'total', 'iban', 'účet']
                detected_kw = result.get('detected_keywords', [])
                reasoning = result.get('reasoning', '').lower()

                has_invoice_keyword = any(kw in str(detected_kw).lower() for kw in invoice_keywords)
                mentions_invoice_elements = any(kw in reasoning for kw in invoice_keywords)

                # If classifier rejected but we found invoice elements, MUST refute
                if classifier_rejected and (has_invoice_keyword or mentions_invoice_elements):
                    if not result.get('refutes_classifier'):
                        logger.warning(f"  🔧 FIX: Auto-setting refutes_classifier=true (invoice elements detected)")
                        result['refutes_classifier'] = True
                        result['reasoning'] += " | Automaticky vyvráceno: detekovány prvky faktury (částky, dodavatel, odběratel)."

            logger.debug(f"✓ Anomalie: {'Detekována' if result['is_anomaly'] else 'Nedetektována'} ({result.get('anomaly_type', 'N/A')})")

            return result
            
        except Exception as e:
            logger.error(f"Anomaly detector chyba: {e}")
            return rule_result

    def _parse_markdown_output(self, markdown_text: str) -> dict:
        """
        Rozebere Markdown výstup z AI modelu a převede ho na standardizovaný slovník.
        """
        if not markdown_text or not markdown_text.strip():
            return None

        result = {
            'is_anomaly': False,
            'anomaly_type': None,
            'confidence': 0.0,
            'detected_keywords': [],
            'flags': [],
            'reasoning': '',
            'refutes_classifier': False
        }

        # 1. Je anomálie
        is_anomaly_match = re.search(r'\*\*Je anomálie:\*\*\s*(✅ ANO|❌ NE)', markdown_text, re.IGNORECASE)
        if is_anomaly_match:
            result['is_anomaly'] = 'ANO' in is_anomaly_match.group(1).upper()

        # 1.5 Vyvrácení Classifieru
        refutes_match = re.search(r'\*\*Vyvrácení Classifieru.*:\*\*\s*(✅ ANO|❌ NE)', markdown_text, re.IGNORECASE)
        if refutes_match:
            result['refutes_classifier'] = 'ANO' in refutes_match.group(1).upper()

        # 2. Typ anomálie
        type_match = re.search(r'\*\*Typ anomálie:\*\*\s*([a-zA-Z_]+)', markdown_text)
        if type_match:
            val = type_match.group(1).lower()
            if val not in ['null', 'none', 'n/a']:
                result['anomaly_type'] = val

        # 3. Confidence
        conf_match = re.search(r'\*\*Confidence:\*\*\s*([\d.]+)', markdown_text)
        if conf_match:
            try:
                result['confidence'] = float(conf_match.group(1))
            except ValueError:
                pass

        # 4. Klíčová slova
        if "## 🔑 Nalezená klíčová slova" in markdown_text:
            kw_part = markdown_text.split("## 🔑 Nalezená klíčová slova")[1]
            kw_section = kw_part.split("## ")[0]
            words = re.findall(r'[-•]\s*(.+)', kw_section)
            result['detected_keywords'] = [w.strip() for w in words if w.strip() and "žádné" not in w.lower()]

        # 5. Flags
        if "## ⚠️ Varovné signály (Flags)" in markdown_text:
            flags_part = markdown_text.split("## ⚠️ Varovné signály (Flags)")[1]
            flags_section = flags_part.split("## ")[0]
            flags = re.findall(r'[-•]\s*(.+)', flags_section)
            result['flags'] = [f.strip() for f in flags if f.strip() and "žádné" not in w.lower()]

        # 6. Reasoning
        if "## 🧠 Reasoning" in markdown_text:
            raw_reasoning = markdown_text.split("## 🧠 Reasoning", 1)[1].strip()
            # 🔒 SECURITY: Sanitize reasoning to prevent prompt injection
            if HAS_SECURITY:
                raw_reasoning = sanitize_llm_output(raw_reasoning)
            result['reasoning'] = raw_reasoning

        # 🔒 SANITY CHECK: Blokování kontradikcí a neplatných hodnot
        # 1. is_anomaly a anomaly_type musí být konzistentní
        if result['is_anomaly'] and not result['anomaly_type']:
            logger.warning(f"Sanity Check: is_anomaly=true ale anomaly_type=null - nastavuji na 'unknown'")
            result['anomaly_type'] = 'unknown'

        if not result['is_anomaly'] and result['anomaly_type']:
            logger.warning(f"Sanity Check: is_anomaly=false ale anomaly_type={result['anomaly_type']} - nulování typu")
            result['anomaly_type'] = None

        # 2. Blokování neplatných typů anomálií
        valid_anomaly_types = ['cv_resume', 'certificate', 'contract', 'reminder', 'offer',
                               'inquiry', 'internal', 'research_report', 'logistics_report', 'unknown']
        if result['anomaly_type'] and result['anomaly_type'] not in valid_anomaly_types:
            logger.warning(f"Sanity Check: Neplatný typ anomálie '{result['anomaly_type']}' - blokováno")
            result['anomaly_type'] = None

        # 3. Pokud jsou detekovány invoice keywords → is_anomaly musí být FALSE
        invoice_keywords = ['faktura', 'invoice', 'total', 'amount', 'vat', 'net', 'subtotal',
                           'supplier', 'customer', 'iban', 'bic']
        detected_kw_lower = [kw.lower() for kw in result.get('detected_keywords', [])]
        has_invoice_kw = any(kw in detected_kw_lower for kw in invoice_keywords)

        if has_invoice_kw and result['is_anomaly']:
            logger.warning(f"Sanity Check: Detekovány invoice keywords ale is_anomaly=true - přepis na FALSE")
            result['is_anomaly'] = False
            result['anomaly_type'] = None
            result['reasoning'] += " | Opraveno: invoice keywords detekovány → není anomálie."

        # 4. Fallback kontrola, zda se nepovedlo najít aspoň bool
        if 'is_anomaly' not in result and result['confidence'] == 0.0:
            return None

        return result

    def _rule_based_detection(self, text: str, metadata: Optional[dict] = None) -> dict:
        """Fast rule-based anomaly detection."""
        text_lower = text.lower()

        detected_anomalies = []
        all_keywords = []

        for anomaly_type, config in self.patterns.items():
            keywords = config.get('keywords', [])
            threshold = config.get('threshold', 2)

            found_keywords = [kw for kw in keywords if kw in text_lower]

            if len(found_keywords) >= threshold:
                detected_anomalies.append({
                    'type': anomaly_type,
                    'keywords': found_keywords,
                    'count': len(found_keywords),
                    'threshold': threshold,
                    'veto': config.get('veto', False)
                })
                all_keywords.extend(found_keywords)

        if not detected_anomalies:
            return {
                'is_anomaly': False,
                'anomaly_type': None,
                'confidence': 0.95,
                'detected_keywords': [],
                'flags': [],
                'reasoning': 'Žádné anomálie detekovány pravidly'
            }

        # Sort by severity (veto types first)
        detected_anomalies.sort(key=lambda x: (x['veto'], x['count']), reverse=True)
        primary = detected_anomalies[0]

        # Calculate confidence
        base_confidence = 0.7
        keyword_bonus = min(0.25, (primary['count'] - primary['threshold'] + 1) * 0.05)
        confidence = base_confidence + keyword_bonus

        return {
            'is_anomaly': True,
            'anomaly_type': primary['type'],
            'confidence': confidence,
            'detected_keywords': list(set(all_keywords)),
            'flags': ['veto'] if primary.get('veto') else [],
            'reasoning': f"Nalezeno {primary['count']} keyword pro {primary['type']}"
        }
    
    def _merge_results(self, rule_result: dict, ai_result: dict) -> dict:
        """Merge rule-based and AI results."""
        if rule_result['is_anomaly'] and ai_result.get('is_anomaly'):
            confidence = max(rule_result['confidence'], ai_result.get('confidence', 0))
            return {
                **ai_result,
                'confidence': confidence,
                'detected_keywords': list(set(
                    rule_result.get('detected_keywords', []) + 
                    ai_result.get('detected_keywords', [])
                ))
            }
        
        if rule_result['is_anomaly'] and not ai_result.get('is_anomaly'):
            if rule_result['confidence'] > 0.85:
                return rule_result
            ai_result['confidence'] = ai_result.get('confidence', 0.5) * 0.8
            return ai_result
        
        if ai_result.get('is_anomaly') and not rule_result['is_anomaly']:
            ai_result['confidence'] = ai_result.get('confidence', 0.5) * 0.7
            return ai_result
        
        return {
            'is_anomaly': False,
            'anomaly_type': None,
            'confidence': 0.95,
            'detected_keywords': [],
            'flags': [],
            'reasoning': 'Žádné anomálie detekovány (shoda)'
        }