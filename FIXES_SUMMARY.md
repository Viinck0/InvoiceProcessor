# 🔧 Opravy chyb v Invoice Processor v6

## 📋 Seznam nalezených a opravených chyb

### 1. ❌ Chybějící model llama3.1
**Problém:** Model `llama3.1` nebyl nainstalován v Ollama.

**Řešení:** Změněn výchozí model na `llama3.2` ve všech souborech:
- `invoice_gui_v6.py`: `TEXT_MODEL = "llama3.2"`
- `agent_workflow/classifier_agent.py`: default model `"llama3.2"`
- `agent_workflow/extractor_agent.py`: default model `"llama3.2"`
- `agent_workflow/anomaly_agent.py`: default model `"llama3.2"`
- `agent_workflow/base_agent.py`: default model `"llama3.2"`
- `test_agents.py`: explicitní použití `"llama3.2"`

**Dostupné modely v systému:**
- ✅ llama3.2:latest (3.2B parametrů)
- ✅ llama3.2-vision:latest (10.7B parametrů)
- ✅ llama3.3:latest (70.6B parametrů)
- ✅ gpt-4o:latest
- ✅ qwen3-coder:30b

---

### 2. ❌ Invalid color name "#28a74520"
**Problém:** 8-místné hex barvy s alpha kanálem nejsou podporovány Tkinter.

**Řešení:** Změněno na 6-místné hex barvy v `invoice_gui_v6.py`:
```python
# Před
self.tree.tag_configure("accept", background="#28a74520")

# Po
self.tree.tag_configure("accept", background="#28a745")
```

---

### 3. ❌ Chyba JSON parseru: '\n "is_invoice"'
**Problém:** AI vrací JSON v různých formátech a parser selhával.

**Řešení:** Vylepšený `_extract_json_from_text()` v `base_agent.py` s 5 kroky:
1. Přímý JSON parse
2. Extract z ```json code block
3. Extract z ``` code block
4. Najít první `{` a poslední `}` v textu
5. Auto-fix chybějících uvozovek u klíčů

**Příklad fungování:**
```python
# Vstup AI:
Based on my analysis:
{
  "is_invoice": true,
  "confidence": 0.9
}

# Výstup parseru:
{'is_invoice': True, 'confidence': 0.9}
```

---

### 4. ❌ TypeError: is_invoice jako string
**Problém:** AI někdy vrací `is_invoice` jako string místo boolean.

**Řešení:** Explicitní konverze v `classifier_agent.py`:
```python
is_invoice_val = parsed.get('is_invoice')
if isinstance(is_invoice_val, bool):
    parsed['is_invoice'] = is_invoice_val
elif isinstance(is_invoice_val, str):
    parsed['is_invoice'] = is_invoice_val.lower() in ['true', 'ano', 'yes', '1']
elif isinstance(is_invoice_val, (int, float)):
    parsed['is_invoice'] = bool(is_invoice_val)
else:
    parsed['is_invoice'] = False
```

---

### 5. ❌ Confidence jako 0-100 místo 0-1
**Problém:** AI vrací confidence jako 95 místo 0.95.

**Řešení:** Normalizace v `classifier_agent.py`:
```python
confidence = parsed.get('confidence', 50)
if isinstance(confidence, str):
    try:
        confidence = float(confidence)
    except ValueError:
        confidence = 50
# Handle if confidence is given as 0-100 or 0-1
if confidence > 1:
    confidence = confidence / 100.0
parsed['confidence'] = min(1.0, max(0.0, confidence))
```

---

### 6. ❌ Exception při timeout agentů
**Problém:** Když agent selhal, celý proces spadl.

**Řešení:** Try-catch kolem `future.result()` v `invoice_gui_v6.py`:
```python
try:
    classifier_result = future_classifier.result(timeout=REQUEST_TIMEOUT + 10)
    extractor_result = future_extractor.result(timeout=REQUEST_TIMEOUT + 25)
    anomaly_result = future_anomaly.result(timeout=REQUEST_TIMEOUT + 10)
except Exception as agent_error:
    logger.error(f"  Agent execution error: {agent_error}")
    # Use fallback results
    classifier_result = {'is_invoice': False, 'confidence': 0.0}
    extractor_result = {'completeness_score': 0.0}
    anomaly_result = {'is_anomaly': False, 'confidence': 0.0}
```

---

### 7. ❌ Validate results are dicts
**Problém:** Když agent vrátil None nebo jiný typ, consensus spadl.

**Řešení:** Validace typů před předáním do consensus:
```python
if not isinstance(classifier_result, dict):
    logger.error(f"  Invalid classifier result type: {type(classifier_result)}")
    classifier_result = {'is_invoice': False, 'confidence': 0.0}

if not isinstance(extractor_result, dict):
    logger.error(f"  Invalid extractor result type: {type(extractor_result)}")
    extractor_result = {'completeness_score': 0.0}

if not isinstance(anomaly_result, dict):
    logger.error(f"  Invalid anomaly result type: {type(anomaly_result)}")
    anomaly_result = {'is_anomaly': False, 'confidence': 0.0}
```

---

### 8. ❌ Exception v calculate_consensus
**Problém:** Když consensus selhal, celý dokument byl zamítnut.

**Řešení:** Try-catch kolem `calculate_consensus()` s fallback:
```python
try:
    consensus_result = self.consensus.calculate_consensus(...)
except Exception as consensus_error:
    logger.error(f"  Consensus error: {consensus_error}")
    # Fallback decision based on classifier only
    clf_is_inv = classifier_result.get('is_invoice', False)
    clf_conf = classifier_result.get('confidence', 0)
    consensus_result = {
        'is_invoice': clf_is_inv,
        'confidence': clf_conf,
        'decision_type': 'auto_reject' if not clf_is_inv else 'auto_accept',
        'reasoning': f'Fallback due to consensus error: {consensus_error}',
        'extracted_data': {}
    }
```

---

### 9. ❌ Debug logging pro lepší troubleshooting
**Problém:** Nebylo vidět co AI skutečně vrací.

**Řešení:** Přidán debug logging v `classifier_agent.py`:
```python
raw_output = response.get("response", "")

# Debug: log raw output
logger.debug(f"Raw classifier output: {raw_output[:500]}...")

parsed = self._extract_json_from_text(raw_output)
```

---

## ✅ Výsledky testů

### Test JSON extraction:
```
Test 1 (normal JSON): {'is_invoice': True, 'confidence': 0.95} ✓
Test 2 (JSON with newlines): {'is_invoice': False, 'confidence': 0.8} ✓
Test 3 (code block): {'is_invoice': True, 'confidence': 95} ✓
Test 4 (text around JSON): {'is_invoice': True, 'confidence': 0.9} ✓
```

### Test Ollama připojení:
```
[OK] Ollama module imported
[OK] Ollama server is running
[OK] llama3.2 model is available
[OK] Generate works!
```

---

## 🚀 Jak spustit aplikaci nyní

```bash
# 1. Ujistěte se že Ollama běží
ollama serve

# 2. Spusťte aplikaci
python invoice_gui_v6.py
```

Aplikace by nyní měla fungovat bez chyb s modelem `llama3.2`.

---

## 📝 Doporučení pro budoucnost

1. **Přidat podporu pro více modelů** - detekovat dostupné modely a nabídnout výběr
2. **Lepší error messages** - zobrazit uživateli co se stalo
3. **Retry mechanism** - zkusit znovu když AI selže
4. **Model fallback** - pokud llama3.2 selže, zkusit llama3.3 nebo gpt-4o

---

## 📊 Statistiky oprav

| Soubor | Počet změn | Typ změny |
|--------|------------|-----------|
| `invoice_gui_v6.py` | 3 | Model + error handling + barvy |
| `base_agent.py` | 2 | Model + JSON parser |
| `classifier_agent.py` | 3 | Model + type validation + logging |
| `extractor_agent.py` | 1 | Model |
| `anomaly_agent.py` | 1 | Model |
| `test_agents.py` | 1 | Model |
| `consensus_engine.py` | 0 | Žádné změny |

**Celkem:** 11 změn v 7 souborech
