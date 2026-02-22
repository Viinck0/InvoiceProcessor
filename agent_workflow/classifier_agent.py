"""
Classifier Agent
Binary invoice/non-invoice decision with confidence scoring
"""

from typing import Optional
import logging
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
    
    CLASSIFICATION_PROMPT = """Jsi expertní systém pro klasifikaci dokumentů. Tvým JEDINÝM úkolem je rozhodnout, zda se jedná o fakturu.

=== DEFINICE FAKTURY ===
Faktura je OBCHODNÍ DOKLAD, který MUSÍ obsahovat VŠECHNY tyto elementy:
1. Identifikace dokladu: "Faktura", "Daňový doklad", "Invoice", "Proforma"
2. Subjekty: DODAVATEL + ODBĚRATEL + IČO/DIČ
3. Časové údaje: Datum vystavení + Datum splatnosti
4. Finanční jádro: Položky + Cena bez DPH + DPH + CELKEM K ÚHRADĚ (ČÁSTKA MUSÍ BÝT ČÍSLO)
5. Platební instrukce: Číslo účtu/IBAN + Variabilní symbol

=== DŮLEŽITÉ PRAVIDLO ===
Pokud dokument NEMÁ všechny elementy, NENÍ to faktura!

=== CO NENÍ FAKTURA ===
- Certifikáty, osvědčení, licence - obsahují jméno, kurz, datum absolvování
- Životopisy - obsahují vzdělání, praxe, dovednosti
- Upomínky, reminder, výzvy k úhradě
- Smlouvy, contracts, dohody, dodatky
- Nabídky, offers, cenové kalkulace, rozpočty
- Objednávky, purchase orders
- E-maily, dopisy, korespondence

=== ROZPOZNÁVÁNÍ NE-FAKTUR ===
Pokud text obsahuje tyto vzorce, pravděpodobně NENÍ faktura:
- "certifikát", "osvědčení", "licence", "absolvoval", "kurz", "školení"
- "životopis", "CV", "curriculum", "vzdělání", "praxe"
- "narozen", "rodné číslo" (u osoby, ne firmy)
- "pracovní pozice", "popis práce", "reference"

=== PRAVIDLA ===
- Pokud chybí ANY z 5 elementů → NENÍ faktura
- Pokud neobsahuje ČÁSTKU (číslo) → NENÍ faktura
- Pokud obsahuje "certifikát", "osvědčení", "absolvoval" → NENÍ faktura
- Pokud si nejsi jistý → nastav is_invoice=false

=== FORMÁT VÝSTUPU ===
Odpovídej POUZE ve formátu JSON:
{{
  "is_invoice": true/false,
  "confidence": 0.0-1.0,
  "elements_present": {{
    "identification": true/false,
    "subjects": true/false,
    "dates": true/false,
    "financial": true/false,
    "payment_info": true/false
  }},
  "reasoning": "stručné vysvětlení které elementy jsou přítomny"
}}

=== TEXT DOKUMENTU ===
{input_data}"""

    def __init__(self, model: str = "llama3.2", timeout: int = 30):
        super().__init__(model, timeout)
    
    def analyze(self, text: str, metadata: Optional[dict] = None) -> dict:
        """
        Analyze document and classify as invoice or non-invoice.
        
        Args:
            text: Extracted text from document
            metadata: Optional metadata (filename, etc.)
            
        Returns:
            Classification result with confidence and reasoning
        """
        if not text or len(text.strip()) < 50:
            return self._empty_result("Prázdný nebo velmi krátký text")
        
        truncated = self._truncate_text(text, max_chars=5000)
        
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
                options={
                    "temperature": 0.01,
                    "num_predict": 256,
                },
                keep_alive="2m"
            )
            
            raw_output = response.get("response", "")
            
            # Debug: log raw output
            logger.debug(f"Raw classifier output: {raw_output[:500]}...")
            
            parsed = self._extract_json_from_text(raw_output)
            
            if parsed is None:
                logger.warning(f"Classifier nevrátil JSON: {raw_output[:200]}")
                return self._fallback_classification(text)
            
            # Ensure is_invoice field exists and is boolean
            if 'is_invoice' not in parsed:
                logger.warning(f"Classifier chybí is_invoice: {parsed.keys()}")
                return self._fallback_classification(text)
            
            # Validate and normalize is_invoice
            is_invoice_val = parsed.get('is_invoice')
            if isinstance(is_invoice_val, bool):
                parsed['is_invoice'] = is_invoice_val
            elif isinstance(is_invoice_val, str):
                parsed['is_invoice'] = is_invoice_val.lower() in ['true', 'ano', 'yes', '1']
            elif isinstance(is_invoice_val, (int, float)):
                parsed['is_invoice'] = bool(is_invoice_val)
            else:
                parsed['is_invoice'] = False
            
            # Validate required fields
            if not self.validate_response(parsed, ['is_invoice', 'confidence']):
                logger.warning(f"Classifier chybějící pole: {parsed.keys()}")
                return self._fallback_classification(text)
            
            # Normalize confidence
            confidence = parsed.get('confidence', 50)
            if isinstance(confidence, str):
                try:
                    confidence = float(confidence)
                except ValueError:
                    confidence = 50
            # Handle if confidence is given as 0-100 or 0-1
            if confidence > 1:
                confidence = confidence / 100.0
            parsed['confidence'] = min(1.0, max(0.0, confidence))
            
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
            'reasoning': reason
        }
    
    def _fallback_classification(self, text: str) -> dict:
        """Fallback rule-based classification when AI fails."""
        text_lower = text.lower()
        
        # Count positive indicators
        positive_keywords = ['faktura', 'invoice', 'daňový doklad', 'celkem k úhradě']
        negative_keywords = ['upomínka', 'smlouva', 'nabídka', 'životopis', 'certifikát']
        
        positive_count = sum(1 for kw in positive_keywords if kw in text_lower)
        negative_count = sum(1 for kw in negative_keywords if kw in text_lower)
        
        is_invoice = positive_count > 0 and negative_count == 0
        confidence = min(0.7, 0.5 + (positive_count - negative_count) * 0.1)
        
        return {
            'is_invoice': is_invoice,
            'confidence': confidence,
            'elements_present': {
                'identification': 'faktura' in text_lower or 'invoice' in text_lower,
                'subjects': 'dodavatel' in text_lower or 'odběratel' in text_lower,
                'dates': any(x in text_lower for x in ['datum', 'splatnost']),
                'financial': 'celkem' in text_lower or 'částka' in text_lower,
                'payment_info': 'účet' in text_lower or 'iban' in text_lower
            },
            'reasoning': f"Pravidla: {positive_count} pozitiv, {negative_count} negativ"
        }
