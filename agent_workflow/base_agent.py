"""
Base Agent Class
Abstract base class for all agents in the workflow
"""

from abc import ABC, abstractmethod
from typing import Any, Optional
import logging

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Base class for all document analysis agents."""
    
    def __init__(self, model: str = "llama3.2", timeout: int = 30):
        """
        Initialize base agent.
        
        Args:
            model: Ollama model name to use
            timeout: Request timeout in seconds
        """
        self.model = model
        self.timeout = timeout
        self._client = None
    
    def _get_client(self):
        """Lazy initialization of Ollama client."""
        if self._client is None:
            try:
                import ollama
                self._client = ollama
            except ImportError:
                logger.error("Ollama knihovna není nainstalována")
                raise ImportError("Nainstalujte: pip install ollama")
        return self._client
    
    @abstractmethod
    def analyze(self, text: str, metadata: Optional[dict] = None) -> dict:
        """
        Analyze document text and return structured result.
        
        Args:
            text: Extracted text from document
            metadata: Additional metadata (filename, file type, etc.)
            
        Returns:
            Dictionary with agent-specific analysis results
        """
        pass
    
    def _truncate_text(self, text: str, max_chars: int = 6000) -> str:
        """Truncate text to maximum character limit."""
        if len(text) <= max_chars:
            return text
        return text[:max_chars]
    
    def _extract_json_from_text(self, text: str) -> Optional[dict]:
        """Extract JSON object from text response."""
        import json
        import re
        
        if not text or not text.strip():
            return None
        
        # Try 1: Direct JSON parse
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass
        
        # Try 2: Extract from code block with language specifier
        code_block = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
        if code_block:
            try:
                return json.loads(code_block.group(1))
            except json.JSONDecodeError:
                pass
        
        # Try 3: Extract from generic code block
        code_block = re.search(r"```\s*(\{.*?\})\s*```", text, re.DOTALL)
        if code_block:
            try:
                return json.loads(code_block.group(1))
            except json.JSONDecodeError:
                pass
        
        # Try 4: Find JSON object anywhere in text (greedy match for nested braces)
        # This handles cases where there's text before/after the JSON
        text_cleaned = text.strip()
        
        # Find the first { and last } to extract potential JSON
        start_idx = text_cleaned.find('{')
        end_idx = text_cleaned.rfind('}')
        
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_candidate = text_cleaned[start_idx:end_idx + 1]
            try:
                return json.loads(json_candidate)
            except json.JSONDecodeError as e:
                # Try to fix common issues
                # Fix missing quotes around keys
                json_fixed = re.sub(r'(\{|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', json_candidate)
                try:
                    return json.loads(json_fixed)
                except json.JSONDecodeError:
                    pass
                
                # Log for debugging
                logger.debug(f"JSON parse error: {e}")
                logger.debug(f"Candidate length: {len(json_candidate)}")
                logger.debug(f"Candidate preview: {json_candidate[:100]}...")
        
        return None
    
    def validate_response(self, response: dict, required_fields: list) -> bool:
        """Validate that response contains required fields."""
        return all(field in response for field in required_fields)
