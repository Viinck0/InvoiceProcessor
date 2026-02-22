"""
Consensus Engine
Combines results from multiple agents using weighted voting
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Konfigurační konstanty
MIN_REALISTIC_AMOUNT = 10  # Minimální realistická částka
CHECK_AMOUNT_REALISTIC = True  # Povolit kontrolu nízkých částek


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
    
    Voting weights:
    - Classifier Agent: 40%
    - Extractor Agent: 30%
    - Anomaly Detector: 30% (with veto power)
    
    Decision thresholds:
    - ≥ 0.7: Auto-accept as invoice
    - 0.5-0.7: Human review
    - < 0.5: Auto-reject
    """
    
    def __init__(
        self,
        threshold_accept: float = 0.7,
        threshold_review: float = 0.5,
        anomaly_veto_threshold: float = 0.85
    ):
        """
        Initialize consensus engine.
        
        Args:
            threshold_accept: Confidence threshold for auto-accept
            threshold_review: Confidence threshold for human review
            anomaly_veto_threshold: Anomaly confidence for veto power
        """
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
        anomaly_result: dict
    ) -> dict:
        """
        Calculate consensus decision from all agent results.
        
        Args:
            classifier_result: Result from ClassifierAgent
            extractor_result: Result from ExtractorAgent
            anomaly_result: Result from AnomalyDetectorAgent
            
        Returns:
            Consensus decision with confidence and reasoning
        """
        # Check for anomaly veto first
        veto_result = self._check_anomaly_veto(anomaly_result)
        
        # SPECIAL CASE 1: If classifier says NOT invoice with high confidence, auto-reject
        classifier_conf = classifier_result.get('confidence', 0)
        classifier_is_invoice = classifier_result.get('is_invoice', False)
        
        if not classifier_is_invoice and classifier_conf >= 0.75:
            logger.debug(f"  ✗ Classifier rozhodl že NENÍ faktura ({classifier_conf:.0%}) → AUTO-REJECT")
            return {
                'is_invoice': False,
                'confidence': classifier_conf,
                'decision_type': 'auto_reject',
                'weighted_score': 1 - classifier_conf,
                'agent_scores': {
                    'classifier': classifier_conf,
                    'extractor': extractor_result.get('completeness_score', 0),
                    'anomaly': 1 - anomaly_result.get('confidence', 0)
                },
                'agent_agreement': {
                    'full_agreement': False,
                    'majority_agreement': True,
                    'agreement_count': 2,
                    'total_agents': 3
                },
                'reasoning': f"Classifier rozhodl že není faktura ({classifier_conf:.0%}): {classifier_result.get('reasoning', 'N/A')[:100]}",
                'extracted_data': self._extract_final_data(classifier_result, extractor_result)
            }
        
        # SPECIAL CASE 2: If classifier is very confident AND all elements present, auto-accept
        elements = classifier_result.get('elements_present', {})
        all_elements_present = all([
            elements.get('identification', False),
            elements.get('subjects', False),
            elements.get('dates', False),
            elements.get('financial', False),
            elements.get('payment_info', False)
        ])
        
        # CRITICAL CHECK: Extractor MUST find total_amount for auto-accept
        # If amount is missing, this is likely NOT a valid invoice regardless of classifier
        extractor_amount = extractor_result.get('total_amount')
        has_critical_data = extractor_amount is not None
        
        # If classifier is >85% confident, says is_invoice=true, all 5 elements present, 
        # AND extractor found critical data (amount), auto-accept
        if (classifier_conf >= 0.85 and all_elements_present and classifier_is_invoice 
            and has_critical_data):
            logger.debug(f"  ✓ Classifier velmi jistý ({classifier_conf:.0%}) + všechny elementy + částka nalezena → AUTO-ACCEPT")
            return {
                'is_invoice': True,
                'confidence': classifier_conf,
                'decision_type': 'auto_accept',
                'weighted_score': classifier_conf,
                'agent_scores': {
                    'classifier': classifier_conf,
                    'extractor': extractor_result.get('completeness_score', 0),
                    'anomaly': 1 - anomaly_result.get('confidence', 0)
                },
                'agent_agreement': {
                    'full_agreement': False,
                    'majority_agreement': True,
                    'agreement_count': 2,
                    'total_agents': 3
                },
                'reasoning': f"Classifier velmi jistý ({classifier_conf:.0%}) + všech 5 elementů přítomno + částka {extractor_amount}",
                'extracted_data': self._extract_final_data(classifier_result, extractor_result)
            }
        elif (classifier_conf >= 0.85 and all_elements_present and classifier_is_invoice 
              and not has_critical_data):
            # Classifier says invoice but Extractor found no amount - trust Extractor
            logger.debug(f"  ⚠️ Classifier řekl faktura ale Extractor nenašel částku → pokračovat normálním consensus")
        
        # Normal consensus calculation
        if veto_result:
            return veto_result
        
        # Calculate weighted score
        weighted_score = self._calculate_weighted_score(
            classifier_result,
            extractor_result,
            anomaly_result
        )
        
        # Apply penalties for missing elements
        final_score = self._apply_penalties(weighted_score, extractor_result)
        
        # Make decision
        decision, decision_type = self._make_decision(final_score)
        
        # Build consensus result
        return {
            'is_invoice': decision,
            'confidence': round(final_score, 3),
            'decision_type': decision_type,
            'weighted_score': round(weighted_score, 3),
            'agent_scores': {
                'classifier': classifier_result.get('confidence', 0),
                'extractor': extractor_result.get('completeness_score', 0),
                'anomaly': 1 - anomaly_result.get('confidence', 0)  # Invert anomaly score
            },
            'agent_agreement': self._check_agreement(
                classifier_result,
                extractor_result,
                anomaly_result
            ),
            'reasoning': self._build_reasoning(
                classifier_result,
                extractor_result,
                anomaly_result,
                final_score
            ),
            'extracted_data': self._extract_final_data(
                classifier_result,
                extractor_result
            )
        }
    
    def _check_anomaly_veto(self, anomaly_result: dict) -> Optional[dict]:
        """Check if anomaly detector should veto the decision."""
        if not anomaly_result.get('is_anomaly'):
            return None
        
        confidence = anomaly_result.get('confidence', 0)
        anomaly_type = anomaly_result.get('anomaly_type')
        flags = anomaly_result.get('flags', [])
        
        # Veto if confidence > threshold OR has veto flag
        if confidence >= self.anomaly_veto_threshold or 'veto' in flags:
            logger.debug(f"🚫 Anomaly veto: {anomaly_type} (confidence: {confidence:.2f})")
            return {
                'is_invoice': False,
                'confidence': confidence,
                'decision_type': 'anomaly_veto',
                'veto_reason': f"Detekována anomálie: {anomaly_type}",
                'anomaly_type': anomaly_type,
                'agent_scores': {
                    'classifier': 0,
                    'extractor': 0,
                    'anomaly': confidence
                }
            }
        
        return None
    
    def _calculate_weighted_score(
        self,
        classifier: dict,
        extractor: dict,
        anomaly: dict
    ) -> float:
        """Calculate weighted score from all agents."""
        # Classifier score (is invoice confidence)
        classifier_score = classifier.get('confidence', 0)
        if not classifier.get('is_invoice'):
            classifier_score = 1 - classifier_score
        
        # Extractor score (completeness)
        extractor_score = extractor.get('completeness_score', 0)
        
        # Anomaly score (inverted - lower anomaly = higher score)
        anomaly_score = 1 - anomaly.get('confidence', 0) if anomaly.get('is_anomaly') else 0.95
        
        # Weighted sum
        weighted = (
            classifier_score * self.weights['classifier'] +
            extractor_score * self.weights['extractor'] +
            anomaly_score * self.weights['anomaly']
        )
        
        return min(1.0, max(0.0, weighted))
    
    def _apply_penalties(self, score: float, extractor_result: dict) -> float:
        """Apply penalties for missing invoice elements."""
        penalty = 0.0
        
        # Get critical missing fields
        validation_errors = extractor_result.get('validation_errors', [])
        completeness = extractor_result.get('completeness_score', 0)
        
        # Critical fields that MUST be present for a valid invoice
        has_vendor = bool(extractor_result.get('vendor_name'))
        has_customer = bool(extractor_result.get('customer_name'))
        has_amount = extractor_result.get('total_amount') is not None
        has_issue_date = bool(extractor_result.get('issue_date') and extractor_result.get('issue_date') != '0000-00-00')
        
        # CRITICAL: If amount is missing, this is likely NOT an invoice
        if not has_amount:
            logger.debug("  ⚠️ Chybí částka - PRAVDĚPODOBNĚ NENÍ faktura")
            penalty += 0.6  # MAJOR penalty - missing amount is critical
        else:
            # Check for suspicious amounts (too low for real invoice)
            if CHECK_AMOUNT_REALISTIC:
                amount = extractor_result.get('total_amount')
                if amount is not None:
                    try:
                        amount_value = float(amount) if isinstance(amount, str) else amount
                        if amount_value < MIN_REALISTIC_AMOUNT:  # Less than 10 CZK/EUR/USD
                            logger.debug(f"  ⚠️ Podezřele nízká částka: {amount_value} - možná testovací dokument")
                            penalty += 0.25  # Suspicious - might be test/sample invoice
                        elif amount_value < 50:
                            logger.debug(f"  ⚠️ Nízká částka: {amount_value} - může být skutečná ale neobvyklá")
                            penalty += 0.1
                    except (ValueError, TypeError):
                        pass
        
        # If customer is missing but vendor present, minor penalty (some invoices don't have customer)
        if has_vendor and not has_customer:
            logger.debug("  ⚠️ Chybí odběratel - některé faktury nemusí mít")
            penalty += 0.05  # Minor penalty
        
        # If invoice number is missing, suspicious
        if not extractor_result.get('invoice_number'):
            logger.debug("  ⚠️ Chybí číslo faktury")
            penalty += 0.10
        
        # If no bank account or payment info, minor penalty (not all invoices need it)
        if not extractor_result.get('bank_account') and not extractor_result.get('variable_symbol'):
            logger.debug("  ⚠️ Chybí platební údaje - nemusí být vždy vyžadováno")
            penalty += 0.05  # Minor penalty
        
        # Penalty for low completeness - but be more lenient
        if completeness < 0.5:
            penalty += 0.15
        elif completeness < 0.6:
            penalty += 0.10
        elif completeness < 0.7:
            penalty += 0.05  # Reduced from 0.05
        
        # Penalty for many validation errors
        if len(validation_errors) >= 3:
            penalty += 0.15
        elif len(validation_errors) >= 2:
            penalty += 0.08
        elif len(validation_errors) >= 1:
            penalty += 0.03  # Reduced from 0.05
        
        final_score = score * (1 - penalty)
        logger.debug(f"  Penalizace: {penalty:.2f}, finální skóre: {final_score:.2f}")
        return min(1.0, max(0.0, final_score))
    
    def _make_decision(self, score: float) -> Tuple[bool, str]:
        """Make final decision based on score."""
        if score >= self.threshold_accept:
            return True, 'auto_accept'
        elif score >= self.threshold_review:
            return None, 'human_review'  # None = uncertain
        else:
            return False, 'auto_reject'
    
    def _check_agreement(
        self,
        classifier: dict,
        extractor: dict,
        anomaly: dict
    ) -> dict:
        """Check agreement between agents."""
        classifier_says_invoice = classifier.get('is_invoice', False)
        extractor_says_invoice = extractor.get('completeness_score', 0) > 0.5
        anomaly_says_clean = not anomaly.get('is_anomaly')
        
        agreements = [
            classifier_says_invoice,
            extractor_says_invoice,
            anomaly_says_clean
        ]
        
        agreement_count = sum(agreements)
        
        return {
            'full_agreement': agreement_count == 3,
            'majority_agreement': agreement_count >= 2,
            'agreement_count': agreement_count,
            'total_agents': 3
        }
    
    def _build_reasoning(
        self,
        classifier: dict,
        extractor: dict,
        anomaly: dict,
        final_score: float
    ) -> str:
        """Build human-readable reasoning for the decision."""
        reasons = []
        
        # Classifier reasoning
        if classifier.get('is_invoice'):
            reasons.append(f"Klasifikátor: faktura ({classifier.get('confidence', 0):.0%})")
        else:
            reasons.append(f"Klasifikátor: není faktura ({classifier.get('confidence', 0):.0%})")
        
        # Extractor reasoning
        completeness = extractor.get('completeness_score', 0)
        errors = extractor.get('validation_errors', [])
        if completeness > 0.7:
            reasons.append(f"Extraktor: kompletní data ({completeness:.0%})")
        elif errors:
            reasons.append(f"Extraktor: chybí {len(errors)} polí")
        
        # Anomaly reasoning
        if anomaly.get('is_anomaly'):
            reasons.append(f"Anomalie: {anomaly.get('anomaly_type')} ({anomaly.get('confidence', 0):.0%})")
        else:
            reasons.append("Anomalie: žádné detekovány")
        
        # Final score explanation
        if final_score >= self.threshold_accept:
            reasons.append(f"→ Auto-accept (score: {final_score:.2f})")
        elif final_score >= self.threshold_review:
            reasons.append(f"→ Human review (score: {final_score:.2f})")
        else:
            reasons.append(f"→ Auto-reject (score: {final_score:.2f})")
        
        return " | ".join(reasons)
    
    def _extract_final_data(
        self,
        classifier: dict,
        extractor: dict
    ) -> dict:
        """Extract final invoice data from results."""
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
    
    def get_statistics(self, results: List[dict]) -> dict:
        """Calculate statistics from multiple consensus results."""
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
