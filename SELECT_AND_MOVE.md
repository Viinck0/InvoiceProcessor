# 📋 Výběr a přesun faktur - Invoice Processor v6

## 🆕 Nová funkce: Selektivní přesun faktur

Místo automatického přesunu všech faktur nyní můžete **vybrat konkrétní faktury** které chcete přesunout.

---

## 🎯 Jak používat

### 1. Označení faktur

#### Jednotlivý výběr:
- **Kliknutí** na řádek v tabulce

#### Vícenásobný výběr:
- **Shift + Klik** - Vybere rozsah řádků (od posledního vybraného po aktuální)
- **Ctrl + Klik** - Přidá/odebere jednotlivé řádky z výběru

#### Hromadný výběr:
- **✅ Označit vše** - Označí všechny faktury v aktuálním filtru
- **❌ Zrušit výběr** - Zruší výběr všech faktur

### 2. Přesun označených

1. Označte faktury které chcete přesunout
2. Klikněte na **📂 Přesunout označené**
3. Potvrďte přesun
4. Hotovo!

---

## 📊 Visual indicators

### Barevné označení:
| Barva | Význam |
|-------|--------|
| 🟢 **Zelená** | Auto-Accept (jistá faktura) |
| 🟡 **Žlutá** | Human Review (nejistý případ) |
| 🔴 **Červená** | Auto-Reject (není faktura) |
| 🔵 **Modrá** | Vybráno k přesunu |

### Počítadlo:
Tlačítko "📂 Přesunout označené" zobrazuje počet vybraných faktur:
```
📂 Přesunout označené (5)
```

---

## 🔧 Technická implementace

### Nové proměnné:
```python
self.selected_invoices: set[Invoice] = set()  # Množina vybraných faktur
self._last_selected_idx = None  # Pro Shift+Click výběr
```

### Nové funkce:

#### `_on_tree_select(event)`
- Obsluha změny výběru v tabulce
- Aktualizuje `selected_invoices` podle výběru
- Mění text tlačítka s počtem vybraných

#### `_select_all_invoices()`
- Označí všechny faktury v aktuálním filtru
- Projde treeview a přidá všechny itemy s `is_invoice=True`

#### `_deselect_all_invoices()`
- Zruší celý výběr
- Vyčistí `selected_invoices`

#### `_move_selected_invoices()`
- Přesune pouze označené faktury
- Validuje že jsou vybrány nějaké faktury
- Po přesunu odznačí přesunuté faktury
- Aktualizuje zobrazení

---

## 💡 Příklady použití

### Příklad 1: Přesun konkrétních faktur
```
1. Spustit analýzu
2. V tabulce vidíte 20 faktur
3. Ctrl+Klik označte 5 faktur které chcete přesunout
4. Klikněte "📂 Přesunout označené (5)"
5. Potvrďte
```

### Příklad 2: Hromadný přesun všech faktur
```
1. Spustit analýzu
2. Klikněte "✅ Označit vše"
3. Všechny faktury jsou označeny modře
4. Klikněte "📂 Přesunout označené (20)"
5. Potvrďte
```

### Příklad 3: Výběr rozsahu
```
1. Klikněte na první fakturu v seznamu
2. Shift+Klik na desátou fakturu
3. Označí se řádky 1-10
4. Klikněte "📂 Přesunout označené (10)"
```

### Příklad 4: Filtrování a výběr
```
1. Nastavit filtr "Odesílatel obsahuje: ABC"
2. Klikněte "✅ Označit vše" (označí jen filtrované)
3. Přesunout pouze faktury od ABC s.r.o.
```

---

## ⚙️ Nastavení

### Změna chování výběru:

V `invoice_gui_v6.py`:

```python
# Povolit/zakázat Shift+Click výběr
self.tree.configure(selectmode="extended")  # Více výběrů
# nebo
self.tree.configure(selectmode="browse")    # Pouze jeden výběr
```

### Vlastní barvy:

```python
# Barva pro vybrané řádky
self.tree.tag_configure("selected", background="#007bff", foreground="white")

# Barva pro auto-accept
self.tree.tag_configure("accept", background="#28a745", foreground="white")

# Barva pro human review
self.tree.tag_configure("review", background="#ffc107", foreground="black")

# Barva pro auto-reject
self.tree.tag_configure("reject", background="#dc3545", foreground="white")
```

---

## 🐛 Řešení problémů

### "Žádné označené faktury k přesunu"
**Příčina:** Nebyly vybrány žádné faktury

**Řešení:**
1. Označte faktury kliknutím
2. Nebo použijte "✅ Označit vše"

---

### "Vybrané faktury zmizely po filtrování"
**Příčina:** Filtr změnil zobrazené položky

**Řešení:**
- Výběr je vázán na aktuální filtr
- Po změně filtru je třeba vybrat znovu
- Nebo označit vše před filtrováním

---

### "Shift+Klik nefunguje"
**Příčina:** Treeview není v "extended" režimu

**Řešení:**
```python
# Zkontrolujte v _setup_ui()
self.tree = ttk.Treeview(
    ...
    selectmode="extended"  # Musí být "extended"
)
```

---

## 📈 Statistiky

### Před implementací:
```
- Přesunout všechny faktury (žádný výběr)
- Pouze auto-accept faktury
- Žádná kontrola uživatele
```

### Po implementaci:
```
- Výběr jednotlivých faktur ✅
- Hromadný výběr Shift+Click ✅
- Označit vše / Zrušit výběr ✅
- Přesun pouze označených ✅
- Počítadlo vybraných ✅
- Barevné zvýraznění výběru ✅
```

---

## 🎯 Výhody

1. **Kontrola** - Uživatel rozhoduje které faktury přesunout
2. **Flexibilita** - Lze vybrat libovolnou podmnožinu faktur
3. **Přehlednost** - Barevné rozlišení stavů a výběru
4. **Rychlost** - Shift+Click pro rychlý výběr rozsahu
5. **Bezpečnost** - Dvojí potvrzení přesunu

---

## 🔮 Budoucí vylepšení

- [ ] Uložení výběru mezi relacemi
- [ ] Export seznamu vybraných faktur
- [ ] Hromadné akce (smazat, kopírovat, přejmenovat)
- [ ] Filtr vybraných položek
- [ ] Klávesové zkratky (Ctrl+A pro výběr všeho)

---

## Licence

MIT — volně použitelné a upravitelné.
