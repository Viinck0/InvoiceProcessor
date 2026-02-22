# 🧾 Invoice Processor

AI-powered nástroj pro automatické vyhledávání, analýzu a třídění faktur na lokálním disku. Využívá lokální LLM model přes **Ollama** — žádná data neopouštějí váš počítač.

**Dvě verze k dispozici:**
- 🖥️ **GUI verze** — Moderní grafické rozhraní (doporučeno)
- ⌨️ **CLI verze** — Klasická příkazová řádka

---

## 📋 Obsah

- [Předpoklady](#předpoklady)
- [Instalace](#instalace)
- [Spuštění](#spuštění)
  - [GUI verze](#gui-verze)
  - [CLI verze](#cli-verze)
- [Funkce](#funkce)
- [Jak to funguje](#jak-to-funguje)
- [Konfigurace](#konfigurace)
- [Řešení problémů](#řešení-problémů)
- [Architektura kódu](#architektura-kódu)

---

## Předpoklady

| Požadavek | Verze | Poznámka |
|-----------|-------|----------|
| Python | ≥ 3.11 | Kvůli `list[Path]` type hints |
| [Ollama](https://ollama.com) | nejnovější | Lokální LLM runtime |
| LLM model | `llama3.1` nebo `mistral` | Stažení viz níže |
| Tesseract *(volitelné)* | ≥ 4.0 | Pro OCR obrázků (JPG/PNG) |

---

## Instalace

### 1. Naklonujte / stáhněte projekt

```bash
git clone <repo-url> invoice-processor
cd invoice-processor
```

### 2. Vytvořte a aktivujte virtuální prostředí

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

### 3. Nainstalujte Python závislosti

```bash
pip install -r requirements.txt
```

### 4. Nainstalujte a spusťte Ollama

**macOS / Linux:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

**Windows:** Stáhněte instalátor z [ollama.com](https://ollama.com/download)

### 5. Stáhněte LLM model

```bash
# Doporučený model (dobré výsledky, ~4.7 GB)
ollama pull llama3.1

# Alternativa – menší, rychlejší (~4.1 GB)
ollama pull mistral
```

Ověřte, že Ollama běží:
```bash
ollama list          # Zobrazí stažené modely
ollama run llama3.1  # Test – napište /bye pro ukončení
```

### 6. (Volitelné) Instalace Tesseract pro OCR obrázků

Pro analýzu JPG/PNG souborů s textem:

```bash
# Ubuntu / Debian
sudo apt install tesseract-ocr tesseract-ocr-ces

# macOS
brew install tesseract tesseract-lang

# Windows – stáhněte installer z:
# https://github.com/tesseract-ocr/tesseract/releases
```

---

## Spuštění

### 🖥️ GUI verze (doporučeno)

```bash
python invoice_gui.py
```

**Funkce GUI:**
- 📁 **Výběr složek** — Proklikové dialogy pro zdrojovou i cílovou složku
- 🤖 **Výběr modelu** — Přepínání mezi Ollama modely (llama3.1, mistral, gemma2...)
- 🔀 **Kritérium třídění** — Rozbalovací menu pro výběr způsobu třídění
- 📊 **Progress tracking** — vizuální ukazatel průběhu zpracování
- 📝 **Live log** — Detailní výpis všech operací v reálném čase
- ⏹ **Zrušení** — Tlačítko pro přerušení zpracování kdykoliv během procesu

![GUI náhled](docs/gui-screenshot.png)

**Ovládání:**
1. Klikněte na **"Procházet"** a vyberte zdrojovou složku
2. Klikněte na **"Procházet"** a vyberte cílovou složku
3. Zvolte **kritérium třídění** z rozbalovací nabídky
4. (Volitelné) Změňte **Ollama model**
5. Klikněte na **"▶ Spustit zpracování"**
6. Sledujte průběh v logu a progress baru
7. Po dokončení se zobrazí shrnutí výsledků

---

### ⌨️ CLI verze

```bash
python invoice_processor.py
```

Aplikace se vás interaktivně zeptá na:
1. **Zdrojová složka** — kde hledat soubory (např. `C:\Dokumenty\Faktury` nebo `~/Downloads`)
2. **Cílová složka** — kam přesunout zpracované faktury (vytvoří se automaticky)
3. **Potvrzení** — spuštění AI analýzy
4. **Kritérium třídění** — jak pojmenovat a seřadit soubory

---

## Funkce

| Funkce | Popis |
|--------|-------|
| 🔍 **Vyhledávání faktur** | Rekurzivní prohledávání složek, podpora PDF, JPG, PNG |
| 🤖 **AI analýza** | Ollama LLM detekuje faktury a extrahuje klíčová data |
| 📅 **Extrakce dat** | Odesílatel, příjemce, datum vystavení, datum splatnosti |
| 🔀 **Třídění** | 4 kritéria: odesílatel, příjemce, datum vystavení, datum splatnosti |
| 📁 **Přesun a přejmenování** | Automatické třídění do cílové složky s pořadovými čísly |
| 🌐 **Multi-model support** | Podpora llama3.1, mistral, gemma2, llama3, llama3.2 |
| 🖥️ **GUI + CLI** | Dvě verze pro různé použití |

---

## Jak to funguje

```
┌─────────────────────────────────────────────────────┐
│                    PIPELINE                         │
│                                                     │
│  1. FileDiscovery                                   │
│     └─ Rekurzivní sken složky (PDF, JPG, PNG)       │
│                                                     │
│  2. TextExtractor                                   │
│     ├─ PDF  → PyMuPDF (fitz)                        │
│     └─ IMG  → pytesseract OCR (volitelné)           │
│                                                     │
│  3. OllamaAnalyzer                                  │
│     └─ LLM prompt → JSON { is_invoice, sender, … }  │
│                                                     │
│  4. InvoiceOrganizer                                │
│     ├─ Třídění dle zvoleného kritéria               │
│     ├─ shutil.move() → cílová složka                │
│     └─ Přejmenování: 001_[Kriterium]_[Název].pdf    │
└─────────────────────────────────────────────────────┘
```

---

## Příklad běhu

```
╔═══════════════════════════════════════════════════════╗
║          🧾  Invoice Processor  v1.0                  ║
║   AI-powered třídění faktur pomocí Ollama + LLM       ║
╚═══════════════════════════════════════════════════════╝

📁  NASTAVENÍ SLOŽEK
───────────────────────────────────────────────────────
Zdrojová složka (kde hledat soubory): /home/user/dokumenty
Cílová složka (kam přesunout faktury): /home/user/faktury-sorted

🔍  VYHLEDÁVÁNÍ SOUBORŮ
───────────────────────────────────────────────────────
Nalezeno 15 souborů ke zpracování.

Nalezeno 15 souborů. Spustit AI analýzu? [A/n]: a

🤖  AI ANALÝZA (model: llama3.1)
───────────────────────────────────────────────────────
  [  1/15] faktura_alza.pdf ... ✓ Faktura | Od: Alza_cz_a_s_             | Datum: 2024-03-15
  [  2/15] smlouva.pdf ...      – Není faktura.
  [  3/15] faktura_001.pdf ...  ✓ Faktura | Od: ABC_sro                   | Datum: 2024-01-08
  ...

📊  VÝSLEDKY ANALÝZY
───────────────────────────────────────────────────────
  Celkem zpracováno souborů : 15
  Identifikováno faktur     : 11
  Přeskočeno (není faktura) : 4

═══════════════════════════════════════════════════════
Podle čeho chcete faktury vytřídit a přejmenovat?
───────────────────────────────────────────────────────
  1 - Odesílatel
  2 - Příjemce
  3 - Datum vystavení
  4 - Datum splatnosti
═══════════════════════════════════════════════════════
Vaše volba [1-4]: 3

Přesouvám 11 faktur do: /home/user/faktury-sorted
───────────────────────────────────────────────────────
  ✓ [001] faktura_001.pdf
        → 001_2024-01-08_faktura_001.pdf
  ✓ [002] faktura_alza.pdf
        → 002_2024-03-15_faktura_alza.pdf
  ...
───────────────────────────────────────────────────────
  Úspěšně přesunuto: 11  |  Chyby: 0

✅  Hotovo!
```

---

## Konfigurace

### Jiný model Ollama

Přes proměnnou prostředí:
```bash
OLLAMA_MODEL=mistral python invoice_processor.py
```

Nebo editujte konstantu v `invoice_processor.py`:
```python
OLLAMA_MODEL = "mistral"   # nebo "llama3", "gemma2", apod.
```

### Limit délky textu

Pokud máte velmi dlouhé faktury a model vrací špatné výsledky, upravte:
```python
MAX_TEXT_CHARS = 4000   # Zvyšte pro delší dokumenty
```

---

## Řešení problémů

### `ConnectionRefusedError` při komunikaci s Ollama

Ollama není spuštěna. Spusťte ji:
```bash
ollama serve        # V samostatném terminálu
# nebo
systemctl start ollama   # Linux s systemd
```

### `Error: model 'llama3.1' not found`

Model není stažen:
```bash
ollama pull llama3.1
```

### LLM vrací špatné JSON / detekuje vše jako fakturu

Zkuste jiný model nebo snižte teplotu:
```python
options={"temperature": 0.0}   # V metodě analyze() v OllamaAnalyzer
```

### PDF nejde přečíst (prázdný text)

Může jít o skenovaný PDF bez textové vrstvy. Řešení:
1. Nainstalujte Tesseract a pytesseract
2. Rozšiřte `_extract_pdf()` o OCR fallback (PDF → obrázky stránek → OCR)

### `PermissionError` při přesunu souborů

- Ujistěte se, že soubory nejsou otevřeny v jiné aplikaci
- Na Windows spusťte terminál jako administrátor
- Zkontrolujte oprávnění cílové složky

### Pomalé zpracování

Velký počet souborů + pomalý model = hodiny čekání. Tipy:
- Testujte nejprve na malé složce (10–20 souborů)
- Použijte rychlejší model: `mistral` nebo `llama3.2:3b`
- Zkraťte `MAX_TEXT_CHARS` na 2000

---

## Architektura kódu

```
invoice_processor.py (CLI)    invoice_gui.py (GUI)
├── Invoice (dataclass)       ├── InvoiceProcessorGUI (ctk.CTk)
├── FileDiscovery             │   ├── _setup_ui()
├── TextExtractor             │   ├── _browse_source/target()
│   ├── _extract_pdf()        │   ├── _start_processing()
│   └── _extract_image()      │   ├── _process_thread()
├── OllamaAnalyzer            │   └── _log/update_progress()
│   └── analyze()             └── (sdílí tyto moduly s CLI)
├── InvoiceOrganizer              ├── FileDiscovery
│   ├── ask_sort_criterion()      ├── TextExtractor
│   ├── sort_invoices()           ├── OllamaAnalyzer
│   └── process()                 └── Invoice (dataclass)
└── InvoiceProcessor
    └── run()
```

---

## Licence

MIT — volně použitelné a upravitelné.
