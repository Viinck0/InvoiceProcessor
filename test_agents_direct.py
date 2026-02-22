#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test agentů přímo"""

import sys
sys.path.insert(0, r'c:\Users\Vinci\Desktop\projekt třídění faktur')

from agent_workflow import ClassifierAgent, ExtractorAgent, AnomalyDetectorAgent, ConsensusEngine

# Testovací text (jednoduchá faktura)
test_text = """
FAKTURA č. 2024001

Dodavatel: ABC s.r.o.
IČO: 12345678
DIČ: CZ12345678

Odběratel: XYZ a.s.
IČO: 87654321

Datum vystavení: 15.01.2024
Datum splatnosti: 15.02.2024

Celkem k úhradě: 1500 Kč
Bankovní spojení: 123456789/0100
Variabilní symbol: 2024001
"""

print('Testing agents with sample invoice text...')
print('=' * 60)

# Inicializace
print('\n1. Initializing agents...')
try:
    classifier = ClassifierAgent(model='llama3.2', timeout=30)
    print('   [OK] Classifier initialized')
except Exception as e:
    print(f'   [ERROR] Classifier init failed: {e}')
    sys.exit(1)

try:
    extractor = ExtractorAgent(model='llama3.2', timeout=45)
    print('   [OK] Extractor initialized')
except Exception as e:
    print(f'   [ERROR] Extractor init failed: {e}')
    sys.exit(1)

try:
    anomaly = AnomalyDetectorAgent(model='llama3.2', timeout=30)
    print('   [OK] Anomaly Detector initialized')
except Exception as e:
    print(f'   [ERROR] Anomaly Detector init failed: {e}')
    sys.exit(1)

try:
    consensus = ConsensusEngine()
    print('   [OK] Consensus Engine initialized')
except Exception as e:
    print(f'   [ERROR] Consensus Engine init failed: {e}')
    sys.exit(1)

# Test classifier
print('\n2. Testing Classifier Agent...')
try:
    clf_result = classifier.analyze(test_text, {'filename': 'test.pdf'})
    print(f'   Result type: {type(clf_result)}')
    print(f'   Result: {clf_result}')
except Exception as e:
    print(f'   [ERROR] Classifier failed: {e}')
    import traceback
    traceback.print_exc()

# Test extractor
print('\n3. Testing Extractor Agent...')
try:
    ext_result = extractor.analyze(test_text, {'filename': 'test.pdf'})
    print(f'   Result type: {type(ext_result)}')
    print(f'   Completeness: {ext_result.get("completeness_score", "N/A")}')
except Exception as e:
    print(f'   [ERROR] Extractor failed: {e}')
    import traceback
    traceback.print_exc()

# Test anomaly detector
print('\n4. Testing Anomaly Detector Agent...')
try:
    anom_result = anomaly.analyze(test_text, {'filename': 'test.pdf'})
    print(f'   Result type: {type(anom_result)}')
    print(f'   Is anomaly: {anom_result.get("is_anomaly", "N/A")}')
except Exception as e:
    print(f'   [ERROR] Anomaly Detector failed: {e}')
    import traceback
    traceback.print_exc()

# Test consensus
print('\n5. Testing Consensus Engine...')
try:
    # Use mock results for testing
    mock_clf = {'is_invoice': True, 'confidence': 0.9}
    mock_ext = {'completeness_score': 0.8, 'validation_errors': []}
    mock_anom = {'is_anomaly': False, 'confidence': 0.95}
    
    consensus_result = consensus.calculate_consensus(mock_clf, mock_ext, mock_anom)
    print(f'   Result type: {type(consensus_result)}')
    print(f'   Is invoice: {consensus_result.get("is_invoice")}')
    print(f'   Confidence: {consensus_result.get("confidence")}')
    print(f'   Decision: {consensus_result.get("decision_type")}')
except Exception as e:
    print(f'   [ERROR] Consensus failed: {e}')
    import traceback
    traceback.print_exc()

print('\n' + '=' * 60)
print('Test completed!')
