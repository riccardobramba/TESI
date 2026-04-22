# Dashboard Monitoraggio Rete Nodi e Sensori

Dashboard web interattiva sviluppata con **Python + Dash** per monitorare una rete di nodi/sensori con:

- mappa geospaziale della rete;
- stato server per nodo;
- statistiche e grafici trend;
- dettaglio nodi/archi;
- log eventi e allarmi smart.

## Stato attuale (feature principali)

### Mappa rete

- Colore nodo = stato server:
  - **Verde** `< 5 min`
  - **Giallo** `5-30 min`
  - **Rosso** `> 30 min`
- Opacita nodo = livello batteria.
- Sigla ruolo dentro al nodo:
  - `BR` = Border Router
  - `R` = Router
  - `C` = Child
- Supporto selezione:
  - click singolo nodo;
  - lasso multi-nodo;
  - lasso su arco (coppia nodi).

### Ruoli

- Canonical interni: `router`, `parent`, `child`.
- Label UI:
  - `router` -> **Border Router**
  - `parent` -> **Router**
  - `child` -> **Child**
- Regola topologica: un nodo "parent" e valido solo se ha almeno un child collegato.
- Garanzia di connettivita minima: nessun nodo resta isolato nella topologia mostrata.

### Generale / Trend

- Card statistiche cliccabili:
  - `Temperatura`, `Umidita`, `Batteria`;
  - click = cambia metrica e apre il modale grafico relativo.
- Selezione metrica anche da dropdown (clickabile normalmente).
- Titoli gruppo mostrano i nomi nodi selezionati (non solo il numero).

### Modale grafico

- Solo **Visualizzazione Giornaliera** (rimossa la modalita "Completa/Scorrevole").
- Filtro su data + fascia oraria con step **30 minuti**.
- Se in fascia non ci sono dati: grafico vuoto (nessun fallback fuori fascia).
- Pulsanti rapidi nel titolo modale per passare alle altre 2 metriche senza chiudere.

### Settings

- Polling configurabile.
- Downsampling globale grafici:
  - 15 min, 30 min, 1h, 2h, 4h, raw.
- Log eventi in dashboard:
  - ordine dal piu recente al meno recente;
  - include riepilogo "Profilo stato server" per ruolo.

### Allarmi

- Colonna `Dettaglio` tradotta in italiano.

## Struttura file

- `index.py`  
  Entry-point applicazione (inizializzazione DB, layout, callback, avvio server Dash).

- `app.py`  
  Istanza Dash/Flask e configurazioni base app.

- `layout.py`  
  Definizione componenti UI (tabs, card, mappa, modale, settings, log).

- `callbacks.py`  
  Logica interattiva Dash (selezioni, refresh, grafici, modale, log, salvataggi setting).

- `queries.py`  
  Data layer e regole dominio: query SQL, normalizzazione ruoli, topologia, statistiche, health, fallback.

- `database.py`  
  Utility setup/reset DB.

- `generate_test_data.py`  
  Generazione dataset test.

- `simulate_readings.py`  
  Simulazione letture/eventi in tempo quasi reale.

- `data.db`  
  Database SQLite operativo.

- `dashboard.log`  
  File log applicativo mostrato anche nella tab Settings.

- `requirements.txt`  
  Dipendenze Python.

## Requisiti

- Python 3.8+ (consigliato 3.10+)
- `pip`
- (opzionale) `git`

## Installazione

```bash
git clone <URL_REPOSITORY>
cd <CARTELLA_PROGETTO>
python -m venv .venv
```

### Attivazione virtual environment

- Windows:

```bash
.venv\Scripts\activate
```

- macOS/Linux:

```bash
source .venv/bin/activate
```

### Installazione dipendenze

```bash
pip install -r requirements.txt
```

## Avvio

```bash
python index.py
```

Poi apri il browser su:

[http://127.0.0.1:8050/](http://127.0.0.1:8050/)

## Modalita con simulazione dati

Terminale 1:

```bash
python index.py
```

Terminale 2:

```bash
python simulate_readings.py
```

## Reset / rigenerazione dati

Reset schema/database:

```bash
python database.py
```

Generazione dati test:

```bash
python generate_test_data.py --output data.db --overwrite
```

## Note operative utili

- Se il grafico modale mostra "Nessun dato nella fascia oraria selezionata", e corretto: significa che nella finestra scelta non ci sono campioni.
- Il log "Profilo stato server" e allineato alla logica colore mappa.
- Le regole ruolo/topologia sono centralizzate in `queries.py`.
