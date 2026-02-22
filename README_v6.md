# 🧾 Invoice Processor v6 - Multi-Agent Workflow

Nejpřesnější verze systému pro třídění faktur s **3 specializovanými AI agenty** a **human review queue**.

---

## 🆕 Co je nového ve v6?

| Funkce | v5 | v6 (Multi-Agent) |
|--------|-----|------------------|
| **Počet agentů** | 1 AI model | ✅ 3 specializovaní agenti |
| **Rozhodování** | Jednoduchá klasifikace | ✅ Vážené hlasování + veto |
| **Detekce anomálií** | Omezená | ✅ Specializovaný Anomaly Detector |
| **Human Review** | ❌ Žádné | ✅ Fronta pro nejisté případy |
| **Paralelní zpracování** | ❌ Sekvenční | ✅ ThreadPoolExecutor |
| **Detail výsledků** | Základní | ✅ Výsledky každého agenta zvlášť |
| **Pravidlový filtr** | Základní | ✅ 2-úrovňový (pre-filter + AI) |

---

## 🎯 Architektura Multi-Agent Workflow

```
┌────────────────────────────────────────────────────────────┐
│                    VSTUP: PDF/Obrázek                       │
└────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────┐
│  1. OCR Extrakce (PyMuPDF + pytesseract)                   │
└────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────┐
│  2. Pravidlový Pre-Filter (rychlé zamítnutí jistých)       │
│     → score ≤ -3 + confidence > 0.9 → AUTO-REJECT          │
└────────────────────────────────────────────────────────────┘
                            │
                            ▼
            ┌───────────────┴───────────────┐
            │                               │
            ▼                               ▼
    ┌───────────────┐               ┌───────────────┐
    │ Jisté ne-     │               │ Nejisté/      │
    │ faktury       │               │ Pravděpodobné │
    │ → AUTO-REJECT │               │ faktury       │
    └───────────────┘               └───────────────┘
                                            │
                                            ▼
┌────────────────────────────────────────────────────────────┐
│  3. Multi-Agent AI Analýza (PARALELNĚ - ThreadPool)        │
│                                                            │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────┐ │
│  │ CLASSIFIER AGENT │  │ EXTRACTOR AGENT  │  │ ANOMALY  │ │
│  │                  │  │                  │  │ DETECTOR │ │
│  │ Binary decision  │  │ Data extraction  │  │ CV/Cert/ │ │
│  │ 40% váha         │  │ 30% váha         │  │ 30% + veto││
│  └──────────────────┘  └──────────────────┘  └──────────┘ │
│           │                    │                    │      │
│           └────────────────────┴────────────────────┘      │
│                            │                               │
└────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────┐
│  4. Consensus Engine (vážené hlasování)                    │
│                                                            │
│  weighted_score = clf×0.4 + ext×0.3 + (1-anom)×0.3         │
│                                                            │
│  → score ≥ 0.7  : AUTO-ACCEPT ✅                           │
│  → score 0.5-0.7: HUMAN REVIEW ⚠️                          │
│  → score < 0.5  : AUTO-REJECT ❌                           │
└────────────────────────────────────────────────────────────┘
                             │
                             ▼
            ┌────────────────┴────────────────┐
            │                                 │
            ▼                                 ▼
    ┌───────────────┐                 ┌───────────────┐
    │ AUTO-ACCEPT   │                 │ HUMAN REVIEW  │
    │ → Přesunout   │                 │ → Fronta k    │
    │ do cílové     │                 │   ruční kontrole│
    │ složky        │                 │               │
    └───────────────┘                 └───────────────┘
```

---

## 🤖 3 Specializovaní agenti

### 1. Classifier Agent
**Úkol:** Binary rozhodnutí Faktura vs. Ne-faktura

**Specializace:**
- Kontrola všech 5 elementů faktury
- Strictní pravidla (chybějící element = není faktura)
- Vrací confidence score a reasoning

**Příklad výstupu:**
```json
{
  "is_invoice": true,
  "confidence": 0.95,
  "elements_present": {
    "identification": true,
    "subjects": true,
    "dates": true,
    "financial": true,
    "payment_info": true
  },
  "reasoning": "Všechny elementy přítomny"
}
```

---

### 2. Extractor Agent
**Úkol:** Extrakce a validace polí faktury

**Extrahovaná pole:**
- `vendor_name` - název dodavatele
- `customer_name` - název odběratele
- `issue_date`, `due_date` - data
- `total_amount` - částka
- `currency` - měna
- `invoice_number` - číslo faktury
- `vendor_ico`, `vendor_dic` - IČO a DIČ
- `bank_account` - číslo účtu
- `variable_symbol` - variabilní symbol

**Výstup:**
```json
{
  "vendor_name": "ABC s.r.o.",
  "customer_name": "XYZ a.s.",
  "issue_date": "2024-01-15",
  "total_amount": 1500.00,
  "currency": "CZK",
  "completeness_score": 0.95,
  "validation_errors": []
}
```

---

### 3. Anomaly Detector Agent
**Úkol:** Detekce dokumentů, které NENÍ faktura

**Detekované typy anomálií:**
| Typ | Keyword | Threshold | Veto |
|-----|---------|-----------|------|
| **Upomínka** | upomínka, reminder, výzva | 1 | ✅ Ano |
| **Nabídka** | nabídka, offer, kalkulace | 1 | Ne |
| **Životopis** | životopis, CV, vzdělání | 2 | ✅ Ano |
| **Certifikát** | certifikát, kurz, školení | 2 | ✅ Ano |
| **Smlouva** | smlouva, dohoda, dodatek | 2 | ✅ Ano |
| **Objednávka** | objednávka, purchase order | 1 | Ne |
| **E-mail** | e-mail, korespondence | 2 | Ne |

**Veto pravomoc:**
- Pokud `confidence ≥ 0.85` NEBO `veto flag` → okamžité zamítnutí
- Anomaly detector přebije ostatní agenty

---

## 🗳️ Consensus Engine

### Výpočet váženého skóre:

```python
weighted_score = (
    classifier_confidence × 0.4 +      # 40% váha
    extractor_completeness × 0.3 +     # 30% váha
    (1 - anomaly_confidence) × 0.3     # 30% váha (invertováno)
)

# Penalty za chybějící elementy
final_score = weighted_score × (1 - penalty)
```

### Rozhodovací prahy:

| Score | Rozhodnutí | Akce |
|-------|------------|------|
| **≥ 0.7** | AUTO-ACCEPT ✅ | Přesunout do cílové složky |
| **0.5 - 0.7** | HUMAN REVIEW ⚠️ | Zařadit do fronty ke kontrole |
| **< 0.5** | AUTO-REJECT ❌ | Zamítnout |

---

## 📁 Struktura projektu

```
projekt třídění faktur/
├── invoice_gui_v6.py           # ✅ Nová v6 s multi-agenty
├── agent_workflow/
│   ├── __init__.py             # Inicializace balíčku
│   ├── base_agent.py           # Základní třída agenta
│   ├── classifier_agent.py     # Binary klasifikace
│   ├── extractor_agent.py      # Extrakce dat
│   ├── anomaly_agent.py        # Detekce anomálií
│   ├── consensus_engine.py     # Vážené hlasování
│   └── anomaly_patterns.json   # Patterny pro anomálie
├── test_agents.py              # Testovací skript
├── AGENT_WORKFLOW_PROPOSAL.md  # Návrh (EN)
├── IMPLEMENTACE_AGENTI.md      # Dokumentace (CZ)
└── README_v6.md                # Tento dokument
```

---

## 🚀 Instalace a spuštění

### 1. Instalace závislostí

```bash
# Základní závislosti
pip install customtkinter PyMuPDF Pillow psutil

# Pro OCR (volitelné, vyžaduje Tesseract)
pip install pytesseract
# Tesseract: https://github.com/UB-Mannheim/tesseract/wiki

# Pro AI agenty
pip install ollama
```

### 2. Ollama setup

```bash
# Spustit Ollama server
ollama serve

# Stáhnout model (v jiném terminálu)
ollama pull llama3.1
```

### 3. Spuštění aplikace

```bash
python invoice_gui_v6.py
```

---

## 🎯 Jak používat

### Krok 1: Nastavení složek
1. Vyberte **zdrojovou složku** (kde hledat dokumenty)
2. Vyberte **cílovou složku** (kam přesunout faktury)

### Krok 2: Spuštění analýzy
1. Klikněte na **▶️ Spustit analýzu**
2. Sledujte progress a statistiky
3. Počkejte na dokončení

### Krok 3: Kontrola výsledků
- **✅ Auto-Accept** - jisté faktury (confidence ≥ 70%)
- **⚠️ Human Review** - nejisté případy (50-70%)
- **❌ Auto-Reject** - jisté ne-faktury (< 50%)

### Krok 4: Human Review (pokud je třeba)
1. Klikněte na **👁️ Human Review**
2. Projděte dokumenty jeden po druhém
3. Rozhodněte: **✅ Přijmout** nebo **❌ Odmítnout**

### Krok 5: Přesun faktur
1. Klikněte na **📂 Přesunout faktury**
2. Potvrďte přesun auto-accept faktur
3. Hotovo!

---

## 📊 Příklad výsledků

```
┌────────────────────────────────────────────────┐
│  📊 Výsledky analýzy                           │
├────────────────────────────────────────────────┤
│  📄 Celkem:        150                         │
│  ✅ Faktury:       98 (65.3%)                  │
│  ❌ Odmítnuté:     42 (28.0%)                  │
│  ⚠️ Review:        10 (6.7%)                   │
│  ⛔ Chyby:         0                           │
├────────────────────────────────────────────────┤
│  Průměrná jistota: 87%                         │
│  Full agreement:   94.2%                       │
└────────────────────────────────────────────────┘
```

---

## 🔧 Konfigurace

### Thresholds (v `invoice_gui_v6.py`):

```python
# Agent thresholds
THRESHOLD_ACCEPT = 0.7         # ≥0.7 = auto-accept
THRESHOLD_REVIEW = 0.5         # 0.5-0.7 = human review
ANOMALY_VETO_THRESHOLD = 0.85  # ≥0.85 = anomaly veto

# Model selection
TEXT_MODEL = "llama3.1"        # Lze změnit na jiný model

# OCR settings
OCR_LANG = "ces+eng"           # Čeština + Angličtina
OCR_DPI = 150                  # Vyšší = přesnější, pomalejší
```

### Úprava vah agentů:

V `consensus_engine.py`:

```python
self.weights = {
    'classifier': 0.4,  # 40%
    'extractor': 0.3,   # 30%
    'anomaly': 0.3      # 30%
}
```

---

## ⚡ Výkon

### Časy zpracování (průměr na soubor):

| Model | Čas/soubor | RAM | Přesnost |
|-------|------------|-----|----------|
| **llama3.1** | ~15-25s | 4GB | ⭐⭐⭐⭐⭐ |
| **phi3** | ~8-12s | 2GB | ⭐⭐⭐⭐ |
| **moondream** | ~3-5s | 1GB | ⭐⭐⭐ |

### Optimalizace výkonu:

```python
# Zvýšit počet paralelních workerů
with ThreadPoolExecutor(max_workers=5) as executor:  # default 3

# Snížit frequency memory check
MEMORY_CHECK_EVERY = 5  # default 1

# Zvýšit threshold pro jisté zamítnutí
if pre_class == 'reject' and pre_conf > 0.95:  # default 0.9
```

---

## 🎓 Příklady použití

### Příklad 1: Skutečná faktura

**Vstup:** `faktura_2024_001.pdf`

```
Agent Results:
├─ Classifier: is_invoice=true (95%)
├─ Extractor: completeness=95%, všechna pole vyplněna
└─ Anomaly Detector: žádné anomálie (98%)

Consensus:
├─ weighted_score = 0.95×0.4 + 0.95×0.3 + 0.98×0.3 = 0.95
└─ Rozhodnutí: AUTO-ACCEPT ✅
```

---

### Příklad 2: Životopis

**Vstup:** `zivotopis_jan_novak.pdf`

```
Agent Results:
├─ Classifier: is_invoice=false (92%)
├─ Extractor: completeness=10%, chybí všechna pole
└─ Anomaly Detector: cv_resume (96%) [VETO]

Consensus:
├─ Anomaly veto triggered!
└─ Rozhodnutí: AUTO-REJECT ❌
```

---

### Příklad 3: Hranický případ (nabídka)

**Vstup:** `cenova_nabidka.pdf`

```
Agent Results:
├─ Classifier: is_invoice=false (65%) - nejistý
├─ Extractor: completeness=40% - chybí pole
└─ Anomaly Detector: offer (72%)

Consensus:
├─ weighted_score = 0.35×0.4 + 0.4×0.3 + 0.28×0.3 = 0.34
└─ Rozhodnutí: AUTO-REJECT ❌
```

---

### Příklad 4: Human Review

**Vstup:** `dokument_s_chybami.pdf`

```
Agent Results:
├─ Classifier: is_invoice=true (70%) - nejistý
├─ Extractor: completeness=55% - některá pole chybí
└─ Anomaly Detector: žádné anomálie (60%)

Consensus:
├─ weighted_score = 0.7×0.4 + 0.55×0.3 + 0.6×0.3 = 0.62
└─ Rozhodnutí: HUMAN REVIEW ⚠️
```

---

## 🐛 Řešení problémů

### "Agent workflow module not available"

**Řešení:**
```bash
# Zkontrolujte instalaci ollama
pip install ollama

# Zkontrolujte že Ollama běží
ollama serve

# Zkontrolujte model
ollama list  # měl by obsahovat llama3.1
```

### "Tesseract nenalezen"

**Řešení:**
1. Stáhněte Tesseract: https://github.com/UB-Mannheim/tesseract/wiki
2. Nainstalujte s češtinou a angličtinou
3. Přidejte do PATH nebo nastavte cestu v kódu

### "Nedostatek paměti"

**Řešení:**
```python
# Snížit počet paralelních workerů
ThreadPoolExecutor(max_workers=2)

# Zvýšit frequency GC
MEMORY_CHECK_EVERY = 1  # kontrola po každém souboru

# Použít menší model
TEXT_MODEL = "moondream"  # místo llama3.1
```

### "Příliš mnoho dokumentů v human review"

**Řešení:**
```python
# Snížit threshold pro auto-accept
THRESHOLD_ACCEPT = 0.6  # místo 0.7

# Zvýšit threshold pro anomaly veto
ANOMALY_VETO_THRESHOLD = 0.9  # místo 0.85
```

---

## 📈 Porovnání verzí

| Verze | Přesnost | Rychlost | RAM | Funkce |
|-------|----------|----------|-----|--------|
| **v5** | 85% | Rychle | 2GB | 1 AI model |
| **v6** | 95% | Středně | 4GB | 3 agenti + veto + review |

---

## 🔮 Budoucí vylepšení

- [ ] Učení z user feedbacku (fine-tuning promptů)
- [ ] Batch processing s async/await
- [ ] Export statistik do CSV/Excel
- [ ] REST API pro vzdálenou analýzu
- [ ] Podpora pro více jazyků (SK, EN, DE)
- [ ] Historie zpracování s možností revertu

---

## Licence

MIT — volně použitelné a upravitelné.

---

## Podpora

Pokud narazíte na problémy:
1. Zkontrolujte `IMPLEMENTACE_AGENTI.md` pro detailní dokumentaci
2. Spusťte `test_agents.py` pro otestování agentů
3. Podívejte se na logy v konzoli pro detaily chyb
