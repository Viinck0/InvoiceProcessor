"""
Anomaly Detector Agent
Detect non-invoice documents (CVs, certificates, contracts, etc.)
"""

from typing import Optional
import json
import logging
import re
from pathlib import Path
from .base_agent import BaseAgent

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

=== TYPY ANOMÁLIÍ ===
1. ŽIVOTOPIS/CV: dokument je čistý životopis (curriculum vitae, resume, vzdělání, praxe, dovednosti, education, experience, skills). POZOR: slova jako "školení" nebo "kurz" mohou být fakturované položky, ale seznam pracovních zkušeností je vždy anomálie!
2. CERTIFIKÁT: osvědčení o absolvování (úspěšně složil, absolvoval, diplom, certificate, diploma, professional qualification). POZOR: faktura za "certifikaci" NENÍ anomálie!
3. SMLOUVA: dokument je samotná smlouva (pracovní smlouva, nájemní smlouva, contract, agreement). POZOR: věty jako "fakturujeme dle smlouvy" nebo "na základě objednávky" jsou na fakturách normální!
4. UPOMÍNKA: výzva k úhradě již splatné částky (upomínka, payment reminder).
5. NABÍDKA/POPTÁVKA: dokument je teprve návrhem ceny (cenová nabídka, kalkulace, žádost o nabídku, quote, proposal).
6. INTERNÍ DOKUMENT: nehotový dokument (koncept, draft, working copy).

=== PRAVIDLA ===
- Pokud dokument vypadá jako běžná faktura (obsahuje částky k úhradě, dodavatele, odběratele) → is_anomaly=false
- NEZAMÍTEJ faktury jen proto, že obsahují slova "objednávka", "smlouva", "školení", "kurz" nebo "licence".
- Odpovídej POUZE ve formátu JSON.
- Žádný další text, žádné vysvětlení mimo JSON.
- Začni hned JSON objektem.

=== FORMÁT VÝSTUPU ===
{{
  "is_anomaly": true/false,
  "anomaly_type": "cv_resume|certificate|contract|reminder|offer|inquiry|internal|null",
  "confidence": 0.0-1.0,
  "detected_keywords": ["seznam nalezených keyword"],
  "flags": ["seznam varovných signálů"],
  "reasoning": "vysvětlení proč je/není anomálie"
}}

=== TEXT DOKUMENTU ===
{input_data}"""

    def __init__(self, model: str = "llama3.2", timeout: int = 30, patterns_file: Optional[str] = None, vram_limit_gb: int = None, num_ctx: int = None):
        super().__init__(model, timeout, vram_limit_gb, num_ctx)
        self.patterns = self._load_patterns(patterns_file)
    
    def _load_patterns(self, patterns_file: Optional[str]) -> dict:
        """Load anomaly detection patterns from JSON file."""
        if patterns_file and Path(patterns_file).exists():
            try:
                with open(patterns_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Chyba načtení patternů: {e}")
        
        # VYLEPŠENÍ: Rozšířeno o EN termíny a specifická slova z životopisů
        return {
            "cv_resume": {
                "keywords": [
                    "životopis", "curriculum vitae", "curriculum", "resume", "cv ", 
                    "rodné číslo", "narozen", "date of birth", "birth date", 
                    "dovednosti", "vzdělání", "pracovní zkušenosti", "praxe",
                    "skills", "education", "experience", "work experience"
                ],
                "threshold": 2,
                "veto": True
            },
            "certificate": {
                "keywords": [
                    "certifikát", "osvědčení", "absolvoval", "úspěšně složil", "zkoušku", 
                    "profesní kvalifikace", "certificate", "certification", "graduated", 
                    "successfully passed", "exam", "professional qualification", "diplom", "diploma"
                ],
                "threshold": 2,
                "veto": True
            },
            "contract": {
                "keywords": [
                    "pracovní smlouva", "nájemní smlouva", "kupní smlouva", "employment contract", 
                    "lease agreement", "purchase agreement", "non-disclosure agreement", "nda",
                    "dohoda", "agreement"
                ],
                "threshold": 2,
                "veto": True
            },
            "reminder": {
                "keywords": [
                    "upomínka", "výzva k úhradě", "upomínací dopis", "reminder", 
                    "payment reminder", "overdue notice", "demand for payment", "dunning letter"
                ],
                "threshold": 1,
                "veto": True
            },
            "offer": {
                "keywords": [
                    "nabídka", "cenová kalkulace", "rozpočet", "kalkulace nákladů", "cenová nabídka", 
                    "offer", "quote", "quotation", "price estimate", "budget", "cost calculation", "proposal"
                ],
                "threshold": 1,
                "veto": False
            },
            "inquiry": {
                "keywords": [
                    "poptávka", "žádost o nabídku", "inquiry", "request for proposal", 
                    "rfp", "request for quote", "rfq"
                ],
                "threshold": 1,
                "veto": False
            },
            "internal": {
                "keywords": [
                    "koncept", "návrh dokumentu", "working copy", "draft", "document proposal"
                ],
                "threshold": 2,
                "veto": False
            }
        }
    
    def analyze(self, text: str, metadata: Optional[dict] = None) -> dict:
        """
        Analyze document for non-invoice anomalies.
        """
        # First, do fast rule-based detection
        rule_result = self._rule_based_detection(text, metadata)
        
        # If rule-based found clear anomaly with high confidence, return immediately
        if rule_result.get('confidence', 0) > 0.9 and rule_result.get('is_anomaly'):
            logger.debug(f"✓ Anomalie detekována pravidly: {rule_result['anomaly_type']}")
            return rule_result
        
        truncated = self._truncate_text(text, max_chars=5000)
        
        try:
            prompt = self.DETECTION_PROMPT.format(input_data=truncated)
        except (KeyError, IndexError) as e:
            logger.warning(f"Prompt format error: {e}")
            return rule_result
        
        try:
            client = self._get_client()
            response = client.generate(
                model=self.model,
                prompt=prompt,
                format="json",
                options={
                    "temperature": 0.0,
                    "num_predict": 512,     # OPRAVA: Zvýšeno na 512, aby se neusekl reasoning a nezničil se JSON
                    "num_ctx": self.num_ctx,
                    "top_p": 0.1,
                    "repeat_penalty": 1.1,
                },
                keep_alive="0s"
            )
            
            raw_output = response.get("response", "")
            parsed = self._extract_json_from_text(raw_output)
            
            if parsed is None:
                logger.warning(f"Anomaly detector nevrátil JSON: {raw_output[:150]}...")
                return rule_result
            
            # Merge with rule-based results
            result = self._merge_results(rule_result, parsed)
            
            logger.debug(f"✓ Anomalie: {'Detekována' if result['is_anomaly'] else 'Nedetektována'} ({result.get('anomaly_type', 'N/A')})")
            
            return result
            
        except Exception as e:
            logger.error(f"Anomaly detector chyba: {e}")
            return rule_result
    
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
                'reasoning': 'Žádné anomálie detekovány'
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
            'reasoning': 'Žádné anomálie detekovány'
        }