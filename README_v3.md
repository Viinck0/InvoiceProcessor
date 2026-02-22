# 🧾 Invoice Processor v3 - AI Agent s přímým přístupem

Nová generace nástroje pro třídění faktur s **AI Agentem**, který má přímý přístup k souborům a může provádět detailní vícekrokovou analýzu.

---

## 🆕 Co je nového ve v3?

| Funkce | v2 (Vision) | v3 (AI Agent) |
|--------|-------------|---------------|
| **Přístup k souborům** | Nepřímý (obrázek) | ✅ Přímý (otevírá soubory) |
| **Počet stránek PDF** | 1 stránka | ✅ Až 3 stránky |
| **Analýza** | Jednoduchý prompt | ✅ 3-kroková analýza |
| **Výstup** | JSON | ✅ JSON + Analysis Log |
| **Rozhodnutí** | Je/není faktura | ✅ I s vysvětlením |
| **Paměť** | ~2-4GB | ~3-6GB |

---

## 🎯 Jak AI Agent pracuje

### Krok 1: Celkový přehled
```
- Kolik stránek dokument má?
- Jaký je typ dokumentu?
- Obsahuje hlavičku "FAKTURA"?
```

### Krok 2: Detailní analýza
```
- Dodavatel
- Odběratel
- Číslo faktury
- Datum vystavení/splatnosti
- Částka a měna
```

### Krok 3: Rozhodnutí
```
- Je to skutečná faktura? (true/false)
- Jaká je jistota? (0.0-1.0)
- Důvod rozhodnutí
- Analysis Log (co AI viděla)
```

---

## 📋 Předpoklady

| Požadavek | Verze | Poznámka |
|-----------|-------|----------|
| Python | ≥ 3.11 | |
| [Ollama](https://ollama.com) | nejnovější | |
| Vision model | `llama3.2-vision`, `llava`, `phi3-vision` | |
| RAM | 8GB+ | Doporučeno 16GB |

---

## 🚀 Instalace a spuštění

### 1. Instalace závislostí

```bash
pip install -r requirements.txt
```

### 2. Stažení modelu

```bash
# Doporučený model
ollama pull llama3.2-vision

# Nebo alternativy
ollama pull phi3-vision   # Rychlejší
ollama pull llava         # Přesnější
```

### 3. Spuštění GUI

```bash
python invoice_gui_v3.py
```

---

## 📊 Příklad výstupu AI Agenta

### Pro fakturu:
```json
{
  "step1_overview": {
    "page_count": 2,
    "document_type": "faktura",
    "has_invoice_header": true
  },
  "step2_details": {
    "sender_name": "ABC s.r.o.",
    "recipient_name": "XYZ a.s.",
    "invoice_number": "2024001",
    "issue_date": "2024-01-15",
    "due_date": "2024-02-15",
    "total_amount": "1500",
    "currency": "CZK"
  },
  "step3_decision": {
    "is_invoice": true,
    "confidence": 0.95,
    "reason": "Dokument obsahuje všechny náležitosti faktury",
    "analysis_log": [
      "Vidím hlavičku FAKTURA na stránce 1",
      "Našel jsem údaje dodavatele: ABC s.r.o.",
      "Částka 1500 Kč je uvedena na stránce 2"
    ]
  }
}
```

### Pro ne-fakturu:
```json
{
  "step1_overview": {
    "page_count": 1,
    "document_type": "poznámka",
    "has_invoice_header": false
  },
  "step2_details": {...prázdné...},
  "step3_decision": {
    "is_invoice": false,
    "confidence": 0.9,
    "reason": "Dokument obsahuje pouze datum a jméno",
    "analysis_log": [
      "Vidím pouze jméno a datum",
      "Chybí hlavička FAKTURA",
      "Žádná částka ani údaje o firmě"
    ]
  }
}
```

---

## ⚙️ Konfigurace

### Úprava v `invoice_gui_v3.py`

```python
# Kolik stránek PDF analyzovat
MAX_PDF_PAGES = 3  # Více stránek = lepší analýza, více paměti

# Rozlišení obrázků
MAX_IMAGE_SIZE = 512  # Nižší = méně paměti, ale horší kvalita

# Dávky pro pauzy
BATCH_SIZE = 2  # Pauza po každých 2 souborech

# Timeout
REQUEST_TIMEOUT = 180  # 3 minuty na soubor
```

---

## 🔍 Rozdíly mezi modely

| Model | Velikost | RAM | Čas/soubor | Přesnost |
|-------|----------|-----|------------|----------|
| `moondream` | 0.8GB | 2GB | ~5s | ⭐⭐⭐ |
| `phi3-vision` | 2.1GB | 4GB | ~10s | ⭐⭐⭐⭐ |
| `bakllava` | 2.1GB | 4GB | ~12s | ⭐⭐⭐⭐ |
| `llama3.2-vision` | 3.5GB | 8GB | ~20s | ⭐⭐⭐⭐⭐ |
| `llava` | 3.8GB | 8GB | ~25s | ⭐⭐⭐⭐⭐ |

---

## 📈 Monitoring paměti

### Během analýzy uvidíte:

```
💾 Počáteční paměť: 245.3MB
🔍 AI Agent analyzuje: faktura_001.pdf (245.3 KB)
  📄 PDF má 2 stránek, analyzuji prvních 2
  🖼️ Připraveno 2 obrázků k analýze
  ✓ faktura_001.pdf: Faktura (2 stran) od ABC s.r.o. (95%)
  📝 Vidím hlavičku FAKTURA na stránce 1 | Našel jsem údaje dodavatele...
⏸ Pauza po 2 souborech pro uvolnění paměti...
💾 Paměť po pauze: 312.5MB
```

---

## ⚠️ Řešení problémů

### "memory layout cannot be allocated"

**Řešení:**
```bash
# 1. Stáhnout menší model
ollama pull moondream

# 2. Snížit rozlišení v kódu
MAX_IMAGE_SIZE = 384

# 3. Snížit počet stránek
MAX_PDF_PAGES = 1

# 4. Nebo použít emergency mode
python invoice_gui_emergency.py
```

### "Analýza trvá příliš dlouho"

**Řešení:**
- Použít rychlejší model: `phi3-vision` místo `llama3.2-vision`
- Snížit `MAX_PDF_PAGES = 1`
- Snížit `BATCH_SIZE = 1`

### "Nenalezeny žádné faktury"

AI Agent může špatně klasifikovat. Zkuste:
- Jiný model (`llava` je přesnější)
- Upravit prompt v `AIVisionAgent.ANALYSIS_PROMPT`

---

## 🎯 Kdy použít kterou verzi

| Situace | Doporučená verze |
|---------|------------------|
| **Mám 4GB RAM** | v2 + emergency mode |
| **Mám 8GB RAM** | v2 nebo v3 s `moondream` |
| **Mám 16GB+ RAM** | ✅ v3 s `llama3.2-vision` |
| **Potřebuji rychlost** | v2 + `bakllava` |
| **Potřebuji přesnost** | ✅ v3 + `llava` |
| **Složité vícestránkové faktury** | ✅ v3 (analyzuje více stránek) |
| **Jen jednoduché faktury** | v2 (stačí 1 stránka) |

---

## 📁 Struktura projektu

```
projekt třídění faktur/
├── invoice_gui_v3.py          # ✅ Nová v3 s AI Agentem
├── invoice_gui_v2.py          # v2 (jednodušší, méně paměti)
├── invoice_gui_emergency.py   # Emergency mode pro malou RAM
├── invoice_processor_v3.py    # CLI verze v3 (připravováno)
├── invoice_processor_v2.py    # CLI verze v2
├── invoice_processor.py       # Legacy v1 (textová analýza)
├── requirements.txt           # Závislosti
├── README_v3.md               # Tento dokument
├── README_v2.md               # Dokumentace v2
└── DEBUG_MEMORY.md            # Řešení problémů s pamětí
```

---

## 🔮 Budoucí vylepšení

- [ ] Podpora pro všechny stránky PDF (nejen prvních N)
- [ ] Zoom na konkrétní oblasti (AI si přiblíží detaily)
- [ ] Interaktivní analýza (AI se ptá na nejasnosti)
- [ ] Učení z uživatelových oprav
- [ ] Export do CSV/Excel s analysis logem

---

## Licence

MIT — volně použitelné a upravitelné.
