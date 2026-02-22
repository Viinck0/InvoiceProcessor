#!/usr/bin/env python3
"""
Testovací skript pro Multi-Agent Workflow
Ukázka použití agentů pro klasifikaci faktur
"""

import sys
import json
from pathlib import Path

# Přidání parent directory do path pro import
sys.path.insert(0, str(Path(__file__).parent))

from agent_workflow import (
    ClassifierAgent,
    ExtractorAgent,
    AnomalyDetectorAgent,
    ConsensusEngine
)


def print_separator(title: str = ""):
    """Vytiskne oddělovač."""
    print("\n" + "=" * 70)
    if title:
        print(f"  {title}")
        print("=" * 70)


def print_result(test_name: str, result: dict):
    """Vytiskne výsledek testu."""
    print(f"\n📄 {test_name}")
    print("-" * 70)
    
    is_invoice = result.get('is_invoice')
    confidence = result.get('confidence', 0)
    decision_type = result.get('decision_type', 'unknown')
    
    if is_invoice is True:
        status = "✅ FAKTURA"
    elif is_invoice is False:
        status = "❌ NENÍ FAKTURA"
    else:
        status = "⚠️  HUMAN REVIEW"
    
    print(f"  Status: {status}")
    print(f"  Confidence: {confidence:.0%}")
    print(f"  Decision type: {decision_type}")
    
    if 'reasoning' in result:
        print(f"  Reasoning: {result['reasoning']}")
    
    if 'extracted_data' in result:
        data = result['extracted_data']
        if data.get('vendor_name'):
            print(f"  Dodavatel: {data['vendor_name']}")
        if data.get('customer_name'):
            print(f"  Odběratel: {data['customer_name']}")
        if data.get('total_amount'):
            print(f"  Částka: {data['total_amount']} {data.get('currency', '')}")
        if data.get('issue_date'):
            print(f"  Datum: {data['issue_date']}")


def test_invoice():
    """Test s skutečnou fakturou."""
    sample_text = """
    FAKTURA č. 2024001
    
    Dodavatel: ABC s.r.o.
    IČO: 12345678
    DIČ: CZ12345678
    Adresa: Hlavní 123, Praha 1
    
    Odběratel: XYZ a.s.
    IČO: 87654321
    Adresa: Dlouhá 456, Brno
    
    Datum vystavení: 15.01.2024
    Datum splatnosti: 15.02.2024
    
    Položky:
    1. Služba A  1000 Kč
    2. Služba B   500 Kč
    
    Celkem bez DPH: 1500 Kč
    DPH 21%: 315 Kč
    CELKEM K ÚHRADĚ: 1815 Kč
    
    Bankovní spojení: 123456789/0100
    Variabilní symbol: 2024001
    """
    
    return sample_text


def test_cv():
    """Test s životopisem."""
    sample_text = """
    ŽIVOTOPIS
    
    Jan Novák
    Narozen: 1.1.1990
    Bydliště: Praha
    
    Vzdělání:
    2008-2013: ČVUT Praha
    2013-2015: VŠE Praha
    
    Pracovní zkušenosti:
    2015-2018: Programátor ABC s.r.o.
    2018-2023: Senior Developer XYZ a.s.
    
    Dovednosti:
    - Python, Java, C++
    - Team leadership
    - Projektové řízení
    """
    
    return sample_text


def test_reminder():
    """Test s upomínkou."""
    sample_text = """
    UPMÍNKA
    
    Výzva k úhradě faktury č. 2023999
    
    Upozorňujeme Vás, že dne 15.12.2023 uplynula splatnost Vaší faktury.
    
    Prosíme o úhradu částky 5000 Kč do 5 pracovních dnů.
    
    V případě že jste již uhradili, považujte tuto upomínku za bezpředmětnou.
    """
    
    return sample_text


def test_offer():
    """Test s nabídkou."""
    sample_text = """
    CENOVÁ NABÍDKA
    
    Pro firmu: Test s.r.o.
    
    Položka                 Cena
    ----------------------------------
    Služba A                1000 Kč
    Služba B                 800 Kč
    ----------------------------------
    Celkem                  1800 Kč
    
    Nabídka platná do 30.01.2024
    
    Tato nabídka není závazná.
    """
    
    return sample_text


def test_contract():
    """Test se smlouvou."""
    sample_text = """
    KUPNÍ SMLOUVA
    
    uzavřená podle § 2079 zákona č. 89/2012 Sb.
    
    Smluvní strany:
    
    Prodávající: ABC s.r.o.
    IČO: 12345678
    
    Kupující: XYZ a.s.
    IČO: 87654321
    
    Předmět smlouvy:
    Prodej zboží za cenu 10000 Kč.
    
    Podmínky:
    1. Zboží bude dodáno do 30 dnů
    2. Záruční doba 24 měsíců
    3. Smlouva nabývá platnosti podpisem
    """
    
    return sample_text


def run_tests():
    """Spustí všechny testy."""
    print_separator("MULTI-AGENT WORKFLOW TEST")
    print("Testing invoice classification with 3 specialized agents")
    
    # Inicializace agentů
    print("\n🔄 Inicializace agentů...")
    classifier = ClassifierAgent(model="llama3.2", timeout=30)
    extractor = ExtractorAgent(model="llama3.2", timeout=45)
    anomaly = AnomalyDetectorAgent(model="llama3.2", timeout=30)
    consensus = ConsensusEngine(
        threshold_accept=0.7,
        threshold_review=0.5,
        anomaly_veto_threshold=0.85
    )
    print("✓ Agenti připraveni")
    
    # Testovací případy
    test_cases = [
        ("Skutečná faktura", test_invoice()),
        ("Životopis", test_cv()),
        ("Upomínka", test_reminder()),
        ("Nabídka", test_offer()),
        ("Smlouva", test_contract()),
    ]
    
    results = []
    
    for test_name, text in test_cases:
        print_separator(f"Test: {test_name}")
        
        # Spuštění agentů
        print("\n🔍 Analýza dokumentu...")
        
        print("  [1/3] Classifier Agent...", end=" ", flush=True)
        classifier_result = classifier.analyze(text)
        print(f"✓ {classifier_result.get('is_invoice', 'N/A')}")
        
        print("  [2/3] Extractor Agent...", end=" ", flush=True)
        extractor_result = extractor.analyze(text)
        print(f"✓ Completeness: {extractor_result.get('completeness_score', 0):.0%}")
        
        print("  [3/3] Anomaly Detector...", end=" ", flush=True)
        anomaly_result = anomaly.analyze(text, metadata={"filename": f"{test_name}.pdf"})
        print(f"✓ {'Anomalie' if anomaly_result.get('is_anomaly') else 'Čistý'}")
        
        # Konsenzus
        print("\n🗳️  Výpočet konsenzu...")
        final_result = consensus.calculate_consensus(
            classifier_result,
            extractor_result,
            anomaly_result
        )
        
        results.append(final_result)
        print_result(test_name, final_result)
    
    # Shrnutí
    print_separator("SHRNUTÍ TESTŮ")
    stats = consensus.get_statistics(results)
    
    print(f"""
  Celkem testů:        {stats.get('total', 0)}
  Faktury:             {stats.get('invoices', 0)} ({stats.get('invoice_percentage', 0):.1f}%)
  Ne-faktury:          {stats.get('non_invoices', 0)}
  Human review:        {stats.get('human_review', 0)}
  
  Průměrná confidence: {stats.get('average_confidence', 0):.2f}
  Full agreement rate: {stats.get('full_agreement_rate', 0):.1f}%
    """)
    
    print_separator("HOTOVO")
    print("Více informací najdete v IMPLEMENTACE_AGENTI.md\n")


if __name__ == "__main__":
    try:
        run_tests()
    except KeyboardInterrupt:
        print("\n\n⚠️  Přerušeno uživatelem")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Chyba: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
