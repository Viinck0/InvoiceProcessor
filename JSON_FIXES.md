# 🔧 Opravy JSON parsing chyb - Multi-Agent Workflow

## 📋 Problém

AI agenti (hlavně Anomaly Detector a Extractor) často nevraceli JSON, přestože byli v promptu instruováni.

**Příznaky:**
- `WARNING: Anomaly detector nevrátil JSON`
- `WARNING: Extractor nevrátil JSON`
- `WARNING: Classifier nevrátil JSON`

---

## 🔍 Příčiny

### 1. Chyba v `.format()` - KeyError
**Problém:** V promptech byly JSON příklady se složenými závorkami `{}`, které Python interpretoval jako formátovací placeholdery.

**Příklad chyby:**
```python
prompt = """Vrať JSON:
{
  "is_invoice": true
}
Text: {input_data}"""

# Python se snaží interpretovat { "is_invoice" } jako placeholder
# → KeyError: '\n "is_invoice"'
```

**Řešení:** Escapování zdvojením závorek `{{}}`:
```python
prompt = """Vrať JSON:
{{
  "is_invoice": true
}}
Text: {input_data}"""
```

---

### 2. AI ignoruje instrukce pro JSON
**Problém:** llama3.2 někdy vrací text místo JSON, zejména když:
- Text je příliš dlouhý
- Dokument je nejasný
- Model je "upovídaný"

**Příklad špatné odpovědi:**
```
Jsem expertní systém pro klasifikaci dokumentů. 
Na základě poskytnutých informací budu rozhodovat...

Po analýze textu jsem dospěl k následujícímu závěru:
{
  "is_invoice": true,
  "confidence": 0.9
}
```

**Řešení:**
1. **Důraznější prompty** - přidány instrukce "ODPOVÍDEJ POUZE JSON"
2. **Lepší JSON extraction** - hledání JSON kdekoli v textu
3. **Fallback na rule-based** - když AI selže

---

## ✅ Provedené opravy

### 1. Escapování závorek ve všech prompotech

**Soubory:**
- `classifier_agent.py` - CLASSIFICATION_PROMPT
- `extractor_agent.py` - EXTRACTION_PROMPT  
- `anomaly_agent.py` - DETECTION_PROMPT

**Příklad:**
```python
# Před
{
  "is_invoice": true/false
}

# Po
{{
  "is_invoice": true/false
}}
```

---

### 2. Lepší error handling při format()

**Přidáno do všech agentů:**
```python
try:
    prompt = self.PROMPT.format(input_data=truncated)
except (KeyError, IndexError) as e:
    logger.warning(f"Prompt format error: {e}")
    return self._fallback_classification(text)  # nebo _fallback_extraction
```

---

### 3. Důraznější instrukce v prompotech

**Přidáno do anomaly_detector.py:**
```python
=== DŮLEŽITÉ ===
- Odpovídej POUZE ve formátu JSON
- Žádný další text, žádné vysvětlení mimo JSON
- Začni hned JSON objektem
```

---

### 4. Vylepšený JSON parser

**V `base_agent.py`:**
- Lepší logging pro debugging
- Zkrácený preview candidate textu
- Detekce délky candidate

---

### 5. Fallback logika

Když AI nevrátí JSON:
1. **Classifier** → `_fallback_classification()` (pravidla)
2. **Extractor** → `_fallback_extraction()` (regex)
3. **Anomaly Detector** → `_rule_based_detection()` (keyword matching)

**Příklad:**
```python
parsed = self._extract_json_from_text(raw_output)

if parsed is None:
    logger.warning(f"Anomaly detector nevrátil JSON: {raw_output[:150]}...")
    # Fallback to rule-based
    return rule_result
```

---

## 📊 Výsledky

### Před opravami:
```
11:33:54 [WARNING] Anomaly detector nevrátil JSON
11:34:09 [WARNING] Anomaly detector nevrátil JSON
11:34:12 [WARNING] Anomaly detector nevrátil JSON
11:34:18 [WARNING] Anomaly detector nevrátil JSON
11:34:42 [WARNING] Anomaly detector nevrátil JSON
11:34:44 [WARNING] Anomaly detector nevrátil JSON
11:34:54 [WARNING] Anomaly detector nevrátil JSON
...
11:34:47 [WARNING] Extractor nevrátil JSON
11:35:15 [WARNING] Extractor nevrátil JSON
11:35:22 [WARNING] Extractor nevrátil JSON
...
11:35:36 [WARNING] Classifier nevrátil JSON
```

### Po opravách:
- ✅ Všechny prompty nyní fungují s `.format()`
- ✅ Fallback logika zabraňuje pádům
- ✅ AI dostává důraznější instrukce pro JSON
- ✅ Lepší debugging díky logům

---

## 🎯 Doporučení pro budoucnost

### 1. Použít Ollama chat API místo generate
```python
response = client.chat(
    model=self.model,
    messages=[
        {"role": "system", "content": "You are a JSON-only assistant."},
        {"role": "user", "content": prompt}
    ]
)
```

### 2. Few-shot prompting
Přidat příklady správných JSON odpovědí do promptu:
```python
PROMPT = """...

=== PŘÍKLAD ===
Input: "Faktura č. 123 od ABC s.r.o."
Output: {{"is_invoice": true, "confidence": 0.95}}

=== TVŮJ ÚKOL ===
{input_data}
"""
```

### 3. JSON mode (pokud podporováno)
Některé modely mají režim který vynucuje JSON výstup.

### 4. Validace schématu
```python
from jsonschema import validate

try:
    validate(instance=parsed, schema=EXPECTED_SCHEMA)
except ValidationError:
    return fallback_result
```

---

## 📝 Změněné soubory

| Soubor | Změny |
|--------|-------|
| `classifier_agent.py` | Escapování + error handling |
| `extractor_agent.py` | Escapování + error handling |
| `anomaly_agent.py` | Escapování + error handling + důraznější instrukce |
| `base_agent.py` | Lepší logging |

---

## ✅ Testování

```bash
# Spustit test
python test_agents_direct.py

# Očekávaný výsledek:
# [OK] Classifier → dict
# [OK] Extractor → dict  
# [OK] Anomaly Detector → dict
# [OK] Consensus → dict
```

---

## 🔍 Jak číst logy

### Dobrá odpověď:
```
11:33:54 [INFO] HTTP Request: POST ... "HTTP/1.1 200 OK"
11:33:54 [DEBUG] ✓ Anomalie: Nedetekována (N/A)
11:33:54 [INFO] ✓ Document.pdf: FAKTURA (77%, 5.4s)
```

### Špatná odpověď (fallback):
```
11:33:54 [INFO] HTTP Request: POST ... "HTTP/1.1 200 OK"
11:33:54 [WARNING] Anomaly detector nevrátil JSON: Jsem expert...
11:33:54 [DEBUG] ✓ Anomalie detekována pravidly: contract
11:33:54 [INFO] ✓ Document.pdf: NENÍ FAKTURA (42%, 6.6s)
```

I když AI nevrátí JSON, systém stále funguje díky fallback logice!
