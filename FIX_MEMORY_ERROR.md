# 🚨 Řešení: "memory layout cannot be allocated"

Tato chyba znamená, že **Ollama vision model nemůže alokovat paměť** pro zpracování obrázku.

---

## ⚡ Rychlá řešení (vyberte jedno)

### 1. **Stáhnout menší model** (DOPORUČENO)

```bash
# Zkontrolovat dostupné modely
ollama list

# Stáhnout nejmenší vision model (0.8GB)
ollama pull moondream

# Nebo středně velký (2.1GB)
ollama pull bakllava

# Nebo phi-3 vision (2.1GB)
ollama pull phi3-vision
```

**Poté spustit:**
```bash
# Emergency mode s moondream
python invoice_gui_emergency.py

# Nebo normálně a vybrat model v GUI
python invoice_gui_v2.py
```

---

### 2. **Uvolnit paměť zavřením aplikací**

- Zavřít prohlížeč (Chrome/Edge žere 1-4GB RAM)
- Zavřít další náročné aplikace
- Restartovat počítač před spuštěním

---

### 3. **Restartovat Ollama server**

```bash
# Windows - ukončit Ollama
taskkill /F /IM ollama.exe

# Zkontrolovat že běží
ollama serve

# Nebo restartovat službu
net stop ollama
net start ollama
```

---

### 4. **Použít emergency script**

```bash
# Extrémně nízká paměť, ale funguje
python invoice_gui_emergency.py
```

---

## 📊 Porovnání modelů

| Model | Velikost | Min. RAM | Doporučeno pro |
|-------|----------|----------|----------------|
| `moondream` | 0.8GB | 2GB | ⭐ Emergency |
| `bakllava` | 2.1GB | 4GB | ⭐ Rychlé |
| `phi3-vision` | 2.1GB | 4GB | ⭐ Přesné |
| `llama3.2-vision` | 3.5GB | 8GB | Běžné |
| `llava` | 3.8GB | 8GB | Běžné |

---

## 🔧 Trvalé řešení

### Krok 1: Stáhnout menší model

```bash
ollama pull moondream
```

### Krok 2: Nastavit jako výchozí

Upravit `invoice_gui_v2.py`:

```python
VISION_MODEL = "moondream"  # Místo "llama3.2-vision"
MAX_IMAGE_SIZE = 384        # Místo 512
BATCH_SIZE = 1              # Místo 3
```

### Krok 3: Spustit

```bash
python invoice_gui_v2.py
```

---

## 📈 Monitoring paměti

### Před spuštěním

```bash
# Windows - zkontrolovat volnou RAM
tasklist /FI "IMAGENAME eq ollama.exe" /FO TABLE /NH

# Nebo v Task Manageru
Ctrl+Shift+Esc → Performance → Memory
```

### Během zpracování

Sledujte logy:
```
💾 Paměť [1/14]: 245.3MB (12.4%)  ✓ OK
💾 Paměť [5/14]: 512.8MB (26.1%)  ✓ OK
💾 Paměť [10/14]: 834.2MB (42.3%) ✓ OK
⚠️ Vysoká paměť! Zvažuji přerušení...  > 70%
```

---

## 🆘 Když nic nepomáhá

### 1. Zvýšit virtuální paměť (Windows)

```
Ovládací panely → Systém → Upřesnit nastavení →
Výkon → Upřesnit → Virtuální paměť → Změnit

Nastavit na 2x fyzické RAM (např. 16GB pro 8GB RAM)
```

### 2. Použít CLI místo GUI

GUI přidává 50-100MB overhead:

```bash
python invoice_processor_v2.py
```

### 3. Zpracovávat po menších dávkách

Rozdělit soubory do více složek:
```
Downloads/
├── batch1/   (5 souborů)
├── batch2/   (5 souborů)
└── batch3/   (4 souborů)
```

Zpracovat každou zvlášť.

---

## ✅ Checklista před dalším spuštěním

- [ ] Stažen menší model (`ollama pull moondream`)
- [ ] Zavřený prohlížeč a další náročné aplikace
- [ ] Restartovaný Ollama server
- [ ] Nastaveno `MAX_IMAGE_SIZE = 384` nebo méně
- [ ] Nastaveno `BATCH_SIZE = 1`
- [ ] Vyzkoušen emergency script

---

## 🎯 Doporučená konfigurace pro 8GB RAM

```python
# V invoice_gui_v2.py
VISION_MODEL = "moondream"     # Nejmenší model
MAX_IMAGE_SIZE = 384           # Minimální rozlišení
BATCH_SIZE = 1                 # Jeden soubor najednou
REQUEST_TIMEOUT = 180          # Delší timeout
keep_alive = "30s"             # Rychle uvolnit paměť
```

**Výsledek:**
- Paměť: ~1-2GB během zpracování
- Čas: ~10-20s na soubor
- Přesnost: Stále dostatečná pro faktury

---

## 📞 Další pomoc

Pokud problém přetrvává:
1. Zkopírovat celý log včetně chyb
2. Uvést kolik máte RAM
3. Uvést který model používáte
4. Přiložit výpis z `ollama list`
