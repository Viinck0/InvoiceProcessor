#!/usr/bin/env python3
"""
Invoice Processor v2 - Emergency Low Memory Mode
Pro zpracování velkých souborů na systémech s omezenou pamětí.

Tento režim:
- Používá extrémně nízké rozlišení (384px)
- Zpracovává pouze 1 soubor najednou
- Dělá dlouhé pauzy mezi soubory
- Používá nejmenší dostupný model

Použití:
    python invoice_gui_emergency.py
"""

import os
import sys
import logging

# Override konstant PŘED importem GUI
os.environ['VISION_MODEL'] = 'moondream'  # Nejmenší vision model (~0.8GB)
os.environ['MAX_IMAGE_SIZE'] = '384'       # Minimální rozlišení
os.environ['BATCH_SIZE'] = '1'             # Jeden soubor najednou
os.environ['REQUEST_TIMEOUT'] = '180'      # Delší timeout pro pomalejší model

# Import upraveného GUI
from invoice_gui_v2 import (
    InvoiceProcessorGUIV2,
    MAX_IMAGE_SIZE,
    VISION_MODEL,
    BATCH_SIZE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


class EmergencyInvoiceGUI(InvoiceProcessorGUIV2):
    """GUI verze s emergency nastavením pro nízkou paměť."""

    def __init__(self):
        super().__init__()
        
        self.title("🧾 Invoice Processor v2 - Emergency Low Memory Mode")
        
        # Vynutit emergency nastavení
        self.model_name.set("moondream")
        
        # Přidat warning label
        self._add_warning_label()
        
        logger.info("=" * 60)
        logger.info("EMERGENCY LOW MEMORY MODE")
        logger.info(f"  MAX_IMAGE_SIZE: {MAX_IMAGE_SIZE}px")
        logger.info(f"  Model: {VISION_MODEL}")
        logger.info(f"  BATCH_SIZE: {BATCH_SIZE}")
        logger.info("=" * 60)

    def _add_warning_label(self):
        """Přidá varovný label o pomalém zpracování."""
        import customtkinter as ctk
        
        warning_frame = ctk.CTkFrame(self, fg_color="#8B0000")
        warning_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=5)
        warning_frame.grid_columnconfigure(0, weight=1)
        
        warning_label = ctk.CTkLabel(
            warning_frame,
            text="⚠️ EMERGENCY MODE: Velmi pomalé, ale funguje i s malou pamětí",
            font=ctk.CTkFont(weight="bold"),
            text_color="white"
        )
        warning_label.grid(row=0, column=0, sticky="w", padx=15, pady=10)


def main():
    """Spustit emergency GUI."""
    # Zkontrolovat dostupnost moondream modelu
    try:
        import ollama
        models = ollama.list()
        model_names = [m['name'] for m in models.get('models', [])]
        
        if not any('moondream' in m for m in model_names):
            logger.error("❌ Model 'moondream' není stažen!")
            logger.error("   Stáhněte příkazem: ollama pull moondream")
            
            # Fallback na llama3.2-vision
            logger.warning("   Fallback na llama3.2-vision...")
            os.environ['VISION_MODEL'] = 'llama3.2-vision'
            
    except Exception as e:
        logger.warning(f"Nelze zkontrolovat modely: {e}")
    
    app = EmergencyInvoiceGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
