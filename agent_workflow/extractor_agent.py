"""
Extractor Agent
Extract and validate invoice data fields

OPTIMIZATIONS (v2):
- Shorter, more strict prompt
- Reduced context window for faster processing
- Higher num_predict limit for complete JSON
- Stronger JSON enforcement with examples
- Two-stage extraction (quick fields first, then detailed)
"""

from typing import Optional
import logging
import re
from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


class ExtractorAgent(BaseAgent):
    """
    Specialized agent for invoice data extraction.
    Optimized for speed and JSON compliance.

    Extracts and validates:
    - Vendor and customer names
    - Invoice number
    - Dates (issue, due)
    - Amounts and currency
    - Payment details
    """

    # OPTIMIZED PROMPT - shorter, stricter, with examples
    # v6.8: ANTI-HALLUCINATION - Removed template values, added validation instructions
    EXTRACTION_PROMPT = """Jsi AI pro extrakci dat z faktur. VRAT POUZE JSON.

=== KRITICKÉ ANTI-HALUCINAČNÍ PRAVIDLO ===
NESMÍŠ si VYMÝŠLET hodnoty! Extrahuj POUZE to, co je explicitně v textu.
Pokud hodnotu nevidíš v textu, vrať null. NIKDY nepoužívej vzorové hodnoty!

ZAKÁZANÉ HODNOTY (pokud je vrátíš, je to halucinace):
- vendor_name: "VZOROVY_DODAVATEL_sro", "VZOROVY_ODBERATEL_as" - toto jsou PŘÍKLADY!
- issue_date: "2099-12-31", "2099-01-01" - toto jsou PŘÍKLADY!
- total_amount: 99999.99, 80000.0 - toto jsou PŘÍKLADY!
- bank_account: "111111111/1111" - toto je PŘÍKLAD!

=== POSTUP EXTRAKCE ===
1. Přečti si celý text dokumentu
2. Pro KAŽDOU hodnotu kterou extrahuješ, musíš ji najít KONKRÉTNĚ v textu
3. Pokud hodnotu nenajdeš, vrať null
4. Extrahované hodnoty musí odpovídat tomu co je v dokumentu, ne příkladům!

=== PRAVIDLA ===
- Žádný text mimo JSON
- Chybějící hodnoty = null (ne "unknown", ne "")
- Částky jako čísla (bez měny)
- Data jako YYYY-MM-DD
- Žádné vysvětlování

=== CO EXTRAKOVAT ===
Hledej tyto hodnoty v textu:
- invoice_number: Číslo faktury (např. "2026/001", "FV-123")
- vendor_name: Název dodavatele (firma která účtuje)
- customer_name: Název odběratele (komu se účtuje)
- issue_date: Datum vystavení (formát DD.MM.YYYY nebo YYYY-MM-DD)
- due_date: Datum splatnosti
- total_amount: Celková částka k úhradě (číslo)
- currency: Měna (CZK, EUR, USD)
- vat_amount: Výše DPH (pokud je uvedena)
- base_amount: Základ bez DPH (pokud je uveden)
- bank_account: Číslo účtu (např. "123456789/0100")
- variable_symbol: Variabilní symbol
- vendor_ico: IČO dodavatele
- vendor_dic: DIČ dodavatele

=== VALIDACE ===
Před vrácením zkontroluj:
- Pokud total_amount je null, pak i vat_amount a base_amount musí být null
- Pokud vendor_name je null, pak i vendor_ico a vendor_dic musí být null
- Pokud nevidíš konkrétní hodnotu v textu, vrať null

=== JSON STRUKTURA ===
{{"invoice_number":"číslo nebo null","vendor_name":"název dodavatele","customer_name":"název odběratele","issue_date":"YYYY-MM-DD","due_date":"YYYY-MM-DD","total_amount":číslo,"currency":"CZK","vat_amount":číslo,"base_amount":číslo,"bank_account":"účet","variable_symbol":"VS","total_amount_raw":"částka s měnou","vendor_ico":"IČO","vendor_dic":"DIČ","customer_ico":"IČO","completeness_score":0.0,"validation_errors":[]}}

=== PŘÍKLAD SPRÁVNÉ EXTRAKCE ===
Text: "Faktura č. 2026/001, Dodavatel: ABC s.r.o., Odběratel: XYZ a.s., Částka: 15000 Kč, Datum: 25.2.2026"
Správná odpověď: {{"invoice_number":"2026/001","vendor_name":"ABC_sro","customer_name":"XYZ_as","issue_date":"2026-02-25","due_date":null,"total_amount":15000.0,"currency":"CZK","vat_amount":null,"base_amount":null,"bank_account":null,"variable_symbol":null,"total_amount_raw":"15000 Kč","vendor_ico":null,"vendor_dic":null,"customer_ico":null,"completeness_score":0.6,"validation_errors":["Chybí datum splatnosti"]}}

{classifier_info}=== TEXT DOKUMENTU ===
{input_data}"""  # OPRAVA: Změněno z {{input_data}} na {input_data}

    # Validation patterns - VYLEPŠENÍ PRO EN: Přidána podpora pro mezinárodní formáty
    DATE_PATTERN = r'(\d{1,2})[./-](\d{1,2})[./-](\d{4})'  # Přidána podpora pro lomítka a pomlčky
    AMOUNT_PATTERN = r'(\d{1,3}(?:[\s\.,]\d{3})*(?:[\s\.,]\d{1,2})?)'
    ICO_PATTERN = r'(?:ičo|ič|reg\.?\s*no\.?|company\s*no\.?|crn)\s*[:.]?\s*(\d{6,10})' # Vylepšeno pro EN
    DIC_PATTERN = r'(?:dič|vat\s*id|tax\s*id|vat\s*no\.?)\s*[:.]?\s*([a-zA-Z]{2}\d{8,12})' # Vylepšeno pro EN
    ACCOUNT_PATTERN = r'(cz|sk)?\d{4,6}[- ]?\d{6,10}[- ]?\d{2,4}'
    IBAN_PATTERN = r'iban:\s*([a-z]{2}\d{2,24})'

    def __init__(self, model: str = "llama3.2", timeout: int = 60, vram_limit_gb: int = None, num_ctx: int = None):
        """
        Initialize Extractor Agent.

        Args:
            model: Ollama model name
            timeout: Request timeout (increased to 60s for complex documents)
            vram_limit_gb: VRAM limit in GB (optional)
            num_ctx: Context window size (optional)
        """
        super().__init__(model, timeout, vram_limit_gb, num_ctx)

    def analyze(self, text: str, metadata: Optional[dict] = None) -> dict:
        """
        Extract invoice data from document text.
        
        v6.9: Now accepts metadata from classifier to guide extraction

        Args:
            text: Extracted text from document
            metadata: Optional metadata containing classifier_reasoning

        Returns:
            Extracted data with completeness score
        """
        if not text or len(text.strip()) < 50:
            return self._empty_result()

        # v6.9: Check if classifier found specific elements
        classifier_hints = {}
        classifier_found_elements = {}  # Track what classifier found (even if extraction failed)
        
        if metadata:
            # Track what classifier found in elements_present
            elements = metadata.get('classifier_elements', {})
            classifier_found_elements['supplier'] = elements.get('supplier_and_buyer_present', False)
            classifier_found_elements['customer'] = elements.get('supplier_and_buyer_present', False)
            classifier_found_elements['amount'] = elements.get('total_amount_present', False)
            classifier_found_elements['date'] = elements.get('date_present', False)
            
            # Prefer extracted_values from classifier (structured data)
            extracted_values = metadata.get('extracted_values', {})

            # Map classifier fields to extractor fields
            if extracted_values.get('supplier'):
                classifier_hints['vendor_name'] = extracted_values['supplier']
            if extracted_values.get('customer'):
                classifier_hints['customer_name'] = extracted_values['customer']
            if extracted_values.get('amount'):
                classifier_hints['total_amount_raw'] = extracted_values['amount']
            if extracted_values.get('date'):
                classifier_hints['issue_date'] = extracted_values['date']
            if extracted_values.get('document_type'):
                classifier_hints['document_type'] = extracted_values['document_type']

            # Fallback: If no extracted_values, try to parse from reasoning
            if not classifier_hints and metadata.get('classifier_reasoning'):
                reasoning = metadata.get('classifier_reasoning', '')

                # Try to extract supplier and buyer from reasoning
                if elements.get('supplier_and_buyer_present'):
                    supplier_match = re.search(r'Dodavatel\s*[-:]\s*([^,\n]+)', reasoning)
                    buyer_match = re.search(r'Odběratel\s*[-:]\s*([^,\n]+)', reasoning)
                    if supplier_match:
                        classifier_hints['vendor_name'] = supplier_match.group(1).strip()
                    if buyer_match:
                        classifier_hints['customer_name'] = buyer_match.group(1).strip()

                if elements.get('total_amount_present'):
                    amount_match = re.search(r'(\d+(?:[\s,.]\d+)*)\s*(Kč|EUR|USD|CZK)', reasoning, re.IGNORECASE)
                    if amount_match:
                        classifier_hints['total_amount_raw'] = amount_match.group(0).strip()

                if elements.get('date_present'):
                    date_match = re.search(r'(\d{1,2}[-./]\d{1,2}[-./]\d{2,4})', reasoning)
                    if date_match:
                        classifier_hints['issue_date'] = date_match.group(0).strip()

            logger.debug(f"  Classifier hints: {classifier_hints}")
            logger.debug(f"  Classifier found elements: {classifier_found_elements}")

        # OPTIMIZATION: Truncate to 4000 chars (was 6000) - sufficient for most invoices
        truncated = self._truncate_text(text, max_chars=4000)

        try:
            # Build classifier info for prompt
            classifier_info = ""
            if classifier_hints:
                info_lines = []
                if 'vendor_name' in classifier_hints:
                    info_lines.append(f"- Dodavatel k nalezení: {classifier_hints['vendor_name']}")
                if 'customer_name' in classifier_hints:
                    info_lines.append(f"- Odběratel k nalezení: {classifier_hints['customer_name']}")
                if 'total_amount_raw' in classifier_hints:
                    info_lines.append(f"- Částka k nalezení: {classifier_hints['total_amount_raw']}")
                if 'issue_date' in classifier_hints:
                    info_lines.append(f"- Datum k nalezení: {classifier_hints['issue_date']}")
                if info_lines:
                    classifier_info = "\n".join(info_lines) + "\n\n"

            # Add classifier info to prompt if available
            if classifier_info:
                # Insert classifier info into the prompt (before document text)
                prompt = self.EXTRACTION_PROMPT.format(
                    classifier_info=f"""=== DODATEČNÉ INFORMACE OD CLASSIFIERU ===
{classifier_info}
Použij tyto informace pro lepší extrakci - tyto hodnoty byly NALEZENY v textu. Hledej je!

""",
                    input_data=truncated
                )
            else:
                prompt = self.EXTRACTION_PROMPT.format(
                    classifier_info="",
                    input_data=truncated
                )
        except (KeyError, IndexError) as e:
            logger.warning(f"Prompt format error: {e}")
            return self._fallback_extraction(text)

        try:
            client = self._get_client()

            # OPTIMIZED PARAMETERS for speed and JSON compliance:
            # v6.7: Použít num_ctx z nastavení agenta (z GUI)
            response = client.generate(
                model=self.model,
                prompt=prompt,
                format="json",
                options={
                    "temperature": 0.0,     # v6.12: Deterministický výstup (převzato z čtečka/app.py)
                    "num_predict": 1024,
                    "num_ctx": self.num_ctx,
                    "top_p": 0.1,           # v6.12: Omezený sampling pro lepší konzistenci
                    "repeat_penalty": 1.1,  # Prevence opakování
                },
                keep_alive="0s"  # Okamžitě uvolnit paměť po requestu
            )

            raw_output = response.get("response", "")

            # Debug: log raw output pro troubleshooting
            logger.debug(f"Extractor raw output ({len(raw_output)} chars): {raw_output[:300]}...")

            # OPTIMIZATION: Try multiple JSON extraction strategies
            parsed = self._extract_json_from_text(raw_output)

            if parsed is None:
                logger.warning(f"Extractor nevrátil JSON. Raw output preview: {raw_output[:200]}...")
                # OPTIMIZATION: Try aggressive JSON fix before fallback
                parsed = self._try_fix_json(raw_output)
                if parsed is None:
                    return self._fallback_extraction(text)

            # Validate and enhance extraction
            result = self._validate_extraction(parsed, text)

            # v6.9: Use classifier hints FIRST to fill in missing values
            # This way, hint-filled values are available for verification
            filled_fields = set()
            if classifier_hints:
                result, filled_fields = self._fill_missing_with_hints(result, text, classifier_hints)
            elif classifier_found_elements:
                # Even if classifier didn't extract values, it found elements - try to extract them
                logger.debug(f"  Classifier found elements but no hints - extracting from text...")
                result = self._extract_with_classifier_guidance(result, text, classifier_found_elements)
                # Mark these as "found" so they skip strict verification
                for elem in classifier_found_elements:
                    if classifier_found_elements[elem]:
                        filled_fields.add(elem.replace('supplier', 'vendor_name').replace('customer', 'customer_name'))

            # v6.8: ANTI-HALLUCINATION - Verify only LLM-extracted values (not hint-filled ones)
            # Also skip verification for fields where we found at least partial match in text
            exclude_from_verify = filled_fields | set(classifier_hints.keys()) if classifier_hints else set()
            result = self._verify_extraction(result, text, exclude_fields=exclude_from_verify)

            # Calculate completeness
            result['completeness_score'] = self._calculate_completeness(result)

            logger.debug(f"✓ Extrakce: completeness={result['completeness_score']:.0%}")

            return result

        except Exception as e:
            logger.error(f"Extractor chyba: {e}")
            return self._fallback_extraction(text)

    def _try_fix_json(self, raw_output: str) -> Optional[dict]:
        """
        Try to fix common JSON issues before falling back to rule-based extraction.
        """
        import json

        # Quick check: if no JSON-like structure at all, skip to Strategy 3
        if '{' not in raw_output or '}' not in raw_output:
            logger.debug("No JSON structure in output, skipping to Strategy 3")
            return self._manual_extract_fields(raw_output)

        # Strategy 1: Remove any text before first { and after last }
        cleaned = raw_output.strip()
        start = cleaned.find('{')
        end = cleaned.rfind('}')
        if start != -1 and end != -1 and end > start:
            candidate = cleaned[start:end + 1]
            try:
                return json.loads(candidate)
            except:
                pass
        else:
            # No JSON structure found, skip to Strategy 3
            logger.debug("Strategy 1 failed: No JSON structure found")
            return self._manual_extract_fields(raw_output)

        # Strategy 2: Fix common issues - missing quotes, trailing commas
        # Only run if we found a candidate in Strategy 1
        if 'candidate' in locals():
            fixed = re.sub(r'(\{|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', candidate)
            fixed = re.sub(r',\s*}', '}', fixed)  # Remove trailing commas
            fixed = re.sub(r',\s*]', ']', fixed)
            try:
                return json.loads(fixed)
            except:
                pass
        
        # Strategy 3: Try to extract key-value pairs manually
        return self._manual_extract_fields(raw_output)

    def _manual_extract_fields(self, raw_output: str) -> Optional[dict]:
        """Manually extract key-value pairs from non-JSON output."""
        result = {}
        string_fields = ['invoice_number', 'vendor_name', 'customer_name',
                        'issue_date', 'due_date', 'currency', 'total_amount_raw',
                        'bank_account', 'variable_symbol', 'vendor_ico', 'vendor_dic', 'customer_ico']
        number_fields = ['total_amount', 'vat_amount', 'base_amount', 'completeness_score']

        # Strategy 1: Try JSON format first (with quotes)
        for field in string_fields:
            pattern = rf'"{field}"\s*:\s*"([^"]*)"'
            match = re.search(pattern, raw_output)
            if match:
                result[field] = match.group(1) if match.group(1) != 'null' else None

        for field in number_fields:
            pattern = rf'"{field}"\s*:\s*([\d.]+)'
            match = re.search(pattern, raw_output)
            if match:
                try:
                    result[field] = float(match.group(1))
                except:
                    result[field] = None

        # Strategy 2: Try list format (e.g., "- Invoice number: 2565021833")
        # This handles cases where AI outputs data as a list instead of JSON
        if not result.get('invoice_number'):
            patterns_map = {
                'invoice_number': [r'[-•]?\s*Invoice number[:\s]+([^\n]+)', r'[-•]?\s*Číslo faktury[:\s]+([^\n]+)'],
                'vendor_name': [r'[-•]?\s*Vendor name[:\s]+([^\n]+)', r'[-•]?\s*Dodavatel[:\s]+([^\n]+)'],
                'customer_name': [r'[-•]?\s*Customer name[:\s]+([^\n]+)', r'[-•]?\s*Odběratel[:\s]+([^\n]+)'],
                'issue_date': [r'[-•]?\s*Issue date[:\s]+([^\n]+)', r'[-•]?\s*Datum vystavení[:\s]+([^\n]+)'],
                'due_date': [r'[-•]?\s*Due date[:\s]+([^\n]+)', r'[-•]?\s*Datum splatnosti[:\s]+([^\n]+)'],
                'total_amount': [r'[-•]?\s*Total amount[:\s]+([^\n]+)', r'[-•]?\s*Celkem[:\s]+([^\n]+)'],
                'currency': [r'[-•]?\s*Currency[:\s]+([^\n]+)'],
                'bank_account': [r'[-•]?\s*Bank account[:\s]+([^\n]+)', r'[-•]?\s*Účet[:\s]+([^\n]+)'],
                'vendor_ico': [r'[-•]?\s*Vendor ICO[:\s]+([^\n]+)', r'[-•]?\s*IČO[:\s]+([^\n]+)'],
                'vendor_dic': [r'[-•]?\s*Vendor DIC[:\s]+([^\n]+)', r'[-•]?\s*DIČ[:\s]+([^\n]+)'],
            }
            
            for field, field_patterns in patterns_map.items():
                for pattern in field_patterns:
                    match = re.search(pattern, raw_output, re.IGNORECASE)
                    if match:
                        value = match.group(1).strip()
                        # Clean up value
                        value = value.strip('-•: ')
                        if value and value.lower() not in ['null', 'none', 'n/a']:
                            if field == 'total_amount':
                                # Try to extract number from value
                                num_match = re.search(r'([\d\s,.]+)', value)
                                if num_match:
                                    try:
                                        num_str = num_match.group(1).replace(' ', '').replace(',', '.')
                                        result[field] = float(num_str)
                                    except:
                                        result[field] = value
                            else:
                                result[field] = value
                        break

        # Array field - validation errors
        errors_match = re.search(r'"validation_errors"\s*:\s*\[([^\]]*)\]', raw_output)
        if errors_match:
            result['validation_errors'] = [e.strip().strip('"') for e in errors_match.group(1).split(',') if e.strip()]
        else:
            # If we found data, clear validation errors (they were false negatives)
            if result:
                result['validation_errors'] = []
            else:
                result['validation_errors'] = []

        if result:  # If we extracted anything
            return result

        return None

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

        # Track if we found data in manual extraction (to avoid false validation errors)
        had_manual_data = bool(result.get('invoice_number') or result.get('vendor_name') or result.get('customer_name'))

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

        # Validate dates (only if we have a date to validate)
        if result.get('issue_date'):
            result['issue_date'] = self._validate_date(result.get('issue_date'))
        if result.get('due_date'):
            result['due_date'] = self._validate_date(result.get('due_date'))

        # Validate amounts (only if we have an amount to validate)
        if result.get('total_amount') is not None:
            validated_amount = self._validate_amount(result.get('total_amount'))
            if validated_amount is not None:
                result['total_amount'] = validated_amount

        # Build validation errors - but be lenient if we found manual data
        result['validation_errors'] = []
        
        if had_manual_data:
            # If we found data through manual extraction, don't add validation errors
            # for fields that might have different names in the document
            logger.debug("  Manual extraction found data - skipping strict validation")
            if not result.get('vendor_name'):
                result['validation_errors'].append('Chybí dodavatel (možná jiný formát)')
            if not result.get('customer_name'):
                result['validation_errors'].append('Chybí odběratel (možná jiný formát)')
        else:
            # Strict validation for AI extraction
            if not result.get('vendor_name'):
                result['validation_errors'].append('Chybí dodavatel')
            if not result.get('customer_name'):
                result['validation_errors'].append('Chybí odběratel')

        # Always add errors for truly missing critical fields
        if not result.get('issue_date'):
            result['validation_errors'].append('Chybí datum vystavení')
        if result.get('total_amount') is None:
            result['validation_errors'].append('Chybí částka')

        return result
    
    def _validate_date(self, date_value) -> Optional[str]:
        """Validate and normalize date format."""
        if not date_value:
            return None

        # If already in YYYY-MM-DD format
        if re.match(r'\d{4}-\d{2}-\d{2}', str(date_value)):
            # Validate the date components
            try:
                match = re.match(r'(\d{4})-(\d{2})-(\d{2})', str(date_value))
                if match:
                    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
                    # Check if date is valid
                    if month < 1 or month > 12:
                        logger.debug(f"  Invalid month {month} in date {date_value}")
                        return None
                    if day < 1 or day > 31:
                        logger.debug(f"  Invalid day {day} in date {date_value}")
                        return None
                    # Basic validation passed
                    return date_value
            except:
                pass
            return date_value

        # Try to parse DD.MM.YYYY or MM/DD/YYYY
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
    
    def _verify_extraction(self, result: dict, text: str, exclude_fields: set = None) -> dict:
        """
        v6.8: ANTI-HALLUCINATION - Verify that extracted values are actually in the text.
        v6.9: RELAXED CHECK - Use substring matching instead of exact match to avoid false positives.
        v6.9: SKIP VERIFICATION for fields filled from classifier hints.
        v6.12: Removed verbose warnings - silent validation for cleaner logs.

        Args:
            result: Extracted data
            text: Original document text
            exclude_fields: Set of field names to skip verification for (filled from hints)

        Returns:
            Result with hallucinated values set to null
        """
        text_lower = text.lower()
        exclude_fields = exclude_fields or set()

        # Check each extracted field with relaxed matching
        fields_to_check = [
            ('vendor_name', 'vendor_name'),
            ('customer_name', 'customer_name'),
            ('invoice_number', 'invoice_number'),
            ('bank_account', 'bank_account'),
        ]

        for field_key, _ in fields_to_check:
            # Skip verification for fields filled from classifier hints
            if field_key in exclude_fields:
                continue

            value = result.get(field_key)
            if value:
                # Convert value to string and normalize
                value_str = str(value)
                value_lower = value_str.lower()

                # Check 1: Exact match (case insensitive)
                exact_match = value_lower in text_lower

                # Check 2: Substring match - at least 80% of value must be in text
                # This handles cases where LLM adds extra formatting
                if not exact_match:
                    # Remove common suffixes and check core part
                    core_value = value_str.replace(' s.r.o.', '').replace(' a.s.', '').replace(' spol. s r.o.', '').strip()
                    if len(core_value) > 3:
                        core_match = core_value.lower() in text_lower
                        if core_match:
                            exact_match = True

                # Check 3: Word-by-word match for multi-word values
                if not exact_match and len(value_str.split()) > 1:
                    words = [w for w in value_str.split() if len(w) > 2 and w.lower() not in {'s.r.o.', 'a.s.', 'spol.', 'the'}]
                    if words:
                        # Check if at least half of significant words are in text
                        matches = sum(1 for w in words if w.lower() in text_lower)
                        if matches >= len(words) * 0.5:
                            exact_match = True

                # If no match found, silently set to null
                if not exact_match:
                    result[field_key] = None
                    result['validation_errors'].append(f"{field_key} nebyl nalezen v textu")

        # Check dates - must be in format from text
        issue_date = result.get('issue_date')
        if issue_date and issue_date != '0000-00-00':
            # Check if date pattern exists in text (not the exact date, just any date pattern)
            import re
            date_patterns = [
                r'\d{1,2}[./-]\d{1,2}[./-]\d{4}',  # Podpora DD.MM.YYYY i DD/MM/YYYY
                r'\d{4}-\d{2}-\d{2}',              # YYYY-MM-DD
            ]
            date_found = any(re.search(pattern, text) for pattern in date_patterns)
            if not date_found:
                result['issue_date'] = None
                result['validation_errors'].append("issue_date nebyl nalezen v textu")

        # Check amounts - must be numeric value from text
        total_amount = result.get('total_amount')
        if total_amount is not None and total_amount > 0:
            # Check if amount pattern exists in text
            amount_str = str(total_amount).replace('.', ',')
            amount_found = (
                str(total_amount) in text or
                amount_str in text or
                f"{int(total_amount)}" in text or
                f"{int(total_amount):,}" in text.replace(' ', '') or
                f"{int(total_amount):.2f}" in text
            )
            if not amount_found:
                result['total_amount'] = None
                result['vat_amount'] = None  # Also clear VAT if no total
                result['base_amount'] = None
                result['validation_errors'].append("total_amount nebyl nalezen v textu")

        # Check for template/example values (common hallucinations) - silent check
        TEMPLATE_VALUES = [
            'vzorovy_dodavatel', 'vzorovy_odberatel', 'vzor', 'example',
            '2099-12-31', '2099-01-01', '99999', '111111111',
        ]

        for field in ['vendor_name', 'customer_name', 'invoice_number', 'bank_account']:
            value = result.get(field)
            if value and any(tv in value.lower() for tv in TEMPLATE_VALUES):
                result[field] = None
                result['validation_errors'].append(f"{field} je šablonová hodnota")

        return result

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
    
    def _fill_missing_with_hints(self, result: dict, text: str, hints: dict) -> tuple:
        """
        v6.9: Fill missing values using classifier hints.

        When LLM returns null for fields that classifier found, use regex
        to search for the hinted values directly in the text.

        Args:
            result: Extracted data from LLM
            text: Original document text
            hints: Values found by classifier

        Returns:
            Tuple (result, filled_fields) where filled_fields is a set of field names that were filled
        """
        text_lower = text.lower()
        filled_fields = set()

        # Fill vendor_name if missing but hinted
        if not result.get('vendor_name') and hints.get('vendor_name'):
            hinted_value = hints['vendor_name']
            # Search for the hinted value in text (case insensitive)
            if hinted_value.lower() in text_lower:
                # Find the exact occurrence with original casing
                pattern = re.escape(hinted_value)
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    result['vendor_name'] = match.group(0)
                    logger.debug(f"  ✓ Filled vendor_name from hint: {result['vendor_name']}")
                    filled_fields.add('vendor_name')
                    # Remove related validation error
                    result['validation_errors'] = [e for e in result.get('validation_errors', [])
                                                   if 'dodavatel' not in e.lower()]
            else:
                # Strategy 2: Extract key words from hint and search for them
                # "firma XYZ" → search for "XYZ"
                words = hinted_value.split()
                # Take the most distinctive word (not "firma", "s.r.o.", etc.)
                stop_words = {'firma', 's.r.o.', 'a.s.', 'spol.', 's', 'r.o.', 'o', 'z.s.', 'ič'}
                key_words = [w for w in words if w.lower() not in stop_words and len(w) > 2]

                for key_word in key_words:
                    # Search for the key word as a whole word
                    pattern = r'\b' + re.escape(key_word) + r'\b'
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        # Found a key word - use it as vendor name
                        result['vendor_name'] = match.group(0)
                        logger.debug(f"  ✓ Filled vendor_name from hint (keyword '{key_word}'): {result['vendor_name']}")
                        filled_fields.add('vendor_name')
                        result['validation_errors'] = [e for e in result.get('validation_errors', [])
                                                       if 'dodavatel' not in e.lower()]
                        break

        # Fill customer_name if missing but hinted (same strategy)
        if not result.get('customer_name') and hints.get('customer_name'):
            hinted_value = hints['customer_name']
            if hinted_value.lower() in text_lower:
                pattern = re.escape(hinted_value)
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    result['customer_name'] = match.group(0)
                    logger.debug(f"  ✓ Filled customer_name from hint: {result['customer_name']}")
                    filled_fields.add('customer_name')
                    result['validation_errors'] = [e for e in result.get('validation_errors', [])
                                                   if 'odběratel' not in e.lower()]
            else:
                words = hinted_value.split()
                stop_words = {'firma', 's.r.o.', 'a.s.', 'spol.', 's', 'r.o.', 'o', 'z.s.', 'ič'}
                key_words = [w for w in words if w.lower() not in stop_words and len(w) > 2]

                for key_word in key_words:
                    pattern = r'\b' + re.escape(key_word) + r'\b'
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        result['customer_name'] = match.group(0)
                        logger.debug(f"  ✓ Filled customer_name from hint (keyword '{key_word}'): {result['customer_name']}")
                        filled_fields.add('customer_name')
                        result['validation_errors'] = [e for e in result.get('validation_errors', [])
                                                       if 'odběratel' not in e.lower()]
                        break

        # Fill issue_date if missing but hinted
        if not result.get('issue_date') and hints.get('issue_date'):
            hinted_date = hints['issue_date']
            # Try to find the date in text in various formats
            date_patterns = [
                re.escape(hinted_date),  # Exact match
                hinted_date.replace('.', r'[./-]'),  # Flexible separators
                hinted_date.replace('-', r'[./-]'),
            ]
            for pattern in date_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    # Normalize to YYYY-MM-DD
                    found_date = match.group(0)
                    normalized = self._validate_date(found_date)
                    if normalized:
                        result['issue_date'] = normalized
                        logger.debug(f"  ✓ Filled issue_date from hint: {hinted_date} → {normalized}")
                        filled_fields.add('issue_date')
                        result['validation_errors'] = [e for e in result.get('validation_errors', [])
                                                       if 'datum' not in e.lower()]
                    break

        # Fill total_amount if missing but hinted
        if not result.get('total_amount') and hints.get('total_amount_raw'):
            hinted_amount = hints['total_amount_raw']
            # Extract numeric value from hinted amount
            amount_match = re.search(r'(\d+(?:[\s,.]\d+)*)', hinted_amount)
            if amount_match:
                amount_str = amount_match.group(1).replace(' ', '').replace(',', '.')
                try:
                    result['total_amount'] = float(amount_str)
                    result['total_amount_raw'] = hinted_amount
                    logger.debug(f"  ✓ Filled total_amount from hint: {hinted_amount} → {result['total_amount']}")
                    filled_fields.add('total_amount')
                    result['validation_errors'] = [e for e in result.get('validation_errors', [])
                                                   if 'částka' not in e.lower()]
                except ValueError:
                    pass

        return result, filled_fields

    def _extract_with_classifier_guidance(self, result: dict, text: str, found_elements: dict) -> dict:
        """
        v6.9: Extract values from text when classifier found elements but didn't extract specific values.
        
        This handles the case where classifier says "supplier found" but extracted_values is null.
        We use regex to find the values directly in the text.
        
        Args:
            result: Current extraction result
            text: Original document text
            found_elements: Dict of elements that classifier found (True/False)
            
        Returns:
            Result with extracted values
        """
        text_lower = text.lower()
        
        # Extract supplier if classifier found it
        if found_elements.get('supplier') and not result.get('vendor_name'):
            # Look for common patterns
            patterns = [
                r'dodavatel[:\s]+([^,\n]+)',
                r'vendor[:\s]+([^,\n]+)',
                r'from[:\s]+([^,\n]+)',
            ]
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    result['vendor_name'] = match.group(1).strip()
                    logger.debug(f"  ✓ Extracted vendor_name: {result['vendor_name']}")
                    result['validation_errors'] = [e for e in result.get('validation_errors', [])
                                                   if 'dodavatel' not in e.lower()]
                    break
        
        # Extract customer if classifier found it
        if found_elements.get('customer') and not result.get('customer_name'):
            patterns = [
                r'odběratel[:\s]+([^,\n]+)',
                r'customer[:\s]+([^,\n]+)',
                r'for[:\s]+([^,\n]+)',
                r'faktura pro[:\s]+([^,\n]+)',
            ]
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    result['customer_name'] = match.group(1).strip()
                    logger.debug(f"  ✓ Extracted customer_name: {result['customer_name']}")
                    result['validation_errors'] = [e for e in result.get('validation_errors', [])
                                                   if 'odběratel' not in e.lower()]
                    break
        
        # Extract amount if classifier found it
        if found_elements.get('amount') and not result.get('total_amount'):
            # Look for amount patterns with currency
            patterns = [
                r'celkem[:\s]+(\d+(?:[\s,.]\d+)*)\s*(Kč|EUR|USD|CZK)',
                r'total[:\s]+(\d+(?:[\s,.]\d+)*)\s*(Kč|EUR|USD|CZK)',
                r'k úhradě[:\s]+(\d+(?:[\s,.]\d+)*)\s*(Kč|EUR|USD|CZK)',
                r'(\d+(?:[\s,.]\d+)*)\s*(Kč|EUR|USD|CZK)',
            ]
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    amount_str = match.group(1).replace(' ', '').replace('.', '').replace(',', '.')
                    try:
                        result['total_amount'] = float(amount_str)
                        result['total_amount_raw'] = f"{match.group(1)} {match.group(2)}"
                        logger.debug(f"  ✓ Extracted total_amount: {result['total_amount']}")
                        result['validation_errors'] = [e for e in result.get('validation_errors', [])
                                                       if 'částka' not in e.lower()]
                        break
                    except ValueError:
                        pass
        
        # Extract date if classifier found it
        if found_elements.get('date') and not result.get('issue_date'):
            # Look for date patterns
            patterns = [
                r'datum vystavení[:\s]+(\d{1,2}\.\d{1,2}\.\d{4})',
                r'issue date[:\s]+(\d{1,2}\.\d{1,2}\.\d{4})',
                r'(\d{1,2}\.\d{1,2}\.\d{4})',
                r'(\d{4}-\d{2}-\d{2})',
            ]
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    found_date = match.group(0)
                    normalized = self._validate_date(found_date)
                    if normalized:
                        result['issue_date'] = normalized
                        logger.debug(f"  ✓ Extracted issue_date: {normalized}")
                        result['validation_errors'] = [e for e in result.get('validation_errors', [])
                                                       if 'datum' not in e.lower()]
                        break
        
        return result


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
        
        # Detect currency - ONLY if explicitly found in text, NO fallback!
        if '€' in text or 'EUR' in text_lower or 'eur' in text_lower:
            result['currency'] = 'EUR'
        elif '$' in text or 'USD' in text_lower or 'usd' in text_lower:
            result['currency'] = 'USD'
        elif 'Kč' in text or 'CZK' in text_lower or 'czk' in text_lower or 'KC' in text:
            result['currency'] = 'CZK'
        # NO FALLBACK - currency must be explicitly present in text!
        
        # Extract first company name - VYLEPŠENÍ PRO EN (podpora Ltd., Inc., LLC, GmbH atd.)
        company_match = re.search(r'\b([A-ZČŠŽŘĎŤŇĚÁÉÍÓÚÝ][a-zčšžřďťňěáéíóúýA-Za-z\s]+(?:s\.r\.o\.|a\.s\.|spol\.\s+r\.o\.|Ltd\.?|Inc\.?|LLC|GmbH))', text)
        if company_match:
            result['vendor_name'] = company_match.group(1).strip()
        
        result['completeness_score'] = self._calculate_completeness(result)
        
        return result