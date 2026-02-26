"""
Consensus Engine
Combines results from multiple agents using weighted voting

v6.5 (Aktualizováno):
- Sjednocení a rozšíření NON_INVOICE typů
- Přidána robustní podpora pro anglické životopisy a nesouvisející texty
- Čištění duplicitního kódu (konstanty přesunuty do třídy)
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Konfigurační konstanty
MIN_REALISTIC_AMOUNT = 10  
CHECK_AMOUNT_REALISTIC = False  

# Invoice keywords pro kontrolu v anomaly detectoru (česká + anglická)
INVOICE_KEYWORDS_CS = [
    'faktura', 'faktúry', 'daňový doklad', 'zálohová faktura', 'konečná faktura', 'proforma',
    'dodavatel', 'odběratel', 'objednatel', 'zhotovitel', 'poskytovatel', 'příjemce',
    'ičo', 'dič', 'společnost', 'firma', 's.r.o.', 'a.s.', 'v.o.s.',
    'datum vystavení', 'datum splatnosti', 'vystaveno', 'splatnost', 'du', 'dv',
    'celkem', 'k úhradě', 'částka', 'cena', 'součet', 'úhrada', 'platba',
    'bez dph', 'dpH', 'sazba', 'základ daně',
    'variabilní symbol', 'konstantní symbol', 'specifický symbol', 'banka', 'účet', 'iban', 'bic', 'swift',
    'kč', 'czk', 'eur', '€', 'usd', '$', 'gbp', '£',
]

INVOICE_KEYWORDS_EN = [
    'invoice', 'tax document', 'proforma', 'bill', 'receipt',
    'supplier', 'vendor', 'customer', 'buyer', 'seller', 'contractor',
    'company', 'limited', 'inc.', 'corp.', 'gmbh',
    'vat', 'tax id', 'registration no',
    'issue date', 'due date', 'date of issue', 'dated',
    'total', 'amount', 'price', 'sum', 'payment', 'balance',
    'excl. vat', 'incl. vat', 'vat rate', 'tax base', 'subtotal',
    'payment reference', 'bank account', 'account no', 'iban', 'bic', 'swift',
    'eur', 'usd', 'gbp', 'czk', 'pln', 'huf', '€', '$', '£',
]


@dataclass
class AgentResult:
    """Result from a single agent."""
    agent_name: str
    result: dict
    weight: float
    veto_power: bool = False


class ConsensusEngine:
    """
    Combines results from multiple agents using weighted voting.
    """
    
    # VYLEPŠENÍ: Centrální definice anomálií pro celý engine (CZ + EN)
    NON_INVOICE_ANOMALY_TYPES = [
        # Životopisy a osobní dokumenty
        'cv_resume', 'zivotopis', 'životopis', 'cv', 'resume', 'curriculum',
        'education', 'vzdělání', 'skills', 'dovednosti', 'experience', 'praxe',
        # Certifikáty a smlouvy
        'certifikát', 'certifikat', 'osvědčení', 'certificate',
        'smlouva', 'contract', 'dohoda', 'agreement', 'plná moc', 'plnomocenství',
        # Obchodní texty
        'nabídka', 'offer', 'objednávka', 'order', 'licence', 'license'
    ]
    
    def __init__(
        self,
        threshold_accept: float = 0.7,
        threshold_review: float = 0.5,
        anomaly_veto_threshold: float = 0.85
    ):
        self.threshold_accept = threshold_accept
        self.threshold_review = threshold_review
        self.anomaly_veto_threshold = anomaly_veto_threshold
        
        # Agent weights
        self.weights = {
            'classifier': 0.4,
            'extractor': 0.3,
            'anomaly': 0.3
        }
    
    def calculate_consensus(
        self,
        classifier_result: dict,
        extractor_result: dict,
        anomaly_result: dict,
        raw_text: str = None 
    ) -> dict:
        
        classifier_conf = classifier_result.get('confidence', 0)
        classifier_is_invoice = classifier_result.get('is_invoice', False)

        veto_result = self._check_anomaly_veto(anomaly_result, classifier_result)
        if veto_result:
            return veto_result

        reasoning = classifier_result.get('reasoning', '').lower()
        
        # VYLEPŠENÍ: Rozšířeno o naše nová slova ze životopisů
        NON_INVOICE_PATTERNS = [
            # CZ
            ('jízdenka', 'ticket'), ('lístek', 'ticket'), ('vstupenka', 'ticket'),
            ('životopis', 'cv/resume'), ('praxe', 'cv/resume'), ('dovednosti', 'cv/resume'), ('vzdělání', 'cv/resume'),
            ('certifikát', 'certificate'), ('osvědčení', 'certificate'),
            ('smlouva', 'contract'), ('dohoda', 'agreement'),
            ('plná moc', 'power of attorney'), ('plnomocenství', 'power of attorney'),
            ('nabídka', 'offer'), ('poptávka', 'inquiry'), ('ceník', 'price list'),
            ('katalog', 'catalog'), ('reklama', 'advertisement'), ('leták', 'flyer'),
            ('pozvánka', 'invitation'), ('upomínka', 'reminder'), ('výzva', 'notice'),
            ('rozhodnutí', 'decision'), ('usnesení', 'resolution'), ('zápis', 'minutes'),
            ('prezentace', 'presentation'), ('návod', 'manual'), ('manuál', 'manual'),
            # EN
            ('ticket', 'ticket'),
            ('cv', 'cv/resume'), ('resume', 'cv/resume'), ('curriculum', 'cv/resume'),
            ('education', 'cv/resume'), ('skills', 'cv/resume'), ('experience', 'cv/resume'),
            ('certificate', 'certificate'), ('contract', 'contract'), ('agreement', 'agreement'),
            ('offer', 'offer'), ('quote', 'quote'), ('quotation', 'quote'),
            ('price list', 'price list'), ('catalog', 'catalog'), ('flyer', 'flyer'),
            ('invitation', 'invitation'), ('reminder', 'reminder'), ('notice', 'notice'),
            ('minutes', 'minutes'), ('presentation', 'presentation'), ('manual', 'manual'), ('guide', 'manual'),
        ]
        
        reasoning_classifier_says_not_invoice = False
        detected_document_type = None
        
        NOT_INVOICE_PATTERNS = [
            'není faktura', 'není to faktura', 'nejedná se o fakturu',
            'není daňový doklad', 'nejedná o fakturu',
            'dokument není', 'toto není faktura',
            'not an invoice', 'is not an invoice', 'not a tax document',
            'this is not an invoice', 'document is not', 'does not appear to be an invoice'
        ]
        
        for pattern in NOT_INVOICE_PATTERNS:
            if pattern in reasoning:
                reasoning_classifier_says_not_invoice = True
                for doc_pattern, doc_name in NON_INVOICE_PATTERNS:
                    if doc_pattern in reasoning:
                        detected_document_type = doc_name
                        break
                break
        
        if not reasoning_classifier_says_not_invoice:
            for doc_pattern, doc_name in NON_INVOICE_PATTERNS:
                if (f'je to {doc_pattern}' in reasoning or 
                    f'jedná se o {doc_pattern}' in reasoning or 
                    f'jde o {doc_pattern}' in reasoning or
                    f'this is a {doc_pattern}' in reasoning or
                    f'this is an {doc_pattern}' in reasoning):
                    reasoning_classifier_says_not_invoice = True
                    detected_document_type = doc_name
                    break
        
        if reasoning_classifier_says_not_invoice:
            logger.debug(f"  ✗ Classifier REASONING explicitně říká NENÍ faktura: '{reasoning[:100]}...'")
            logger.debug(f"  Detekovaný typ dokumentu: {detected_document_type}")
            return {
                'is_invoice': False,
                'confidence': max(classifier_conf, 0.9), 
                'decision_type': 'auto_reject',
                'weighted_score': 0.1,
                'agent_scores': {
                    'classifier': 1 - classifier_conf, 
                    'extractor': 0.0, 
                    'anomaly': 1 - anomaly_result.get('confidence', 0)
                },
                'agent_agreement': {
                    'full_agreement': False,
                    'majority_agreement': True,
                    'agreement_count': 2, 
                    'total_agents': 3
                },
                'reasoning': f"Classifier v reasoningu explicitně uvedl že dokument NENÍ faktura: '{reasoning[:150]}...' → NENÍ faktura (typ: {detected_document_type or 'ne-faktura'})",
                'extracted_data': {},
                'classifier_reasoning_detected': True,
                'detected_document_type': detected_document_type
            }
        
        keyword_check = None
        if classifier_is_invoice and classifier_conf >= 0.5 and raw_text:
            keyword_check = self._check_invoice_keywords(raw_text)
            if not keyword_check['found']:
                logger.warning(f"  ⚠️ RETRY: Classifier řekl JE faktura ({classifier_conf:.0%}) ale text neobsahuje invoice keywords!")
                return {
                    'is_invoice': None, 
                    'confidence': 0.0,
                    'decision_type': 'retry',
                    'retry_reason': 'missing_invoice_keywords',
                    'keyword_analysis': keyword_check,
                    'reasoning': f"Classifier potvrdil fakturu ({classifier_conf:.0%}) ale text neobsahuje klíčová invoice slova. Nutná kontrola."
                }

        extractor_fields_found = sum([
            1 if extractor_result.get('invoice_number') else 0,
            1 if extractor_result.get('vendor_name') else 0,
            1 if extractor_result.get('customer_name') else 0,
            1 if extractor_result.get('issue_date') and extractor_result.get('issue_date') != '0000-00-00' else 0,
            1 if extractor_result.get('due_date') and extractor_result.get('due_date') != '0000-00-00' else 0,
            1 if extractor_result.get('total_amount') is not None or bool(extractor_result.get('total_amount_raw')) else 0,
            1 if extractor_result.get('bank_account') else 0,
        ])
        
        extractor_completeness = extractor_result.get('completeness_score', 0)
        elements = classifier_result.get('elements_present', {})
        has_identification = elements.get('identification', False)
        has_financial = elements.get('financial', False)
        has_subjects = elements.get('subjects', False)
        has_dates = elements.get('dates', False)
        has_payment_info = elements.get('payment_info', False)

        if classifier_is_invoice and classifier_conf >= 0.6 and extractor_fields_found <= 1:
            logger.debug(f"  ⚠️ DETEKOVÁNA HALUCINACE: Classifier řekl JE faktura ({classifier_conf:.0%}) ale Extractor našel pouze {extractor_fields_found} elementů → REJECT")
            return {
                'is_invoice': False,
                'confidence': max(classifier_conf, 0.9),
                'decision_type': 'auto_reject',
                'weighted_score': 0.1,
                'agent_scores': {
                    'classifier': 0.0,
                    'extractor': extractor_completeness,
                    'anomaly': 1 - anomaly_result.get('confidence', 0)
                },
                'agent_agreement': {
                    'full_agreement': False,
                    'majority_agreement': True,
                    'agreement_count': 2,
                    'total_agents': 3
                },
                'reasoning': f"Klasifikátor tvrdil že všechny elementy jsou přítomny ({classifier_conf:.0%}) ale Extraktor našel pouze {extractor_fields_found} elementů → Klasifikátor PRAVDĚPODOBNĚ HALUCINUJE → NENÍ faktura",
                'extracted_data': {},
                'classifier_hallucination_detected': True
            }

        if classifier_is_invoice and classifier_conf >= 0.5 and 2 <= extractor_fields_found <= 3:
            logger.debug(f"  ⚠️ ROZPOR: Classifier řekl JE faktura ({classifier_conf:.0%}) ale Extractor našel pouze {extractor_fields_found} elementy → HUMAN REVIEW")
            return {
                'is_invoice': None,
                'confidence': 0.5,
                'decision_type': 'human_review',
                'weighted_score': 0.5,
                'agent_scores': {
                    'classifier': classifier_conf,
                    'extractor': extractor_completeness,
                    'anomaly': 0.5
                },
                'agent_agreement': {
                    'full_agreement': False,
                    'majority_agreement': False,
                    'agreement_count': 1,
                    'total_agents': 3
                },
                'reasoning': f"Klasifikátor tvrdí že je to faktura ({classifier_conf:.0%}) ale Extraktor našel pouze {extractor_fields_found} elementy (vendor/customer/account) → Chybí datum nebo částka → Nutná lidská kontrola",
                'extracted_data': self._extract_final_data(classifier_result, extractor_result),
                'classifier_extractor_conflict': True
            }

        if not classifier_is_invoice and extractor_fields_found < 3:
            logger.debug(f"  ✗ Classifier: NENÍ faktura + Extractor našel jen {extractor_fields_found} elementů → AUTO-REJECT")
            anomaly_confirms = anomaly_result.get('is_anomaly', False)
            return {
                'is_invoice': False,
                'confidence': max(classifier_conf, 0.85) if anomaly_confirms else max(classifier_conf, 0.8),
                'decision_type': 'auto_reject',
                'weighted_score': 0.2,
                'agent_scores': {
                    'classifier': 1 - classifier_conf,
                    'extractor': extractor_completeness,
                    'anomaly': 1 - anomaly_result.get('confidence', 0)
                },
                'agent_agreement': {
                    'full_agreement': anomaly_confirms,
                    'majority_agreement': True,
                    'agreement_count': 3 if anomaly_confirms else 2,
                    'total_agents': 3
                },
                'reasoning': f"Classifier řekl není faktura + Extractor našel jen {extractor_fields_found} elementů (potřebuje ≥3) → NENÍ faktura" + (" + Anomalie potvrzuje" if anomaly_confirms else ""),
                'extracted_data': {}
            }

        if not classifier_is_invoice and extractor_fields_found >= 4:
            anomaly_type = anomaly_result.get('anomaly_type', '')
            
            # VYLEPŠENÍ: Využíváme společný seznam anomálií ze třídy
            is_definite_non_invoice = any(
                keyword in (anomaly_type or '').lower() 
                for keyword in self.NON_INVOICE_ANOMALY_TYPES
            )
            
            if is_definite_non_invoice and anomaly_result.get('is_anomaly', False):
                logger.debug(f"  ✗ Classifier: NENÍ faktura + Extractor našel {extractor_fields_found} elementů ALE Anomaly detekovala '{anomaly_type}' → REJECT (Extractor halucinace)")
                return {
                    'is_invoice': False,
                    'confidence': max(classifier_conf, 0.9),
                    'decision_type': 'auto_reject',
                    'weighted_score': 0.15,
                    'agent_scores': {
                        'classifier': 1 - classifier_conf,
                        'extractor': 0.0,
                        'anomaly': anomaly_result.get('confidence', 0)
                    },
                    'agent_agreement': {
                        'full_agreement': True,
                        'majority_agreement': True,
                        'agreement_count': 2,
                        'total_agents': 3
                    },
                    'reasoning': f"Classifier řekl není faktura + Anomaly detekovala '{anomaly_type}' → NENÍ faktura (Extractor halucinoval {extractor_fields_found} elementů)",
                    'extracted_data': {},
                    'extractor_hallucination_detected': True
                }
            
            extracted_amount = extractor_result.get('total_amount')
            extracted_amount_raw = extractor_result.get('total_amount_raw')
            has_real_amount = (extracted_amount is not None and extracted_amount > 0) or bool(extracted_amount_raw)

            if not has_real_amount:
                logger.debug(f"  ✗ Classifier: NENÍ faktura + Extractor našel {extractor_fields_found} elementů ALE žádná skutečná částka → REJECT (halucinace)")
                return {
                    'is_invoice': False,
                    'confidence': max(classifier_conf, 0.9),
                    'decision_type': 'auto_reject',
                    'weighted_score': 0.15,
                    'agent_scores': {
                        'classifier': 1 - classifier_conf,
                        'extractor': 0.0,
                        'anomaly': 1 - anomaly_result.get('confidence', 0)
                    },
                    'agent_agreement': {
                        'full_agreement': True,
                        'majority_agreement': True,
                        'agreement_count': 2,
                        'total_agents': 3
                    },
                    'reasoning': f"Classifier řekl není faktura + Extractor nemůže najít skutečnou částku → NENÍ faktura (halucinace)",
                    'extracted_data': {},
                    'extractor_hallucination_detected': True
                }

            logger.debug(f"  ✓ Classifier: NENÍ faktura ALE Extractor našel {extractor_fields_found} elementů (vč. částky {extracted_amount_raw or extracted_amount}) → JE FAKTURA (Extractor priorita)")
            return {
                'is_invoice': True,
                'confidence': min(0.85, extractor_completeness + 0.1),
                'decision_type': 'auto_accept',
                'weighted_score': extractor_completeness,
                'agent_scores': {
                    'classifier': 0.0,
                    'extractor': extractor_completeness,
                    'anomaly': 1 - anomaly_result.get('confidence', 0)
                },
                'agent_agreement': {
                    'full_agreement': False,
                    'majority_agreement': True,
                    'agreement_count': 2,
                    'total_agents': 3
                },
                'reasoning': f"Extractor našel {extractor_fields_found} elementů (≥4) vč. částky → JE FAKTURA i přes classifier",
                'extracted_data': self._extract_final_data(classifier_result, extractor_result)
            }

        if classifier_is_invoice and classifier_conf >= 0.7 and extractor_fields_found == 0:
            logger.debug(f"  ✗ Classifier: JE faktura ({classifier_conf:.0%}) ALE Extractor nenašel ŽÁDNÉ elementy → NENÍ faktura (Extractor priorita)")
            return {
                'is_invoice': False,
                'confidence': max(classifier_conf, 0.75),
                'decision_type': 'auto_reject',
                'weighted_score': 0.2,
                'agent_scores': {
                    'classifier': 0.0,
                    'extractor': 0.0,
                    'anomaly': 1 - anomaly_result.get('confidence', 0)
                },
                'agent_agreement': {
                    'full_agreement': False,
                    'majority_agreement': True,
                    'agreement_count': 2,
                    'total_agents': 3
                },
                'reasoning': f"Classifier řekl faktura ({classifier_conf:.0%}) ale Extractor nenašel žádné elementy → NENÍ faktura (halucinace)",
                'extracted_data': {}
            }

        if classifier_is_invoice and extractor_fields_found >= 1:
            pass

        all_elements_present = all([
            has_identification, has_subjects, has_dates, has_financial, has_payment_info
        ])

        if classifier_conf >= 0.85 and all_elements_present and classifier_is_invoice:
            logger.debug(f"  ✓ Classifier velmi jistý ({classifier_conf:.0%}) + všech 5 elementů → AUTO-ACCEPT")
            return {
                'is_invoice': True,
                'confidence': classifier_conf,
                'decision_type': 'auto_accept',
                'weighted_score': classifier_conf,
                'agent_scores': {
                    'classifier': classifier_conf,
                    'extractor': extractor_completeness,
                    'anomaly': 1 - anomaly_result.get('confidence', 0)
                },
                'agent_agreement': {
                    'full_agreement': False,
                    'majority_agreement': True,
                    'agreement_count': 2,
                    'total_agents': 3
                },
                'reasoning': f"Classifier velmi jistý ({classifier_conf:.0%}) + všech 5 elementů přítomno",
                'extracted_data': self._extract_final_data(classifier_result, extractor_result)
            }

        weighted_score = self._calculate_weighted_score(classifier_result, extractor_result, anomaly_result)
        final_score = self._apply_penalties(weighted_score, extractor_result, classifier_result)
        decision, decision_type = self._make_decision(final_score)

        return {
            'is_invoice': decision,
            'confidence': round(final_score, 3),
            'decision_type': decision_type,
            'weighted_score': round(weighted_score, 3),
            'agent_scores': {
                'classifier': classifier_result.get('confidence', 0),
                'extractor': extractor_result.get('completeness_score', 0),
                'anomaly': 1 - anomaly_result.get('confidence', 0)
            },
            'agent_agreement': self._check_agreement(classifier_result, extractor_result, anomaly_result),
            'reasoning': self._build_reasoning(classifier_result, extractor_result, anomaly_result, final_score, extractor_fields_found),
            'extracted_data': self._extract_final_data(classifier_result, extractor_result)
        }
    
    def _check_anomaly_veto(self, anomaly_result: dict, classifier_result: dict = None) -> Optional[dict]:
        if not anomaly_result.get('is_anomaly'):
            return None

        confidence = anomaly_result.get('confidence', 0)
        anomaly_type = anomaly_result.get('anomaly_type', '')
        flags = anomaly_result.get('flags', [])

        classifier_agrees = False
        if classifier_result:
            clf_is_invoice = classifier_result.get('is_invoice', False)
            clf_conf = classifier_result.get('confidence', 0)
            if not clf_is_invoice and clf_conf >= 0.5:
                classifier_agrees = True

        # VYLEPŠENÍ: Využíváme společný seznam anomálií ze třídy
        is_definite_non_invoice = any(
            keyword in (anomaly_type or '').lower() 
            for keyword in self.NON_INVOICE_ANOMALY_TYPES
        )

        effective_threshold = self.anomaly_veto_threshold
        if classifier_agrees:
            effective_threshold = 0.5
        
        if is_definite_non_invoice and classifier_agrees:
            if confidence >= 0.4:
                logger.debug(f"🚫 ANOMALY VETO: {anomaly_type} (conf: {confidence:.2f}) + Classifier agrees = DEFINITE NON-INVOICE")
                return {
                    'is_invoice': False,
                    'confidence': max(confidence, 0.9),
                    'decision_type': 'anomaly_veto',
                    'veto_reason': f"Detekován konkrétní typ dokumentu: {anomaly_type} (Classifier souhlasí)",
                    'anomaly_type': anomaly_type,
                    'agent_scores': {
                        'classifier': 1 - classifier_result.get('confidence', 0),
                        'extractor': 0,
                        'anomaly': confidence
                    },
                    'is_definite_non_invoice': True
                }

        if confidence >= effective_threshold or 'veto' in flags:
            veto_confidence = confidence
            if classifier_agrees:
                veto_confidence = max(confidence, 0.85)
                logger.debug(f"🚫 Anomaly veto + Classifier agrees: {anomaly_type} (confidence: {veto_confidence:.2f})")
            else:
                logger.debug(f"🚫 Anomaly veto: {anomaly_type} (confidence: {confidence:.2f})")

            return {
                'is_invoice': False,
                'confidence': veto_confidence,
                'decision_type': 'anomaly_veto',
                'veto_reason': f"Detekována anomálie: {anomaly_type}" + (" (Classifier souhlasí)" if classifier_agrees else ""),
                'anomaly_type': anomaly_type,
                'agent_scores': {
                    'classifier': 1 - classifier_result.get('confidence', 0) if classifier_agrees else 0,
                    'extractor': 0,
                    'anomaly': confidence
                }
            }

        return None
    
    def _calculate_weighted_score(self, classifier: dict, extractor: dict, anomaly: dict) -> float:
        classifier_score = classifier.get('confidence', 0)
        if not classifier.get('is_invoice'):
            classifier_score = 1 - classifier_score
        
        extractor_score = extractor.get('completeness_score', 0)
        anomaly_score = 1 - anomaly.get('confidence', 0) if anomaly.get('is_anomaly') else 0.95
        
        weighted = (
            classifier_score * self.weights['classifier'] +
            extractor_score * self.weights['extractor'] +
            anomaly_score * self.weights['anomaly']
        )
        return min(1.0, max(0.0, weighted))
    
    def _apply_penalties(self, score: float, extractor_result: dict, classifier_result: dict = None) -> float:
        penalty = 0.0

        validation_errors = extractor_result.get('validation_errors', [])
        completeness = extractor_result.get('completeness_score', 0)

        has_vendor = bool(extractor_result.get('vendor_name'))
        has_customer = bool(extractor_result.get('customer_name'))
        has_amount = extractor_result.get('total_amount') is not None or bool(extractor_result.get('total_amount_raw'))
        has_issue_date = bool(extractor_result.get('issue_date') and extractor_result.get('issue_date') != '0000-00-00')

        classifier_confirms_invoice = False
        extractor_technical_failure = False
        extractor_found_data_no_amount = False
        
        if classifier_result:
            clf_conf = classifier_result.get('confidence', 0)
            clf_is_invoice = classifier_result.get('is_invoice', False)
            elements = classifier_result.get('elements_present', {})
            all_elements_present = all([
                elements.get('identification', False),
                elements.get('subjects', False),
                elements.get('dates', False),
                elements.get('financial', False),
                elements.get('payment_info', False)
            ])
            if clf_conf >= 0.85 and clf_is_invoice and all_elements_present:
                classifier_confirms_invoice = True
                extractor_completeness = extractor_result.get('completeness_score', 0)
                if extractor_completeness < 0.3:
                    extractor_technical_failure = True
                elif extractor_completeness > 0.3 and not has_amount:
                    extractor_found_data_no_amount = True

        if not has_amount:
            if classifier_confirms_invoice and extractor_found_data_no_amount:
                logger.debug("  ✗ Chybí částka - extractor běžel ale nenašel → AUTO-REJECT")
                penalty += 0.9 
            elif classifier_confirms_invoice and extractor_technical_failure:
                logger.debug("  ⚠️ Chybí částka, ale classifier potvrdil fakturu a extractor selhal technicky → ignoruji penalizaci")
            elif classifier_confirms_invoice:
                logger.debug("  ⚠️ Chybí částka - classifier řekl faktura ale extractor nenašel → penalty")
                penalty += 0.6
            else:
                logger.debug("  ⚠️ Chybí částka - PRAVDĚPODOBNĚ NENÍ faktura")
                penalty += 0.6
        else:
            if CHECK_AMOUNT_REALISTIC:
                amount = extractor_result.get('total_amount')
                if amount is not None:
                    try:
                        amount_value = float(amount) if isinstance(amount, str) else amount
                        if amount_value < MIN_REALISTIC_AMOUNT:
                            logger.debug(f"  ⚠️ Nízká částka: {amount_value} - mírná penalizace")
                            penalty += 0.10
                    except (ValueError, TypeError):
                        pass
        
        if has_vendor and not has_customer:
            logger.debug("  ⚠️ Chybí odběratel - některé faktury nemusí mít")
            penalty += 0.05
        
        if not extractor_result.get('invoice_number'):
            logger.debug("  ⚠️ Chybí číslo faktury")
            penalty += 0.10
        
        if not extractor_result.get('bank_account') and not extractor_result.get('variable_symbol'):
            logger.debug("  ⚠️ Chybí platební údaje - nemusí být vždy vyžadováno")
            penalty += 0.05
        
        if completeness < 0.5:
            penalty += 0.15
        elif completeness < 0.6:
            penalty += 0.10
        elif completeness < 0.7:
            penalty += 0.05
        
        if len(validation_errors) >= 3:
            penalty += 0.15
        elif len(validation_errors) >= 2:
            penalty += 0.08
        elif len(validation_errors) >= 1:
            penalty += 0.03
        
        final_score = score * (1 - penalty)
        logger.debug(f"  Penalizace: {penalty:.2f}, finální skóre: {final_score:.2f}")
        return min(1.0, max(0.0, final_score))
    
    def _make_decision(self, score: float) -> Tuple[bool, str]:
        if score >= self.threshold_accept:
            return True, 'auto_accept'
        elif score >= self.threshold_review:
            return None, 'human_review'
        else:
            return False, 'auto_reject'
    
    def _check_agreement(self, classifier: dict, extractor: dict, anomaly: dict) -> dict:
        classifier_says_invoice = classifier.get('is_invoice', False)
        extractor_says_invoice = extractor.get('completeness_score', 0) > 0.5
        anomaly_says_clean = not anomaly.get('is_anomaly')
        
        agreements = [classifier_says_invoice, extractor_says_invoice, anomaly_says_clean]
        agreement_count = sum(agreements)
        
        return {
            'full_agreement': agreement_count == 3,
            'majority_agreement': agreement_count >= 2,
            'agreement_count': agreement_count,
            'total_agents': 3
        }
    
    def _build_reasoning(self, classifier: dict, extractor: dict, anomaly: dict, final_score: float, extractor_fields_found: int = None) -> str:
        reasons = []

        if classifier.get('is_invoice'):
            reasons.append(f"Klasifikátor: faktura ({classifier.get('confidence', 0):.0%})")
        else:
            reasons.append(f"Klasifikátor: není faktura ({classifier.get('confidence', 0):.0%})")

        completeness = extractor.get('completeness_score', 0)
        errors = extractor.get('validation_errors', [])
        
        if extractor_fields_found is not None:
            reasons.append(f"Extraktor: {extractor_fields_found} elementů")
        elif completeness > 0.7:
            reasons.append(f"Extraktor: kompletní data ({completeness:.0%})")
        elif errors:
            reasons.append(f"Extraktor: chybí {len(errors)} polí")
        else:
            reasons.append(f"Extraktor: data ({completeness:.0%})")

        if anomaly.get('is_anomaly'):
            reasons.append(f"Anomalie: {anomaly.get('anomaly_type')} ({anomaly.get('confidence', 0):.0%})")
        else:
            reasons.append("Anomalie: žádné detekovány")

        if final_score >= self.threshold_accept:
            reasons.append(f"→ Auto-accept (score: {final_score:.2f})")
        elif final_score >= self.threshold_review:
            reasons.append(f"→ Human review (score: {final_score:.2f})")
        else:
            reasons.append(f"→ Auto-reject (score: {final_score:.2f})")

        return " | ".join(reasons)
    
    def _extract_final_data(self, classifier: dict, extractor: dict) -> dict:
        return {
            'invoice_number': extractor.get('invoice_number'),
            'vendor_name': extractor.get('vendor_name'),
            'customer_name': extractor.get('customer_name'),
            'issue_date': extractor.get('issue_date'),
            'due_date': extractor.get('due_date'),
            'total_amount': extractor.get('total_amount'),
            'currency': extractor.get('currency'),
            'vendor_ico': extractor.get('vendor_ico'),
            'vendor_dic': extractor.get('vendor_dic'),
            'bank_account': extractor.get('bank_account'),
            'variable_symbol': extractor.get('variable_symbol'),
        }

    def _check_invoice_keywords(self, text: str) -> dict:
        text_lower = text.lower()
        
        categories = {
            'identification': {
                'cs': ['faktura', 'faktúry', 'daňový doklad', 'zálohová faktura', 'proforma'],
                'en': ['invoice', 'tax document', 'proforma', 'bill']
            },
            'subjects': {
                'cs': ['dodavatel', 'odběratel', 'objednatel', 'zhotovitel', 'ičo', 'dič', 's.r.o.', 'a.s.'],
                'en': ['supplier', 'vendor', 'customer', 'contractor', 'vat', 'tax id', 'limited', 'inc.', 'gmbh']
            },
            'dates': {
                'cs': ['datum vystavení', 'datum splatnosti', 'vystaveno', 'splatnost', 'du', 'dv'],
                'en': ['issue date', 'due date', 'date of issue', 'dated']
            },
            'financial': {
                'cs': ['celkem', 'k úhradě', 'částka', 'cena', 'úhrada', 'bez dph', 'dpH', 'sazba'],
                'en': ['total', 'amount', 'price', 'payment', 'balance', 'excl. vat', 'incl. vat', 'subtotal']
            },
            'payment_info': {
                'cs': ['variabilní symbol', 'banka', 'účet', 'iban', 'bic', 'swift'],
                'en': ['payment reference', 'bank account', 'account no', 'iban', 'bic', 'swift']
            },
            'currency': {
                'cs': ['kč', 'czk', 'eur', '€', 'usd', '$', '£', 'gbp'],
                'en': ['eur', 'usd', 'gbp', 'czk', '€', '$', '£']
            }
        }
        
        found_keywords = []
        categories_found = []
        categories_missing = []
        
        for category, keywords in categories.items():
            category_keywords = keywords['cs'] + keywords['en']
            found_in_category = [kw for kw in category_keywords if kw in text_lower]
            
            if found_in_category:
                found_keywords.extend(found_in_category)
                categories_found.append(category)
            else:
                categories_missing.append(category)
        
        has_identification = 'identification' in categories_found
        has_financial = 'financial' in categories_found
        
        category_score = len(categories_found) / len(categories)
        
        critical_bonus = 0.0
        if has_identification:
            critical_bonus += 0.2
        if has_financial:
            critical_bonus += 0.2
        
        final_score = min(1.0, category_score * 0.6 + critical_bonus)
        
        is_found = (
            (has_identification and has_financial and len(categories_found) >= 3) or
            len(categories_found) >= 4
        )
        
        if 'faktura' in text_lower or 'invoice' in text_lower:
            is_found = True
        
        return {
            'found': is_found,
            'keywords_found': list(set(found_keywords)),
            'keywords_missing': categories_missing,
            'score': round(final_score, 3),
            'categories_found': categories_found,
            'categories_missing': categories_missing
        }

    def get_statistics(self, results: List[dict]) -> dict:
        if not results:
            return {}
        
        total = len(results)
        invoices = sum(1 for r in results if r.get('is_invoice'))
        non_invoices = sum(1 for r in results if r.get('is_invoice') is False)
        reviews = sum(1 for r in results if r.get('is_invoice') is None)
        
        confidences = [r.get('confidence', 0) for r in results]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0
        
        full_agreements = sum(
            1 for r in results 
            if r.get('agent_agreement', {}).get('full_agreement')
        )
        
        return {
            'total': total,
            'invoices': invoices,
            'non_invoices': non_invoices,
            'human_review': reviews,
            'invoice_percentage': round(invoices / total * 100, 1) if total > 0 else 0,
            'average_confidence': round(avg_confidence, 3),
            'full_agreement_rate': round(full_agreements / total * 100, 1) if total > 0 else 0,
        }