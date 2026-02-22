# 🔧 Debug a optimalizace paměti pro Llama Vision

Tento dokument popisuje jak řešit problémy s pamětí při použití Llama Vision modelu.

---

## 🆕 Nové funkce v2.1

### 1. Detailní monitoring paměti

Aplikace nyní sleduje využití paměti a loguje ho:

```python
# V logu uvidíte:
💾 Paměť [1/14]: 245.3MB (12.4%)
💾 Paměť [5/14]: 512.8MB (26.1%)
💾 Paměť [10/14]: 834.2MB (42.3%)
```

### 2. Automatický cleanup

- **Po každém souboru**: `gc.collect()` + `del image_data`
- **Při vysoké paměti**: Automatické pozastavení a uvolnění
- **Na konci**: Úklid všech seznamů faktur

### 3. Konfigurační parametry

```python
# V invoice_gui_v2.py
MAX_IMAGE_SIZE = 768           # Rozlišení obrázků (px)
REQUEST_TIMEOUT = 120          # Timeout pro request (s)
MAX_MEMORY_PERCENT = 85        # Max. využití paměti (%)
DEBUG_MEMORY = True            # Povolit debug logy
```

---

## 🚀 Rychlá řešení

### Problém: Aplikace se zasekne a žere paměť

**Řešení 1: Snížit rozlišení**
```python
MAX_IMAGE_SIZE = 512  # Místo 768
```

**Řešení 2: Zkrátit keep_alive**
```python
keep_alive="30s"  # Místo "2m" v analyze() metodě
```

**Řešení 3: Použít menší model**
```bash
ollama pull moondream  # 0.8GB místo 3.5GB
```

---

## 📊 Typické využití paměti

| Fáze | RSS (MB) | % RAM |
|------|----------|-------|
| Start | 50-100 | 3-5% |
| Po načtení souborů | 100-150 | 5-8% |
| Po přípravě obrázku | +50-100 | +3-5% |
| Během Ollama requestu | +500-1500 | +25-75% |
| Po cleanup | -400-1200 | -20-60% |

**Poznámka:** Ollama vision modely načítají celý model do paměti při prvním requestu.

---

## 🔍 Debug mód

### Povolení detailního logu

```python
# V konstantách na začátku souboru
DEBUG_MEMORY = True  # Detailní logy paměti
```

### Co se bude logovat

```
💾 PAMĚŤ [START file.pdf]: RSS=245.1MB (12.4%)
💾 PAMĚŤ [PO PŘÍPRAVĚ OBRÁZKU file.pdf]: RSS=298.3MB (15.2%)
💾 PAMĚŤ [PO OLLAMA RESPONSE file.pdf]: RSS=812.5MB (41.3%)
💾 PAMĚŤ [PO CLEANUP file.pdf]: RSS=356.2MB (18.1%)
```

---

## ⚠️ Časté problémy a řešení

### 1. "Paměť neustále roste"

**Příčina:** Ollama drží model v paměti mezi requesty

**Řešení:**
```python
# Změnit keep_alive na minimum
keep_alive="30s"  # Model se vyhodí po 30s nečinnosti

# Nebo úplně vypnout
keep_alive="0s"  # Model se vyhodí okamžitě
```

### 2. "Aplikace spadne při velkém PDF"

**Příčina:** Obrázek z PDF je příliš velký

**Řešení:**
```python
# Snížit rozlišení
MAX_IMAGE_SIZE = 512

# Nebo snížit zoom v ImagePreprocessor
mat = fitz.Matrix(1.5, 1.5)  # Místo 2.0, 2.0
```

### 3. "Ollama request timeoutuje"

**Příčina:** Model je příliš pomalý nebo obrázek velký

**Řešení:**
```python
# Zvýšit timeout
REQUEST_TIMEOUT = 180  # 3 minuty místo 2

# Použít rychlejší model
ollama pull phi3-vision  # nebo moondream
```

### 4. "GC nestačí uvolňovat paměť"

**Příčina:** Python GC nestíhá nebo reference nejsou uvolněny

**Řešení:**
```python
# Přidat explicitní delay pro GC
import time
time.sleep(0.5)  # Čekat půl sekundy mezi soubory
```

---

## 🛠️ Pokročilé techniky

### Batch processing s pauzami

Upravit `_process_thread` pro zpracování po dávkách:

```python
BATCH_SIZE = 5
for idx, file_path in enumerate(self.found_files, start=1):
    # ... zpracování ...
    
    # Pauza po každé dávce
    if idx % BATCH_SIZE == 0:
        self._log("💾 Pauza pro uvolnění paměti...")
        gc.collect()
        time.sleep(2)
```

### Limit maximálního počtu souborů

```python
# Omezit na 20 souborů najednou
MAX_FILES_PER_RUN = 20
if len(self.found_files) > MAX_FILES_PER_RUN:
    self._log(f"⚠️ Příliš mnoho souborů ({len(self.found_files)}), omezuji na {MAX_FILES_PER_RUN}")
    self.found_files = self.found_files[:MAX_FILES_PER_RUN]
```

### Parallel processing s omezením

```python
# Místo threading.Thread použít ThreadPoolExecutor s max_workers=1
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=1) as executor:
    future = executor.submit(self._process_thread, source, target)
```

---

## 📈 Monitoring v reálném čase

### Windows Task Manager

Otevřít Detaily → najít `python.exe` → sledovat:
- **Memory (MB)**: Pracovní sada
- **CPU**: Využití procesoru

### Pomocí psutil v kódu

```python
import psutil
import time

process = psutil.Process(os.getpid())

while processing:
    mem = process.memory_info().rss / 1024 / 1024
    print(f"Paměť: {mem:.1f} MB")
    time.sleep(5)  # Kontrolovat každých 5s
```

### Ollama monitoring

```bash
# Sledovat Ollama procesy
ollama ps

# Nebo přes systémový monitor
# Windows: Task Manager → Details → ollama.exe
# Linux: htop | grep ollama
```

---

## 🎯 Doporučená konfigurace

### Pro malé složky (1-10 souborů)

```python
MAX_IMAGE_SIZE = 768
REQUEST_TIMEOUT = 120
keep_alive = "2m"
DEBUG_MEMORY = True
```

### Pro střední složky (10-50 souborů)

```python
MAX_IMAGE_SIZE = 512
REQUEST_TIMEOUT = 90
keep_alive = "1m"
DEBUG_MEMORY = True
BATCH_SIZE = 10  # Pauza po 10 souborech
```

### Pro velké složky (50+ souborů)

```python
MAX_IMAGE_SIZE = 512
REQUEST_TIMEOUT = 60
keep_alive = "30s"
DEBUG_MEMORY = False  # Šetřit pamětí
BATCH_SIZE = 5
MAX_FILES_PER_RUN = 50
```

---

## 📝 Checklista před spuštěním

- [ ] Nainstalovat `psutil`: `pip install psutil`
- [ ] Zkontrolovat volné místo v RAM (alespoň 4GB)
- [ ] Zavřít náročné aplikace (prohlížeč, IDE)
- [ ] Spustit Ollama server předem
- [ ] Otestovat na malém vzorku (3-5 souborů)
- [ ] Sledovat logy paměti v prvních kolech

---

## 🆘 Když nic nepomáhá

### 1. Restartovat Ollama

```bash
# Windows
taskkill /F /IM ollama.exe
ollama serve

# Linux
systemctl restart ollama
```

### 2. Spustit s menším modelem

```bash
ollama pull moondream
# V GUI vybrat "moondream"
```

### 3. Použít CLI místo GUI

GUI přidává overhead. Zkusit:
```bash
python invoice_processor_v2.py
```

### 4. Zvýšit pagefile/swap

**Windows:**
- Systém → Upřesnit nastavení výkonu → Upřesnit → Virtuální paměť
- Nastavit na 2x RAM

**Linux:**
```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

---

## 📞 Kontakt a podpora

Pokud problémy přetrvávají:
1. Zkopírovat celý log s pamětí
2. Uvést velikost RAM a počet zpracovávaných souborů
3. Přiložit screenshot Task Manageru
