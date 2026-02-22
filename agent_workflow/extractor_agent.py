"""
Extractor Agent
Extract and validate invoice data fields
"""

from typing import Optional
import logging
import re
from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


class ExtractorAgent(BaseAgent):
    """
    Specialized agent for invoice data extraction.
    
    Extracts and validates:
    - Vendor and customer names
    - Invoice number
    - Dates (issue, due)
    - Amounts and currency
    - Payment details
    """
    
    EXTRACTION_PROMPT = """Jsi expert na extrakci dat z faktur. Tvým úkolem je extrahovat strukturovaná data.

=== PRAVIDLA PRO EXTRAKCI ===
1. Extrahuj pouze explicitně uvedené hodnoty
2. Ověř formát každého pole
3. Pokud pole chybí, nastav null
4. Částky převáděj na čísla (bez měny)
5. Data ve formátu YYYY-MM-DD

=== POŽADOVANÁ STRUKTURA JSON ===
{{
  "invoice_number": "číslo faktury nebo null",
  "vendor_name": "název dodavatele nebo null",
  "vendor_ico": "IČO nebo null",
  "vendor_dic": "DIČ nebo null",
  "customer_name": "název odběratele nebo null",
  "customer_ico": "IČO odběratele nebo null",
  "issue_date": "YYYY-MM-DD nebo null",
  "due_date": "YYYY-MM-DD nebo null",
  "total_amount": číslo nebo null,
  "total_amount_raw": "částka s měnou nebo null",
  "currency": "CZK/EUR/USD nebo null",
  "vat_amount": číslo nebo null,
  "base_amount": číslo nebo null,
  "bank_account": "číslo účtu nebo null",
  "variable_symbol": "variabilní symbol nebo null",
  "completeness_score": 0.0-1.0,
  "validation_errors": ["seznam chybějících polí"]
}}

=== TEXT DOKUMENTU ===
{input_data}"""

    # Validation patterns
    DATE_PATTERN = r'(\d{1,2})\.(\d{1,2})\.(\d{4})'
    AMOUNT_PATTERN = r'(\d{1,3}(?:[\s\.,]\d{3})*(?:[\s\.,]\d{1,2})?)'
    ICO_PATTERN = r'ičo\s*[:.]?\s*(\d{8})'
    DIC_PATTERN = r'dič\s*[:.]?\s*([a-z]{2}\d{8,12})'
    ACCOUNT_PATTERN = r'(cz|sk)?\d{4,6}[- ]?\d{6,10}[- ]?\d{2,4}'
    IBAN_PATTERN = r'iban:\s*([a-z]{2}\d{2,24})'

    def __init__(self, model: str = "llama3.2", timeout: int = 45):
        super().__init__(model, timeout)
    
    def analyze(self, text: str, metadata: Optional[dict] = None) -> dict:
        """
        Extract invoice data from document text.
        
        Args:
            text: Extracted text from document
            metadata: Optional metadata
            
        Returns:
            Extracted data with completeness score
        """
        if not text or len(text.strip()) < 50:
            return self._empty_result()
        
        truncated = self._truncate_text(text, max_chars=6000)
        
        try:
            prompt = self.EXTRACTION_PROMPT.format(input_data=truncated)
        except (KeyError, IndexError) as e:
            logger.warning(f"Prompt format error: {e}")
            return self._fallback_extraction(text)
        
        try:
            client = self._get_client()
            response = client.generate(
                model=self.model,
                prompt=prompt,
                options={
                    "temperature": 0.01,
                    "num_predict": 512,
                },
                keep_alive="2m"
            )
            
            raw_output = response.get("response", "")
            
            # Debug: log raw output pro troubleshooting
            logger.debug(f"Extractor raw output ({len(raw_output)} chars): {raw_output[:300]}...")
            
            parsed = self._extract_json_from_text(raw_output)
            
            if parsed is None:
                logger.warning(f"Extractor nevrátil JSON. Raw output preview: {raw_output[:200]}...")
                return self._fallback_extraction(text)
            
            # Validate and enhance extraction
            result = self._validate_extraction(parsed, text)
            
            # Calculate completeness
            result['completeness_score'] = self._calculate_completeness(result)
            
            logger.debug(f"✓ Extrakce: completeness={result['completeness_score']:.0%}")
            
            return result
            
        except Exception as e:
            logger.error(f"Extractor chyba: {e}")
            return self._fallback_extraction(text)
    
    def _validate_extraction(self, result: dict, original_text: str) -> dict:
        """Validate and enhance extracted data using regex patterns."""
        text_lower = original_text.lower()
        
        # Ensure all expected fields exist
        expected_fields = [
            'invoice_number', 'vendor_name', 'vendor_ico', 'vendor_dic',
            'customer_name', 'customer_ico', 'issue_date', 'due_date',
            'total_amount', 'total_amount_raw', 'currency',
            'vat_amount', 'base_amount', 'bank_account', 'variable_symbol'
        ]
        
        for field in expected_fields:
            if field not in result:
                result[field] = None
        
        # Extract ICO if not found by AI
        if not result.get('vendor_ico'):
            ico_match = re.search(self.ICO_PATTERN, text_lower)
            if ico_match:
                result['vendor_ico'] = ico_match.group(1)
        
        # Extract DIČ if not found by AI
        if not result.get('vendor_dic'):
            dic_match = re.search(self.DIC_PATTERN, text_lower)
            if dic_match:
                result['vendor_dic'] = dic_match.group(1).upper()
        
        # Extract bank account if not found by AI
        if not result.get('bank_account'):
            iban_match = re.search(self.IBAN_PATTERN, text_lower)
            if iban_match:
                result['bank_account'] = iban_match.group(1).upper()
            else:
                account_match = re.search(self.ACCOUNT_PATTERN, text_lower)
                if account_match:
                    result['bank_account'] = account_match.group(0)
        
        # Detect currency
        if not result.get('currency'):
            if '€' in original_text or 'eur' in text_lower:
                result['currency'] = 'EUR'
            elif '$' in original_text or 'usd' in text_lower:
                result['currency'] = 'USD'
            else:
                result['currency'] = 'CZK'
        
        # Validate dates
        result['issue_date'] = self._validate_date(result.get('issue_date'))
        result['due_date'] = self._validate_date(result.get('due_date'))
        
        # Validate amounts
        result['total_amount'] = self._validate_amount(result.get('total_amount'))
        
        # Build validation errors
        result['validation_errors'] = []
        if not result.get('vendor_name'):
            result['validation_errors'].append('Chybí dodavatel')
        if not result.get('customer_name'):
            result['validation_errors'].append('Chybí odběratel')
        if not result.get('issue_date'):
            result['validation_errors'].append('Chybí datum vystavení')
        if not result.get('total_amount'):
            result['validation_errors'].append('Chybí částka')
        
        return result
    
    def _validate_date(self, date_value) -> Optional[str]:
        """Validate and normalize date format."""
        if not date_value:
            return None
        
        # If already in YYYY-MM-DD format
        if re.match(r'\d{4}-\d{2}-\d{2}', str(date_value)):
            return date_value
        
        # Try to parse DD.MM.YYYY
        match = re.search(self.DATE_PATTERN, str(date_value))
        if match:
            return f"{match.group(3)}-{match.group(2).zfill(2)}-{match.group(1).zfill(2)}"
        
        return None
    
    def _validate_amount(self, amount_value) -> Optional[float]:
        """Validate and normalize amount."""
        if amount_value is None:
            return None
        
        if isinstance(amount_value, (int, float)):
            return float(amount_value)
        
        try:
            # Remove spaces and handle Czech number format
            cleaned = str(amount_value).replace(' ', '').replace(',', '.')
            return float(cleaned)
        except (ValueError, TypeError):
            return None
    
    def _calculate_completeness(self, result: dict) -> float:
        """Calculate completeness score based on extracted fields."""
        required_fields = ['vendor_name', 'customer_name', 'issue_date', 'total_amount']
        optional_fields = ['invoice_number', 'due_date', 'currency', 'vendor_ico', 'bank_account']
        
        score = 0.0
        
        # Required fields (60% of score)
        for field in required_fields:
            if result.get(field):
                score += 0.15
        
        # Optional fields (40% of score)
        for field in optional_fields:
            if result.get(field):
                score += 0.08
        
        return min(1.0, score)
    
    def _empty_result(self) -> dict:
        """Return empty extraction result."""
        return {
            'invoice_number': None,
            'vendor_name': None,
            'vendor_ico': None,
            'vendor_dic': None,
            'customer_name': None,
            'customer_ico': None,
            'issue_date': None,
            'due_date': None,
            'total_amount': None,
            'total_amount_raw': None,
            'currency': None,
            'vat_amount': None,
            'base_amount': None,
            'bank_account': None,
            'variable_symbol': None,
            'completeness_score': 0.0,
            'validation_errors': ['Žádná data k extrakci']
        }
    
    def _fallback_extraction(self, text: str) -> dict:
        """Fallback rule-based extraction when AI fails."""
        text_lower = text.lower()
        result = self._empty_result()
        
        # Extract date
        date_match = re.search(self.DATE_PATTERN, text)
        if date_match:
            result['issue_date'] = f"{date_match.group(3)}-{date_match.group(2).zfill(2)}-{date_match.group(1).zfill(2)}"
        
        # Extract amount
        amount_match = re.search(self.AMOUNT_PATTERN, text_lower)
        if amount_match:
            amount_str = amount_match.group(1).replace(' ', '').replace('.', '').replace(',', '.')
            try:
                result['total_amount'] = float(amount_str)
            except ValueError:
                pass
        
        # Detect currency
        if '€' in text or 'eur' in text_lower:
            result['currency'] = 'EUR'
        elif '$' in text or 'usd' in text_lower:
            result['currency'] = 'USD'
        else:
            result['currency'] = 'CZK'
        
        # Extract first company name
        company_match = re.search(r'\b([A-ZČŠŽŘĎŤŇĚÁÉÍÓÚÝ][a-zčšžřďťňěáéíóúý]+\s+(?:s\.r\.o\.|a\.s\.|spol\.\s+r\.o\.))', text)
        if company_match:
            result['vendor_name'] = company_match.group(1)
        
        result['completeness_score'] = self._calculate_completeness(result)
        
        return result
