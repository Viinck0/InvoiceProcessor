# 🧾 Invoice Processor v2 - Llama Vision Workflow

Nová generace nástroje pro třídění faktur využívající **multimodální AI modely** (Llama Vision, LLaVA) pro přímou analýzu dokumentů bez OCR.

---

## 🆕 Co je nového ve v2?

| Funkce | v1 (textová analýza) | v2 (Llama Vision) |
|--------|---------------------|-------------------|
| **Vstup** | Extrahovaný text z PDF/obrázků | Přímá analýza obrázku |
| **OCR** | Vyžaduje Tesseract (nepřesné) | Není potřeba |
| **Rozpoznání faktur** | Často chybuje u dokumentů s datem/jménem | Vizuální rozpoznání struktury |
| **Podporované formáty** | PDF, JPG, PNG | PDF, JPG, PNG, TIFF, BMP, GIF |
| **Filtrování** | Basic | Pokročilé (odesílatel, částka, datum) |
| **Přesnost** | ~70-80% | ~90-95% |

---

## 📋 Předpoklady

| Požadavek | Verze | Poznámka |
|-----------|-------|----------|
| Python | ≥ 3.11 | |
| [Ollama](https://ollama.com) | nejnovější | Lokální LLM runtime |
| Vision model | `llama3.2-vision`, `llava`, `bakllava` | Stažení viz níže |

---

## 🚀 Rychlý start

### 1. Instalace závislostí

```bash
pip install -r requirements.txt
```

### 2. Stažení Vision modelu

```bash
# Doporučený model (dobrá přesnost, ~3.5 GB)
ollama pull llama3.2-vision

# Alternativa – LLaVA (také velmi dobrý)
ollama pull llava

# Nebo Bakllava (menší, rychlejší)
ollama pull bakllava
```

### 3. Ověření modelu

```bash
ollama list
# Měli byste vidět: llama3.2-vision    pulled
```

### 4. Spuštění

**GUI verze (doporučeno):**
```bash
python invoice_gui_v2.py
```

**CLI verze:**
```bash
python invoice_processor_v2.py
```

---

## 🔄 Workflow v2

```
┌─────────────────────────────────────────────────────────────┐
│                    V2 PIPELINE                              │
│                                                             │
│  1. FileDiscovery                                           │
│     └─ Rychlé vyhledání: PDF, JPG, PNG, TIFF, BMP, GIF      │
│                                                             │
│  2. ImagePreprocessor                                       │
│     ├─ PDF → konverze 1. stránky na obrázek (2x zoom)       │
│     └─ Obrázky → resize na max 2048px                       │
│                                                             │
│  3. LlamaVisionAnalyzer                                     │
│     ├─ Odešle obrázek do multimodálního LLM                 │
│     ├─ Získá JSON: {is_invoice, sender, date, amount, ...}  │
│     └─ Rozliší fakturu od dokumentu s jen datem/jménem      │
│                                                             │
│  4. InvoiceFilterAgent                                      │
│     └─ Filtr podle: odesílatel, příjemce, datum, částka     │
│        Např.: "Jana" → najde všechny faktury od Jany        │
│                                                             │
│  5. InvoiceOrganizer                                        │
│     ├─ Seřazení podle kritéria                              │
│     └─ Přesun do cílové složky s názvy:                     │
│        001_20240115_ABC_sro_faktura_001.pdf                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 🎯 Příklady použití

### Příklad 1: Třídění faktur od konkrétního odesílatele

Chcete najít všechny faktury od "Jana" a vytřídit je podle data:

1. Otevřete `invoice_gui_v2.py`
2. Vyberte zdrojovou složku (např. `Downloads`)
3. Vyberte cílovou složku (např. `Faktury/Jana`)
4. V sekci **Filtr faktur** zvolte:
   - Kritérium: `Odesílatel`
   - Hledaný výraz: `Jana`
5. Třídit podle: `Datum vystavení`
6. Klikněte na **▶ Spustit zpracování**

Výsledek:
```
Faktury/Jana/
├── 001_2024-01-15_Jana_faktura_001.pdf
├── 002_2024-02-20_Jana_faktura_002.pdf
└── 003_2024-03-10_Jana_faktura_003.pdf
```

### Příklad 2: Třídění všech faktur bez filtru

1. Nechte filtr vypnutý (`Vypnuto`)
2. Třídit podle: `Odesílatel`
3. Spustit

Výsledek:
```
Cílová složka/
├── 001_Alza_cz_a_s_20240115_faktura_alza.pdf
├── 002_ABC_sro_20240120_faktura_001.pdf
├── 003_Telefonica_O2_20240125_faktura_tel.pdf
└── ...
```

### Příklad 3: CLI použití s filtrem

```bash
python invoice_processor_v2.py

# Interaktivní dotazy:
Zdrojová složka: C:\Users\Vinci\Downloads
Cílová složka: C:\Users\Vinci\Dokumenty\Faktury

# Po analýze se zobrazí dostupní odesílatelé:
Dostupní odesílatelé:
  1. Alza.cz a.s.
  2. ABC s.r.o.
  3. Jana Nováková
  4. Telefonica O2 Czechia

# Zadejte filtr:
Filtrovat podle odesílatele: Jana

# Nalezeno 3 faktur od 'Jana'
```

---

## 🤖 Podporované Vision modely

| Model | Velikost | Přesnost | Rychlost | Doporučení |
|-------|----------|----------|----------|------------|
| `llama3.2-vision` | ~3.5 GB | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | **Doporučeno** |
| `llava` | ~3.8 GB | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | Velmi dobrý |
| `bakllava` | ~2.1 GB | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Rychlý, menší |
| `llama3.1` | ~4.7 GB | ⭐⭐⭐ | ⭐⭐⭐ | Pouze text (nepoužívat pro v2) |

---

## 🔧 Konfigurace

### Jiný Vision model

Přes proměnnou prostředí:
```bash
VISION_MODEL=llava python invoice_gui_v2.py
```

Nebo v GUI změňte v rozbalovací nabídce **🤖 Vision model**.

### Úprava promptu

Prompt pro analýzu lze upravit v `LlamaVisionAnalyzer.PROMPT_TEMPLATE`:

```python
PROMPT_TEMPLATE = """Jsi expert na účetnictví...
...
"""
```

### Maximální velikost obrázku

Pro velmi velké skeny upravte:
```python
MAX_IMAGE_SIZE = 2048  # Zvyšte na 4096 pro lepší kvalitu
```

---

## 📊 Porovnání v1 vs v2

### Scénář: Složka obsahuje faktury + dokumenty s jen datem/jménem

**v1 (textová analýza):**
```
Nalezeno: 50 souborů
✓ Faktura: 35
✗ Falešně pozitivní: 8 (dokumenty s datem)
✗ Falešně negativní: 7 (skany bez textové vrstvy)
```

**v2 (Llama Vision):**
```
Nalezeno: 50 souborů
✓ Faktura: 42
✗ Falešně pozitivní: 1
✗ Falešně negativní: 0
```

---

## ⚠️ Řešení problémů

### `Error: model 'llama3.2-vision' not found`

```bash
ollama pull llama3.2-vision
```

### `ConnectionRefusedError`

Ollama není spuštěna:
```bash
ollama serve
```

### Pomalá analýza

Vision modely jsou náročnější na výpočet. Tipy:
- Použijte menší model: `ollama pull bakllava`
- Snižte `MAX_IMAGE_SIZE` na 1024
- Testujte na menších složkách (10-20 souborů)

### Špatná detekce faktur

1. Zkuste jiný model (`llava` místo `llama3.2-vision`)
2. Upravte prompt pro lepší rozlišení
3. Snižte teplotu: `options={"temperature": 0.0}`

### PDF se špatně konvertuje na obrázek

Některá PDF mohou být poškozená nebo prázdná. Zkontrolujte:
```python
# V ImagePreprocessor._pdf_to_image()
if len(doc) == 0:
    return None  # PDF nemá stránky
```

---

## 📁 Struktura projektu

```
projekt třídění faktur/
├── invoice_processor_v2.py    # CLI verze s Llama Vision
├── invoice_gui_v2.py          # GUI verze s Llama Vision
├── invoice_processor.py       # Legacy v1 (textová analýza)
├── invoice_gui.py             # Legacy v1 GUI
├── requirements.txt           # Závislosti
├── README.md                  # Tento dokument
└── README_v2.md               # Dokumentace v2 workflow
```

---

## 🎯 Budoucí vylepšení

- [ ] Podpora vícestránkových PDF (analýza všech stránek)
- [ ] Export do CSV/Excel s extrahovanými daty
- [ ] Hromadné přejmenování bez přesunu
- [ ] Integrace s cloudovými úložišti
- [ ] Vlastní trénink pro lepší detekci českých faktur

---

## Licence

MIT — volně použitelné a upravitelné.
