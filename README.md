# Invoice Processor v6.3 - Multi-Agent Workflow

Nejpřesnější systém pro třídění faktur s 3 specializovanými AI agenty + OCR validátorem

## 📋 Obsah

- [Přehled](#přehled)
- [Architektura systému](#architektura-systému)
- [Agent Workflow](#agent-workflow)
- [Detailní popis komponent](#detailní-popis-komponent)
- [Rozhodovací logika](#rozhodovací-logika)
- [Instalace a spuštění](#instalace-a-spuštění)
- [Konfigurace](#konfigurace)

---

## 📊 Přehled

### Hlavní funkce

- **Multi-Agent AI Workflow** - 3 specializované agenty pracující paralelně
- **Hybridní OCR** - RapidOCR (PaddleOCR přes ONNX Runtime) + přímá extrakce textu z PDF
- **OCR Text Validator** - Detekce a oprava halucinovaných slov z OCR
- **Consensus Engine** - Vážené hlasování agentů s veto právem
- **Human Review Queue** - Nejisté případy k ruční kontrole
- **GUI Interface** - Moderní desktopové rozhraní (CustomTkinter)

### Klíčové vlastnosti

| Vlastnost | Popis |
|-----------|-------|
| **Rychlost OCR** | RapidOCR je 10x rychlejší než Tesseract |
| **Přesnost** | Hybridní extrakce - 100% pro digitální PDF, OCR fallback pro skeny |
| **Preskok OCR validatoru** | Automaticky přeskočen pro digitální text z PDF |
| **Deterministický výstup** | Temperature=0.0 pro konzistentní výsledky |
| **Anti-halucinace** | Detekce a oprava vymyšlených hodnot |

---

## 🏗️ Architektura systému

```
┌─────────────────────────────────────────────────────────────────┐
│                        GUI (invoice_gui_v6.py)                  │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ File Loader │  │ Progress UI  │  │ Results Display       │  │
│  └──────┬──────┘  └──────────────┘  └───────────────────────┘  │
└─────────┼───────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    OCRExtractor (Hybridní)                      │
│  ┌─────────────────────────┐  ┌────────────────────────────┐   │
│  │ PDF: Direct Text (100%) │  │ PDF/Image: RapidOCR fallback│  │
│  └─────────────────────────┘  └────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                   OCR Text Validator                            │
│  - Detekce halucinovaných slov                                  │
│  - Oprava OCR chyb (2I6→216, rn→m)                              │
│  - Jazyková validace (CS/EN)                                    │
│  - PŘESKOČENO pro digitální PDF text                            │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Pre-Filter (Pravidla)                        │
│  - Rychlá klasifikace před AI                                   │
│  - Zamítnutí prázdných dokumentů                                │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│              PARALELNÍ AI ANALÝZA (3 agenty)                    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  Classifier  │  │  Extractor   │  │   Anomaly Detector   │  │
│  │  Agent       │  │  Agent       │  │   Agent              │  │
│  │  (JE/NENÍ)   │  │  (DATA)      │  │   (CV, certifikáty)  │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
└─────────┼─────────────────┼─────────────────────┼──────────────┘
          │                 │                     │
          └─────────────────┼─────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Consensus Engine                             │
│  - Vážené hlasování (Classifier 40%, Extractor 30%, Anomaly 30%)│
│  - Anomaly veto právo                                           │
│  - Detekce halucinací                                           │
│  - Rozhodnutí: Auto-Accept / Human-Review / Auto-Reject         │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Výsledek                                     │
│  - is_invoice: true/false/null                                  │
│  - confidence: 0.0-1.0                                          │
│  - decision_type: auto_accept/human_review/auto_reject          │
│  - extracted_data: {vendor, customer, amount, dates, ...}       │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🤖 Agent Workflow

### Workflow Diagram

```
                    ┌─────────────────┐
                    │  Document Input │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  OCR Extraction │
                    │  (Hybridní)     │
                    └────────┬────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
    ┌─────────▼─────────┐         ┌────────▼────────┐
    │ Digital PDF Text  │         │  Scan/Image OCR │
    │  (100% přesnost)  │         │  (RapidOCR)     │
    └─────────┬─────────┘         └────────┬────────┘
              │                             │
              │         ┌───────────────────┘
              │         │
              │  ┌──────▼──────┐
              │  │ OCR Validator│
              │  │ (Pouze pro  │
              │  │ OCR text)   │
              │  └──────┬──────┘
              │         │
              └─────────┼──────────────┐
                        │              │
              ┌─────────▼──────────────▼──────┐
              │      Pre-Filter (Pravidla)    │
              └─────────────┬─────────────────┘
                            │
              ┌─────────────▼─────────────────┐
              │   Skip AI processing?         │
              │   (reject > 90% confidence)   │
              └───────┬──────────────┬────────┘
                      │ YES          │ NO
         ┌────────────┘              └─────────────┐
         │                                         │
         │            ┌────────────────────────┐   │
         │            │  PARALELNÍ AI AGENTI   │   │
         │            │  ┌──────────────────┐  │   │
         │            │  │ Classifier Agent │  │   │
         │            │  │ - is_invoice     │  │   │
         │            │  │ - confidence     │  │   │
         │            │  │ - elements       │  │   │
         │            │  │ - extracted vals │  │   │
         │            │  └──────────────────┘  │   │
         │            │  ┌──────────────────┐  │   │
         │            │  │ Extractor Agent  │  │   │
         │            │  │ - invoice data   │  │   │
         │            │  │ - completeness   │  │   │
         │            │  │ - validation     │  │   │
         │            │  └──────────────────┘  │   │
         │            │  ┌──────────────────┐  │   │
         │            │  │ Anomaly Detector │  │   │
         │            │  │ - CV/resume      │  │   │
         │            │  │ - certificates   │  │   │
         │            │  │ - contracts      │  │   │
         │            │  │ - veto power     │  │   │
         │            │  └──────────────────┘  │   │
         │            └───────────┬────────────┘   │
         │                        │                │
         └────────────────────────┼────────────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │    Consensus Engine        │
                    │  - Weighted voting         │
                    │  - Anomaly veto            │
                    │  - Hallucination detection │
                    │  - Decision logic          │
                    └─────────────┬──────────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │      Final Decision        │
                    │  ┌──────────────────────┐  │
                    │  │ Auto-Accept (≥0.7)   │  │
                    │  │ Human-Review (0.5-0.7)│ │
                    │  │ Auto-Reject (<0.5)   │  │
                    │  └──────────────────────┘  │
                    └────────────────────────────┘
```

### Časová osa workflow

```
Čas (sekundy)    Komponenta                    Status
─────────────────────────────────────────────────────────
0.0s             Document loaded               ✓
0.1s             OCR Extraction started        ⏳
0.3s             ├─ Digital PDF text           ✓ (100% přesnost)
   nebo
1.5s             ├─ RapidOCR (sken/obrázek)    ✓
2.0s             OCR Validator (pokud OCR)     ⏳
2.5s             Pre-Filter                    ✓
2.6s             ┌─ Classifier Agent           ⏳ (paralelně)
2.6s             ├─ Extractor Agent            ⏳ (paralelně)
2.6s             └─ Anomaly Detector           ⏳ (paralelně)
5.0s             ├─ Classifier complete        ✓ (~2.4s)
8.0s             ├─ Anomaly complete           ✓ (~5.4s)
10.0s            └─ Extractor complete         ✓ (~7.4s)
10.5s            Consensus Engine              ✓
11.0s            Final Decision                ✓
```

---

## 📦 Detailní popis komponent

### 1. OCRExtractor (Hybridní extrakce)

**Soubor:** `invoice_gui_v6.py` (třída `OCRExtractor`)

**Funkce:**
- Extrahuje text z PDF a obrázků
- Hybridní přístup pro optimální přesnost a rychlost

**Metody extrakce:**

| Typ dokumentu | Metoda | Přesnost | Rychlost |
|---------------|--------|----------|----------|
| PDF s textem | PyMuPDF direct extraction | 100% | ~0.3s |
| PDF sken | RapidOCR fallback | ~95% | ~1.5s |
| Obrázky | RapidOCR | ~95% | ~1.5s |

**Klíčové konstanty:**
```python
MIN_ZNAKU_PRO_DIGITALNI = 30  # Min. znaků pro detekci digitálního PDF
OCR_ZOOM = 2.0  # Zoom pro lepší kvalitu OCR
USE_RAPIDOCR = True  # Použít RapidOCR místo Tesseractu
```

**Workflow:**
1. Načte PDF pomocí PyMuPDF
2. Zkusí přímou extrakci textu (`page.get_text()`)
3. Pokud text ≥ 30 znaků → **digitální text, OCR se přeskočí**
4. Pokud text < 30 znaků → **sken, použije se RapidOCR fallback**

---

### 2. OCR Text Validator

**Soubor:** `agent_workflow/ocr_validator.py`

**Funkce:**
- Validuje text po OCR
- Detekuje halucinovaná slova (nesmyslné znaky z OCR)
- Opravuje běžné OCR chyby
- Filtruje nesmyslná slova

**Typy oprav:**

| Typ chyby | Příklad opravy |
|-----------|----------------|
| Číslo-písmeno | `2I6` → `216`, `l5` → `15` |
| Spojené znaky | `rn` → `m`, `vv` → `w` |
| Diakritika | `ć` → `č`, `ś` → `š` |
| Halucinace | `xxxxxx` → odstraněno |

**Whitelist pro faktury:**
```python
INVOICE_WHITELIST = {
    'faktura', 'dodavatel', 'odběratel', 'ičo', 'dič',
    'splatnosti', 'celkem', 'Kč', 'EUR', 'USD',
    'invoice', 'supplier', 'customer', 'vat', 'total',
    # ... města, příjmení, zkratky
}
```

**Důležité:** Validator se **přeskočí pro digitální PDF text** (100% přesnost).

---

### 3. Classifier Agent

**Soubor:** `agent_workflow/classifier_agent.py`

**Funkce:**
- Binární klasifikace: JE faktura vs. NENÍ faktura
- Extrakce 5 klíčových elementů
- Strukturovaný output s reasoningem

**5 Elementů faktury:**
1. **Document identification** - Název dokumentu
2. **Subjects** - Dodavatel + Odběratel
3. **Dates** - Datum vystavení + splatnosti
4. **Financial data** - Částka, DPH, základ
5. **Payment instructions** - Bankovní účet, IBAN

**Output:**
```json
{
  "is_invoice": true,
  "confidence": 0.92,
  "elements_present": {
    "identification": true,
    "subjects": true,
    "dates": true,
    "financial": true,
    "payment_info": true
  },
  "extracted_values": {
    "document_type": "Faktura",
    "supplier": "ABC s.r.o.",
    "customer": "XYZ a.s.",
    "amount": "15000 Kč",
    "date": "25.2.2026",
    "payment_info": "123456789/0100"
  },
  "reasoning": "Nalezeno: Dodavatel=ABC s.r.o., Odběratel=XYZ a.s., Částka=15000 Kč",
  "confidence": 0.92
}
```

**Anti-halucinační ochrany:**
- Strong negative indicators (životopis, certifikáty, smlouvy)
- Context-dependent indicators (smlouva + fakturační kontext = OK)
- Sanity check pro částku (detekce PSČ jako částky)
- Regex záchrana částky pokud AI selže

---

### 4. Extractor Agent

**Soubor:** `agent_workflow/extractor_agent.py`

**Funkce:**
- Detailní extrakce invoice dat
- Validace formátů (data, částky, IČO, DIČ)
- Výpočet completeness score

**Extrahovaná pole:**

| Pole | Typ | Popis |
|------|-----|-------|
| `invoice_number` | string | Číslo faktury |
| `vendor_name` | string | Název dodavatele |
| `customer_name` | string | Název odběratele |
| `issue_date` | date | Datum vystavení (YYYY-MM-DD) |
| `due_date` | date | Datum splatnosti (YYYY-MM-DD) |
| `total_amount` | float | Částka k úhradě |
| `currency` | string | Měna (CZK, EUR, USD) |
| `vat_amount` | float | DPH |
| `base_amount` | float | Základ bez DPH |
| `bank_account` | string | Číslo účtu |
| `variable_symbol` | string | Variabilní symbol |
| `vendor_ico` | string | IČO dodavatele |
| `vendor_dic` | string | DIČ dodavatele |

**Optimalizace:**
- Truncation na 4000 znaků (dostatečné pro faktury)
- Two-stage extraction (quick fields → detailed)
- Regex fallback pro AI failure
- Completeness score calculation

**Completeness Score:**
```python
completeness_score = (found_fields / expected_fields)
# Např. 8/10 = 0.8 (80% kompletní)
```

---

### 5. Anomaly Detector Agent

**Soubor:** `agent_workflow/anomaly_agent.py`

**Funkce:**
- Detekce dokumentů které NENÍ faktura
- Pravidlová + AI detekce
- Veto právo pro zamítnutí

**Typy anomálií:**

| Typ | Popis | Threshold | Veto |
|-----|-------|-----------|------|
| `cv_resume` | Životopisy (CS/EN) | 2 keyword | ✅ |
| `certificate` | Certifikáty, osvědčení | 2 keyword | ✅ |
| `contract` | Pracovní/nájemní smlouvy | 2 keyword | ✅ |
| `reminder` | Upomínky, výzvy k úhradě | 1 keyword | ✅ |
| `offer` | Nabídky, rozpočty | 1 keyword | ❌ |
| `inquiry` | Poptávky, RFP | 1 keyword | ❌ |
| `internal` | Koncepty, drafty | 2 keyword | ❌ |

**Output:**
```json
{
  "is_anomaly": true,
  "anomaly_type": "cv_resume",
  "confidence": 0.95,
  "detected_keywords": ["životopis", "vzdělání", "praxe"],
  "flags": ["veto"],
  "reasoning": "Nalezeno 3 keyword pro cv_resume"
}
```

---

### 6. Consensus Engine

**Soubor:** `agent_workflow/consensus_engine.py`

**Funkce:**
- Kombinuje výsledky všech 3 agentů
- Vážené hlasování
- Anomaly veto
- Detekce halucinací
- Finální rozhodnutí

**Váhy agentů:**

| Agent | Váha | Popis |
|-------|------|-------|
| Classifier | 40% | Binární rozhodnutí JE/NENÍ |
| Extractor | 30% | Completeness score |
| Anomaly | 30% | Detekce ne-faktur |

**Rozhodovací logika:**

```python
# Pseudokód rozhodování

1. ANOMALY VETO check:
   if anomaly_type in NON_INVOICE_TYPES and confidence >= 0.85:
       return AUTO_REJECT

2. CLASSIFIER REASONING check:
   if reasoning explicitly says "NENÍ faktura":
       return AUTO_REJECT

3. KEYWORD check:
   if classifier says invoice but no invoice keywords in text:
       return RETRY

4. HALLUCINATION check:
   if classifier says invoice (conf >= 0.6) but extractor found <= 1 fields:
       return AUTO_REJECT (classifier hallucinating)

5. CONFLICT check:
   if classifier says invoice but extractor found only 2-3 fields:
       return HUMAN_REVIEW

6. AGREEMENT check:
   if classifier says NOT invoice and extractor found < 3 fields:
       return AUTO_REJECT

7. EXTRACTOR OVERRIDE:
   if classifier says NOT invoice but extractor found >= 4 fields (with amount):
       return AUTO_ACCEPT (extractor priority)

8. HIGH CONFIDENCE check:
   if classifier conf >= 0.85 and all 5 elements present:
       return AUTO_ACCEPT

9. DEFAULT weighted voting:
   weighted_score = 0.4*classifier + 0.3*extractor + 0.3*anomaly
   if weighted_score >= 0.7: return AUTO_ACCEPT
   elif weighted_score >= 0.5: return HUMAN_REVIEW
   else: return AUTO_REJECT
```

**Decision Types:**

| Typ | Confidence | Akce |
|-----|------------|------|
| `auto_accept` | ≥ 0.7 | Automaticky přijmout |
| `human_review` | 0.5 - 0.7 | Ruční kontrola |
| `auto_reject` | < 0.5 | Automaticky zamítnout |
| `anomaly_veto` | - | Zamítnuto anomálií |
| `retry` | - | Opakovat analýzu |

---

## 🎯 Rozhodovací logika (Decision Tree)

```
                         ┌─────────────────────┐
                         │  Anomaly Veto?      │
                         │  (conf >= 0.85)     │
                         └──────────┬──────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │ YES                           │ NO
         ┌──────────▼──────────┐         ┌─────────▼─────────┐
         │  AUTO-REJECT        │         │ Classifier says   │
         │  (anomaly_type)     │         │ "NENÍ faktura"    │
         └─────────────────────┘         │ in reasoning?     │
                                         └─────────┬─────────┘
                                                   │
                                   ┌───────────────┴───────────────┐
                                   │ YES                           │ NO
                        ┌──────────▼──────────┐         ┌─────────▼─────────┐
                        │  AUTO-REJECT        │         │ Extractor found   │
                        │  (reasoning-based)  │         │ fields count?     │
                        └─────────────────────┘         └─────────┬─────────┘
                                                                  │
                              ┌───────────────────────────────────┼───────────────────────────────────┐
                              │                                   │                                   │
                    ┌─────────▼─────────┐              ┌─────────▼─────────┐              ┌──────────▼──────────┐
                    │  <= 1 field       │              │  2-3 fields       │              │  >= 4 fields        │
                    │  (classifier      │              │  (conflict)       │              │  (complete)         │
                    │   hallucinating)  │              │                   │              │                     │
                    └─────────┬─────────┘              └─────────┬─────────┘              └──────────┬──────────┘
                              │                                   │                                   │
                    ┌─────────▼─────────┐              ┌─────────▼─────────┐              ┌──────────▼──────────┐
                    │  AUTO-REJECT      │              │  HUMAN-REVIEW     │              │  Classifier says    │
                    │  (hallucination)  │              │  (conflict)       │              │  invoice?           │
                    └───────────────────┘              └───────────────────┘              └──────────┬──────────┘
                                                                                                      │
                                                                                      ┌───────────────┴───────────────┐
                                                                                      │ YES                           │ NO
                                                                 ┌────────────────────▼───────┐   ┌──▼──────────────────────┐
                                                                 │  All 5 elements present?   │   │  Extractor >= 4 fields  │
                                                                 │  + conf >= 0.85?           │   │  + has amount?          │
                                                                 └──────────────┬─────────────┘   └───────────┬─────────────┘
                                                                                │                             │
                                                                ┌───────────────┴───────────────┐             │
                                                                │ YES                           │ NO          │
                                                     ┌──────────▼──────────┐         ┌─────────▼─────────┐   │
                                                     │  AUTO-ACCEPT        │         │  Weighted voting  │   │
                                                     │  (high confidence)  │         │  (default path)   │   │
                                                     └─────────────────────┘         └─────────┬─────────┘   │
                                                                                               │             │
                                                                                   ┌───────────▼─────────────▼──────┐
                                                                                   │  score >= 0.7: AUTO-ACCEPT     │
                                                                                   │  score 0.5-0.7: HUMAN-REVIEW   │
                                                                                   │  score < 0.5: AUTO-REJECT      │
                                                                                   └────────────────────────────────┘
```

---

## 🚀 Instalace a spuštění

### Požadavky

- **Python:** 3.9+
- **Ollama:** Běžící lokální LLM server (port 11434)
- **Modely:** `llama3.2` (primární), `llama3.1:8b` (volitelný)

### Instalace závislostí

```bash
# Základní závislosti
pip install customtkinter Pillow psutil

# OCR engine
pip install rapidocr_onnxruntime opencv-python numpy fitz

# Ollama client
pip install ollama

# Volitelné: Spellchecker pro OCR validation
pip install pyspellchecker

# Volitelné: LangGraph pro workflow orchestration
pip install langgraph langchain-core
```

### requirements.txt

```txt
customtkinter>=5.0.0
Pillow>=9.0.0
psutil>=5.9.0
rapidocr_onnxruntime>=1.3.0
opencv-python>=4.8.0
numpy>=1.24.0
PyMuPDF>=1.23.0
ollama>=0.1.0
pyspellchecker>=0.7.0  # volitelné
langgraph>=0.0.1       # volitelné
langchain-core>=0.1.0  # volitelné
```

### Spuštění aplikace

```bash
# 1. Ujistěte se že Ollama běží
ollama serve

# 2. Stáhněte požadované modely
ollama pull llama3.2
ollama pull llama3.1:8b  # volitelné pro lepší výsledky

# 3. Spusťte GUI
python invoice_gui_v6.py
```

---

## ⚙️ Konfigurace

### Hlavní konfigurační konstanty

```python
# OCR konstanty
MIN_ZNAKU_PRO_DIGITALNI = 30      # Min. znaků pro detekci digitálního PDF
OCR_ZOOM = 2.0                    # Zoom pro OCR skenů
USE_RAPIDOCR = True               # Použít RapidOCR

# AI parametry (deterministický výstup)
AI_TEMPERATURE = 0.0              # Deterministický výstup
AI_TOP_P = 0.1                    # Omezený sampling
TEXT_MODEL = "llama3.2"           # Primární model
TEXT_MODEL_BETTER = "llama3.1:8b" # Lepší model pro složité dokumenty

# Agent thresholds
THRESHOLD_ACCEPT = 0.7            # Auto-accept hranice
THRESHOLD_REVIEW = 0.5            # Human-review hranice
ANOMALY_VETO_THRESHOLD = 0.85     # Anomaly veto hranice

# Timeout configuration
REQUEST_TIMEOUT = 120             # Base timeout
EXTRACTOR_TIMEOUT = 150           # Extra time pro extractor

# Memory management
MAX_MEMORY_PERCENT = 60           # Max paměť před GC
BATCH_SIZE = 1                    # Zpracovávat po 1 souboru
MEMORY_CHECK_EVERY = 3            # Kontrolovat každé 3 soubory
GC_EVERY = 5                      # Spustit GC každých 5 souborů

# Context window
num_ctx = 4096                    # Velikost kontextového okna
```

### VRAM a Memory Management

```python
# Nastavení VRAM limitu pro Ollama (v GUI)
vram_limit_gb = 4  # Default 4GB

# Agresivní GC po fixu memory leak
DEBUG_MEMORY = True
MAX_MEMORY_PERCENT = 60  # Snížen z 70% pro dřívější reakci
```

### Podporované formáty souborů

```python
SUPPORTED_EXTENSIONS = {
    ".pdf",      # PDF dokumenty
    ".jpg",      # JPEG obrázky
    ".jpeg",     # JPEG obrázky
    ".png",      # PNG obrázky
    ".tiff",     # TIFF obrázky
    ".bmp",      # BMP obrázky
    ".gif"       # GIF obrázky
}
```

---

## 📊 Statistiky a monitoring

### Agent Stats

```python
stats = {
    'total': 0,          # Celkem zpracováno
    'invoices': 0,       # Faktur přijato
    'non_invoices': 0,   # Ne-faktur zamítnuto
    'human_review': 0,   # Odesláno k ruční kontrole
    'errors': 0,         # Chyby
}
```

### Memory Monitoring

```python
def get_memory_usage() -> dict:
    return {
        'rss_mb': memory_info.rss / 1024 / 1024,  # Rezidentní paměť
        'vms_mb': memory_info.vms / 1024 / 1024,  # Virtuální paměť
        'percent': process.memory_percent(),       # % využití
    }
```

---

## 🔧 Troubleshooting

### Časté problémy

| Problém | Příčina | Řešení |
|---------|---------|--------|
| Ollama timeout | Příliš krátký timeout | Zvýšit `REQUEST_TIMEOUT` na 120s |
| Memory leak | Neuvolněná paměť | Snížit `MAX_MEMORY_PERCENT` na 60% |
| Hallucinace částky | AI vymýšlí hodnoty | Kontrola `classifier_hints` a regex záchrana |
| OCR selhává | Nízká kvalita skenu | Zvýšit `OCR_ZOOM` na 3.0 |
| Model nenalezen | Chybí llama3.2 | `ollama pull llama3.2` |

### Logování

```python
# Log levels
LOG_LEVEL = logging.INFO  # Změnit na DEBUG pro detailní logy

# Log file
gui_output.log  # Všechny operace jsou logovány
```

---

## 📝 Licence

Tento projekt je open-source a šířen pod licencí MIT.

---

## 👨‍💻 Autor

Vytvořeno pro efektivní třídění faktur pomocí multi-agent AI workflow.

**Verze:** 6.3  
**Datum poslední aktualizace:** 2026-02-25  
**Poslední změny:** RapidOCR integrace, OCR Validator, anti-hallucination fixes
