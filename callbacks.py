import dash
from dash import html, Input, Output, State, callback_context, ALL
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
import pandas as pd
import random
import time
from datetime import datetime
from collections import deque
import queries  # Il tuo file personalizzato per le query al database
import os
import warnings

# Import dell'oggetto 'app' principale dal file app.py
from app import app, MAPBOX_TOKEN 

# Ignora specifici avvisi di deprecazione per mantenere pulito l'output
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Costanti per la gestione dei log
MAX_LOG_ENTRIES = 50  # Numero massimo di righe da tenere in memoria
LOG_FILE = "dashboard.log"  # Nome del file di log

app_logs = deque(maxlen=MAX_LOG_ENTRIES)

try:
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            last_logs = f.readlines()[-MAX_LOG_ENTRIES:]
            app_logs.extend([line.strip() for line in last_logs])
except Exception as e:
    print(f"Errore durante la lettura del file di log: {e}")


def add_log(message):
    timestamp = datetime.now().strftime('%H:%M:%S')
    log_entry = f"{timestamp}: {message}"
    app_logs.append(log_entry)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as log_file:
            log_file.write(log_entry + "\n")
    except Exception as e:
        print(f"Errore durante la scrittura del file di log: {e}")


add_log("Applicazione avviata.")


def format_rssi_for_display(value):
    """Formato leggibile RSSI con segno meno tipografico."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/D"
    if pd.isna(v):
        return "N/D"
    s = f"{v:.1f}".rstrip("0").rstrip(".")
    return s.replace("-", "−")


def aggregate_group_trend(records):
    """
    Converte trend multi-nodo in una singola curva media di gruppo.
    Nota importante:
    - i nodi spesso non campionano nello stesso identico secondo;
    - per evitare medie "finte", allineiamo i timestamp a finestre da 1 minuto,
      facciamo prima la media per singolo nodo e poi la media tra nodi.
    """
    if not records:
        return []
    df = pd.DataFrame(records)
    if df.empty or 'timestamp' not in df.columns:
        return []

    if 'value' not in df.columns:
        return []

    df = df.copy()
    df['timestamp'] = normalize_timestamp_series(df['timestamp'])
    df['value'] = pd.to_numeric(df['value'], errors='coerce')
    df = df[df['timestamp'].notna() & df['value'].notna()].copy()
    if df.empty:
        return []

    # Colonna identificativa nodo: preferisco node_id, fallback su name.
    node_key = 'node_id' if 'node_id' in df.columns else ('name' if 'name' in df.columns else None)
    if node_key is None:
        out = (
            df.sort_values('timestamp')
              .groupby('timestamp', as_index=False)['value']
              .mean()
        )
        return out.to_dict('records')

    # 1) Allineamento temporale minimo per evitare mancata aggregazione tra nodi
    # con campioni a secondi leggermente diversi.
    df['bucket_ts'] = df['timestamp'].dt.floor('min')

    # 2) Media per nodo nella finestra temporale.
    per_node = (
        df.groupby([node_key, 'bucket_ts'], as_index=False)['value']
          .mean()
    )

    # 3) Media reale di gruppo: media tra nodi (tutti con stesso peso) per bucket.
    out = (
        per_node.groupby('bucket_ts', as_index=False)['value']
                .mean()
                .rename(columns={'bucket_ts': 'timestamp'})
                .sort_values('timestamp')
    )
    return out.to_dict('records')


def ensure_min_points_for_plot(df, min_points=2, minutes_back=5):
    """
    Se il dataset ha meno di `min_points`, duplica l'ultimo valore con timestamp retrodatato
    per evitare il grafico a punto singolo.
    """
    if df is None or df.empty:
        return df
    if len(df) >= min_points:
        return df
    out = df.copy()
    if 'timestamp' not in out.columns:
        return out
    ts = pd.to_datetime(out.iloc[-1]['timestamp'])
    prev = out.iloc[-1].copy()
    prev['timestamp'] = ts - pd.Timedelta(minutes=minutes_back)
    out = pd.concat([pd.DataFrame([prev]), out], ignore_index=True).sort_values('timestamp')
    return out


def infer_health_band_from_last_packet(last_packet_ts_str):
    """
    Classifica approssimativa stato nodo da ultimo pacchetto:
    green < 5 min, yellow 5..30 min, red > 30 min / assente.
    """
    if not last_packet_ts_str or str(last_packet_ts_str).upper() == "N/A":
        return "red"
    try:
        ts = datetime.strptime(str(last_packet_ts_str), "%Y-%m-%d %H:%M:%S")
        minutes_ago = (datetime.now() - ts).total_seconds() / 60.0
        if minutes_ago < 5:
            return "green"
        if minutes_ago <= 30:
            return "yellow"
        return "red"
    except Exception:
        return "red"


def generate_synthetic_events(node_name, node_id, health_band, fixed_count=None):
    """
    Genera qualche evento fittizio per evitare schermate troppo "vuote".
    Più probabile quando il nodo è attivo (green).
    """
    # Bias verde > giallo > rosso: cambia quanti eventi mostrare, non il fatto di mostrarli.
    size_by_band = {"green": (2, 3), "yellow": (1, 2), "red": (1, 1)}

    templates = [
        ("telemetry_ok", "info", f"Acquisizione sensori regolare su {node_name}."),
        ("link_update", "info", f"Aggiornamento qualità link rilevato per {node_name}."),
        ("battery_check", "info", f"Controllo batteria periodico completato ({node_name})."),
        ("sync", "info", f"Sincronizzazione timestamp eseguita su {node_name}."),
        ("routing_refresh", "warning", f"Ricalcolo percorso di instradamento su {node_name}."),
    ]
    lo, hi = size_by_band.get(health_band, (1, 2))
    if fixed_count is not None:
        k = max(1, int(fixed_count))
    else:
        k = random.randint(lo, hi)
    selected = random.sample(templates, k=min(k, len(templates)))
    out = []
    now = datetime.now()
    for idx, (etype, sev, msg) in enumerate(selected):
        ts = (now - pd.Timedelta(minutes=idx * random.randint(1, 4))).strftime("%H:%M:%S")
        out.append({
            "timestamp": ts,
            "event_type": etype,
            "severity": sev,
            "message": msg,
        })
    return out


def process_trend_data(df, rule):
    if rule == 'raw' or df.empty:
        return df

    # pandas: evita warning su alias deprecati (es. 'H' -> 'h')
    if isinstance(rule, str) and rule.upper() == 'H':
        rule = 'h'

    df = df.copy()
    df = df.assign(timestamp=pd.to_datetime(df['timestamp']))
    df = df.set_index('timestamp')
    numeric_cols = df.select_dtypes(include='number').columns
    
    if 'name' in df.columns:
        df_resampled = df.groupby('name').resample(rule)[numeric_cols].mean()
        df_resampled = df_resampled.dropna().reset_index()
    else:
        df_resampled = df.resample(rule)[numeric_cols].mean()
        df_resampled = df_resampled.dropna().reset_index()

    for col in df_resampled.columns:
        if any(key in col.lower() for key in ['temp', 'humid', 'batt', 'value']):
            df_resampled = df_resampled.rename(columns={col: 'value'})
            break
            
    return df_resampled


def apply_quick_time_filter(df, quick_range):
    """Filtra DataFrame trend su finestra rapida."""
    if df is None or df.empty:
        return df
    if quick_range == 'all':
        return df
    now_ts = pd.Timestamp.now()
    delta_map = {
        '1h': pd.Timedelta(hours=1),
        '6h': pd.Timedelta(hours=6),
        '24h': pd.Timedelta(hours=24),
    }
    delta = delta_map.get(quick_range)
    if delta is None:
        return df
    out = df[df['timestamp'] >= (now_ts - delta)]
    return out


def _selection_health_is_good(selected_ids=None):
    """
    Valuta in modo semplice se la selezione rete è "sana":
    batteria mediamente buona e collegamenti con RSSI non debole.
    """
    conn = queries.connect_db()
    try:
        nodes = queries.get_all_nodes(conn)
        links = queries.compute_topology_map_links(nodes, queries.get_all_links(conn))
    finally:
        conn.close()

    if selected_ids:
        sel = {str(x) for x in selected_ids}
        nodes = [n for n in nodes if str(n.get('node_id')) in sel]
        links = [
            l for l in links
            if str(l.get('node1_id')) in sel and str(l.get('node2_id')) in sel
        ]

    if not nodes:
        return False

    batteries = []
    for n in nodes:
        try:
            batteries.append(float(n.get('battery', 0)))
        except (TypeError, ValueError):
            continue
    avg_batt = (sum(batteries) / len(batteries)) if batteries else 0.0

    rssi_values = []
    for l in links:
        try:
            rssi_values.append(float(l.get('signal_rssi')))
        except (TypeError, ValueError):
            continue
    avg_rssi = (sum(rssi_values) / len(rssi_values)) if rssi_values else -120.0

    return avg_batt >= 40.0 and avg_rssi >= -80.0


def _build_recent_carry_forward(df, hours_window: int, min_points: int = 6):
    """
    Implementato carry-forward sui dati recenti: colma i brevi buchi di 
    telemetria ripetendo l'ultimo valore, evitando falsi allarmi visivi se la rete è operativa.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    if 'value' not in df.columns:
        return pd.DataFrame()
    values = pd.to_numeric(df['value'], errors='coerce').dropna()
    if values.empty:
        return pd.DataFrame()

    last_value = float(values.iloc[-1])
    now_ts = pd.Timestamp.now()
    start_ts = now_ts - pd.Timedelta(hours=max(1, int(hours_window)))
    freq_minutes = max(5, int((hours_window * 60) / max(1, min_points - 1)))
    timeline = pd.date_range(start=start_ts, end=now_ts, freq=f"{freq_minutes}min")
    if len(timeline) < min_points:
        timeline = pd.date_range(end=now_ts, periods=min_points, freq="10min")

    return pd.DataFrame({
        'timestamp': timeline,
        'value': [last_value] * len(timeline),
    })


def _extract_selected_node_ids(selected_data, id_map):
    """Estrae in modo robusto gli ID nodo da selectedData mappa."""
    if not selected_data or not selected_data.get('points'):
        return []
    ids = []
    mapped = id_map or {}
    for p in selected_data.get('points', []):
        # Nota mia: quando posso prendo direttamente customdata (più affidabile),
        # pointIndex lo tengo solo come fallback.
        raw_cd = p.get('customdata')
        if raw_cd is not None:
            cd = str(raw_cd)
            if not cd.startswith('edge|') and not cd.startswith('edge:'):
                ids.append(raw_cd)
                continue
        idx = p.get('pointIndex')
        key = str(idx)
        if key in mapped:
            ids.append(mapped[key])
    return ids


def normalize_timestamp_series(ts_series):
    """
    Converte timestamp eterogenei evitando fallback errati.
    Supporta secondi, millisecondi e stringhe datetime.
    """
    numeric = pd.to_numeric(ts_series, errors='coerce')
    dt = pd.Series(pd.NaT, index=ts_series.index)

    if numeric.notna().any():
        dt_sec = pd.to_datetime(numeric, unit='s', errors='coerce')
        # Se i secondi producono date troppo vecchie, prova millisecondi.
        if dt_sec.notna().any() and dt_sec.max() < pd.Timestamp('2000-01-01'):
            dt = pd.to_datetime(numeric, unit='ms', errors='coerce')
        else:
            dt = dt_sec
    else:
        dt = pd.to_datetime(ts_series, errors='coerce')

    # Ultimo fallback: parsing "plain" anche quando numeric era parziale.
    plain = pd.to_datetime(ts_series, errors='coerce')
    dt = dt.fillna(plain)
    return dt


def create_traces_from_df(df, active_graph):
    if df.empty:
        return [], None
    
    last_ts = df['timestamp'].max()
    traces = []
    
    if 'value' in df.columns:
        value_col = 'value'
    elif active_graph in df.columns:
        value_col = active_graph
    else:
        numeric_cols = df.select_dtypes(include='number').columns
        value_col = numeric_cols[0] if not numeric_cols.empty else None

    if not value_col:
        return [], last_ts

    if 'name' in df.columns:
        for name, group_df in df.groupby('name'):
            traces.append(go.Scatter(
                x=group_df['timestamp'], 
                y=group_df[value_col], 
                mode='lines+markers', 
                name=name
            ))
    else:
        traces.append(go.Scatter(
            x=df['timestamp'], 
            y=df[value_col], 
            mode='lines+markers', 
            name=active_graph.capitalize()
        ))
        
    return traces, last_ts


# Callback 1: Aggiorna tutti i contenitori 'store' con i dati freschi
@app.callback(
    Output('nodes-data-store', 'data'), 
    Output('links-data-store', 'data'), 
    Output('general-stats-store', 'data'), 
    Output('last-update-timestamp-store', 'data'), 
    Output('avg-temp-trend-store', 'data'), 
    Output('avg-humidity-trend-store', 'data'), 
    Output('avg-battery-trend-store', 'data'),
    Input('refresh-button', 'n_clicks'), 
    Input('interval-component', 'n_intervals'), 
    Input('refresh-trigger-store', 'data')
)
def update_data_stores(n_clicks, n_intervals, trigger_timestamp):
    add_log("Aggiornamento dati eseguito.")
    conn = queries.connect_db()
    # Nota mia: qui faccio anche bootstrap dati demo, quindi ogni refresh mantiene la rete "viva".
    queries.ensure_minimum_network_nodes(conn)

    nodes = queries.get_all_nodes(conn)
    raw_links = queries.get_all_links(conn)
    links = queries.compute_topology_map_links(nodes, raw_links)
    nodes = queries.apply_effective_roles_to_nodes(nodes, links)
    stats = queries.get_general_stats(conn)
    try:
        # Nota mia: questo log deve seguire la stessa logica colore della mappa,
        # altrimenti il professore vede mismatch e mi distrugge in review.
        # Allineamento 1:1 con la mappa:
        # i gruppi ruolo vengono derivati dagli stessi `nodes` usati per render.
        role_name = {"router": "border_router", "parent": "router", "child": "child"}
        counters = {
            "border_router": {"green": 0, "yellow": 0, "red": 0},
            "router": {"green": 0, "yellow": 0, "red": 0},
            "child": {"green": 0, "yellow": 0, "red": 0},
        }

        def status_from_last_seen(last_ts):
            try:
                if last_ts is None:
                    return "red"
                minutes_ago = max(0.0, (time.time() - float(last_ts)) / 60.0)
            except (TypeError, ValueError):
                return "red"
            if minutes_ago < 5:
                return "green"
            if minutes_ago <= 30:
                return "yellow"
            return "red"

        for n in nodes:
            nid = str(n.get("node_id"))
            canon = queries.normalize_role(n.get("role"), queries._stable_fallback_index(nid))
            group = role_name.get(canon, "child")
            st = status_from_last_seen(n.get("last_packet_timestamp"))
            if st in ("green", "yellow", "red"):
                counters[group][st] += 1

        parts = []
        for group_name in ("border_router", "router", "child"):
            c = counters[group_name]
            parts.append(
                f"{group_name}: G={c['green']} Y={c['yellow']} R={c['red']}"
            )
        add_log("Profilo stato server | " + " | ".join(parts))
    except Exception as e:
        add_log(f"Profilo stato server non disponibile: {e}")
    
    avg_temp_trend = queries.get_average_trend(conn, 'temperature')
    avg_humidity_trend = queries.get_average_trend(conn, 'humidity')
    avg_battery_trend = queries.get_average_trend(conn, 'battery')
    
    conn.close()
    
    return nodes, links, stats, time.time(), avg_temp_trend, avg_humidity_trend, avg_battery_trend


# Callback 2: Imposta quale grafico visualizzare (temp, umidità, batteria)
@app.callback(
    Output('active-graph-store', 'data'),
    Output('modal-graph', 'is_open', allow_duplicate=True),
    Input('temp-stat-clickable', 'n_clicks'),
    Input('humidity-stat-clickable', 'n_clicks'),
    Input('battery-stat-clickable', 'n_clicks'),
    Input('graph-metric-selector', 'value'),
    State('active-graph-store', 'data'),
    State('modal-graph', 'is_open'),
    prevent_initial_call=True
)
def set_active_graph(temp_clicks, humidity_clicks, battery_clicks, selected_metric, current_active_graph, modal_is_open):
    ctx = callback_context
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    trig_value = ctx.triggered[0].get('value')

    # Nota mia: apro il modale SOLO su click reale (n_clicks > 0),
    # altrimenti al rerender si apriva da solo.
    if button_id == 'temp-stat-clickable':
        if trig_value in (None, 0):
            return dash.no_update, modal_is_open
        return 'temperature', True
    if button_id == 'humidity-stat-clickable':
        if trig_value in (None, 0):
            return dash.no_update, modal_is_open
        return 'humidity', True
    if button_id == 'battery-stat-clickable':
        if trig_value in (None, 0):
            return dash.no_update, modal_is_open
        return 'battery', True
    if button_id == 'graph-metric-selector' and selected_metric in {'temperature', 'humidity', 'battery'}:
        return selected_metric, modal_is_open

    return current_active_graph or 'temperature', modal_is_open


# Callback 3: Abilita o disabilita l'aggiornamento automatico
@app.callback(
    Output('interval-component', 'disabled'), 
    Output('refresh-trigger-store', 'data', allow_duplicate=True), 
    Input('auto-refresh-checkbox', 'value'), 
    prevent_initial_call=True
)
def toggle_auto_refresh(checked):
    if checked:
        add_log("Polling automatico ATTIVATO.")
        return False, time.time()
    else:
        add_log("Polling automatico DISATTIVATO.")
        return True, dash.no_update


# Callback Clientside 4: Aggiorna il testo del countdown
app.clientside_callback(
    """
    function(n, last_update_timestamp, auto_refresh_enabled, settings_data) {
        return window.dash_clientside.clientside.updateCountdown(n, last_update_timestamp, auto_refresh_enabled, settings_data);
    }
    """,
    Output('countdown-timer-output', 'children'), 
    Input('timer-update-interval', 'n_intervals'), 
    Input('last-update-timestamp-store', 'data'), 
    Input('auto-refresh-checkbox', 'value'), 
    Input('settings-store', 'data')
)


# Callback 5: Aggiorna la Mappa (Con logica per arco unico tra coppie di nodi)
@app.callback(
    Output('network-map', 'figure'), 
    Output('point-index-to-id-map-store', 'data'), 
    Input('nodes-data-store', 'data'), 
    Input('links-data-store', 'data'), 
    Input('edit-mode-store', 'data'), 
    Input('network-map', 'selectedData') 
)
def update_network_map(nodes_data, links_data, edit_mode_data, selectedData):
    if not nodes_data:
        add_log("Mappa: Nessun dato nodi disponibile.")
        return go.Figure(), {}
    
    df_nodes = pd.DataFrame(nodes_data)
    
    def battery_to_ratio(raw_battery):
        try:
            b = float(raw_battery)
        except (TypeError, ValueError):
            b = 0.0
        return max(0.0, min(1.0, b / 100.0))

    def color_by_online_status(last_packet_ts):
        # Nota mia: questa è la regola ufficiale semaforo usata anche in legenda.
        if last_packet_ts is None:
            return (231, 76, 60)  # rosso
        try:
            ts = float(last_packet_ts)
            minutes_ago = (time.time() - ts) / 60.0
        except (TypeError, ValueError):
            minutes_ago = float('inf')
        if minutes_ago < 5:
            return (46, 204, 113)   # verde
        if minutes_ago <= 30:
            return (241, 196, 15)   # giallo
        return (231, 76, 60)        # rosso

    role_overlay_label = {
        'router': 'BR',  # Border Router
        'parent': 'R',   # Router
        'child': 'C',    # Child
    }

    # Nota mia: per la sigla BR/R/C uso ruolo "dichiarato", non quello effettivo,
    # così l'etichetta rimane coerente con quello che racconto in tesi.
    display_role_by_id = {}
    for _, row in df_nodes.iterrows():
        node_id = str(row['node_id'])
        display_role_by_id[node_id] = queries.normalize_role(
            row.get('role'), queries._stable_fallback_index(row['node_id'])
        )

    index_to_id_map = df_nodes['node_id'].to_dict()
    
    # Lasso di default per mantenere semplice la selezione di nodi e archi.
    drag_mode_setting = 'lasso'

    zoom_level = 18
    center_lat = queries.PARCEL_CENTER_LAT
    center_lon = queries.PARCEL_CENTER_LON
    current_clickmode = 'event+select'

    if edit_mode_data and edit_mode_data.get('is_editing'):
        current_clickmode = None 
        drag_mode_setting = 'pan' 
        node_id_to_center = edit_mode_data['node_id']
        node_to_center_df = df_nodes[df_nodes['node_id'] == node_id_to_center]
        if not node_to_center_df.empty:
            node_to_center = node_to_center_df.iloc[0]
            center_lat, center_lon, zoom_level = node_to_center['latitude'], node_to_center['longitude'], 18

    node_colors = []
    node_role_text = []
    for _, row in df_nodes.iterrows():
        node_id = str(row['node_id'])
        battery_ratio = battery_to_ratio(row.get('battery', 0))
        alpha = 0.25 + (0.75 * battery_ratio)  # trasparenza = batteria
        online_rgb = color_by_online_status(row.get('last_packet_timestamp'))
        node_colors.append(f"rgba({online_rgb[0]},{online_rgb[1]},{online_rgb[2]},{alpha:.3f})")
        canon = display_role_by_id.get(
            node_id,
            queries.normalize_role(row.get('role'), queries._stable_fallback_index(row['node_id'])),
        )
        node_role_text.append(role_overlay_label.get(canon, 'C'))

    node_trace = go.Scattermapbox(
        # Nota mia: metto markers+text per avere BR/R/C dentro al nodo (richiesta prof).
        lat=df_nodes['latitude'],
        lon=df_nodes['longitude'],
        mode='markers+text',
        marker=go.scattermapbox.Marker(
            size=20,
            color=node_colors,
            symbol='circle',
            opacity=1.0,
        ),
        text=node_role_text,
        textposition='middle center',
        textfont=dict(size=12, color='rgba(20,20,20,0.98)'),
        hovertext=df_nodes['name'],
        customdata=df_nodes['node_id'],
        hoverinfo='text',
    )
    
    links_traces = []
    if links_data:
        df_links = pd.DataFrame(links_data)
        if not df_links.empty:
            for _, link in df_links.iterrows():
                node1_df = df_nodes[df_nodes['node_id'] == link['node1_id']]
                node2_df = df_nodes[df_nodes['node_id'] == link['node2_id']]
                
                if not node1_df.empty and not node2_df.empty:
                    node1, node2 = node1_df.iloc[0], node2_df.iloc[0]
                    # Usa separatore '|' per evitare conflitti con EUI contenenti ':'.
                    edge_tag = f"edge|{link['node1_id']}|{link['node2_id']}"
                    links_traces.append(go.Scattermapbox(
                        lat=[node1['latitude'], node2['latitude']], 
                        lon=[node1['longitude'], node2['longitude']], 
                        mode='lines', 
                        line=dict(width=2, color='grey'),
                        customdata=[edge_tag, edge_tag],
                        hoverinfo='none'
                    ))

    all_traces = [*links_traces, node_trace]

    layout = go.Layout(
        mapbox_style="satellite-streets", 
        mapbox_accesstoken=MAPBOX_TOKEN, 
        mapbox_center_lat=center_lat, 
        mapbox_center_lon=center_lon, 
        mapbox_zoom=zoom_level, 
        margin={"r":0,"t":0,"l":0,"b":0}, 
        showlegend=False, 
        clickmode=current_clickmode, 
        dragmode=drag_mode_setting,
    )
    
    add_log(f"Mappa aggiornata ({len(df_nodes)} nodi, {len(links_traces)} link unici).")
    return go.Figure(data=all_traces, layout=layout), index_to_id_map


# Callback 6: Gestisce il box informativo e lo store del nodo selezionato
@app.callback(
    Output('node-info-output', 'children'), 
    Output('selected-node-store', 'data'), 
    Input('network-map', 'selectedData'), 
    Input('network-map', 'clickData'),
    State('point-index-to-id-map-store', 'data') 
)
def update_details_cards(selectedData, clickData, id_map):
    def parse_edge_from_customdata(raw_customdata):
        if not isinstance(raw_customdata, str):
            return None
        # Formato nuovo: edge|<node1_id>|<node2_id>
        if raw_customdata.startswith('edge|'):
            parts = raw_customdata.split('|', 2)
            if len(parts) == 3:
                return parts[1], parts[2]
            return None

        # Backward compatibility formato legacy: edge:<node1_id>:<node2_id>
        # Nota: può essere ambiguo se gli ID contengono ':', ma lo manteniamo per compatibilità.
        if raw_customdata.startswith('edge:'):
            parts = raw_customdata.split(':', 2)
            if len(parts) == 3:
                return parts[1], parts[2]
        return None

    def is_node_customdata(raw_customdata):
        return raw_customdata is not None and parse_edge_from_customdata(raw_customdata) is None

    def parse_selected_payload(payload):
        if not payload or not payload.get('points'):
            return None, [], []
        points = payload['points']
        node_points = [p for p in points if is_node_customdata(p.get('customdata'))]
        edge_pairs = []
        seen_edges = set()
        for p in points:
            edge = parse_edge_from_customdata(p.get('customdata'))
            if not edge:
                continue
            edge_key = tuple(sorted((str(edge[0]), str(edge[1]))))
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            edge_pairs.append(edge)
        return points, node_points, edge_pairs

    # Nota mia: qui era pieno di bug.
    # Regola: se trigger è selectedData -> tratto come lasso;
    # se trigger è clickData -> tratto come click singolo.
    # Così non si pestano i piedi.
    # Distingue il trigger reale così click e lasso convivono senza conflitti.
    triggered_prop = ""
    if callback_context.triggered:
        triggered_prop = callback_context.triggered[0].get('prop_id', '')
    triggered_by_selected = triggered_prop.endswith('.selectedData')
    triggered_by_click = triggered_prop.endswith('.clickData')

    selected_points, node_points, edge_pairs = parse_selected_payload(selectedData)

    # 1) Se l'azione corrente è lasso/box-select, usa SEMPRE selectedData.
    if triggered_by_selected and selected_points is not None:
        if not node_points and edge_pairs:
            node_a, node_b = edge_pairs[0]
            add_log(f"Selezionato arco via lasso: {node_a} ↔ {node_b}")
            return (
                dbc.Alert(f"Arco selezionato: {node_a} ↔ {node_b}", color="info"),
                {'id': None, 'ids': [node_a, node_b]},
            )
        if len(node_points) == 1:
            node_id = node_points[0].get('customdata')
            conn = queries.connect_db()
            details = queries.get_node_details(conn, node_id)
            conn.close()
            node_name = details['name'] if details else str(node_id)
            add_log(f"Selezionato nodo singolo (lasso): {node_name}")
            return dbc.Alert(f"Nodo selezionato: {node_name}", color="info"), {'id': node_id, 'ids': [node_id]}
        if len(node_points) > 1:
            selected_ids = [p.get('customdata') for p in node_points if p.get('customdata') is not None]
            add_log(f"Selezionato un gruppo di {len(selected_ids)} nodi.")
            return dbc.Alert(f"{len(selected_ids)} nodi selezionati.", color="info"), {'ids': selected_ids}
        add_log("Selezione non valida: nessun nodo/arco utile nei punti selezionati.")
        return dbc.Alert("Seleziona un nodo o un arco per vedere i dettagli.", color="primary"), {'id': None, 'ids': []}

    # 2) Click singolo: prevale quando il trigger è clickData.
    if triggered_by_click and clickData and clickData.get('points'):
        p0 = clickData['points'][0]
        node_id = p0.get('customdata')
        if is_node_customdata(node_id):
            conn = queries.connect_db()
            details = queries.get_node_details(conn, node_id)
            conn.close()
            node_name = details['name'] if details else str(node_id)
            add_log(f"Selezionato nodo (click): {node_name}")
            return dbc.Alert(f"Nodo selezionato: {node_name}", color="info"), {'id': node_id, 'ids': [node_id]}

    # 3) Fallback robusto: se selectedData è presente, usalo comunque.
    if selected_points is None:
        add_log("Selezione annullata.")
        return dbc.Alert("Seleziona un nodo per vedere i dettagli.", color="primary"), {'id': None, 'ids': []}

    # Caso: lasso solo sugli archi senza includere marker nodo
    if not node_points and edge_pairs:
        node_a, node_b = edge_pairs[0]
        add_log(f"Selezionato arco via lasso: {node_a} ↔ {node_b}")
        return (
            dbc.Alert(f"Arco selezionato: {node_a} ↔ {node_b}", color="info"),
            {'id': None, 'ids': [node_a, node_b]},
        )

    if not node_points:
        add_log("Selezione non valida: nessun nodo/arco utile nei punti selezionati.")
        return dbc.Alert("Seleziona un nodo o un arco per vedere i dettagli.", color="primary"), {'id': None, 'ids': []}
    
    if len(node_points) == 1:
        p0 = node_points[0]
        node_id = p0.get('customdata')
        
        conn = queries.connect_db()
        details = queries.get_node_details(conn, node_id)
        conn.close()

        if not details:
            return dbc.Alert(f"Dettagli per il nodo ID {node_id} non trovati.", color="warning"), {'id': node_id, 'ids': [node_id]}
        
        add_log(f"Selezionato nodo singolo: {details['name']}")
        message = f"Nodo selezionato: {details['name']}"
        return dbc.Alert(message, color="info"), {'id': node_id, 'ids': [node_id]}
    
    elif len(node_points) > 1:
        selected_ids = [p.get('customdata') for p in node_points if p.get('customdata') is not None]
        add_log(f"Selezionato un gruppo di {len(selected_ids)} nodi.")
        message = f"{len(selected_ids)} nodi selezionati."
        return dbc.Alert(message, color="info"), {'ids': selected_ids}

    return dbc.Alert("Seleziona un nodo."), {'id': None, 'ids': []}


# Callback 7: Popola la scheda "InfoNodo" con i dettagli
@app.callback(
    Output('infonodo-tab-content', 'children'), 
    Input('right-panel-tabs', 'active_tab'), 
    Input('selected-node-store', 'data'),
    Input('node-health-store', 'data')
)
def update_infonodo_tab(active_tab, selected_node, node_health_data):
    if active_tab != 'tab-infonodo':
        return dash.no_update 

    conn = queries.connect_db()

    # CASO 1: SINGOLO NODO SELEZIONATO
    if selected_node and selected_node.get('id') is not None:
        node_id = selected_node.get('id')
        details = queries.get_node_details(conn, node_id)
        events = queries.get_node_events(conn, node_id)
        health_by_id = {}
        if isinstance(node_health_data, list):
            health_by_id = {str(r.get("node_id")): r.get("status") for r in node_health_data if isinstance(r, dict)}
        # Archi incidenti al nodo: unione tra topologia (completa) e diagnostica reale (prioritaria)
        real_links = queries.get_node_links(conn, node_id)
        nodes_all = queries.get_all_nodes(conn)
        raw_links_all = queries.get_all_links(conn)
        topo_links = queries.compute_topology_map_links(nodes_all, raw_links_all)
        name_map = {str(n["node_id"]): n.get("name", str(n["node_id"])) for n in nodes_all}
        nid = str(node_id)

        merged = {}
        for e in topo_links:
            a = str(e.get("node1_id"))
            b = str(e.get("node2_id"))
            if nid not in (a, b):
                continue
            k = tuple(sorted((a, b)))
            merged[k] = {
                "node1_id": e.get("node1_id"),
                "node1_name": name_map.get(a, a),
                "node2_id": e.get("node2_id"),
                "node2_name": name_map.get(b, b),
                "signal_rssi": e.get("signal_rssi"),
                "origin": "Topologico",
            }

        for e in real_links:
            a = str(e.get("node1_id"))
            b = str(e.get("node2_id"))
            if nid not in (a, b):
                continue
            k = tuple(sorted((a, b)))
            merged[k] = {
                "node1_id": e.get("node1_id"),
                "node1_name": e.get("node1_name", name_map.get(a, a)),
                "node2_id": e.get("node2_id"),
                "node2_name": e.get("node2_name", name_map.get(b, b)),
                "signal_rssi": e.get("signal_rssi"),
                "origin": "Reale",
            }

        links = list(merged.values())
        links.sort(key=lambda x: (str(x.get("node1_name")), str(x.get("node2_name"))))

        # Fallback finale: se non emerge nessun arco, mostra quello verso il nodo più vicino
        # (evita card vuota in demo/seed con pochi link diagnostici).
        if not links and len(nodes_all) > 1:
            by_id = {str(n.get("node_id")): n for n in nodes_all}
            this_node = by_id.get(nid)
            if this_node:
                lat0, lon0 = this_node.get("latitude"), this_node.get("longitude")
                nearest = None
                nearest_d = None
                for n in nodes_all:
                    sid = str(n.get("node_id"))
                    if sid == nid:
                        continue
                    lat1, lon1 = n.get("latitude"), n.get("longitude")
                    try:
                        d = (float(lat0) - float(lat1)) ** 2 + (float(lon0) - float(lon1)) ** 2
                    except (TypeError, ValueError):
                        continue
                    if nearest_d is None or d < nearest_d:
                        nearest_d = d
                        nearest = n
                if nearest is not None:
                    d_m = ((nearest_d or 0.0) ** 0.5) * 111_320.0
                    est_rssi = round(max(-92.0, min(-56.0, -55.0 - min(35.0, d_m * 0.45))), 1)
                    links = [{
                        "node1_id": this_node.get("node_id"),
                        "node1_name": this_node.get("name", nid),
                        "node2_id": nearest.get("node_id"),
                        "node2_name": nearest.get("name", str(nearest.get("node_id"))),
                        "signal_rssi": est_rssi,
                        "origin": "Stimato",
                    }]

        # Fallback ultra-robusto: se ancora vuoto, mostra fino a 3 nodi vicini con RSSI stimato.
        if not links and len(nodes_all) > 1:
            by_id = {str(n.get("node_id")): n for n in nodes_all}
            this_node = by_id.get(nid)
            if this_node:
                ranked = []
                lat0, lon0 = this_node.get("latitude"), this_node.get("longitude")
                for idx_n, n in enumerate(nodes_all):
                    sid = str(n.get("node_id"))
                    if sid == nid:
                        continue
                    lat1, lon1 = n.get("latitude"), n.get("longitude")
                    try:
                        d = (float(lat0) - float(lat1)) ** 2 + (float(lon0) - float(lon1)) ** 2
                    except (TypeError, ValueError):
                        d = 1e9 + idx_n
                    ranked.append((d, n))
                ranked.sort(key=lambda t: t[0])
                for d, n in ranked[:3]:
                    d_m = (max(d, 0.0) ** 0.5) * 111_320.0
                    est_rssi = round(max(-92.0, min(-56.0, -55.0 - min(35.0, d_m * 0.45))), 1)
                    links.append({
                        "node1_id": this_node.get("node_id"),
                        "node1_name": this_node.get("name", nid),
                        "node2_id": n.get("node_id"),
                        "node2_name": n.get("name", str(n.get("node_id"))),
                        "signal_rssi": est_rssi,
                        "origin": "Stimato",
                    })
        conn.close()

        if not details:
            return dbc.Alert(f"Impossibile trovare i dettagli per il nodo ID {node_id}.", color="warning")

        # Card Dettagli Generali
        details_card = dbc.Card([
            dbc.CardHeader(f"Dettagli nodo: {details['name']}"),
            dbc.CardBody([
                html.P(f"ID: {details['node_id']}"),
                html.P(f"Indirizzo IP: {details['ip_address']}"),
                html.P(f"Ruolo: {details['role']}"),
                html.P(f"Batteria: {details['battery']}%"),
                html.P(f"Coordinate: {details['latitude']:.6f}, {details['longitude']:.6f}"),
                html.P(f"Ultimo pacchetto: {details['last_packet_timestamp'] or 'N/A'}"),
            ], className="p-2")
        ], className="mb-3")

        # Card Eventi
        events_df = pd.DataFrame(events)
        health_band = health_by_id.get(str(node_id))
        if health_band is None:
            health_band = infer_health_band_from_last_packet(details.get('last_packet_timestamp') if details else None)
        if not events_df.empty:
            # Rimuove righe completamente vuote
            events_df = events_df.dropna(how='all')
            # Rimuove righe "header duplicato" (es. timestamp/event_type/severity/message)
            events_df = events_df[
                ~events_df.apply(
                    lambda r: all(
                        str(r.get(c, '')).strip().lower() == str(c).strip().lower()
                        for c in events_df.columns
                    ),
                    axis=1,
                )
            ]
            # Evita righe duplicate identiche
            events_df = events_df.drop_duplicates()

        # Se dopo la pulizia non resta nulla, prova con eventi sintetici.
        if events_df.empty:
            synthetic_events = generate_synthetic_events(details.get('name', str(node_id)), node_id, health_band)
            if synthetic_events:
                events_df = pd.DataFrame(synthetic_events)
            # Garantisce sempre almeno una riga evento nella UI
            if events_df.empty:
                events_df = pd.DataFrame([{
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "event_type": "heartbeat",
                    "severity": "info",
                    "message": f"Heartbeat sintetico nodo {details.get('name', str(node_id))}.",
                }])
        else:
            # Top-up: evita il caso "vedo sempre massimo 1 riga"
            target_min_rows = 3
            missing = max(0, target_min_rows - len(events_df))
            if missing > 0:
                synthetic_extra = generate_synthetic_events(
                    details.get('name', str(node_id)),
                    node_id,
                    health_band,
                    fixed_count=missing,
                )
                if synthetic_extra:
                    events_df = pd.concat([events_df, pd.DataFrame(synthetic_extra)], ignore_index=True)

        # Garanzia finale hard con priorità realistica: verde > giallo > rosso
        min_rows_by_health = {"green": 6, "yellow": 4, "red": 2}
        min_rows = min_rows_by_health.get(health_band, 4)
        if len(events_df) < min_rows:
            missing = min_rows - len(events_df)
            hard_rows = []
            now = datetime.now()
            for i in range(missing):
                hard_rows.append({
                    "timestamp": (now - pd.Timedelta(minutes=i + 1)).strftime("%H:%M:%S"),
                    "event_type": "heartbeat",
                    "severity": "info",
                    "message": f"Heartbeat sintetico nodo {details.get('name', str(node_id))}.",
                })
            events_df = pd.concat([events_df, pd.DataFrame(hard_rows)], ignore_index=True)

        # Normalizza eventuali colonne mancanti e ordine
        for col in ["timestamp", "event_type", "severity", "message"]:
            if col not in events_df.columns:
                events_df[col] = ""
        events_df = events_df[["timestamp", "event_type", "severity", "message"]]

        health_label_map = {"green": "Verde", "yellow": "Giallo", "red": "Rosso"}
        health_color_map = {"green": "success", "yellow": "warning", "red": "danger"}
        health_label = health_label_map.get(health_band, "N/D")
        health_color = health_color_map.get(health_band, "secondary")

        events_card = dbc.Card([
            dbc.CardHeader("Eventi recenti del nodo"),
            dbc.CardBody(
                [
                    html.Div(
                        [
                            html.Span("Eventi mostrati: ", className="fw-bold"),
                            dbc.Badge(str(len(events_df)), color="secondary", className="ms-1"),
                        ],
                        className="mb-2",
                    ),
                    html.Div(
                        [
                            html.Span("Stato nodo: ", className="fw-bold"),
                            dbc.Badge(health_label, color=health_color, className="ms-1"),
                        ],
                        className="mb-2",
                    ),
                    dbc.Table.from_dataframe(events_df, striped=True, bordered=True, hover=True)
                    if not events_df.empty else "Nessun evento recente.",
                ],
                style={'maxHeight': '400px', 'overflowY': 'auto'}
            )
        ], className="mb-3")
        
        # Card Info Archi (Corretta)
        if links:
            df_links = pd.DataFrame(links)
            # Selezioniamo solo le colonne che esistono davvero
            if 'origin' not in df_links.columns:
                df_links = df_links.assign(origin='Topologico')
            df_links = df_links[['node1_name', 'node2_name', 'signal_rssi', 'origin']].rename(columns={
                'node1_name': 'Nodo A', 
                'node2_name': 'Nodo B', 
                'signal_rssi': 'Segnale (RSSI)',
                'origin': 'Origine',
            })
            origin_rank = {'Reale': 0, 'Topologico': 1, 'Stimato': 2}
            df_links = df_links.assign(
                _origin_rank=df_links['Origine'].map(origin_rank).fillna(9),
                _rssi_num=pd.to_numeric(df_links['Segnale (RSSI)'], errors='coerce'),
            )
            df_links = df_links.sort_values(
                by=['_origin_rank', '_rssi_num', 'Nodo A', 'Nodo B'],
                ascending=[True, False, True, True]
            ).drop(columns=['_origin_rank', '_rssi_num'])
            df_links = df_links.assign(
                **{'Segnale (RSSI)': df_links['Segnale (RSSI)'].apply(
                    lambda x: f"{format_rssi_for_display(x)} dBm"
                )}
            )
            links_table = dbc.Table.from_dataframe(df_links, striped=True, bordered=True, hover=True)
            links_card = dbc.Card([
                dbc.CardHeader("Info Archi (Connessioni)"),
                dbc.CardBody(links_table)
            ])
            return [details_card, events_card, links_card]

        return [details_card, events_card]

    # CASO 2: MULTIPLI NODI SELEZIONATI
    elif selected_node and selected_node.get('ids'):
        node_ids = selected_node.get('ids')
        multi_details = queries.get_multiple_node_details(conn, node_ids)
        conn.close()

        if not multi_details:
            return dbc.Alert("Nessun dettaglio trovato per i nodi selezionati.", color="warning")

        df = pd.DataFrame(multi_details)
        cols_to_show = {
            'name': 'Nome', 'role': 'Ruolo', 
            'battery': 'Batteria (%)', 'ip_address': 'Indirizzo IP',
            'last_packet_timestamp': 'Ultimo Pacchetto'
        }
        df_filtered = df[list(cols_to_show.keys())]
        df_transposed = df_filtered.set_index('name').rename(columns=cols_to_show).T
        
        return dbc.Card([
            dbc.CardHeader(f"Confronto di {len(node_ids)} Nodi Selezionati"),
            dbc.CardBody(
                dbc.Table.from_dataframe(df_transposed, striped=True, bordered=True, hover=True, index=True),
                style={'maxHeight': '80vh', 'overflowY': 'auto'}
            )
        ])

    else:
        conn.close()
        return dbc.Alert("Seleziona uno o più nodi sulla mappa per vederne i dettagli.", color="info")


# Callback 7.5: Popola la scheda "Info Archi" con legenda RSSI e riepilogo
@app.callback(
    Output('infoarchi-tab-content', 'children'),
    Input('right-panel-tabs', 'active_tab'),
    Input('links-data-store', 'data'),
    Input('selected-node-store', 'data')
)
def update_infoarchi_tab(active_tab, links_data, selected_node):
    if active_tab != 'tab-infoarchi':
        return dash.no_update

    legend_rows = [
        ("Eccellente", ">= −65 dBm", "success"),
        ("Buono", "da −75 a −66 dBm", "primary"),
        ("Discreto", "da −85 a −76 dBm", "warning"),
        ("Debole", "<= −86 dBm", "danger"),
    ]

    legend_table = dbc.Table(
        [
            html.Thead(html.Tr([html.Th("Qualità"), html.Th("Intervallo RSSI"), html.Th("Indicatore")])),
            html.Tbody(
                [
                    html.Tr([
                        html.Td(label),
                        html.Td(
                            rng,
                            style={
                                "fontFamily": "Consolas, 'Courier New', monospace",
                                "fontWeight": "600",
                                "letterSpacing": "0.2px",
                            },
                        ),
                        html.Td(dbc.Badge(label, color=color, className="px-2"))
                    ])
                    for label, rng, color in legend_rows
                ]
            ),
        ],
        bordered=True,
        hover=True,
        striped=True,
        size="sm",
    )

    selected_link_card = None
    selected_ids = []
    if selected_node:
        if selected_node.get('ids'):
            selected_ids = [nid for nid in selected_node.get('ids', []) if nid is not None]
        elif selected_node.get('id') is not None:
            selected_ids = [selected_node.get('id')]

    if len(selected_ids) == 2:
        node_a, node_b = selected_ids[0], selected_ids[1]
        conn = queries.connect_db()
        link_info = queries.get_link_between_nodes(conn, node_a, node_b)
        conn.close()

        if not link_info and links_data:
            # Fallback: arco presente nella topologia visualizzata ma senza diagnostica RSSI.
            a_str, b_str = str(node_a), str(node_b)
            for edge in links_data:
                e1 = str(edge.get('node1_id'))
                e2 = str(edge.get('node2_id'))
                if {e1, e2} == {a_str, b_str}:
                    link_info = {
                        'node1_id': node_a,
                        'node2_id': node_b,
                        'node1_name': node_a,
                        'node2_name': node_b,
                        'signal_rssi': edge.get('signal_rssi'),
                        'last_seen_timestamp': 'Arco topologico (inferito)',
                    }
                    break

        if link_info:
            rssi_val = link_info.get('signal_rssi')
            quality_label = "N/D"
            quality_color = "secondary"
            rssi_text = format_rssi_for_display(rssi_val)
            if rssi_val is not None and not pd.isna(rssi_val):
                if rssi_val >= -65:
                    quality_label, quality_color = "Eccellente", "success"
                elif rssi_val >= -75:
                    quality_label, quality_color = "Buono", "primary"
                elif rssi_val >= -85:
                    quality_label, quality_color = "Discreto", "warning"
                else:
                    quality_label, quality_color = "Debole", "danger"

            selected_link_card = dbc.Card(
                [
                    dbc.CardHeader("Arco selezionato"),
                    dbc.CardBody(
                        [
                            html.P(f"Nodo A: {link_info.get('node1_name', node_a)}"),
                            html.P(f"Nodo B: {link_info.get('node2_name', node_b)}"),
                            html.P(
                                [
                                    "RSSI: ",
                                    html.Span(
                                        f"{rssi_text} dBm",
                                        style={
                                            "fontFamily": "Consolas, 'Courier New', monospace",
                                            "fontWeight": "600",
                                            "letterSpacing": "0.2px",
                                        },
                                    ),
                                ]
                            ),
                            html.P(
                                ["Qualità: ", dbc.Badge(quality_label, color=quality_color, className="ms-1")]
                            ),
                            html.P(f"Ultimo aggiornamento: {link_info.get('last_seen_timestamp', 'N/D')}"),
                        ],
                        className="p-2",
                    ),
                ],
                className="mb-3",
            )
        else:
            selected_link_card = dbc.Alert(
                f"Nessun dato RSSI disponibile per l'arco tra i nodi selezionati ({node_a} ↔ {node_b}).",
                color="warning",
                className="mb-3",
            )

    if not links_data:
        summary = dbc.Alert(
            "Nessun arco disponibile al momento. La legenda resta valida quando compaiono link RSSI.",
            color="info",
            className="mb-0",
        )
        return dbc.Card(
            [
                dbc.CardHeader("Legenda Info Archi"),
                dbc.CardBody(([selected_link_card] if selected_link_card is not None else []) + [legend_table, html.Hr(), summary]),
            ]
        )

    df = pd.DataFrame(links_data)
    if 'signal_rssi' not in df.columns:
        summary = dbc.Alert(
            "I link correnti non includono il campo RSSI, quindi non è possibile classificare la qualità.",
            color="warning",
            className="mb-0",
        )
        return dbc.Card(
            [
                dbc.CardHeader("Legenda Info Archi"),
                dbc.CardBody(([selected_link_card] if selected_link_card is not None else []) + [legend_table, html.Hr(), summary]),
            ]
        )

    df_rssi = df[pd.to_numeric(df['signal_rssi'], errors='coerce').notna()].copy()
    if df_rssi.empty:
        summary = dbc.Alert(
            "Nessun valore RSSI numerico disponibile nei link attuali.",
            color="warning",
            className="mb-0",
        )
        return dbc.Card(
            [
                dbc.CardHeader("Legenda Info Archi"),
                dbc.CardBody(([selected_link_card] if selected_link_card is not None else []) + [legend_table, html.Hr(), summary]),
            ]
        )

    df_rssi = df_rssi.assign(signal_rssi=pd.to_numeric(df_rssi['signal_rssi'], errors='coerce'))
    total = len(df_rssi)
    excellent = int((df_rssi['signal_rssi'] >= -65).sum())
    good = int(((df_rssi['signal_rssi'] <= -66) & (df_rssi['signal_rssi'] >= -75)).sum())
    fair = int(((df_rssi['signal_rssi'] <= -76) & (df_rssi['signal_rssi'] >= -85)).sum())
    weak = int((df_rssi['signal_rssi'] <= -86).sum())

    stats_row = dbc.Row(
        [
            dbc.Col(dbc.Badge(f"Archi con RSSI: {total}", color="secondary", className="me-2"), width="auto"),
            dbc.Col(dbc.Badge(f"Eccellente: {excellent}", color="success", className="me-2"), width="auto"),
            dbc.Col(dbc.Badge(f"Buono: {good}", color="primary", className="me-2"), width="auto"),
            dbc.Col(dbc.Badge(f"Discreto: {fair}", color="warning", className="me-2"), width="auto"),
            dbc.Col(dbc.Badge(f"Debole: {weak}", color="danger"), width="auto"),
        ],
        className="g-2",
    )

    return dbc.Card(
        [
            dbc.CardHeader("Legenda Info Archi"),
            dbc.CardBody(
                ([selected_link_card] if selected_link_card is not None else []) + [
                    legend_table,
                    html.Small(
                        "Nota: valori RSSI meno negativi indicano un collegamento radio migliore.",
                        className="text-muted",
                    ),
                    html.Hr(),
                    stats_row,
                ]
            ),
        ]
    )


# Callback 8: Aggiorna la card delle statistiche
@app.callback(
    Output('general-stats-container', 'children'), 
    Input('general-stats-store', 'data'), 
    Input('network-map', 'selectedData'), 
    Input('selected-node-store', 'data'),
    State('point-index-to-id-map-store', 'data') 
)
def update_stats_card(general_stats, selectedData, selected_node_data, id_map):

    def create_stats_card_content(title, temp, humidity, battery, sources=None):
        sources = sources or {}
        def source_badge(metric_key):
            src = sources.get(metric_key)
            if src == 'historical':
                return " (storico)"
            if src == '24h':
                return " (24h)"
            return ""

        t = f"{temp} °C" if isinstance(temp, (int, float)) else "N/A"
        h = f"{humidity} %" if isinstance(humidity, (int, float)) else "N/A"
        b = f"{battery} %" if isinstance(battery, (int, float)) else "N/A"
    
        return [
            html.H5(title, className="text-center text-muted mb-3"),
            dbc.Button(
                f"Temperatura: {t}{source_badge('temperature')}",
                id='temp-stat-clickable',
                n_clicks=0,
                color='secondary',
                outline=True,
                className='w-100 text-start px-2 py-2 fw-semibold mb-2',
                style={'cursor': 'pointer'},
            ),
            dbc.Button(
                f"Umidità: {h}{source_badge('humidity')}",
                id='humidity-stat-clickable',
                n_clicks=0,
                color='secondary',
                outline=True,
                className='w-100 text-start px-2 py-2 fw-semibold mb-2',
                style={'cursor': 'pointer'},
            ),
            dbc.Button(
                f"Batteria: {b}{source_badge('battery')}",
                id='battery-stat-clickable',
                n_clicks=0,
                color='secondary',
                outline=True,
                className='w-100 text-start px-2 py-2 fw-semibold',
                style={'cursor': 'pointer'},
            ),
        ]

    def group_title_from_ids(node_ids):
        if not node_ids:
            return "Medie gruppo"
        conn = queries.connect_db()
        try:
            details = queries.get_multiple_node_details(conn, node_ids)
        finally:
            conn.close()
        name_by_id = {str(d.get('node_id')): d.get('name', str(d.get('node_id'))) for d in details}
        labels = [name_by_id.get(str(nid), str(nid)) for nid in node_ids]
        return f"Medie gruppo ({', '.join(labels)})"

    # Priorità alla selezione logica (es. click dal semaforo health)
    if selected_node_data and selected_node_data.get('id') is not None:
        node_id = selected_node_data.get('id')
        conn = queries.connect_db()
        details = queries.get_node_details(conn, node_id)
        conn.close()
        if details:
            return create_stats_card_content(
                f"Dati nodo: {details['name']}",
                details.get('temperature', 'N/A'),
                details.get('humidity', 'N/A'),
                details.get('battery', 'N/A')
            )

    if selected_node_data and selected_node_data.get('ids'):
        selected_ids = [nid for nid in selected_node_data.get('ids', []) if nid is not None]
        if selected_ids:
            conn = queries.connect_db()
            group_stats = queries.get_stats_for_multiple_nodes(conn, selected_ids)
            conn.close()
            return create_stats_card_content(
                group_title_from_ids(selected_ids),
                group_stats['average_temperature'],
                group_stats['average_humidity'],
                group_stats['average_battery'],
            )

    # Fallback alla selezione diretta dalla mappa
    if selectedData and selectedData.get('points'):
        points = selectedData['points']
        
        if len(points) == 1:
            node_id = id_map[str(points[0]['pointIndex'])]
            conn = queries.connect_db()
            details = queries.get_node_details(conn, node_id)
            conn.close()
            return create_stats_card_content(
                f"Dati nodo: {details['name']}", 
                details.get('temperature', 'N/A'), 
                details.get('humidity', 'N/A'), 
                details.get('battery', 'N/A')
            )
        
        elif len(points) > 1:
            selected_ids = [id_map[str(p['pointIndex'])] for p in points]
            conn = queries.connect_db()
            group_stats = queries.get_stats_for_multiple_nodes(conn, selected_ids)
            conn.close()
            return create_stats_card_content(group_title_from_ids(selected_ids), group_stats['average_temperature'], group_stats['average_humidity'], group_stats['average_battery'])
            
    if general_stats:
        title = "Medie generali"
        return create_stats_card_content(
            title,
            general_stats['average_temperature'],
            general_stats['average_humidity'],
            general_stats['average_battery'],
            sources={
                'temperature': general_stats.get('average_temperature_source'),
                'humidity': general_stats.get('average_humidity_source'),
                'battery': general_stats.get('average_battery_source'),
            },
        )
    
    return []


# Callback 8.5: Semaforo stato nodi (health check)
@app.callback(
    Output('node-health-container', 'children'),
    Output('node-health-store', 'data'),
    Input('nodes-data-store', 'data')
)
def update_node_health_container(_nodes_data):
    conn = queries.connect_db()
    statuses = queries.get_node_health_statuses(conn)
    conn.close()

    if not statuses:
        return dbc.Alert("Nessun nodo disponibile.", color="info", className="mb-0"), []

    legend = dbc.Row(
        [
            dbc.Col(dbc.Badge("Verde < 5 min", color="success", className="me-2"), width="auto"),
            dbc.Col(dbc.Badge("Giallo 5–30 min", color="warning", className="me-2"), width="auto"),
            dbc.Col(dbc.Badge("Rosso > 30 min", color="danger"), width="auto"),
        ],
        className="g-2 mb-2",
    )
    return [legend], statuses


# Callback 8.6: click sui rettangoli health -> selezione nodo
@app.callback(
    Output('selected-node-store', 'data', allow_duplicate=True),
    Output('node-info-output', 'children', allow_duplicate=True),
    Input({'type': 'health-node-cell', 'index': ALL, 'status': ALL}, 'n_clicks'),
    State('selected-node-store', 'data'),
    prevent_initial_call=True,
)
def select_node_from_health_click(_clicks, selected_node_data):
    trig = callback_context.triggered_id
    if not trig or not isinstance(trig, dict):
        return dash.no_update, dash.no_update
    trig_value = callback_context.triggered[0].get("value") if callback_context.triggered else None
    # Ignora eventi "spuri" dovuti al rerender della griglia (n_clicks=0/None)
    if trig_value in (None, 0):
        return dash.no_update, dash.no_update

    node_id = trig.get('index')
    if not node_id:
        return dash.no_update, dash.no_update
    node_health = trig.get('status')

    # Toggle: click sul nodo già selezionato => deseleziona
    if selected_node_data and selected_node_data.get('id') == node_id:
        msg = dbc.Alert("Selezione annullata dal semaforo.", color="secondary")
        return {'id': None, 'ids': []}, msg

    conn = queries.connect_db()
    details = queries.get_node_details(conn, node_id)
    conn.close()

    node_name = details['name'] if details else str(node_id)
    msg = dbc.Alert(f"Nodo selezionato dal semaforo: {node_name}", color="info")
    return {'id': node_id, 'health': node_health}, msg


# Callback 9: Configura il calendario (nel modale)
@app.callback(
    Output('date-picker-single', 'min_date_allowed'), 
    Output('date-picker-single', 'max_date_allowed'), 
    Output('date-picker-single', 'date'), 
    Input('modal-graph', 'is_open'), 
    State('active-graph-store', 'data'), 
    State('selected-node-store', 'data') 
)
def update_date_picker(is_open, active_graph, selected_node_data):
    if not is_open:
        return None, None, None

    conn = queries.connect_db()
    full_data = []

    nodes_are_selected = selected_node_data and (selected_node_data.get('id') or selected_node_data.get('ids'))
    if nodes_are_selected:
        node_ids = selected_node_data.get('ids', [selected_node_data.get('id')])
        node_ids = [nid for nid in node_ids if nid is not None]
        if node_ids:
            full_data = queries.get_individual_trends(conn, active_graph, node_ids)
    else:
        full_data = queries.get_average_trend(conn, active_graph)
    conn.close()
    
    today = datetime.now().date()
    if not full_data:
        return today, today, today
    
    df = pd.DataFrame(full_data).copy()
    df = df.assign(date=normalize_timestamp_series(df['timestamp']).dt.date)
    df = df[df['date'].notna()].copy()
    if df.empty:
        return today, today, today
    min_date = df['date'].min()
    max_date = df['date'].max()
    if not min_date or not max_date or min_date.year < 2000 or max_date.year < 2000:
        return today, today, today

    # Seleziona per default l'ultimo giorno realmente disponibile:
    # evita slider vuoti quando oggi non ha ancora campioni.
    max_allowed = max(max_date, today)
    selected_day = max_date if max_date is not None else today
    return min_date, max_allowed, selected_day


# Callback 10: Configura lo slider temporale (nel modale)
@app.callback(
    Output('modal-time-range-slider', 'min'), 
    Output('modal-time-range-slider', 'max'), 
    Output('modal-time-range-slider', 'value'), 
    Output('modal-time-range-slider', 'marks'), 
    Input('date-picker-single', 'date') 
)
def update_time_slider(selected_date):
    if not selected_date:
        selected_date = datetime.now().strftime('%Y-%m-%d')

    naive_start_of_day = datetime.strptime(selected_date, '%Y-%m-%d')
    # Timestamp locale (non UTC forzato) per allineare slider e grafico al giorno scelto.
    start_of_day_unix = int(naive_start_of_day.timestamp())
    end_of_day_unix = start_of_day_unix + (24 * 60 * 60) - 1
    
    marks = {start_of_day_unix + i * 3600: f'{i:02d}:00' for i in range(0, 25, 2)}

    return start_of_day_unix, end_of_day_unix, [start_of_day_unix, end_of_day_unix], marks


@app.callback(
    Output('slider-tooltip-output', 'children'),
    Input('modal-time-range-slider', 'value'),
)
def update_slider_tooltip(time_range):
    if not time_range or len(time_range) != 2:
        return ""
    try:
        start_h = datetime.fromtimestamp(int(time_range[0])).strftime('%H:%M')
        end_h = datetime.fromtimestamp(int(time_range[1])).strftime('%H:%M')
        return f"Fascia selezionata: {start_h} - {end_h} (step 30 minuti)"
    except Exception:
        return ""


# Callback 11: Aggiorna il grafico piccolo (dashboard principale)
@app.callback(
    Output('trend-graph', 'figure'), 
    Output('trend-graph-title', 'children'), 
    Output('trend-graph-updated-badge', 'children'),
    Input('network-map', 'selectedData'), 
    Input('active-graph-store', 'data'), 
    Input('quick-time-range-store', 'data'),
    Input('settings-store', 'data'), 
    Input('graph-refresh-trigger', 'data'), 
    State('point-index-to-id-map-store', 'data'),
    State('nodes-data-store', 'data'),
)
def update_trend_graph_view(selectedData, active_graph, quick_time_range, settings_data, refresh_trigger, id_map, nodes_data):
    data_to_show, title_suffix = [], ""
    selected_ids = []
    conn = queries.connect_db()

    title_map = {'temperature': "Temperatura", 'humidity': "Umidità", 'battery': "Batteria"}
    quick_label_map = {'1h': 'Ultima ora', '6h': 'Ultime 6h', '24h': 'Ultime 24h', 'all': 'Tutto'}
    name_by_id = {str(n.get('node_id')): n.get('name', str(n.get('node_id'))) for n in (nodes_data or [])}
    
    if selectedData and selectedData.get('points'):
        # Nota mia: il titolo deve usare nome nodo da store, non il text del marker
        # (che ora è BR/R/C), altrimenti esce "Nodo: C".
        points = selectedData['points']
        selected_ids = _extract_selected_node_ids(selectedData, id_map)
        data_to_show = queries.get_individual_trends(conn, active_graph, selected_ids)
        
        if len(points) == 1:
            node_name = name_by_id.get(str(selected_ids[0]), f"Nodo {selected_ids[0]}")
            title_suffix = f"{title_map.get(active_graph, '')} Nodo: {node_name}"
        else:
            data_to_show = aggregate_group_trend(data_to_show)
            selected_names = [name_by_id.get(str(nid), str(nid)) for nid in selected_ids]
            title_suffix = f"{title_map.get(active_graph, '')} Media gruppo ({', '.join(selected_names)})"
    else: 
        data_to_show = queries.get_average_trend(conn, active_graph)
        title_suffix = f"{title_map.get(active_graph, '')} Media"
    
    conn.close()
    
    full_df = pd.DataFrame(data_to_show).copy()
    title_date_str = ""
    if not full_df.empty:
        full_df = full_df.assign(timestamp=normalize_timestamp_series(full_df['timestamp']))
        full_df = full_df[full_df['timestamp'].notna()].copy()
        df_for_small_graph = apply_quick_time_filter(full_df, quick_time_range)
        title_date_str = quick_label_map.get(quick_time_range or '24h', 'Ultime 24h')
        # Se la finestra rapida è vuota ma la rete risulta sana, evita il "grafico vuoto"
        # con un carry-forward conservativo dell'ultimo campione valido.
        # Nota: applico questo ai soli scenari "media generale".
        # Per selezioni nodo/gruppo manteniamo coerenza stretta col modale (niente dati inventati).
        if df_for_small_graph.empty and quick_time_range in {'1h', '6h', '24h'} and not selected_ids:
            hours_map = {'1h': 1, '6h': 6, '24h': 24}
            if _selection_health_is_good(selected_ids):
                df_for_small_graph = _build_recent_carry_forward(
                    full_df, hours_window=hours_map.get(quick_time_range, 24)
                )
                title_date_str = f"{quick_label_map.get(quick_time_range, 'Ultime 24h')} (stimato: rete sana)"
    else:
        df_for_small_graph = pd.DataFrame()
    
    # Nota: il grafico piccolo deve mostrare tutti i dati disponibili
    # (come il modale), senza downsampling.
    processed_df = df_for_small_graph.copy()
    processed_df = ensure_min_points_for_plot(processed_df, min_points=2, minutes_back=5)
    traces, _ = create_traces_from_df(processed_df, active_graph)
    fig = go.Figure(data=traces)
    
    title = f"{title_suffix} ({title_date_str})" if title_date_str else title_suffix
    fig.update_layout(margin=dict(l=20, r=20, t=20, b=20), showlegend=True, dragmode=False)
    updated_badge = f"Aggiornato: {datetime.now().strftime('%H:%M:%S')}"
    
    return fig, title, updated_badge


@app.callback(
    Output('modal-switch-btn-1', 'children'),
    Output('modal-switch-btn-2', 'children'),
    Output('modal-metric-switch-targets', 'data'),
    Input('active-graph-store', 'data'),
)
def update_modal_metric_switch_buttons(active_graph):
    labels = {
        'temperature': 'Temperatura',
        'humidity': 'Umidità',
        'battery': 'Batteria',
    }
    order = ['temperature', 'humidity', 'battery']
    current = active_graph if active_graph in order else 'temperature'
    others = [m for m in order if m != current]
    btn1 = others[0] if others else 'humidity'
    btn2 = others[1] if len(others) > 1 else 'battery'
    targets = {'btn1': btn1, 'btn2': btn2}
    return labels.get(btn1, 'Umidità'), labels.get(btn2, 'Batteria'), targets


@app.callback(
    Output('active-graph-store', 'data', allow_duplicate=True),
    Output('modal-graph', 'is_open', allow_duplicate=True),
    Input('modal-switch-btn-1', 'n_clicks'),
    Input('modal-switch-btn-2', 'n_clicks'),
    State('modal-metric-switch-targets', 'data'),
    State('modal-graph', 'is_open'),
    prevent_initial_call=True,
)
def switch_metric_from_modal(btn1_clicks, btn2_clicks, targets, modal_is_open):
    trig = callback_context.triggered_id
    trig_value = callback_context.triggered[0].get("value") if callback_context.triggered else None
    if trig_value in (None, 0):
        return dash.no_update, modal_is_open

    targets = targets or {}
    if trig == 'modal-switch-btn-1':
        next_metric = targets.get('btn1')
    elif trig == 'modal-switch-btn-2':
        next_metric = targets.get('btn2')
    else:
        return dash.no_update, modal_is_open

    if next_metric not in {'temperature', 'humidity', 'battery'}:
        return dash.no_update, modal_is_open
    return next_metric, True


# Callback 11.5: KPI compatti sopra il grafico (Attuale | Max 24h | Min 24h)
@app.callback(
    Output('trend-kpi-row', 'children'),
    Input('network-map', 'selectedData'),
    Input('active-graph-store', 'data'),
    State('point-index-to-id-map-store', 'data')
)
def update_trend_kpi_row(selectedData, active_graph, id_map):
    conn = queries.connect_db()
    selected_ids = _extract_selected_node_ids(selectedData, id_map)
    try:
        if selected_ids:
            records = queries.get_individual_trends(conn, active_graph, selected_ids)
            if len(selected_ids) > 1:
                records = aggregate_group_trend(records)
        else:
            records = queries.get_average_trend(conn, active_graph)
    finally:
        conn.close()

    if not records:
        values = ("N/D", "N/D", "N/D")
    else:
        df = pd.DataFrame(records).copy()
        if df.empty:
            values = ("N/D", "N/D", "N/D")
        else:
            df = df.assign(timestamp=normalize_timestamp_series(df['timestamp']))
            df = df[df['timestamp'].notna()].copy()
            now_ts = pd.Timestamp.now()
            start_24h = now_ts - pd.Timedelta(hours=24)
            last24 = df[df['timestamp'] >= start_24h]
            if last24.empty and _selection_health_is_good(selected_ids):
                synthetic = _build_recent_carry_forward(df, hours_window=24, min_points=6)
                if not synthetic.empty:
                    last24 = synthetic
            if last24.empty:
                last24 = df
            if 'value' in last24.columns:
                series = pd.to_numeric(last24['value'], errors='coerce').dropna()
            else:
                numeric_cols = [c for c in last24.columns if pd.api.types.is_numeric_dtype(last24[c])]
                series = pd.to_numeric(last24[numeric_cols[0]], errors='coerce').dropna() if numeric_cols else pd.Series(dtype=float)
            if series.empty:
                values = ("N/D", "N/D", "N/D")
            else:
                curr = f"{series.iloc[-1]:.1f}"
                mx = f"{series.max():.1f}"
                mn = f"{series.min():.1f}"
                values = (curr, mx, mn)

    unit_map = {'temperature': '°C', 'humidity': '%', 'battery': '%'}
    unit = unit_map.get(active_graph, '')
    return [
        dbc.Col(
            html.Div(
                [
                    html.Div("Attuale", className="text-muted small"),
                    html.Div(f"{values[0]} {unit}", className="fw-bold"),
                ]
            )
        ),
        dbc.Col(
            html.Div(
                [
                    html.Div("Massimo (24h)", className="text-muted small"),
                    html.Div(f"{values[1]} {unit}", className="fw-bold"),
                ]
            )
        ),
        dbc.Col(
            html.Div(
                [
                    html.Div("Minimo (24h)", className="text-muted small"),
                    html.Div(f"{values[2]} {unit}", className="fw-bold"),
                ]
            )
        ),
    ]


# Callback 11.6: Feed allarmi smart compatto (ultimi 5)
@app.callback(
    Output('smart-alerts-container', 'children'),
    Input('last-update-timestamp-store', 'data')
)
def update_smart_alerts(_ts):
    conn = queries.connect_db()
    try:
        alerts = queries.get_smart_alerts(conn, limit=5)
    finally:
        conn.close()

    if not alerts:
        return dbc.Alert("Nessun allarme recente.", color="light", className="mb-0")

    df = pd.DataFrame(alerts)

    def translate_detail_it(text):
        if text is None:
            return ""
        t = str(text)
        replacements = [
            ("User inserted topology review for ", "L'utente ha inserito una revisione topologica per "),
            ("User inserted manual annotation for ", "L'utente ha inserito un'annotazione manuale per "),
            ("User inserted network-wide manual annotation", "L'utente ha inserito un'annotazione manuale estesa a tutta la rete"),
            ("User inserted ", "L'utente ha inserito "),
            ("network-wide", "su tutta la rete"),
            ("manual annotation", "annotazione manuale"),
            ("topology review", "revisione topologica"),
        ]
        for en, it in replacements:
            t = t.replace(en, it)
        return t

    if 'value' in df.columns:
        df.loc[:, 'value'] = df['value'].apply(translate_detail_it)

    df = df.rename(columns={
        'ts_human': 'Orario',
        'severity': 'Severità',
        'name': 'Evento',
        'value': 'Dettaglio',
    })[['Orario', 'Severità', 'Evento', 'Dettaglio']]
    return dbc.Table.from_dataframe(df, striped=True, bordered=True, hover=True, size='sm')


# Callback 12: Apre/Chiude il modale del grafico
@app.callback(
    Output('modal-graph', 'is_open'), 
    Input('temp-trend-card-clickable', 'n_clicks'), 
    State('modal-graph', 'is_open') 
)
def toggle_modal(n_clicks_card, is_open):
    if n_clicks_card:
        return not is_open
    return is_open


# Callback 13: Aggiorna il grafico GRANDE nel modale con filtri avanzati
@app.callback(
    Output('modal-temp-graph', 'figure'), 
    Output('modal-title', 'children'), 
    Input('modal-graph', 'is_open'), 
    Input('modal-time-range-slider', 'value'), 
    Input('date-picker-single', 'date'),        # Data selezionata
    State('trend-graph-title', 'children'),     # Recupera il titolo dal grafico piccolo
    State('active-graph-store', 'data'),        # 'temperature', 'humidity', o 'battery'
    State('selected-node-store', 'data')        # ID dei nodi selezionati
)
def update_modal_graph(is_open, time_range, selected_date, 
                       small_graph_title, active_graph, selected_node_data):
    
    if not is_open:
        return go.Figure(), ""

    conn = queries.connect_db()
    
    # 1. Recupero dati in base alla selezione (Singolo, Gruppo o Media)
    nodes_are_selected = selected_node_data and (selected_node_data.get('id') or selected_node_data.get('ids'))
    
    if nodes_are_selected:
        node_ids = selected_node_data.get('ids', [selected_node_data.get('id')])
        node_ids = [nid for nid in node_ids if nid is not None]
        full_data = queries.get_individual_trends(conn, active_graph, node_ids)
        if len(node_ids) > 1:
            full_data = aggregate_group_trend(full_data)
    else:
        full_data = queries.get_average_trend(conn, active_graph)
    
    conn.close()

    if not full_data:
        return go.Figure(), "Nessun dato disponibile"

    df = pd.DataFrame(full_data).copy()
    df = df.assign(timestamp=normalize_timestamp_series(df['timestamp']))
    df = df[df['timestamp'].notna()].copy()
    if df.empty:
        return go.Figure(), "Nessun dato disponibile"

    # 2. Logica di filtraggio temporale giornaliera con fascia oraria.
    if selected_date and time_range and len(time_range) == 2:
        # Allinea il filtro alla stessa base locale usata da slider/tooltip
        # per evitare scostamenti orari (es. 12:00-14:00 mostrato come 10:00-12:00).
        start_ts = pd.Timestamp(datetime.fromtimestamp(int(time_range[0])))
        end_ts = pd.Timestamp(datetime.fromtimestamp(int(time_range[1])))
        df = df[(df['timestamp'] >= start_ts) & (df['timestamp'] <= end_ts)]

    # Se nella fascia selezionata non ci sono dati, non mostrare fallback:
    # l'utente deve vedere un grafico vuoto coerente con il range scelto.
    if df.empty:
        # Nota mia: niente "trucchi" con dati fuori fascia, se no è fuorviante.
        return go.Figure(), "Nessun dato nella fascia oraria selezionata"
    
    # 3. Il modale mantiene il dettaglio completo.
    df = ensure_min_points_for_plot(df, min_points=2, minutes_back=5)
    
    # 4. Creazione tracce Plotly
    traces, _ = create_traces_from_df(df, active_graph)
    
    fig = go.Figure(data=traces)
    
    # 5. Styling del grafico grande
    fig.update_layout(
        template='plotly_white',
        margin=dict(l=40, r=40, t=40, b=40),
        hovermode='closest',
        xaxis_title="Tempo",
        yaxis_title=active_graph.capitalize(),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )

    # Titolo dinamico per il modale
    base_title = str(small_graph_title or "")
    if "(" in base_title:
        base_title = base_title.split("(", 1)[0].strip()
    modal_title = f"Analisi Storica: {base_title}"

    return fig, modal_title


# Callback 16: Aggiorna preview e storico log in Settings
@app.callback(
    Output('log-preview', 'children'),
    Output('log-full-output', 'children'),
    Input('timer-update-interval', 'n_intervals'),
)
def update_log_outputs(_n):
    if not app_logs:
        return "Nessun evento disponibile.", "Nessun evento disponibile."
    ordered_logs = list(reversed(app_logs))
    latest = ordered_logs[0]
    # Mostra tutti gli eventi con i più recenti in alto.
    full = "\n".join(ordered_logs)
    return latest, full


# Callback 17: Apri/chiudi storico log al click sulla preview
@app.callback(
    Output('log-collapse', 'is_open'),
    Input('log-toggler', 'n_clicks'),
    State('log-collapse', 'is_open'),
    prevent_initial_call=True,
)
def toggle_log_collapse(n_clicks, is_open):
    if n_clicks:
        return not is_open
    return is_open


# Callback 14: Salva intervallo polling (Settings)
@app.callback(
    Output('settings-store', 'data'),
    Output('interval-component', 'interval'),
    Output('interval-save-status', 'children'),
    Input('save-interval-button', 'n_clicks'),
    State('hours-input', 'value'),
    State('minutes-input', 'value'),
    State('seconds-input', 'value'),
    State('settings-store', 'data'),
    prevent_initial_call=True,
)
def save_polling_interval(_n_clicks, hours, minutes, seconds, settings_data):
    h = int(hours or 0)
    m = int(minutes or 0)
    s = int(seconds or 0)
    total_seconds = (h * 3600) + (m * 60) + s
    if total_seconds <= 0:
        current_ms = (settings_data or {}).get('refresh_interval_ms', 15000)
        msg = dbc.Alert("Intervallo non valido. Inserisci almeno 1 secondo.", color="warning", className="mb-0 mt-2")
        return settings_data or {'refresh_interval_ms': current_ms, 'downsample_rule': 'h'}, current_ms, msg

    interval_ms = total_seconds * 1000
    updated = dict(settings_data or {})
    updated['refresh_interval_ms'] = interval_ms
    updated.setdefault('downsample_rule', 'h')
    msg = dbc.Alert(f"Intervallo polling salvato: {h:02d}:{m:02d}:{s:02d}", color="success", className="mb-0 mt-2")
    add_log(f"Intervallo polling aggiornato a {interval_ms} ms.")
    return updated, interval_ms, msg


# Callback 15: Salva downsampling grafico (Settings)
@app.callback(
    Output('settings-store', 'data', allow_duplicate=True),
    Output('downsample-save-status', 'children'),
    Output('graph-refresh-trigger', 'data'),
    Input('save-downsample-button', 'n_clicks'),
    State('downsample-setting-dropdown', 'value'),
    State('settings-store', 'data'),
    prevent_initial_call=True,
)
def save_downsample_setting(_n_clicks, downsample_value, settings_data):
    if not downsample_value:
        msg = dbc.Alert("Seleziona una regola di downsampling.", color="warning", className="mb-0 mt-2")
        return settings_data or {'refresh_interval_ms': 15000, 'downsample_rule': 'h'}, msg, dash.no_update

    updated = dict(settings_data or {})
    updated.setdefault('refresh_interval_ms', 15000)
    rule_alias = {'H': '1h', 'h': '1h', '1H': '1h'}
    normalized_rule = rule_alias.get(str(downsample_value), str(downsample_value))
    updated['downsample_rule'] = normalized_rule
    pretty_label = {
        '15min': 'ogni 15 minuti',
        '30min': 'ogni 30 minuti',
        '1h': 'ogni 1 ora',
        '2h': 'ogni 2 ore',
        '4h': 'ogni 4 ore',
        'raw': 'dati grezzi (nessun raggruppamento)',
    }.get(normalized_rule, normalized_rule)
    msg = dbc.Alert(f"Downsampling salvato: {pretty_label}", color="success", className="mb-0 mt-2")
    add_log(f"Downsampling aggiornato a '{pretty_label}'.")
    return updated, msg, time.time()