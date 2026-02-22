#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test Ollama connection and model availability"""

import sys
sys.path.insert(0, r'c:\Users\Vinci\Desktop\projekt třídění faktur')

print('Testing Ollama connection...')

try:
    import ollama
    print('[OK] Ollama module imported')
    
    # Check if server is running
    try:
        response = ollama.list()
        print(f'[OK] Ollama server is running')
        print(f'Available models: {response}')
    except Exception as e:
        print(f'[ERROR] Ollama server not running: {e}')
        print('Solution: Run "ollama serve" in another terminal')
        sys.exit(1)
    
    # Check if llama3.1 model exists
    try:
        models = ollama.list()
        model_names = [m.get('name', '') for m in models.get('models', [])]
        
        if any('llama3.1' in m for m in model_names):
            print('[OK] llama3.1 model is available')
        else:
            print('[WARNING] llama3.1 model not found!')
            print('Solution: Run "ollama pull llama3.1"')
    except Exception as e:
        print(f'[ERROR] Cannot list models: {e}')
    
    # Test generate with simple prompt
    print('\nTesting generate with simple prompt...')
    try:
        response = ollama.generate(
            model='llama3.1',
            prompt='Respond with ONLY this JSON: {"test": true}',
            options={'temperature': 0.01, 'num_predict': 50}
        )
        output = response.get('response', '')
        print(f'Response: {output}')
        print('[OK] Generate works!')
    except Exception as e:
        print(f'[ERROR] Generate failed: {e}')
        
except ImportError as e:
    print(f'[ERROR] ollama module not installed: {e}')
    print('Solution: pip install ollama')

print('\nTest completed!')
