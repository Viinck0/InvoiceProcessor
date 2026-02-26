"""
LangGraph Workflow for Invoice Processing
Orchestrace všech komponent pomocí LangChain/LangGraph

Tento modul poskytuje:
1. State management pro celý workflow
2. Node-based processing pipeline
3. Error handling a retry logic
4. Validace mezi kroky

Workflow:
OCR → Validator → Pre-Filter → Classifier/Extractor/Anomaly → Consensus → Result
"""

from typing import TypedDict, List, Optional, Dict, Any
from pathlib import Path
from dataclasses import dataclass
import logging
import concurrent.futures

try:
    from langgraph.graph import StateGraph, END
    from langgraph.graph.message import add_messages
    from langchain_core.messages import BaseMessage
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    print("⚠️ LangGraph not installed. Install: pip install langgraph langchain-core")

from .ocr_validator import OCRTextValidator, validate_ocr_text
from .classifier_agent import ClassifierAgent
from .extractor_agent import ExtractorAgent
from .anomaly_agent import AnomalyDetectorAgent
from .consensus_engine import ConsensusEngine

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# State Definition
# ─────────────────────────────────────────────
class InvoiceWorkflowState(TypedDict):
    """
    State pro celý invoice processing workflow.
    
    All fields are optional to allow partial state updates.
    """
    # Input
    file_path: str
    raw_ocr_text: str
    
    # After validation
    validated_text: str
    validation_result: dict
    
    # After pre-filter
    pre_filter_result: tuple  # (classification, confidence, reason)
    skip_ai_processing: bool
    
    # Agent results
    classifier_result: dict
    extractor_result: dict
    anomaly_result: dict
    
    # Final result
    consensus_result: dict
    is_invoice: bool
    confidence: float
    decision_type: str  # auto_accept, human_review, auto_reject, error
    extracted_data: dict
    
    # Error handling
    errors: List[str]
    iterations: int


# ─────────────────────────────────────────────
# Workflow Nodes
# ─────────────────────────────────────────────
class InvoiceWorkflowNodes:
    """
    Node-based processing steps for the workflow.
    
    Each node:
    - Takes the current state
    - Performs a specific task
    - Returns state updates (dict)
    """
    
    def __init__(
        self,
        classifier: ClassifierAgent,
        extractor: ExtractorAgent,
        anomaly: AnomalyDetectorAgent,
        consensus: ConsensusEngine,
        ocr_validator: OCRTextValidator
    ):
        self.classifier = classifier
        self.extractor = extractor
        self.anomaly = anomaly
        self.consensus = consensus
        self.ocr_validator = ocr_validator
    
    def ocr_validator_node(self, state: InvoiceWorkflowState) -> InvoiceWorkflowState:
        """
        Node: Validate OCR text and fix hallucinations.
        """
        raw_text = state.get("raw_ocr_text", "")
        
        if not raw_text:
            return {"errors": ["No OCR text to validate"], "validated_text": ""}
        
        try:
            validated_text, validation_result = validate_ocr_text(raw_text, language="auto")
            
            logger.debug(f"✓ OCR Validation: {validation_result['valid_words']}/{validation_result['original_words']} valid")
            
            if validation_result['hallucinated_words'] > 0:
                logger.info(f"  Removed {validation_result['hallucinated_words']} hallucinated words")
            
            return {
                "validated_text": validated_text,
                "validation_result": validation_result
            }
        
        except Exception as e:
            logger.error(f"OCR Validator error: {e}")
            return {
                "errors": [f"OCR validation failed: {str(e)}"],
                "validated_text": raw_text,  # Use original text as fallback
                "validation_result": {"confidence": 0.0, "error": str(e)}
            }
    
    def pre_filter_node(self, state: InvoiceWorkflowState) -> InvoiceWorkflowState:
        """
        Node: Quick rule-based pre-filter before AI processing.
        """
        # Import here to avoid circular dependency
        from invoice_gui_v6 import OCRExtractor
        
        text = state.get("validated_text", "") or state.get("raw_ocr_text", "")
        
        if not text:
            return {"skip_ai_processing": True, "pre_filter_result": ('reject', 1.0, 'No text')}
        
        # Use OCRExtractor's pre_filter method
        # Create a temporary instance if needed
        extractor = OCRExtractor()
        pre_class, pre_conf, pre_reason = extractor.pre_filter(text)
        
        # Decide whether to skip AI processing
        skip = (pre_class == 'reject' and pre_conf > 0.9)
        
        logger.debug(f"✓ Pre-filter: {pre_class} (confidence: {pre_conf:.0%}, reason: {pre_reason})")
        
        return {
            "pre_filter_result": (pre_class, pre_conf, pre_reason),
            "skip_ai_processing": skip
        }
    
    def classifier_node(self, state: InvoiceWorkflowState) -> InvoiceWorkflowState:
        """
        Node: Run classifier agent.
        """
        text = state.get("validated_text", "")
        
        if not text or state.get("skip_ai_processing"):
            return {"classifier_result": {"is_invoice": False, "confidence": 0.0, "reason": "Skipped"}}
        
        try:
            result = self.classifier.analyze(text, metadata=None)
            logger.debug(f"✓ Classifier: {'invoice' if result.get('is_invoice') else 'not invoice'} ({result.get('confidence', 0):.0%})")
            return {"classifier_result": result}
        
        except Exception as e:
            logger.error(f"Classifier error: {e}")
            return {"classifier_result": {"is_invoice": False, "confidence": 0.0, "error": str(e)}}
    
    def extractor_node(self, state: InvoiceWorkflowState) -> InvoiceWorkflowState:
        """
        Node: Run extractor agent.
        """
        text = state.get("validated_text", "")
        
        if not text or state.get("skip_ai_processing"):
            return {"extractor_result": {"completeness_score": 0.0, "validation_errors": ["Skipped"]}}
        
        try:
            result = self.extractor.analyze(text, metadata=None)
            logger.debug(f"✓ Extractor: completeness={result.get('completeness_score', 0):.0%}")
            return {"extractor_result": result}
        
        except Exception as e:
            logger.error(f"Extractor error: {e}")
            return {"extractor_result": {"completeness_score": 0.0, "validation_errors": [str(e)]}}
    
    def anomaly_node(self, state: InvoiceWorkflowState) -> InvoiceWorkflowState:
        """
        Node: Run anomaly detector agent.
        """
        text = state.get("validated_text", "")
        
        if not text or state.get("skip_ai_processing"):
            return {"anomaly_result": {"is_anomaly": False, "confidence": 0.0}}
        
        try:
            result = self.anomaly.analyze(text, metadata=None)
            logger.debug(f"✓ Anomaly: {'anomaly detected' if result.get('is_anomaly') else 'normal'} ({result.get('confidence', 0):.0%})")
            return {"anomaly_result": result}
        
        except Exception as e:
            logger.error(f"Anomaly detector error: {e}")
            return {"anomaly_result": {"is_anomaly": False, "confidence": 0.0, "error": str(e)}}
    
    def consensus_node(self, state: InvoiceWorkflowState) -> InvoiceWorkflowState:
        """
        Node: Calculate consensus from all agent results.
        """
        classifier_result = state.get("classifier_result", {})
        extractor_result = state.get("extractor_result", {})
        anomaly_result = state.get("anomaly_result", {})
        
        try:
            consensus = self.consensus.calculate_consensus(
                classifier_result,
                extractor_result,
                anomaly_result
            )
            
            logger.debug(f"✓ Consensus: {'invoice' if consensus.get('is_invoice') else 'not invoice'} "
                        f"({consensus.get('confidence', 0):.0%}, decision: {consensus.get('decision_type')})")
            
            return {
                "consensus_result": consensus,
                "is_invoice": consensus.get("is_invoice", False),
                "confidence": consensus.get("confidence", 0.0),
                "decision_type": consensus.get("decision_type", "unknown"),
                "extracted_data": consensus.get("extracted_data", {})
            }
        
        except Exception as e:
            logger.error(f"Consensus error: {e}")
            return {
                "consensus_result": {"error": str(e)},
                "is_invoice": False,
                "confidence": 0.0,
                "decision_type": "error",
                "extracted_data": {}
            }


# ─────────────────────────────────────────────
# Conditional Edge Functions
# ─────────────────────────────────────────────
def should_skip_ai_processing(state: InvoiceWorkflowState) -> str:
    """Conditional edge: skip AI processing if pre-filter says reject."""
    if state.get("skip_ai_processing"):
        return "skip_to_result"
    return "run_agents"


def should_retry(state: InvoiceWorkflowState) -> str:
    """Conditional edge: retry if errors and iterations < max."""
    max_iterations = 3
    errors = state.get("errors", [])
    iterations = state.get("iterations", 0)
    
    if errors and iterations < max_iterations:
        return "retry"
    return "finish"


# ─────────────────────────────────────────────
# Main Workflow Builder
# ─────────────────────────────────────────────
def build_invoice_workflow(
    classifier: ClassifierAgent,
    extractor: ExtractorAgent,
    anomaly: AnomalyDetectorAgent,
    consensus: ConsensusEngine,
    ocr_validator: OCRTextValidator
):
    """
    Build the LangGraph workflow for invoice processing.
    
    Returns:
        Compiled StateGraph ready to invoke
    """
    if not LANGCHAIN_AVAILABLE:
        logger.warning("LangGraph not available - using simple pipeline instead")
        return None
    
    # Create nodes
    nodes = InvoiceWorkflowNodes(
        classifier=classifier,
        extractor=extractor,
        anomaly=anomaly,
        consensus=consensus,
        ocr_validator=ocr_validator
    )
    
    # Build graph
    workflow = StateGraph(InvoiceWorkflowState)
    
    # Add nodes
    workflow.add_node("ocr_validator", nodes.ocr_validator_node)
    workflow.add_node("pre_filter", nodes.pre_filter_node)
    workflow.add_node("classifier", nodes.classifier_node)
    workflow.add_node("extractor", nodes.extractor_node)
    workflow.add_node("anomaly", nodes.anomaly_node)
    workflow.add_node("consensus", nodes.consensus_node)
    
    # Set entry point
    workflow.set_entry_point("ocr_validator")
    
    # Add edges
    workflow.add_edge("ocr_validator", "pre_filter")
    
    # Conditional edge after pre-filter
    workflow.add_conditional_edges(
        "pre_filter",
        should_skip_ai_processing,
        {
            "skip_to_result": "consensus",  # Skip directly to consensus with empty results
            "run_agents": ["classifier", "extractor", "anomaly"]  # Run all agents in parallel (next step)
        }
    )
    
    # Send all agents to consensus afterwards
    workflow.add_edge("classifier", "consensus")
    workflow.add_edge("extractor", "consensus")
    workflow.add_edge("anomaly", "consensus")
    
    # Add retry logic
    workflow.add_conditional_edges(
        "consensus",
        should_retry,
        {
            "retry": "ocr_validator",
            "finish": END
        }
    )
    
    # Compile
    compiled_workflow = workflow.compile()
    
    logger.info("✓ LangGraph invoice workflow compiled successfully")
    
    return compiled_workflow


# ─────────────────────────────────────────────
# Simple Pipeline Fallback (no LangGraph)
# ─────────────────────────────────────────────
class SimpleInvoicePipeline:
    """
    Simple pipeline fallback when LangGraph is not available.
    
    Same workflow but implemented without LangGraph dependencies.
    """
    
    def __init__(
        self,
        classifier: ClassifierAgent,
        extractor: ExtractorAgent,
        anomaly: AnomalyDetectorAgent,
        consensus: ConsensusEngine,
        ocr_validator: OCRTextValidator
    ):
        self.nodes = InvoiceWorkflowNodes(
            classifier=classifier,
            extractor=extractor,
            anomaly=anomaly,
            consensus=consensus,
            ocr_validator=ocr_validator
        )
    
    def process(self, file_path: str, raw_ocr_text: str) -> Dict[str, Any]:
        """
        Process invoice through the pipeline.
        
        Args:
            file_path: Path to the invoice file
            raw_ocr_text: Raw text from OCR
            
        Returns:
            Final state dict with all results
        """
        state: InvoiceWorkflowState = {
            "file_path": file_path,
            "raw_ocr_text": raw_ocr_text,
            "validated_text": "",
            "validation_result": {},
            "pre_filter_result": (),
            "skip_ai_processing": False,
            "classifier_result": {},
            "extractor_result": {},
            "anomaly_result": {},
            "consensus_result": {},
            "is_invoice": False,
            "confidence": 0.0,
            "decision_type": "pending",
            "extracted_data": {},
            "errors": [],
            "iterations": 0
        }
        
        # Step 1: OCR Validation
        logger.debug(f"🔍 Validating OCR text for {Path(file_path).name}")
        state.update(self.nodes.ocr_validator_node(state))
        
        # Step 2: Pre-filter
        logger.debug(f"📋 Running pre-filter")
        state.update(self.nodes.pre_filter_node(state))
        
        # Step 3: AI Agents (or skip)
        if not state.get("skip_ai_processing"):
            logger.debug(f"🤖 Running AI agents (Parallel)")
            
            # Paralelní běh 3 agentů
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                classifier_future = executor.submit(self.nodes.classifier_node, state)
                extractor_future = executor.submit(self.nodes.extractor_node, state)
                anomaly_future = executor.submit(self.nodes.anomaly_node, state)
                
                # Sběr výsledků
                for future in concurrent.futures.as_completed([classifier_future, extractor_future, anomaly_future]):
                    state.update(future.result())
        else:
            logger.debug(f"⏭️ Skipping AI processing (pre-filter rejected)")
            state["classifier_result"] = {"is_invoice": False, "confidence": 0.0, "reason": "Pre-filter reject"}
            state["extractor_result"] = {"completeness_score": 0.0, "validation_errors": ["Skipped"]}
            state["anomaly_result"] = {"is_anomaly": False, "confidence": 0.0}
        
        # Step 4: Consensus
        logger.debug(f"🎯 Calculating consensus")
        state.update(self.nodes.consensus_node(state))
        
        return state


# ─────────────────────────────────────────────
# Factory Function
# ─────────────────────────────────────────────
def create_invoice_processor(
    classifier: ClassifierAgent,
    extractor: ExtractorAgent,
    anomaly: AnomalyDetectorAgent,
    consensus: ConsensusEngine,
    ocr_validator: OCRTextValidator,
    use_langgraph: bool = True
):
    """
    Factory function to create invoice processor.
    
    Uses LangGraph if available, otherwise falls back to simple pipeline.
    
    Args:
        classifier: Configured classifier agent
        extractor: Configured extractor agent
        anomaly: Configured anomaly detector agent
        consensus: Configured consensus engine
        ocr_validator: Configured OCR validator
        use_langgraph: Whether to try using LangGraph (falls back if not available)
        
    Returns:
        Processor object (either LangGraph workflow or SimplePipeline)
    """
    if use_langgraph and LANGCHAIN_AVAILABLE:
        workflow = build_invoice_workflow(
            classifier=classifier,
            extractor=extractor,
            anomaly=anomaly,
            consensus=consensus,
            ocr_validator=ocr_validator
        )
        
        if workflow:
            logger.info("✓ Using LangGraph workflow")
            return workflow
    
    logger.info("✓ Using simple pipeline (LangGraph not available or disabled)")
    return SimpleInvoicePipeline(
        classifier=classifier,
        extractor=extractor,
        anomaly=anomaly,
        consensus=consensus,
        ocr_validator=ocr_validator
    )
