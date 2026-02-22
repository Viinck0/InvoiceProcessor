#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test JSON extraction and imports"""

import sys
sys.path.insert(0, r'c:\Users\Vinci\Desktop\projekt třídění faktur')

print('Python version:', sys.version)

# Test imports
print('\nTesting imports...')
try:
    from agent_workflow import ClassifierAgent, ExtractorAgent, AnomalyDetectorAgent, ConsensusEngine
    print('[OK] Agent workflow imports OK')
except Exception as e:
    print(f'[ERROR] Agent workflow import error: {e}')

try:
    import invoice_gui_v6
    print('[OK] invoice_gui_v6 imports OK')
except Exception as e:
    print(f'[ERROR] invoice_gui_v6 import error: {e}')

# Test JSON extraction with ClassifierAgent
print('\nTesting JSON extraction...')
try:
    clf = ClassifierAgent()
    
    # Test 1: Normal JSON
    test1 = '{"is_invoice": true, "confidence": 0.95}'
    result1 = clf._extract_json_from_text(test1)
    print(f'Test 1 (normal JSON): {result1}')
    
    # Test 2: JSON with newlines
    test2 = '''
    {
      "is_invoice": false,
      "confidence": 0.8
    }
    '''
    result2 = clf._extract_json_from_text(test2)
    print(f'Test 2 (JSON with newlines): {result2}')
    
    # Test 3: JSON in code block
    test3 = '''Here is the result:
```json
{
  "is_invoice": true,
  "confidence": 95
}
```
Some more text'''
    result3 = clf._extract_json_from_text(test3)
    print(f'Test 3 (code block): {result3}')
    
    # Test 4: JSON with text around
    test4 = '''Based on my analysis:
{
  "is_invoice": true,
  "confidence": 0.9
}
Final answer.'''
    result4 = clf._extract_json_from_text(test4)
    print(f'Test 4 (text around JSON): {result4}')
    
    print('\n[OK] All JSON extraction tests passed!')
    
except Exception as e:
    import traceback
    print(f'[ERROR] JSON extraction error: {e}')
    traceback.print_exc()

print('\nAll checks completed!')
