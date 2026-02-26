"""
Classifier Agent
Binary invoice/non-invoice decision with confidence scoring
"""

from typing import Optional
import logging
import re
from .base_agent import BaseAgent

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
    
    CLASSIFICATION_PROMPT = """Jsi expertní systém pro klasifikaci dokumentů. Tvým JEDINÝM úkolem je na základě textového obsahu rozhodnout, zda se jedná o fakturu.

=== KRITICKÉ PRAVIDLO - CO NENÍ FAKTURA ===
Pokud text obsahuje životopis (CV, vzdělání, pracovní zkušenosti, dovednosti), smlouvu, nebo jiný nesouvisející text, MUSÍŠ vrátit "is_invoice": false a všechny hodnoty v extracted_values musí být null. NIKDY neoznačuj životopis jako fakturu.

=== KRITICKÉ PRAVIDLO - EXTRAKCE HODNOT ===
Pokud JE dokument skutečně faktura, tvým úkolem je EXTRAKOVAT konkrétní hodnoty z textu.
Všechny hodnoty v extracted_values MUSÍ být doslovně převzaty z analyzovaného textu.
NIKDY si nevymýšlej data. NIKDY nepoužívej hodnoty které nejsou v textu.

=== POSTUP ANALÝZY ===
1. Přečti si CELÝ text dokumentu od začátku až do úplného konce. Zhodnoť, o jaký typ dokumentu jde.
2. Pokud to není faktura (např. životopis), rovnou vrať is_invoice: false a null hodnoty.
3. Pokud je to faktura, hledej skutečné hodnoty v textu:
   - Najdi název dodavatele (firma, jméno, Vendor, Supplier, From).
   - Najdi název odběratele (komu je faktura určena, Customer, Bill To, Client).
   - Najdi KONEČNOU ČÁSTKU K ÚHRADĚ. Hledej slova jako: "CELKEM K ÚHRADĚ", "CELKEMKUHRADE", "Total", "Grand Total", "Amount Due", "Částka:".
     DŮLEŽITÉ: Vezmi číslo u těchto slov (např. "1,00" nebo "2576 Kč").
     ABSOLUTNÍ ZÁKAZ: NIKDY neextrahuj adresu, PSČ nebo spojená slova typu "14000Praha4"! Částka NIKDY neobsahuje název města!
   - Najdi datum (jakýkoliv formát, Date, Issue Date).
4. Extrahuj NALEZENÉ hodnoty DO extracted_values.
5. Pokud hodnotu nenajdeš → vrať null.

=== PRAVIDLA PRO REASONING ===
- Reasoning MUSÍ obsahovat rozbor textu - co jsi konkrétně našel a proč to je/není faktura.
- Příklad pro fakturu: "Nalezeno: Dodavatel=[NAZEV], Odběratel=[NAZEV], Celková částka=[CASTKA]"
- Pokud něco chybí, napiš: "Chybí: [co chybí]"

=== PRAVIDLA PRO EXTRACTED_VALUES ===
Každé pole MUSÍ obsahovat doslovnou hodnotu z textu nebo null:

- "document_type": Hlavní název dokumentu z textu (co vidíš)
- "supplier": Název dodavatele z textu (firma která vystavila)
- "customer": Název odběratele z textu (komu je faktura)
- "amount": POUZE konečná celková částka k úhradě. ZÁKAZ: Nesmí to být PSČ ani adresa (např. "14000Praha4")!
- "date": Datum z textu (jakýkoliv formát který vidíš)
- "payment_info": Bankovní účet nebo IBAN z textu

=== FORMAT ODPOVĚDI ===
Odpovídej POUZE platným JSON. Žádný text před ani za JSON.
Všechny hodnoty MUSÍ být z textu výše. Pokud tam není → null.

{{
  "reasoning": "Rozbor textu - co vidím...",
  "elements_present": {{
    "document_type_identified": true/false,
    "supplier_and_buyer_present": true/false,
    "total_amount_present": true/false,
    "date_present": true/false,
    "payment_instructions_present": true/false
  }},
  "extracted_values": {{
    "document_type": "hodnota z textu nebo null",
    "supplier": "hodnota z textu nebo null",
    "customer": "hodnota z textu nebo null",
    "amount": "hodnota z textu nebo null",
    "date": "hodnota z textu nebo null",
    "payment_info": "hodnota z textu nebo null"
  }},
  "confidence": 0.0-1.0,
  "is_invoice": true/false
}}

=== TEXT DOKUMENTU ===
{input_data}"""

    # Strong negative indicators - if found, immediately reject as non-invoice
    STRONG_NEGATIVE_INDICATORS = [
        'plná moc', 'plnomocenství',  
        'dluhopis', 'cenný papír', 'akcie', 'kmenový list', 'směnka',
        'životopis', 'životopisy', 'curriculum vitae', 'resume', 
        'pracovní zkušenosti', 'vzdělání', 'dovednosti', 'jazykové znalosti',
        'work experience', 'education', 'skills',
        'certifikát o absolvování', 'diplom', 'osvědčení o absolvování',  
        'cenová nabídka', 'nabídka ceny', 'rozpočet',
        'upomínka', 'reminder', 'výzva k úhradě',
    ]

    # Context-dependent indicators - only reject if found with specific context
    CONTEXT_NEGATIVE_INDICATORS = {
        'narozen': ['rodné číslo', 'datum narození', 'narozen v'],
        'born': ['birth date', 'date of birth', 'born in'],
        'smlouva': ['fakturujeme za', 'faktura za', 'úhrada za', 'platba za'],
        'certifikát': ['fakturujeme za', 'faktura za', 'úhrada za', 'platba za'],
        'licence': ['fakturujeme za', 'faktura za', 'úhrada za', 'platba za'],
        'objednávka': ['faktura za', 'úhrada za', 'platba za', 'fakturujeme za', 'daňový doklad'],
        'purchase order': ['invoice for', 'payment for', 'billing for'],
    }

    def __init__(self, model: str = "llama3.2", timeout: int = 30, vram_limit_gb: int = None, num_ctx: int = None):
        super().__init__(model, timeout, vram_limit_gb, num_ctx)

    def _check_strong_negatives(self, text: str) -> Optional[dict]:
        """Check for strong negative indicators and reject immediately if found."""
        text_lower = text.lower()

        for indicator in self.STRONG_NEGATIVE_INDICATORS:
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

        for indicator, invoice_contexts in self.CONTEXT_NEGATIVE_INDICATORS.items():
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

    def analyze(self, text: str, metadata: Optional[dict] = None) -> dict:
        """
        Analyze document and classify as invoice or non-invoice.
        """
        if not text or len(text.strip()) < 50:
            return self._empty_result("Prázdný nebo velmi krátký text")

        strong_reject = self._check_strong_negatives(text)
        if strong_reject:
            return strong_reject

        truncated = self._truncate_text(text, max_chars=8000)
        
        try:
            prompt = self.CLASSIFICATION_PROMPT.format(input_data=truncated)
        except (KeyError, IndexError) as e:
            logger.warning(f"Prompt format error: {e}")
            return self._fallback_classification(text)
        
        try:
            client = self._get_client()
            response = client.generate(
                model=self.model,
                prompt=prompt,
                format="json",
                options={
                    "temperature": 0.0,    
                    "num_predict": 1024,    
                    "num_ctx": self.num_ctx,
                    "top_p": 0.1,           
                    "repeat_penalty": 1.1,  
                },
                keep_alive="0s"  
            )
            
            raw_output = response.get("response", "")
            logger.debug(f"Raw classifier output: {raw_output[:500]}...")
            
            parsed = self._extract_json_from_text(raw_output)
            
            if parsed is None:
                logger.warning(f"Classifier nevrátil JSON: {raw_output[:200]}")
                return self._fallback_classification(text)
            
            if 'is_invoice' not in parsed:
                logger.warning(f"Classifier chybí is_invoice: {parsed.keys()}")
                return self._fallback_classification(text)
            
            # Normalizace is_invoice
            is_invoice_val = parsed.get('is_invoice')
            if isinstance(is_invoice_val, bool):
                parsed['is_invoice'] = is_invoice_val
            elif isinstance(is_invoice_val, str):
                parsed['is_invoice'] = is_invoice_val.lower() in ['true', 'ano', 'yes', '1']
            elif isinstance(is_invoice_val, (int, float)):
                parsed['is_invoice'] = bool(is_invoice_val)
            else:
                parsed['is_invoice'] = False
            
            if not self.validate_response(parsed, ['is_invoice', 'confidence']):
                logger.warning(f"Classifier chybějící pole: {parsed.keys()}")
                return self._fallback_classification(text)
            
            confidence = parsed.get('confidence', 50)
            if isinstance(confidence, str):
                try:
                    confidence = float(confidence)
                except ValueError:
                    confidence = 50
            if confidence > 1:
                confidence = confidence / 100.0
            parsed['confidence'] = min(1.0, max(0.0, confidence))

            if 'extracted_values' not in parsed:
                parsed['extracted_values'] = {}

            extracted = parsed.get('extracted_values', {})
            reasoning = parsed.get('reasoning', '').lower()
            elements = parsed.get('elements_present', {})

            # --- OCHRANA PROTI PSČ (Sanity check pro částku) ---
            if extracted.get('amount'):
                amt_str = str(extracted['amount']).lower().replace(" ", "")
                # Pokud částka vypadá jako 4-5 čísel a hned za tím písmena (např. 14000praha), vymaž ji
                if re.search(r'\d{4,5}[a-z]', amt_str) or 'praha' in amt_str or 'brno' in amt_str:
                    logger.warning(f"Sanity Check: Model zkusil jako částku podstrčit PSČ/Adresu '{extracted['amount']}'. Nuluji.")
                    extracted['amount'] = None

            # --- TVRDÁ REGEX ZÁCHRANA PŘÍMO ZDE ---
            # Pokud po kontrole nahoře (nebo od LLM) chybí částka, najdeme ji my ručně přes Python
            if not extracted.get('amount'):
                # Hledáme všechny OCR varianty: s mezerami i bez
                regex_match = re.search(r'(?i)(?:celkem\s*k\s*úhradě|celkemkuhrade|celkem|total|grand\s*total|amount\s*due|k\s*úhradě|k\s*zaplacení|částka\s*:)[\s]*(\d+(?:[\s,.]\d+)*)(?:\s*(Kč|EUR|USD|CZK|GBP|€|\$|£))?', text)
                if regex_match:
                    num_part = regex_match.group(1).strip()
                    curr_part = regex_match.group(2)
                    extracted['amount'] = f"{num_part} {curr_part.strip()}" if curr_part else num_part
                    logger.info(f"Regex Záchrana: Částka úspěšně nalezena Pythonem: {extracted['amount']}")

            # Fallback extrakce z reasoning pro ostatní proměnné
            if elements.get('supplier_and_buyer_present') and not extracted.get('supplier'):
                supplier_match = re.search(r'dodavatel[=:]\s*([^\n,]+)', reasoning)
                if supplier_match:
                    extracted['supplier'] = supplier_match.group(1).strip()

            if elements.get('supplier_and_buyer_present') and not extracted.get('customer'):
                customer_match = re.search(r'odběratel[=:]\s*([^\n,]+)', reasoning)
                if customer_match:
                    extracted['customer'] = customer_match.group(1).strip()

            if elements.get('date_present') and not extracted.get('date'):
                date_match = re.search(r'datum[=:]\s*([^\n,]+)', reasoning)
                if date_match:
                    extracted['date'] = date_match.group(1).strip()

            # Anti-halucinační kontrola (hodnota musí být v textu)
            for field in ['supplier', 'customer', 'amount', 'date', 'document_type', 'payment_info']:
                value = extracted.get(field)
                if value:
                    value_str = str(value)
                    value_lower = value_str.lower().strip()
                    
                    if not value_str or value_lower in ['', 'null', 'none']:
                        extracted[field] = None
                        continue
                    
                    is_in_text = value_lower in text.lower()
                    
                    if not is_in_text and field == 'amount':
                        number_match = re.search(r'[\d\s,.]+', value_str)
                        if number_match:
                            number = number_match.group().strip()
                            if number in text:
                                is_in_text = True
                    
                    if not is_in_text and field == 'document_type':
                        if any(word in text.lower() for word in ['invoice', 'faktura', 'bill']):
                            is_in_text = True
                    
                    if not is_in_text:
                        logger.debug(f"  ⚠️ HALUCINACE: {field}='{value}' není v textu → null")
                        extracted[field] = None

            parsed['extracted_values'] = extracted
            
            if parsed.get('extracted_values'):
                logger.debug(f"  Extracted values: {parsed['extracted_values']}")

            # --- SANITY CHECK PRO FAKTURU ---
            if parsed.get('is_invoice'):
                text_lower_check = text.lower()
                invoice_mandatory_words = ['faktura', 'invoice', 'daňový doklad', 'účtenka', 'bill', 'receipt']
                
                has_invoice_word = any(word in text_lower_check for word in invoice_mandatory_words)
                
                if not has_invoice_word:
                    logger.warning("Sanity Check: Model označil text jako fakturu, ale chybí klíčová slova! Přepisuji na False.")
                    parsed['is_invoice'] = False
                    parsed['confidence'] = 0.9
                    parsed['reasoning'] = "Systémová korekce: Dokument neobsahuje slovo 'faktura' nebo podobné. LLM halucinovalo."
                    
                    for key in parsed['extracted_values']:
                        parsed['extracted_values'][key] = None
            # --- KONEC SANITY CHECKU ---

            logger.debug(f"✓ Klasifikace: {'Faktura' if parsed['is_invoice'] else 'Není faktura'} (jistota: {parsed['confidence']:.0%})")

            return parsed
            
        except Exception as e:
            logger.error(f"Classifier chyba: {e}")
            return self._empty_result(f"Chyba analýzy: {str(e)}")
    
    def _empty_result(self, reason: str) -> dict:
        """Return empty classification result."""
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
        """Fallback rule-based classification when AI fails."""
        text_lower = text.lower()

        positive_keywords = [
            'faktura', 'daňový doklad', 'celkem k úhradě',
            'invoice', 'tax document', 'total amount', 'total due', 'balance due'
        ]
        negative_keywords = [
            'upomínka', 'smlouva', 'nabídka', 'životopis', 'certifikát',
            'plná moc', 'plnomocenství', 'dluhopis', 'cenný papír', 'akcie',
            'osvědčení', 'licence', 'absolvoval', 'kurz', 'školení'
        ]

        positive_count = sum(1 for kw in positive_keywords if kw in text_lower)
        negative_count = sum(1 for kw in negative_keywords if kw in text_lower)

        is_invoice = positive_count > 0 and negative_count == 0
        confidence = min(0.7, 0.5 + (positive_count - negative_count) * 0.1)

        extracted_values = {
            'document_type': None,
            'supplier': None,
            'customer': None,
            'amount': None,
            'date': None,
            'payment_info': None
        }

        date_match = re.search(r'(\d{1,2}[-./]\d{1,2}[-./]\d{2,4})', text)
        if date_match:
            extracted_values['date'] = date_match.group(0)
        
        if not extracted_values['date']:
            date_label_match = re.search(r'date[:\s]+([^\n]+)', text_lower)
            if date_label_match:
                extracted_values['date'] = date_label_match.group(1).strip()

        # SUPER-REGEX pro částku: chytá OCR chyby (celkemkuhrade) i slovo Částka:
        amount_match = re.search(r'(?i)(?:celkem\s*k\s*úhradě|celkemkuhrade|celkem|total|grand\s*total|amount\s*due|balance\s*due|k\s*úhradě|k\s*zaplacení|částka\s*:)[\s]*(\d+(?:[\s,.]\d+)*)(?:\s*(Kč|EUR|USD|CZK|GBP|€|\$|£))?', text)
        
        if amount_match:
            num_part = amount_match.group(1).strip()
            curr_part = amount_match.group(2)
            if curr_part:
                extracted_values['amount'] = f"{num_part} {curr_part.strip()}"
            else:
                extracted_values['amount'] = num_part
        else:
            amount_match = re.search(r'(\d{1,3}(?:[\s,.]\d{3})*(?:,\d+)?)\s*(Kč|EUR|USD|CZK|GBP|€|\$|£)', text, re.IGNORECASE)
            if amount_match:
                extracted_values['amount'] = f"{amount_match.group(1).strip()} {amount_match.group(2).strip()}"

        return {
            'is_invoice': is_invoice,
            'confidence': confidence,
            'elements_present': {
                'identification': any(x in text_lower for x in ['faktura', 'invoice', 'tax document']),
                'subjects': any(x in text_lower for x in [
                    'dodavatel', 'odběratel', 'objednatel',  
                    'supplier', 'vendor', 'customer', 'bill to', 'ship to',  
                    's.r.o.', 'a.s.', 'gmbh', 'ltd.', 'inc.', 'llc', 'b.v.', 'n.v.',  
                    'ust-id', 'vat id', 'tax id', 'ico', 'dic'  
                ]),
                'dates': any(x in text_lower for x in [
                    'datum', 'splatnost', 'vystaveno',  
                    'date', 'issued', 'due date', 'invoice date'  
                ]),
                'financial': any(x in text_lower for x in [
                    'celkem', 'částka', 'k úhradě',  
                    'total', 'amount', 'price', 'subtotal', 'balance'  
                ]),
                'payment_info': any(x in text_lower for x in [
                    'účet', 'iban', 'bic', 'swift',  
                    'bank account', 'account no', 'payment reference'  
                ])
            },
            'extracted_values': extracted_values,
            'reasoning': f"Pravidla: {positive_count} pozitiv, {negative_count} negativ"
        }