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
    - CVs and resumes
    - Certificates and licenses
    - Contracts and agreements
    - Reminders and offers
    - Orders and purchase requests
    - Emails and correspondence
    - Internal documents
    """
    
    DETECTION_PROMPT = """Jsi specialista na detekci anomálií v dokumentech. Tvým úkolem je identifikovat dokumenty, které NENÍ faktura.

=== TYPY ANOMÁLIÍ ===
1. ŽIVOTOPIS/CV: obsahuje jméno, vzdělání, pracovní zkušenosti, dovednosti
2. CERTIFIKÁT: obsahuje kurz, školení, absolvování, osvědčení, úspěšně složil
3. SMLOUVA: obsahuje smluvní strany, paragrafy, podmínky
4. UPOMÍNKA: obsahuje výzva k úhradě, upomínka, reminder
5. NABÍDKA: obsahuje cenová nabídka, kalkulace, rozpočet
6. OBJEDNÁVKA: obsahuje objednávka, purchase order, poptávka
7. E-MAIL: obsahuje e-mailová komunikace, předmět, od/komu
8. INTERNÍ DOKUMENT: obsahuje interní, pracovní koncept, návrh
9. VZDĚLÁVACÍ MATERIÁL: obsahuje kurz, modul, kapitola, lekce, IBM SkillsBuild

=== PRAVIDLA ===
- Pokud najdeš 2+ keyword pro daný typ → označ jako anomálii
- UPOMÍNKA a NABÍDKA stačí 1 keyword
- CERTIFIKÁT s "absolvoval", "kurz", "IBM SkillsBuild" → 1 keyword stačí
- Pokud dokument vypadá jako faktura → is_anomaly=false

=== DŮLEŽITÉ ===
- Odpovídej POUZE ve formátu JSON
- Žádný další text, žádné vysvětlení mimo JSON
- Začni hned JSON objektem

=== FORMÁT VÝSTUPU ===
{{
  "is_anomaly": true/false,
  "anomaly_type": "cv_resume|certificate|contract|reminder|offer|order|email|internal|education|null",
  "confidence": 0.0-1.0,
  "detected_keywords": ["seznam nalezených keyword"],
  "flags": ["seznam varovných signálů"],
  "reasoning": "vysvětlení proč je/není anomálie"
}}

=== TEXT DOKUMENTU ===
{input_data}"""

    def __init__(self, model: str = "llama3.2", timeout: int = 30, patterns_file: Optional[str] = None):
        super().__init__(model, timeout)
        self.patterns = self._load_patterns(patterns_file)
    
    def _load_patterns(self, patterns_file: Optional[str]) -> dict:
        """Load anomaly detection patterns from JSON file."""
        if patterns_file and Path(patterns_file).exists():
            try:
                with open(patterns_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Chyba načtení patternů: {e}")
        
        # Default patterns (embedded fallback)
        return {
            "reminder": {
                "keywords": ["upomínka", "reminder", "výzva k úhradě"],
                "threshold": 1,
                "veto": True  # Immediate reject
            },
            "offer": {
                "keywords": ["nabídka", "offer", "cenová kalkulace", "rozpočet"],
                "threshold": 1,
                "veto": False
            },
            "cv_resume": {
                "keywords": ["životopis", "curriculum", "vzdělání", "praxe", "narozen"],
                "threshold": 2,
                "veto": True
            },
            "certificate": {
                "keywords": ["certifikát", "osvědčení", "absolvoval", "kurz", "školení"],
                "threshold": 2,
                "veto": True
            },
            "contract": {
                "keywords": ["smlouva", "dohoda", "dodatek", "nájemní"],
                "threshold": 2,
                "veto": True
            }
        }
    
    def analyze(self, text: str, metadata: Optional[dict] = None) -> dict:
        """
        Analyze document for non-invoice anomalies.
        
        Args:
            text: Extracted text from document
            metadata: Optional metadata (filename can hint at document type)
            
        Returns:
            Anomaly detection result with type and confidence
        """
        # First, do fast rule-based detection
        rule_result = self._rule_based_detection(text, metadata)
        
        # If rule-based found clear anomaly with high confidence, return immediately
        if rule_result.get('confidence', 0) > 0.9 and rule_result.get('is_anomaly'):
            logger.debug(f"✓ Anomalie detekována pravidly: {rule_result['anomaly_type']}")
            return rule_result
        
        # Otherwise, use AI for detailed analysis
        truncated = self._truncate_text(text, max_chars=5000)
        
        try:
            prompt = self.DETECTION_PROMPT.format(input_data=truncated)
        except (KeyError, IndexError) as e:
            logger.warning(f"Prompt format error: {e}")
            # Fallback to rule-based only
            return rule_result
        
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
            parsed = self._extract_json_from_text(raw_output)
            
            if parsed is None:
                logger.warning(f"Anomaly detector nevrátil JSON: {raw_output[:150]}...")
                # Fallback to rule-based
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
        
        # Check filename for additional hints
        if metadata and 'filename' in metadata:
            filename = metadata['filename'].lower()
            for anomaly_type in ['životopis', 'certifikát', 'smlouva', 'upomínka', 'nabídka']:
                if anomaly_type in filename:
                    confidence = min(0.98, confidence + 0.1)
                    if primary['type'] != anomaly_type.replace('ů', 'u').replace('í', 'i'):
                        primary['type'] = anomaly_type.replace('ů', 'u').replace('í', 'i')
        
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
        # If both agree on anomaly
        if rule_result['is_anomaly'] and ai_result.get('is_anomaly'):
            # Use AI's detailed analysis but keep rule-based confidence if higher
            confidence = max(rule_result['confidence'], ai_result.get('confidence', 0))
            return {
                **ai_result,
                'confidence': confidence,
                'detected_keywords': list(set(
                    rule_result.get('detected_keywords', []) + 
                    ai_result.get('detected_keywords', [])
                ))
            }
        
        # If rules say anomaly but AI disagrees
        if rule_result['is_anomaly'] and not ai_result.get('is_anomaly'):
            # Trust rules if confidence is high
            if rule_result['confidence'] > 0.85:
                return rule_result
            # Otherwise, use AI with reduced confidence
            ai_result['confidence'] = ai_result.get('confidence', 0.5) * 0.8
            return ai_result
        
        # If AI found anomaly but rules didn't
        if ai_result.get('is_anomaly') and not rule_result['is_anomaly']:
            # Reduce confidence for AI-only detection
            ai_result['confidence'] = ai_result.get('confidence', 0.5) * 0.7
            return ai_result
        
        # Both agree: no anomaly
        return {
            'is_anomaly': False,
            'anomaly_type': None,
            'confidence': 0.95,
            'detected_keywords': [],
            'flags': [],
            'reasoning': 'Žádné anomálie detekovány'
        }
