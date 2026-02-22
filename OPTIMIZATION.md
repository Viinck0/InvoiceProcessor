# ⚡ Zrychlení Llama Vision analýzy

Pokud analýza faktur trvá příliš dlouho, zde jsou osvědčené metody pro zrychlení:

---

## 🎯 Rychlá řešení (okamžitý efekt)

### 1. Snížení rozlišení obrázků

**Již nastaveno na 768px** (původně 2048px)

```python
# V invoice_gui_v2.py a invoice_processor_v2.py
MAX_IMAGE_SIZE = 768  # Stačí pro rozpoznání textu na faktuře
```

**Úspora času:** ~60-70% rychlejší

---

### 2. Použití rychlejšího modelu

| Model | Velikost | Čas/soubor | Přesnost |
|-------|----------|------------|----------|
| `llama3.2-vision` | 3.5 GB | ~15-30s | ⭐⭐⭐⭐⭐ |
| `phi3-vision` | 2.1 GB | ~8-15s | ⭐⭐⭐⭐ |
| `bakllava` | 2.1 GB | ~5-10s | ⭐⭐⭐⭐ |
| `moondream` | 0.8 GB | ~2-5s | ⭐⭐⭐ |

**Stažení rychlejších modelů:**
```bash
# Phi-3 Vision (doporučeno - dobrý poměr rychlost/přesnost)
ollama pull phi3-vision

# Bakllava (rychlý, stále přesný)
ollama pull bakllava

# Moondream (nejrychlejší, ale méně přesný)
ollama pull moondream
```

**Úspora času:** ~50-80% rychlejší (záleží na modelu)

---

### 3. Optimalizace parametrů inference

**Již nastaveno v kódu:**
```python
options={
    "temperature": 0.01,    # Nižší = rychlejší konvergence
    "num_predict": 512,     # Omezení délky odpovědi
}
keep_alive="5m"             # Model zůstane v paměti
```

**Úspora času:** ~20-30% rychlejší

---

## 🚀 Pokročilé optimalizace

### 4. GPU akcelerace

Ujistěte se, že Ollama využívá GPU:

**Windows (NVIDIA):**
```bash
# Ollama automaticky využívá GPU pokud je dostupné
# Zkontrolujte:
ollama ps
```

**Linux s NVIDIA:**
```bash
# Instalace s GPU podporou
curl -fsSL https://ollama.com/install.sh | sh
```

**Zrychlení:** 3-5x rychlejší s GPU

---

### 5. Batch processing (připravováno)

Místo analýzy soubor po souboru lze zpracovávat více najednou:

```python
# TODO: Implementace batch mode
# Ollama podporuje batch requests pro některé modely
```

---

### 6. Cache výsledků

Pro opakovanou analýzu stejných souborů:

```python
# Přidáno do budoucí verze
# Hash obrázku → cache výsledků
```

---

## 📊 Porovnání času zpracování

### Scénář: 50 souborů (PDF + obrázky)

| Konfigurace | Čas/soubor | Celkový čas |
|-------------|------------|-------------|
| **Výchozí** (llama3.2-vision, 2048px) | 25s | ~21 minut |
| **Optimalizováno** (llama3.2-vision, 768px) | 10s | ~8 minut |
| **Rychle** (phi3-vision, 768px) | 6s | ~5 minut |
| **Nejrychleji** (moondream, 512px) | 3s | ~2.5 minuty |

---

## ⚙️ Doporučená konfigurace

### Pro nejlepší poměr rychlost/přesnost:

```bash
# 1. Stáhnout Phi-3 Vision
ollama pull phi3-vision

# 2. Spustit GUI
python invoice_gui_v2.py

# 3. V nastavení vybrat:
#    - Vision model: phi3-vision
#    - (MAX_IMAGE_SIZE = 768 již nastaveno)
```

**Očekávaný čas:** ~5-8 sekund na soubor

---

## 🔧 Další tipy

### Zavřít jiné aplikace
Vision modely využívají GPU paměť. Zavřete náročné aplikace.

### Spustit Ollama server předem
```bash
# Udrží model nahraný v paměti
ollama serve
```

### Testovat na malé složce
Nejprve otestujte na 5-10 souborech:
```bash
# Vytvořte testovací složku s několika soubory
# Změřte čas a upravte parametry
```

### Použít CLI místo GUI
GUI přidává malé zpoždění. Pro hromadné zpracování:
```bash
python invoice_processor_v2.py
```

---

## 📈 Monitorování výkonu

### Zkontrolujte využití GPU
```bash
# Windows (NVIDIA)
nvidia-smi

# Linux
watch -n 1 nvidia-smi
```

### Logování času zpracování
Přidejte do kódu:
```python
import time

start = time.time()
result = analyzer.analyze(file_path)
elapsed = time.time() - start
logger.info(f"  Čas analýzy: {elapsed:.2f}s")
```

---

## ❓ Časté problémy

### "Model se načítá příliš dlouho"
- První spuštění trvá déle (načítání modelu do paměti)
- Další requesty jsou rychlejší díky `keep_alive="5m"`

### "Došla GPU paměť"
- Snižte `MAX_IMAGE_SIZE` na 512
- Zavřete jiné aplikace využívající GPU
- Použijte menší model (moondream)

### "Analýza trvá věčně"
- Zkontrolujte zda Ollama běží: `ollama list`
- Restartujte Ollama server: `ollama serve`
- Zkuste menší model

---

## 🎯 Rychlý souhrn

**Pro okamžité zrychlení:**
1. ✅ `MAX_IMAGE_SIZE = 768` (již nastaveno)
2. ✅ `temperature = 0.01` (již nastaveno)
3. ✅ `num_predict = 512` (již nastaveno)
4. 📥 Stáhnout `phi3-vision` nebo `bakllava`
5. 🎮 Použít GPU pokud je dostupné

**Očekávané zrychlení:** 3-5x rychlejší než výchozí konfigurace
