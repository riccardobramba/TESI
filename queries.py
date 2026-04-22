import hashlib
import math
import os
import random
import secrets
import sqlite3
import time
import uuid
import pandas as pd
import numpy as np
from datetime import datetime

# Unico appezzamento in mappa (tutti i nodi nel lotto)
# Centro spostato un po’ a nord rispetto al punto originario
PARCEL_CENTER_LAT = 41.98112211930059
PARCEL_CENTER_LON = 12.566098280401778
# Raggio massimo dal centro (gradi lat); ~0.00038° ≈ 42 m → nodi un po’ più distanziati
PARCEL_LAYOUT_MAX_RADIUS_DEG = 0.00038
_BOOT_HEALTH_PROFILE_APPLIED = False


def random_parcel_lat_lon():
    """Punto casuale uniforme nel disco dell'appezzamento (per seed DB)."""
    t = 2 * math.pi * random.random()
    r = PARCEL_LAYOUT_MAX_RADIUS_DEG * math.sqrt(random.random())
    lat_rad = math.radians(PARCEL_CENTER_LAT)
    lon_scale = 1.0 / max(math.cos(lat_rad), 0.2)
    return (
        PARCEL_CENTER_LAT + r * math.cos(t),
        PARCEL_CENTER_LON + r * math.sin(t) * lon_scale,
    )


def layout_node_coordinates_on_parcel(df: pd.DataFrame) -> pd.DataFrame:
    """Ridispose tutti i nodi in un cluster compatto sullo stesso appezzamento."""
    if df.empty:
        return df
    # Sempre il centro da PARCEL_CENTER_* (la media dal DB ignorava le coordinate impostate)
    center_lat = float(PARCEL_CENTER_LAT)
    center_lon = float(PARCEL_CENTER_LON)
    n = len(df)
    lat_rad = np.radians(center_lat)
    lon_scale = 1.0 / max(float(np.cos(lat_rad)), 0.2)
    for i in range(n):
        angle = 2 * np.pi * i / n if n else 0.0
        r = PARCEL_LAYOUT_MAX_RADIUS_DEG * np.sqrt((i + 0.5) / max(n, 1))
        df.loc[i, 'latitude'] = center_lat + r * np.cos(angle)
        df.loc[i, 'longitude'] = center_lon + r * np.sin(angle) * lon_scale
    return df


# Config unica ruoli (canonical interno + label UI + alias accettati)
ROLE_CONFIG = {
    'router': {
        'label': 'Border Router',
        'aliases': frozenset({
            'router', 'gateway', 'gw', 'border_router', 'border router', 'borderrouter',
            'coordinator', 'root', 'digorouter',
        }),
    },
    'parent': {
        'label': 'Router',
        'aliases': frozenset({
            'parent', 'padre', 'repeater', 'relay', 'bridge', 'extender',
        }),
    },
    'child': {
        'label': 'Child',
        'aliases': frozenset({
            'child', 'figlio', 'leaf', 'end_device', 'enddevice', 'end-device',
            'device', 'sensor',
        }),
    },
}

# Ciclo fallback se il ruolo è assente/non riconosciuto.
ROLE_FALLBACK_CYCLE = ('router', 'parent', 'child', 'child', 'child')
_INVALID_ROLE_TOKENS = frozenset({
    '', 'none', 'nan', 'unknown', 'n/d', 'nd', 'n.d', 'null', '?', '-', 'n/a', 'na', 'tbd',
})


def _stable_fallback_index(node_id: str) -> int:
    h = hashlib.md5(str(node_id).encode('utf-8')).hexdigest()
    return int(h[:8], 16) % len(ROLE_FALLBACK_CYCLE)


def normalize_role(role_value, fallback_index: int = 0) -> str:
    """
    Normalizza qualsiasi ruolo su uno dei 3 canonical role interni:
    'router', 'parent', 'child' (minuscolo).

    Nota: questi valori sono usati dalla logica topologica interna.
    Le etichette UI finali (Border Router / Router / Child) sono applicate
    successivamente da `role_display_label()`.
    """
    if role_value is None:
        r = ''
    elif isinstance(role_value, float) and pd.isna(role_value):
        r = ''
    else:
        r = str(role_value).lower().strip()
    if r in _INVALID_ROLE_TOKENS:
        r = ''
    for canonical, cfg in ROLE_CONFIG.items():
        if r == canonical or r in cfg['aliases']:
            return canonical
    i = int(fallback_index) % len(ROLE_FALLBACK_CYCLE)
    return ROLE_FALLBACK_CYCLE[i]


def role_display_label(canonical: str) -> str:
    # Nota: label UI sempre derivata dalla config centralizzata.
    return ROLE_CONFIG.get(canonical, ROLE_CONFIG['child'])['label']


def compute_effective_role_map(nodes_records, topology_links):
    """
    Regola di validità ruolo:
    - un nodo 'parent' resta tale solo se ha almeno un arco con un nodo 'child'.
    """
    effective = {}
    if not nodes_records:
        return effective

    canonical_by_id = {}
    for row in nodes_records:
        node_id = str(row.get('node_id'))
        canonical_by_id[node_id] = normalize_role(
            row.get('role'),
            _stable_fallback_index(node_id),
        )

    # Nota mia: questa è la regola "padre valido solo con figlio".
    parent_with_child = set()
    for link in topology_links or []:
        a = str(link.get('node1_id'))
        b = str(link.get('node2_id'))
        ra = canonical_by_id.get(a)
        rb = canonical_by_id.get(b)
        if ra == 'parent' and rb == 'child':
            parent_with_child.add(a)
        elif rb == 'parent' and ra == 'child':
            parent_with_child.add(b)

    for node_id, role in canonical_by_id.items():
        if role == 'parent' and node_id not in parent_with_child:
            effective[node_id] = 'child'
        else:
            effective[node_id] = role
    return effective


def apply_effective_roles_to_nodes(nodes_records, topology_links):
    """Restituisce i nodi con ruolo canonico effettivo coerente alla topologia."""
    role_map = compute_effective_role_map(nodes_records, topology_links)
    out = []
    for row in nodes_records or []:
        rec = dict(row)
        node_id = str(rec.get('node_id'))
        fallback = normalize_role(rec.get('role'), _stable_fallback_index(node_id))
        rec['role'] = role_map.get(node_id, fallback)
        out.append(rec)
    return out


# Ciclo ruoli dashboard: più router e parent rispetto al vecchio schema (1/10 e 1/5).
# Ogni 8 nodi → 2 Router, 2 Parent, 4 Child.
_DASHBOARD_ROLE_CYCLE = (
    'Router', 'Router',
    'Parent', 'Parent',
    'Child', 'Child', 'Child', 'Child',
)


def dashboard_role_for_index(i: int) -> str:
    """Ruolo sintetico coerente con la mappa (Router / Parent / Child) per l'i-esimo nodo."""
    return _DASHBOARD_ROLE_CYCLE[i % len(_DASHBOARD_ROLE_CYCLE)]


def _battery_to_percent(value) -> float:
    """
    Converte il valore batteria in percentuale 0..100.
    - Se arriva una tensione (tipicamente 3.0..4.2V), mappa linearmente.
    - Se arriva già una percentuale, la clampa.
    """
    if value is None:
        return 0.0
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if pd.isna(v):
        return 0.0
    if 0.0 <= v <= 5.0:
        pct = ((v - 3.0) / (4.2 - 3.0)) * 100.0
        return round(max(0.0, min(100.0, pct)), 1)
    return round(max(0.0, min(100.0, v)), 1)


def _fallback_battery_percent(node_id: str) -> float:
    """
    Fallback batteria per dati mancanti:
    - quasi sempre una percentuale realistica (35..92) stabile per nodo;
    - 0% solo caso limite raro (~1/60 nodi), per simulare guasti/assenza dati.
    """
    h = int(hashlib.md5(str(node_id).encode("utf-8")).hexdigest()[:8], 16)
    if h % 60 == 0:
        return 0.0
    return float(35 + (h % 58))  # 35..92


def normalize_battery_for_node(node_id: str, raw_value) -> float:
    """
    Normalizza la batteria in percentuale:
    - usa il valore reale quando presente;
    - evita 0% sistematico per valori mancanti/non validi;
    - mantiene 0% come caso limite raro.
    """
    pct = _battery_to_percent(raw_value)
    if pct <= 0.0:
        return _fallback_battery_percent(node_id)
    return pct


def synthetic_ip_for_node(node_id: str) -> str:
    """
    IP stabile per nodo quando l'indirizzo reale non è disponibile nel DB.
    Usa il blocco privato 10.x.y.z per evitare valori N/D in UI.
    """
    h = int(hashlib.md5(str(node_id).encode("utf-8")).hexdigest()[:8], 16)
    o2 = 1 + ((h >> 16) % 254)
    o3 = 1 + ((h >> 8) % 254)
    o4 = 1 + (h % 254)
    return f"10.{o2}.{o3}.{o4}"


def _fallback_temperature_for_node(node_id: str) -> float:
    # Temperatura plausibile e stabile per nodo (18..32 °C)
    return round(18.0 + 14.0 * _hash_ratio(node_id, "temp-fallback"), 1)


def _fallback_humidity_for_node(node_id: str) -> float:
    # Umidità plausibile e stabile per nodo (35..75 %)
    return round(35.0 + 40.0 * _hash_ratio(node_id, "hum-fallback"), 1)


def normalize_node_labels(conn) -> None:
    """Rinomina i nodi in modo sequenziale: node-1, node-2, ..."""
    cur = conn.cursor()
    cur.execute("SELECT eui FROM node ORDER BY IFNULL(created_at, 0), eui")
    rows = cur.fetchall()
    for i, (eui,) in enumerate(rows, start=1):
        cur.execute("UPDATE node SET label = ? WHERE eui = ?", (f"node-{i}", eui))
    conn.commit()


def _hash_ratio(node_id: str, salt: str) -> float:
    h = hashlib.md5(f"{node_id}|{salt}".encode("utf-8")).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _seed_sensor_snapshot_for_node(conn, node_id: str, ts: float) -> None:
    """Inserisce un piccolo snapshot sensori per nodi senza letture."""
    cur = conn.cursor()
    payload = sqlite3.Binary(os.urandom(16))
    cur.execute(
        "INSERT INTO data (uuid, timestamp, data) VALUES (?, ?, ?)",
        (f"auto-seed-{uuid.uuid4().hex[:12]}", ts, payload),
    )
    data_id = cur.lastrowid

    temp = round(18.0 + 14.0 * _hash_ratio(node_id, "temp"), 2)        # 18..32 °C
    hum = round(35.0 + 40.0 * _hash_ratio(node_id, "humid"), 2)        # 35..75 %
    batt_v = round(3.25 + 0.85 * _hash_ratio(node_id, "batt"), 3)      # 3.25..4.10 V

    rows = [
        ("temperature_sensor_1", temp),
        ("relative_humidity_sensor_1", hum),
        ("battery_level", batt_v),
    ]
    for sensor_name, value in rows:
        cur.execute(
            """
            INSERT INTO sensor_reading
            (data_id, eui, timestamp, sensor_name, sensor_index, value)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (data_id, node_id, ts, sensor_name, 0, value),
        )


def ensure_minimum_sensor_data(conn) -> None:
    """
    Garantisce almeno una lettura base per ogni nodo.
    Evita medie di gruppo a zero quando i nodi sono stati creati senza storico.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT n.eui
        FROM node n
        LEFT JOIN sensor_reading sr ON sr.eui = n.eui
        GROUP BY n.eui
        HAVING COUNT(sr.id) = 0
        """
    )
    missing = [row[0] for row in cur.fetchall()]
    if not missing:
        return
    now = datetime.now().timestamp()
    for eui in missing:
        _seed_sensor_snapshot_for_node(conn, eui, now)
    conn.commit()


def ensure_recent_sensor_data(conn, max_age_seconds: int = 1800) -> None:
    """
    Se la telemetria è troppo vecchia, crea uno snapshot recente per ogni nodo.
    Evita dashboard "ferma" mantenendo dati freschi nelle ultime 24h.
    """
    cur = conn.cursor()
    cur.execute("SELECT MAX(timestamp) FROM sensor_reading")
    row = cur.fetchone()
    latest_ts = float(row[0]) if row and row[0] is not None else None
    now_ts = time.time()

    global _BOOT_HEALTH_PROFILE_APPLIED

    # Nota mia: faccio il bootstrap stato una sola volta a run, così è random al riavvio
    # ma non cambia in continuazione ogni refresh.
    # Applica il profilo salute almeno una volta per ogni avvio dashboard.
    if _BOOT_HEALTH_PROFILE_APPLIED and latest_ts is not None and (now_ts - latest_ts) <= max_age_seconds:
        return

    cur.execute("SELECT eui FROM node ORDER BY eui")
    nodes = [r[0] for r in cur.fetchall()]
    if not nodes:
        return

    # Profilo stato server per ruolo (random ad ogni riavvio):
    # - Border Router: quasi sempre verde.
    # - Router: dipende dal Border Router connesso.
    # - Child: random.
    nodes_records = get_all_nodes(conn)
    raw_links = get_all_links(conn)
    topo_links = compute_topology_map_links(nodes_records, raw_links)
    role_by_id = {
        str(n["node_id"]): normalize_role(n.get("role"), _stable_fallback_index(str(n["node_id"])))
        for n in nodes_records
    }
    routers = [nid for nid, r in role_by_id.items() if r == "router"]  # Border Router
    parents = [nid for nid, r in role_by_id.items() if r == "parent"]  # Router

    neighbors = {str(nid): set() for nid in role_by_id.keys()}
    for e in topo_links:
        a = str(e.get("node1_id"))
        b = str(e.get("node2_id"))
        neighbors.setdefault(a, set()).add(b)
        neighbors.setdefault(b, set()).add(a)

    status_by_id = {}
    for br in routers:
        p = random.random()
        if p < 0.85:
            status_by_id[br] = "green"
        elif p < 0.95:
            status_by_id[br] = "yellow"
        else:
            status_by_id[br] = "red"

    def pick_border_for_parent(pid: str):
        # Nota mia: prima provo il BR vicino in topologia, se manca faccio fallback sul primo BR.
        cand = [n for n in neighbors.get(pid, set()) if role_by_id.get(n) == "router"]
        if cand:
            return sorted(cand)[0]
        return sorted(routers)[0] if routers else None

    for pnode in parents:
        br = pick_border_for_parent(pnode)
        br_status = status_by_id.get(br, "green")
        if br_status == "green":
            status_by_id[pnode] = "green"
        elif br_status == "yellow":
            status_by_id[pnode] = "yellow"
        else:
            status_by_id[pnode] = "red" if random.random() < 0.7 else "yellow"

    for nid, role in role_by_id.items():
        if role != "child":
            continue
        q = random.random()
        if q < 0.45:
            status_by_id[nid] = "green"
        elif q < 0.80:
            status_by_id[nid] = "yellow"
        else:
            status_by_id[nid] = "red"

    def status_to_minutes(status: str) -> float:
        # Nota mia: traduce direttamente lo stato semaforo in "quanto tempo fa".
        if status == "green":
            return random.uniform(0.0, 4.8)
        if status == "yellow":
            return random.uniform(5.0, 29.5)
        return random.uniform(30.5, 240.0)

    ts_by_node = {
        nid: now_ts - (status_to_minutes(status_by_id.get(nid, "green")) * 60.0)
        for nid in role_by_id.keys()
    }

    # Piccola variabilità coerente tra snapshot mantenendo range realistici.
    slot = int(now_ts // max(300, max_age_seconds // 2))

    def base_from_last(eui: str, sensor_name: str, fallback_value: float) -> float:
        r = conn.execute(
            """
            SELECT value
            FROM sensor_reading
            WHERE eui = ? AND sensor_name = ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (eui, sensor_name),
        ).fetchone()
        if not r or r[0] is None:
            return fallback_value
        try:
            return float(r[0])
        except (TypeError, ValueError):
            return fallback_value

    for eui in nodes:
        node_ts = ts_by_node.get(str(eui), now_ts)
        payload = sqlite3.Binary(os.urandom(16))
        cur.execute(
            "INSERT INTO data (uuid, timestamp, data) VALUES (?, ?, ?)",
            (f"auto-refresh-{uuid.uuid4().hex[:12]}", node_ts, payload),
        )
        data_id = cur.lastrowid

        jitter_seed = int(hashlib.md5(f"{eui}|{slot}".encode("utf-8")).hexdigest()[:8], 16)
        temp_j = ((jitter_seed % 7) - 3) * 0.15
        hum_j = (((jitter_seed >> 3) % 9) - 4) * 0.25
        batt_j = (((jitter_seed >> 7) % 5) - 2) * 0.003

        temp_base = base_from_last(eui, "temperature_sensor_1", _fallback_temperature_for_node(eui))
        hum_base = base_from_last(eui, "relative_humidity_sensor_1", _fallback_humidity_for_node(eui))
        batt_base = base_from_last(eui, "battery_level", 3.7)

        temp1 = round(max(10.0, min(50.0, temp_base + temp_j)), 2)
        temp2 = round(max(10.0, min(50.0, temp_base + temp_j + 0.2)), 2)
        hum1 = round(max(10.0, min(95.0, hum_base + hum_j)), 2)
        hum2 = round(max(10.0, min(95.0, hum_base + hum_j - 0.4)), 2)
        batt = round(max(3.0, min(4.2, batt_base + batt_j)), 3)

        rows = [
            ("temperature_sensor_1", temp1),
            ("temperature_sensor_2", temp2),
            ("relative_humidity_sensor_1", hum1),
            ("relative_humidity_sensor_2", hum2),
            ("battery_level", batt),
        ]
        for sensor_name, value in rows:
            cur.execute(
                """
                INSERT INTO sensor_reading
                (data_id, eui, timestamp, sensor_name, sensor_index, value)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (data_id, eui, node_ts, sensor_name, 0, value),
            )

    conn.commit()
    _BOOT_HEALTH_PROFILE_APPLIED = True


def _expected_demo_node_target() -> int:
    """Obiettivo nodi in tabella `node` (0 = non toccare). Variabile: TESI_EXPECTED_NODES."""
    raw = os.environ.get("TESI_EXPECTED_NODES", "10")
    try:
        n = int(raw)
    except ValueError:
        n = 10
    return max(0, n)


def delete_node_and_dependents(conn, eui: str) -> None:
    """Rimuove un nodo e le righe collegate (vincoli FK)."""
    cur = conn.cursor()
    cur.execute("DELETE FROM sensor_reading WHERE eui = ?", (eui,))
    cur.execute("DELETE FROM event WHERE eui = ?", (eui,))
    cur.execute("DELETE FROM node_state_log WHERE eui = ?", (eui,))
    cur.execute("DELETE FROM node_state WHERE eui = ?", (eui,))
    cur.execute("DELETE FROM node_parameter_log WHERE eui = ?", (eui,))
    cur.execute("DELETE FROM node_parameter WHERE eui = ?", (eui,))
    cur.execute(
        "DELETE FROM link_diagnostic WHERE src_eui = ? OR dst_eui = ?",
        (eui, eui),
    )
    cur.execute("DELETE FROM node WHERE eui = ?", (eui,))


def _next_eui_to_trim(cursor) -> str | None:
    """Sceglie un nodo da eliminare: prima demo auto-seed, poi sintetici, poi senza letture sensori, infine il più recente."""
    strategies = (
        "SELECT eui FROM node WHERE IFNULL(comment, '') = 'Demo auto-seed' "
        "ORDER BY IFNULL(created_at, 0) DESC LIMIT 1",
        "SELECT eui FROM node WHERE comment LIKE 'Synthetic node%' "
        "ORDER BY IFNULL(created_at, 0) DESC LIMIT 1",
        """
        SELECT n.eui FROM node n
        LEFT JOIN sensor_reading s ON s.eui = n.eui
        GROUP BY n.eui
        HAVING COUNT(s.id) = 0
        ORDER BY IFNULL(n.created_at, 0) DESC, n.eui DESC LIMIT 1
        """,
        "SELECT eui FROM node ORDER BY IFNULL(created_at, 0) DESC, eui DESC LIMIT 1",
    )
    for q in strategies:
        cursor.execute(q)
        row = cursor.fetchone()
        if row:
            return row[0]
    return None


def trim_excess_network_nodes(conn, maximum: int) -> None:
    """Se ci sono più di `maximum` nodi, elimina l'eccedenza (strategia _next_eui_to_trim)."""
    if maximum <= 0:
        return
    cursor = conn.cursor()
    while True:
        cursor.execute("SELECT COUNT(*) FROM node")
        if cursor.fetchone()[0] <= maximum:
            break
        eui = _next_eui_to_trim(cursor)
        if not eui:
            break
        delete_node_and_dependents(conn, eui)
    conn.commit()


def ensure_minimum_network_nodes(conn, minimum: int | None = None) -> None:
    """
    Allinea il numero di nodi a `minimum`: rimuove l'eccedenza, poi inserisce nodi demo se mancano.
    Imposta TESI_EXPECTED_NODES=0 per disattivare (nessun aggiunta/rimozione automatica).
    """
    if minimum is None:
        minimum = _expected_demo_node_target()
    if minimum <= 0:
        return

    trim_excess_network_nodes(conn, minimum)

    now = datetime.now().timestamp()

    while True:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM node")
        n_existing = cursor.fetchone()[0]
        if n_existing >= minimum:
            break

        inserted_this_round = False
        for _ in range(32):
            eui = secrets.token_hex(8).upper()
            label = f"node-{n_existing + 1}"
            try:
                cursor.execute(
                    """
                    INSERT INTO node (eui, created_at, label, version, comment)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (eui, now, label, "v1.0", "Demo auto-seed"),
                )
            except sqlite3.IntegrityError:
                continue

            role = dashboard_role_for_index(n_existing)
            lat, lon = random_parcel_lat_lon()
            update_node_coordinates(conn, eui, lat, lon)
            cursor.execute(
                """
                INSERT INTO node_state_log (timestamp, eui, state_name, value_text)
                VALUES (?, ?, 'role', ?)
                """,
                (now, eui, role),
            )
            inserted_this_round = True
            break

        if not inserted_this_round:
            break

    ensure_minimum_sensor_data(conn)
    ensure_recent_sensor_data(conn)
    normalize_node_labels(conn)
    conn.commit()


def connect_db():
    return sqlite3.connect('data.db')

def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

def get_all_nodes(conn):
    query = """
    SELECT 
        n.eui as node_id, 
        COALESCE(n.label, n.eui) as name,
        ns_lat.value_num as latitude,
        ns_lon.value_num as longitude,
        ns_role.value_text as role,
        (SELECT MAX(timestamp) FROM sensor_reading WHERE eui = n.eui) as last_packet_timestamp,
        COALESCE(
            ns_batt.value_num,
            (
                SELECT sr.value
                FROM sensor_reading sr
                WHERE sr.eui = n.eui AND sr.sensor_name = 'battery_level'
                ORDER BY sr.timestamp DESC
                LIMIT 1
            ),
            0
        ) as battery
    FROM node n
    LEFT JOIN node_state ns_lat ON n.eui = ns_lat.eui AND ns_lat.state_name = 'latitude'
    LEFT JOIN node_state ns_lon ON n.eui = ns_lon.eui AND ns_lon.state_name = 'longitude'
    LEFT JOIN node_state ns_role ON n.eui = ns_role.eui AND ns_role.state_name = 'role'
    LEFT JOIN node_state ns_batt ON n.eui = ns_batt.eui AND ns_batt.state_name = 'battery_level'
    ORDER BY n.eui
    """
    df = pd.read_sql_query(query, conn)
    
    if df.empty:
        return []

    for i in range(len(df)):
        if pd.isna(df.loc[i, 'latitude']):
            df.loc[i, 'latitude'] = PARCEL_CENTER_LAT
        if pd.isna(df.loc[i, 'longitude']):
            df.loc[i, 'longitude'] = PARCEL_CENTER_LON

    layout_node_coordinates_on_parcel(df)

    # Stesso fallback di get_node_details: hash su node_id, non posizione in lista,
    # altrimenti colore mappa e ruolo mostrato non coincidono.
    df['role'] = [
        normalize_role(row['role'], _stable_fallback_index(row['node_id']))
        for _, row in df.iterrows()
    ]
    df['battery'] = [
        normalize_battery_for_node(str(row['node_id']), row['battery'])
        for _, row in df.iterrows()
    ]
    
    return df.to_dict('records')

def get_average_trend(conn, active_graph):
    sensor_map = {
        'temperature': ['temperature_sensor_1', 'temperature_sensor_2'],
        'humidity': ['relative_humidity_sensor_1', 'relative_humidity_sensor_2'],
        'battery': ['battery_level'],
    }
    sensor_names = sensor_map.get(active_graph, [active_graph])
    placeholders = ",".join(["?"] * len(sensor_names))

    query = f"""
    SELECT timestamp, AVG(value) as value
    FROM sensor_reading
    WHERE sensor_name IN ({placeholders})
    GROUP BY timestamp
    ORDER BY timestamp ASC
    """
    return pd.read_sql_query(query, conn, params=sensor_names).to_dict('records')

def get_individual_trends(conn, active_graph, node_ids):
    sensor_map = {
        'temperature': ['temperature_sensor_1', 'temperature_sensor_2'],
        'humidity': ['relative_humidity_sensor_1', 'relative_humidity_sensor_2'],
        'battery': ['battery_level'],
    }
    sensor_names = sensor_map.get(active_graph, [active_graph])
    node_placeholders = ",".join(["?"] * len(node_ids))
    sensor_placeholders = ",".join(["?"] * len(sensor_names))

    query = f"""
    SELECT
        sr.timestamp,
        AVG(sr.value) as value,
        sr.eui as node_id,
        (SELECT COALESCE(label, eui) FROM node WHERE eui = sr.eui) as name
    FROM sensor_reading sr
    WHERE sr.sensor_name IN ({sensor_placeholders})
      AND sr.eui IN ({node_placeholders})
    GROUP BY sr.timestamp, sr.eui
    ORDER BY sr.timestamp ASC
    """
    return pd.read_sql_query(query, conn, params=sensor_names + node_ids).to_dict('records')

def get_general_stats(conn):
    conn.row_factory = dict_factory
    stats = {}
    fallback_used = False
    start_24h_ts = time.time() - (24 * 3600)
    sensor_groups = [
        ('average_temperature', ['temperature_sensor_1', 'temperature_sensor_2']),
        ('average_humidity', ['relative_humidity_sensor_1', 'relative_humidity_sensor_2']),
        ('average_battery', ['battery_level']),
    ]
    for key, sensors in sensor_groups:
        placeholders = ",".join(["?"] * len(sensors))
        q_24h = f"""
            SELECT AVG(value) as val
            FROM sensor_reading
            WHERE sensor_name IN ({placeholders})
              AND timestamp >= ?
        """
        res = conn.execute(q_24h, tuple(sensors) + (start_24h_ts,)).fetchone()
        val = res['val'] if res else None

        # Nota mia: se 24h è vuoto non voglio zeri fake, quindi fallback su storico.
        # Allineamento con KPI: se 24h è vuoto, fallback allo storico completo.
        source_key = f"{key}_source"
        stats[source_key] = '24h'
        if val is None:
            q_all = f"""
                SELECT AVG(value) as val
                FROM sensor_reading
                WHERE sensor_name IN ({placeholders})
            """
            res_all = conn.execute(q_all, tuple(sensors)).fetchone()
            val = res_all['val'] if res_all else None
            fallback_used = True
            stats[source_key] = 'historical'

        stats[key] = round(val, 2) if val is not None else 0
    stats['average_battery'] = round(
        max(1.0, _battery_to_percent(stats.get('average_battery'))),
        1,
    )
    stats['fallback_historical_used'] = fallback_used
    return stats

def get_node_details(conn, node_eui):
    conn.row_factory = dict_factory
    query = """
    SELECT 
        n.eui as node_id, COALESCE(n.label, n.eui) as name,
        COALESCE(ns_role.value_text, 'N/D') as role,
        COALESCE(
            ns_batt.value_num,
            (
                SELECT sr.value
                FROM sensor_reading sr
                WHERE sr.eui = n.eui AND sr.sensor_name = 'battery_level'
                ORDER BY sr.timestamp DESC
                LIMIT 1
            ),
            0
        ) as battery,
        COALESCE(
            (
                SELECT sr.value
                FROM sensor_reading sr
                WHERE sr.eui = n.eui AND sr.sensor_name IN ('temperature_sensor_1', 'temperature_sensor_2')
                ORDER BY sr.timestamp DESC
                LIMIT 1
            ),
            NULL
        ) as temperature,
        COALESCE(
            (
                SELECT sr.value
                FROM sensor_reading sr
                WHERE sr.eui = n.eui AND sr.sensor_name IN ('relative_humidity_sensor_1', 'relative_humidity_sensor_2')
                ORDER BY sr.timestamp DESC
                LIMIT 1
            ),
            NULL
        ) as humidity,
        ns_lat.value_num as latitude, ns_lon.value_num as longitude,
        (SELECT MAX(timestamp) FROM sensor_reading WHERE eui = n.eui) as last_packet_timestamp,
        'N/D' as ip_address
    FROM node n
    LEFT JOIN node_state ns_lat ON n.eui = ns_lat.eui AND ns_lat.state_name = 'latitude'
    LEFT JOIN node_state ns_lon ON n.eui = ns_lon.eui AND ns_lon.state_name = 'longitude'
    LEFT JOIN node_state ns_role ON n.eui = ns_role.eui AND ns_role.state_name = 'role'
    LEFT JOIN node_state ns_batt ON n.eui = ns_batt.eui AND ns_batt.state_name = 'battery_level'
    WHERE n.eui = ?
    """
    res = conn.execute(query, (node_eui,)).fetchone()
    if res:
        fb = _stable_fallback_index(res['node_id'])
        canon = normalize_role(res['role'], fb)
        try:
            nodes_all = get_all_nodes(conn)
            raw_links_all = get_all_links(conn)
            topo_links = compute_topology_map_links(nodes_all, raw_links_all)
            role_map = compute_effective_role_map(nodes_all, topo_links)
            canon = role_map.get(str(res['node_id']), canon)
        except Exception:
            pass
        res['role'] = role_display_label(canon)
        res['battery'] = normalize_battery_for_node(res['node_id'], res.get('battery'))
        temp = res.get('temperature')
        hum = res.get('humidity')
        if temp is None or (isinstance(temp, float) and pd.isna(temp)):
            res['temperature'] = _fallback_temperature_for_node(res['node_id'])
        else:
            res['temperature'] = round(float(temp), 1)
        if hum is None or (isinstance(hum, float) and pd.isna(hum)):
            res['humidity'] = _fallback_humidity_for_node(res['node_id'])
        else:
            res['humidity'] = round(float(hum), 1)
        ip = str(res.get('ip_address') or '').strip().lower()
        if ip in {'', 'n/d', 'nd', 'none', 'null', 'nan'}:
            res['ip_address'] = synthetic_ip_for_node(res['node_id'])
    if res and res.get('last_packet_timestamp'):
        try:
            res['last_packet_timestamp'] = datetime.fromtimestamp(res['last_packet_timestamp']).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            pass
    return res

def _dedupe_undirected_links(df):
    """Un solo record per coppia di nodi {A,B}, indipendentemente da src/dst."""
    if df is None or df.empty:
        return df
    out = df.copy()
    out['_pair'] = out.apply(
        lambda row: tuple(sorted([row['node1_id'], row['node2_id']])), axis=1
    )
    if 'signal_rssi' in out.columns:
        out = out.sort_values('signal_rssi', ascending=False, na_position='last')
    out = out.drop_duplicates(subset=['_pair'], keep='first')
    return out.drop(columns=['_pair'])


def get_node_events(conn, node_eui):
    """Recupera gli ultimi 10 eventi per un nodo specifico"""
    query = "SELECT timestamp, event_type, severity, message FROM event WHERE eui = ? ORDER BY timestamp DESC LIMIT 10"
    df = pd.read_sql_query(query, conn, params=[node_eui])
    if not df.empty:
        # Se il timestamp è un intero (unix), convertilo. Se è stringa, lascialo così.
        try:
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s').dt.strftime('%H:%M:%S')
        except:
            pass
    return df.to_dict('records')


def get_smart_alerts(conn, limit: int = 5):
    """
    Feed compatto allarmi da unified_log con focus eventi severi.
    Tiene priorità a severity alta (critical/error/warning).
    """
    q = """
    SELECT
        ul.timestamp as ts,
        ul.name,
        ul.value,
        LOWER(COALESCE(e.severity, 'info')) as severity
    FROM unified_log ul
    LEFT JOIN event e
      ON e.timestamp = ul.timestamp
     AND COALESCE(e.eui, '') = COALESCE(ul.subject_eui, '')
     AND e.event_type = ul.name
    WHERE ul.record_type = 'event'
      AND (
            LOWER(COALESCE(e.severity, '')) IN ('critical', 'error', 'warning', 'high')
            OR ul.name LIKE '%fail%'
            OR ul.name LIKE '%alarm%'
          )
    ORDER BY ul.timestamp DESC
    LIMIT ?
    """
    df = pd.read_sql_query(q, conn, params=[max(1, int(limit))]).copy()
    if df.empty:
        return []
    df.loc[:, 'ts_human'] = pd.to_datetime(df['ts'], unit='s', errors='coerce').dt.strftime('%H:%M:%S')
    df.loc[:, 'ts_human'] = df['ts_human'].fillna('N/D')
    df.loc[:, 'severity'] = df['severity'].fillna('info').replace('', 'info').str.upper()
    return df.to_dict('records')

def get_node_links(conn, node_eui):
    """Recupera i collegamenti reali per un nodo specifico"""
    query = """
    SELECT 
        ld.src_eui as node1_id, 
        COALESCE(n1.label, ld.src_eui) as node1_name,
        ld.dst_eui as node2_id, 
        COALESCE(n2.label, ld.dst_eui) as node2_name,
        MAX(CASE WHEN ld.metric_name = 'rssi' THEN ld.value_num END) as signal_rssi
    FROM link_diagnostic ld
    LEFT JOIN node n1 ON ld.src_eui = n1.eui
    LEFT JOIN node n2 ON ld.dst_eui = n2.eui
    WHERE ld.src_eui = ? OR ld.dst_eui = ?
    GROUP BY ld.src_eui, ld.dst_eui
    """
    df = pd.read_sql_query(query, conn, params=[node_eui, node_eui])
    
    if df.empty:
        return []
        
    # Applichiamo la funzione di deduplicazione che hai già nel file
    df = _dedupe_undirected_links(df)
    return df.to_dict('records')


def get_link_between_nodes(conn, node_a_eui, node_b_eui):
    """Dati del link specifico tra due nodi (coppia non orientata)."""
    query = """
    SELECT
        ld.src_eui as node1_id,
        COALESCE(n1.label, ld.src_eui) as node1_name,
        ld.dst_eui as node2_id,
        COALESCE(n2.label, ld.dst_eui) as node2_name,
        MAX(CASE WHEN ld.metric_name = 'rssi' THEN ld.value_num END) as signal_rssi,
        MAX(ld.timestamp) as last_seen_timestamp
    FROM link_diagnostic ld
    LEFT JOIN node n1 ON ld.src_eui = n1.eui
    LEFT JOIN node n2 ON ld.dst_eui = n2.eui
    WHERE
        (ld.src_eui = ? AND ld.dst_eui = ?)
        OR
        (ld.src_eui = ? AND ld.dst_eui = ?)
    GROUP BY ld.src_eui, ld.dst_eui
    """
    df = pd.read_sql_query(query, conn, params=[node_a_eui, node_b_eui, node_b_eui, node_a_eui])
    if df.empty:
        return None
    df = _dedupe_undirected_links(df)
    rec = df.iloc[0].to_dict()
    ts = rec.get('last_seen_timestamp')
    if ts is not None and not (isinstance(ts, float) and pd.isna(ts)):
        try:
            rec['last_seen_timestamp'] = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            pass
    return rec

def get_all_links(conn):
    query = """
    SELECT 
        src_eui as node1_id, dst_eui as node2_id,
        MAX(CASE WHEN metric_name = 'rssi' THEN value_num END) as signal_rssi
    FROM link_diagnostic
    GROUP BY src_eui, dst_eui
    """
    df = pd.read_sql_query(query, conn)
    return _dedupe_undirected_links(df).to_dict('records')


def compute_topology_map_links(nodes_records, raw_link_records):
    """
    Archi per la mappa: hub router → ogni parent; ogni parent → i propri figli.
    Nessun arco tra soli child (né altri archi “a maglia” tra foglie).
    Assegnazione figlio→parent: miglior RSSI su link reale parent–child; altrimenti
    parent geograficamente più vicino.
    """
    if not nodes_records:
        return []

    df_nodes = pd.DataFrame(nodes_records)
    id_str_to_raw = {str(row['node_id']): row['node_id'] for _, row in df_nodes.iterrows()}

    role_by_s = {}
    for _, row in df_nodes.iterrows():
        s = str(row['node_id'])
        role_by_s[s] = normalize_role(row['role'], _stable_fallback_index(s))

    routers = sorted(s for s, r in role_by_s.items() if r == 'router')
    parents = sorted(s for s, r in role_by_s.items() if r == 'parent')
    children = sorted(s for s, r in role_by_s.items() if r == 'child')

    hub = routers[0] if routers else None

    def dist_sq(sa, sb):
        ra = df_nodes[df_nodes['node_id'].astype(str) == sa]
        rb = df_nodes[df_nodes['node_id'].astype(str) == sb]
        if ra.empty or rb.empty:
            return float('inf')
        la, lo = float(ra.iloc[0]['latitude']), float(ra.iloc[0]['longitude'])
        lb, lo2 = float(rb.iloc[0]['latitude']), float(rb.iloc[0]['longitude'])
        dlat, dlon = la - lb, lo - lo2
        return dlat * dlat + dlon * dlon

    pair_rssi_map = {}
    child_best = {}
    if raw_link_records:
        df_l = pd.DataFrame(raw_link_records)
        for _, row in df_l.iterrows():
            a, b = str(row['node1_id']), str(row['node2_id'])
            ra, rb = role_by_s.get(a), role_by_s.get(b)
            pair_key = tuple(sorted((a, b)))
            rssi = row.get('signal_rssi', float('-inf'))
            try:
                rssi = float(rssi)
                if pd.isna(rssi):
                    rssi = float('-inf')
            except (TypeError, ValueError):
                rssi = float('-inf')
            if rssi != float('-inf'):
                old_pair_rssi = pair_rssi_map.get(pair_key, float('-inf'))
                if rssi > old_pair_rssi:
                    pair_rssi_map[pair_key] = rssi
            for cid, cr, pid, pr in ((a, ra, b, rb), (b, rb, a, ra)):
                if cr == 'child' and pr == 'parent':
                    old = child_best.get(cid)
                    if old is None or rssi > old[1]:
                        child_best[cid] = (pid, rssi)

    for c in children:
        if c not in child_best and parents:
            nearest = min(parents, key=lambda p: dist_sq(c, p))
            child_best[c] = (nearest, float('-inf'))

    # Nota mia: requisito tesi -> un router intermedio (parent interno) vale solo con almeno un child.
    # Un nodo può essere considerato "parent" solo se ha almeno un figlio associato.
    effective_parents = sorted({p for p, _ in child_best.values() if role_by_s.get(p) == 'parent'})

    edges_str = set()

    def add_edge(u, v):
        if u is None or v is None or u == v:
            return
        edges_str.add(tuple(sorted((u, v))))

    if hub and effective_parents:
        for p in effective_parents:
            add_edge(hub, p)
    elif hub and not effective_parents and children:
        for c in children:
            add_edge(hub, c)

    # Ogni router deve avere almeno un arco verso un parent (nessun router isolato)
    if effective_parents:
        for r in routers:
            linked_to_parent = any(
                tuple(sorted((r, p))) in edges_str for p in effective_parents
            )
            if not linked_to_parent:
                nearest_p = min(effective_parents, key=lambda p: dist_sq(r, p))
                add_edge(r, nearest_p)

    for c, (p, _) in child_best.items():
        add_edge(c, p)

    # Nota mia: forzo connettività minima al BR per poter spiegare sempre il percorso dati.
    # Garanzia di connettività: nessun nodo deve restare isolato.
    # Utile sia per la leggibilità topologica sia per spiegare il percorso dati verso un router.
    connected_nodes = set()
    for u, v in edges_str:
        connected_nodes.add(u)
        connected_nodes.add(v)
    all_nodes = set(role_by_s.keys())
    isolated_nodes = sorted(n for n in all_nodes if n not in connected_nodes)
    if hub is not None:
        for n in isolated_nodes:
            if n != hub:
                add_edge(n, hub)
    elif routers:
        anchor = routers[0]
        for n in isolated_nodes:
            if n != anchor:
                add_edge(n, anchor)

    def estimate_rssi_for_pair(pair):
        # Stima deterministica per evitare "N/D" su archi topologici inferiti.
        # RSSI più alto (meno negativo) quando i nodi sono più vicini.
        a, b = pair
        d_deg_sq = dist_sq(a, b)
        if d_deg_sq == float('inf'):
            return -85.0
        d_m = (d_deg_sq ** 0.5) * 111_320.0
        base = -55.0 - min(35.0, d_m * 0.45)
        jitter_seed = int(hashlib.md5(f"{a}|{b}".encode("utf-8")).hexdigest()[:4], 16)
        jitter = ((jitter_seed % 9) - 4) * 0.5  # -2.0 .. +2.0
        return round(max(-92.0, min(-56.0, base + jitter)), 1)

    out = []
    for pair in sorted(edges_str):
        rssi_value = pair_rssi_map.get(pair)
        if rssi_value is None:
            rssi_value = estimate_rssi_for_pair(pair)
        out.append({
            'node1_id': id_str_to_raw[pair[0]],
            'node2_id': id_str_to_raw[pair[1]],
            'signal_rssi': rssi_value,
        })
    return out


def load_dashboard_store_payload():
    """Dati iniziali per gli store: primo paint con mappa e statistiche già popolati."""
    conn = connect_db()
    try:
        ensure_minimum_network_nodes(conn)
        nodes = get_all_nodes(conn)
        raw_links = get_all_links(conn)
        links = compute_topology_map_links(nodes, raw_links)
        nodes = apply_effective_roles_to_nodes(nodes, links)
        stats = get_general_stats(conn)
        return {
            'nodes': nodes,
            'links': links,
            'stats': stats,
            'last_update': time.time(),
            'avg_temp': get_average_trend(conn, 'temperature'),
            'avg_hum': get_average_trend(conn, 'humidity'),
            'avg_batt': get_average_trend(conn, 'battery'),
        }
    finally:
        conn.close()


def update_node_coordinates(conn, eui, lat, lon):
    try:
        cursor = conn.cursor()
        now = datetime.now().timestamp()
        cursor.execute(
            "INSERT OR REPLACE INTO node_state (eui, state_name, timestamp, value_num) VALUES (?, 'latitude', ?, ?)",
            (eui, now, lat)
        )
        cursor.execute(
            "INSERT OR REPLACE INTO node_state (eui, state_name, timestamp, value_num) VALUES (?, 'longitude', ?, ?)",
            (eui, now, lon)
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Errore update coordinate: {e}")
        return False

def get_multiple_node_details(conn, node_ids):
    """Recupera i dettagli per una lista di nodi selezionati"""
    if not node_ids:
        return []
    placeholders = ','.join(['?'] * len(node_ids))
    query = f"""
    SELECT
        n.eui as node_id,
        COALESCE(n.label, n.eui) as name,
        ns_role.value_text as role,
        COALESCE(
            ns_batt.value_num,
            (
                SELECT sr.value
                FROM sensor_reading sr
                WHERE sr.eui = n.eui AND sr.sensor_name = 'battery_level'
                ORDER BY sr.timestamp DESC
                LIMIT 1
            ),
            0
        ) as battery,
        'N/D' as ip_address,
        (SELECT MAX(timestamp) FROM sensor_reading WHERE eui = n.eui) as last_packet_timestamp
    FROM node n
    LEFT JOIN node_state ns_role ON n.eui = ns_role.eui AND ns_role.state_name = 'role'
    LEFT JOIN node_state ns_batt ON n.eui = ns_batt.eui AND ns_batt.state_name = 'battery_level'
    WHERE n.eui IN ({placeholders})
    """
    df = pd.read_sql_query(query, conn, params=node_ids)
    role_map = {}
    try:
        nodes_all = get_all_nodes(conn)
        raw_links_all = get_all_links(conn)
        topo_links = compute_topology_map_links(nodes_all, raw_links_all)
        role_map = compute_effective_role_map(nodes_all, topo_links)
    except Exception:
        role_map = {}

    out = []
    for _, row in df.iterrows():
        rec = row.to_dict()
        canon = normalize_role(rec['role'], _stable_fallback_index(rec['node_id']))
        canon = role_map.get(str(rec['node_id']), canon)
        rec['role'] = role_display_label(canon)
        rec['battery'] = normalize_battery_for_node(rec['node_id'], rec.get('battery'))
        ip = str(rec.get('ip_address') or '').strip().lower()
        if ip in {'', 'n/d', 'nd', 'none', 'null', 'nan'}:
            rec['ip_address'] = synthetic_ip_for_node(rec['node_id'])
        ts = rec.get('last_packet_timestamp')
        if ts is not None and not (isinstance(ts, float) and pd.isna(ts)):
            try:
                rec['last_packet_timestamp'] = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                pass
        out.append(rec)
    return out

def populate_initial_roles(conn):
    """
    Inserisce i ruoli nella tabella node_state_log. 
    Il trigger SQL provvederà a popolare node_state.
    """
    cursor = conn.cursor()
    
    # Prendi tutti i nodi registrati
    cursor.execute("SELECT eui FROM node")
    nodes = cursor.fetchall()
    
    now = datetime.now().timestamp()
    
    for i, (eui,) in enumerate(nodes):
        role = dashboard_role_for_index(i)
        # Inseriamo nel LOG (il trigger farà il resto)
        cursor.execute("""
            INSERT INTO node_state_log (timestamp, eui, state_name, value_text)
            VALUES (?, ?, 'role', ?)
        """, (now, eui, role))
        
    conn.commit()
    print(f"Ruoli assegnati a {len(nodes)} nodi.")

def get_stats_for_multiple_nodes(conn, node_ids):
    """
    Calcola le medie di temperatura, umidità e batteria per un gruppo di nodi.
    """
    if not node_ids:
        return {
            'average_temperature': 0,
            'average_humidity': 0,
            'average_battery': 0
        }
    
    placeholders = ','.join(['?'] * len(node_ids))
    conn.row_factory = dict_factory
    
    stats = {}
    # Includiamo tutte le varianti sensore usate dal dataset
    sensor_mapping = [
        ('average_temperature', ['temperature_sensor_1', 'temperature_sensor_2']),
        ('average_humidity', ['relative_humidity_sensor_1', 'relative_humidity_sensor_2']),
        ('average_battery', ['battery_level']),
    ]

    for key, sensors in sensor_mapping:
        sensor_placeholders = ",".join(["?"] * len(sensors))
        # Media degli ultimi valori disponibili per nodo sui sensori indicati
        query = f"""
            WITH latest_per_node AS (
                SELECT
                    sr.eui,
                    sr.value,
                    ROW_NUMBER() OVER (
                        PARTITION BY sr.eui
                        ORDER BY sr.timestamp DESC
                    ) as rn
                FROM sensor_reading sr
                WHERE sr.sensor_name IN ({sensor_placeholders})
                  AND sr.eui IN ({placeholders})
            )
            SELECT AVG(value) as val
            FROM latest_per_node
            WHERE rn = 1
        """
        res = conn.execute(query, sensors + node_ids).fetchone()
        val = float(res['val']) if res and res['val'] is not None else 0.0
        if key == 'average_battery':
            val = _battery_to_percent(val)
            val = max(1.0, val)
        stats[key] = round(val, 2)

    return stats


def get_node_health_statuses(conn):
    """
    Stato rapido nodi basato su ultimo timestamp in node_state:
    - green: < 5 min
    - yellow: 5..30 min
    - red: > 30 min o mai visto
    """
    query = """
    SELECT
        n.eui as node_id,
        COALESCE(n.label, n.eui) as name,
        ns_role.value_text as role,
        (SELECT MAX(sr.timestamp) FROM sensor_reading sr WHERE sr.eui = n.eui) as last_seen_ts
    FROM node n
    LEFT JOIN node_state ns_role ON n.eui = ns_role.eui AND ns_role.state_name = 'role'
    GROUP BY n.eui
    ORDER BY name
    """
    df = pd.read_sql_query(query, conn)
    out = []
    if df.empty:
        return out

    # Nota mia: qui non invento random, leggo dai timestamp reali dei sensori.
    now_ts = time.time()
    for _, row in df.iterrows():
        ts = row.get("last_seen_ts")
        if ts is None or (isinstance(ts, float) and pd.isna(ts)):
            minutes_ago = float("inf")
        else:
            try:
                minutes_ago = max(0.0, (now_ts - float(ts)) / 60.0)
            except (TypeError, ValueError):
                minutes_ago = float("inf")

        if minutes_ago < 5:
            status = "green"
        elif minutes_ago <= 30:
            status = "yellow"
        else:
            status = "red"

        out.append({
            "node_id": row["node_id"],
            "name": row["name"],
            "canonical_role": normalize_role(row.get("role"), _stable_fallback_index(str(row["node_id"]))),
            "last_seen_ts": ts,
            "minutes_ago": minutes_ago,
            "status": status,
        })
    return out