# 🔒 Zpřísnění klasifikace - Detekce certifikátů

## 📋 Problém

**Certifikát byl chybně klasifikován jako faktura** i když neobsahoval žádné klíčové údaje:

```json
// Classifier (příliš optimistický):
{
  "is_invoice": true,
  "confidence": 0.9,
  "elements_present": {
    "identification": true,
    "subjects": true,
    "dates": true,
    "financial": true,
    "payment_info": true
  }
}

// Extractor (realita):
{
  "invoice_number": null,           ❌ Chybí
  "vendor_name": "IBM SkillsBuild", ✓
  "customer_name": "Václav Krajkář", ✓
  "issue_date": "2026-02-14",      ✓
  "total_amount": null,             ❌ Chybí - KRITICKÉ!
  "bank_account": null,             ❌ Chybí
  "variable_symbol": null,          ❌ Chybí
  "completeness_score": 0.53
}
```

**Výsledek:** Certifikát s pouze jménem a datem byl označen jako faktura!

---

## ✅ Provedená zpřísnění

### 1. Consensus Engine - Kritické chybějící pole

**Soubor:** `consensus_engine.py`

**Změna:** Přísnější penalizace za chybějící klíčová data

```python
# CRITICAL: If amount is missing, this is likely NOT an invoice
if not has_amount:
    logger.debug("  ⚠️ Chybí částka - pravděpodobně není faktura")
    penalty += 0.5  # Major penalty

# If invoice number is missing, suspicious
if not extractor_result.get('invoice_number'):
    penalty += 0.15

# If no bank account or payment info, suspicious  
if not extractor_result.get('bank_account') and not extractor_result.get('variable_symbol'):
    penalty += 0.1
```

**Efekt:**
- Chybějící částka → **-50% skóre**
- Chybějící číslo faktury → **-15% skóre**
- Chybějící platební údaje → **-10% skóre**
- Nízká completeness → **-15% skóre**

**Příklad:**
```
Původní score: 0.70
- Chybí částka:    0.70 * (1 - 0.50) = 0.35
- Chybí invoice #: 0.35 * (1 - 0.15) = 0.30
- Chybí payment:   0.30 * (1 - 0.10) = 0.27
- Low complete:    0.27 * (1 - 0.15) = 0.23

Výsledek: 0.23 → AUTO-REJECT ❌
```

---

### 2. Classifier Prompt - Důraz na částku

**Soubor:** `classifier_agent.py`

**Změny:**
1. Přidán důraz na ČÁSTKU jako kritický element
2. Přidány sekce "CO NENÍ FAKTURA" a "ROZPOZNÁVÁNÍ NE-FAKTUR"
3. Zřetelnější instrukce pro certifikáty

```python
=== DŮLEŽITÉ PRAVIDLO ===
Pokud dokument NEMÁ všechny elementy, NENÍ to faktura!

=== CO NENÍ FAKTURA ===
- Certifikáty, osvědčení, licence - obsahují jméno, kurz, datum absolvování
- Životopisy - obsahují vzdělání, praxe, dovednosti
...

=== ROZPOZNÁVÁNÍ NE-FAKTUR ===
Pokud text obsahuje tyto vzorce, pravděpodobně NENÍ faktura:
- "certifikát", "osvědčení", "licence", "absolvoval", "kurz", "školení"
- "životopis", "CV", "curriculum", "vzdělání", "praxe"
...

=== PRAVIDLA ===
- Pokud chybí ANY z 5 elementů → NENÍ faktura
- Pokud neobsahuje ČÁSTKU (číslo) → NENÍ faktura  ← NOVÉ!
- Pokud obsahuje "certifikát", "osvědčení", "absolvoval" → NENÍ faktura ← NOVÉ!
```

---

### 3. Anomaly Detector - Detekce vzdělávacích materiálů

**Soubor:** `anomaly_agent.py`

**Změny:**
1. Přidán nový typ anomálie: **VZDĚLÁVACÍ MATERIÁL**
2. Specifická keyword pro certifikáty a kurzy
3. Nižší threshold pro certifikáty (stačí 1 keyword)

```python
=== TYPY ANOMÁLIÍ ===
...
9. VZDĚLÁVACÍ MATERIÁL: obsahuje kurz, modul, kapitola, lekce, IBM SkillsBuild

=== PRAVIDLA ===
...
- CERTIFIKÁT s "absolvoval", "kurz", "IBM SkillsBuild" → 1 keyword stačí ← NOVÉ!
```

**Nová keyword:**
```json
"education": {
    "keywords": [
        "ibm skillsbuild",
        "skillsbuild",
        "modul",
        "kapitola",
        "lekce",
        "studijní materiál",
        "vzdělávací obsah",
        "e-learning",
        "online kurz"
    ],
    "threshold": 1,  // Stačí 1 keyword!
    "veto": true     // Okamžité zamítnutí
}
```

---

### 4. Anomaly Patterns - Rozšířená detekce

**Soubor:** `agent_workflow/anomaly_patterns.json`

**Změny:**
- Přidán typ `education` s vlastními keyword
- Rozšířeny keyword pro `certificate`
- Všechny vzdělávací patterny mají `veto: true`

---

## 📊 Očekávané výsledky

### Před zpřísněním:

```
Certifikát IBM SkillsBuild:
- Classifier: is_invoice=true (90%)
- Extractor: completeness=53%, amount=null
- Anomaly: is_anomaly=false
- Consensus: 0.70 * (1 - 0.05) = 0.67 → HUMAN REVIEW ⚠️
```

### Po zpřísnění:

```
Certifikát IBM SkillsBuild:
- Classifier: is_invoice=false (85%) ← Lepší prompt
- Extractor: completeness=53%, amount=null
- Anomaly: is_anomaly=true (education, 95%) [VETO] ← Nová detekce
- Consensus: ANOMALY VETO → AUTO-REJECT ❌
```

**Nebo pokud Classifier selže:**
```
- Classifier: is_invoice=true (90%) ← Stále optimistický
- Extractor: completeness=53%, amount=null
- Anomaly: is_anomaly=true (education, 95%) [VETO]
- Consensus: 
    base_score = 0.90*0.4 + 0.53*0.3 + 0.05*0.3 = 0.54
    - Chybí částka: 0.54 * 0.50 = 0.27
    - Chybí invoice#: 0.27 * 0.85 = 0.23
    - Chybí payment: 0.23 * 0.90 = 0.21
    Výsledek: 0.21 → AUTO-REJECT ❌
```

---

## 🎯 Testovací scénáře

### Scénář 1: Certifikát s "absolvoval"
```
Text: "Václav Krajkář úspěšně absolvoval kurz IBM SkillsBuild..."

Očekávaný výsledek:
- Anomaly Detector: is_anomaly=true (education)
- Rozhodnutí: AUTO-REJECT ❌
```

### Scénář 2: Životopis
```
Text: "ŽIVOTOPIS - Václav Krajkář, vzdělání, praxe..."

Očekávaný výsledek:
- Anomaly Detector: is_anomaly=true (cv_resume)
- Rozhodnutí: AUTO-REJECT ❌
```

### Scénář 3: Skutečná faktura
```
Text: "FAKTURA č. 2024001, ABC s.r.o., částka 1500 Kč, účet 123456789/0100"

Očekávaný výsledek:
- Classifier: is_invoice=true
- Extractor: completeness > 0.8, amount=1500
- Anomaly: is_anomaly=false
- Rozhodnutí: AUTO-ACCEPT ✅
```

### Scénář 4: Dokument bez částky
```
Text: "Smlouva mezi ABC a XYZ, datum 2024-01-15"

Očekávaný výsledek:
- Classifier: is_invoice=false (chybí částka)
- Extractor: amount=null
- Consensus: score < 0.5 → AUTO-REJECT ❌
```

---

## 📝 Změněné soubory

| Soubor | Změna | Dopad |
|--------|-------|-------|
| `consensus_engine.py` | Přísnější penalizace | -50% za chybějící částku |
| `classifier_agent.py` | Lepší prompt s příklady | Lepší detekce ne-faktur |
| `anomaly_agent.py` | Nový typ "education" | Detekce certifikátů |
| `anomaly_patterns.json` | Rozšířená keyword | Lepší rule-based detekce |

---

## 🔍 Jak poznat že funguje

### Správně zamítnuto (certifikát):
```
11:45:22 [INFO] HTTP Request: POST ... "HTTP/1.1 200 OK"
11:45:22 [DEBUG] ✓ Anomalie detekována pravidly: education
11:45:22 [INFO] ✓ Certifikat.pdf: NENÍ FAKTURA (95%, 2.1s)
```

### Správně přijato (faktura):
```
11:45:25 [INFO] HTTP Request: POST ... "HTTP/1.1 200 OK"
11:45:25 [DEBUG] ✓ Klasifikace: Faktura (jistota: 95%)
11:45:25 [DEBUG] ✓ Extrakce: completeness=95%
11:45:25 [DEBUG] ✓ Anomalie: Žádné anomálie
11:45:25 [INFO] ✓ Faktura_2024.pdf: FAKTURA (92%, 5.4s)
```

### Zamítnuto kvůli chybějícím údajům:
```
11:45:30 [DEBUG] ⚠️ Chybí částka - pravděpodobně není faktura
11:45:30 [DEBUG] ⚠️ Chybí číslo faktury
11:45:30 [DEBUG] ⚠️ Chybí platební údaje
11:45:30 [INFO] ✓ Neznamy_dokument.pdf: NENÍ FAKTURA (23%, 3.2s)
```

---

## ✅ Shrnutí

**Cíl:** Zabránit chybné klasifikaci certifikátů a dokumentů bez klíčových údajů

**Prostředky:**
1. ✅ Přísnější penalizace v Consensus Engine
2. ✅ Lepší Classifier prompt s důrazem na částku
3. ✅ Nový typ anomálie "education" pro certifikáty
4. ✅ Rozšířená keyword pro rule-based detekci

**Výsledek:**
- Certifikáty → **AUTO-REJECT** ❌
- Dokumenty bez částky → **AUTO-REJECT** ❌
- Skutečné faktury → **AUTO-ACCEPT** ✅
- Hranické případy → **HUMAN REVIEW** ⚠️
