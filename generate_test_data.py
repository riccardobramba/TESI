#!/usr/bin/env python
"""
Genera dati di test sintetici per il database della rete di sensori.
Crea pacchetti grezzi casuali, letture dei sensori analizzate, eventi, 
modifiche di stato dei nodi, aggiornamenti dei parametri dei nodi, diagnostica dei collegamenti e diagnostica di rete.

Supporta entrambe le modalità:
- generazione iniziale con dati preesistenti in un intervallo di tempo
- modalità continua che aggiunge costantemente nuove letture dei sensori ed eventi
"""

import sqlite3
import random
import time
import argparse
from datetime import datetime
import os
import uuid
import queries

# Configuration
DEFAULT_DB_NAME = "data.db"
DEFAULT_NUM_DEVICES = 10
DEFAULT_NUM_DATA_POINTS = 500
DEFAULT_HOURS_BACK = 2.0
DEFAULT_CONTINUOUS_INTERVAL = 10

# Sensor names matching the actual payload structure
SENSOR_TYPES = [
    'temperature_sensor_1',
    'relative_humidity_sensor_1',
    'battery_level',
    'temperature_sensor_2',
    'relative_humidity_sensor_2',
    'pressure_sensor_2',
    'gas_sensor_2'
]

# Sensor value ranges (min, max)
SENSOR_RANGES = {
    'temperature_sensor_1': (15.0, 35.0),
    'temperature_sensor_2': (15.0, 35.0),
    'relative_humidity_sensor_1': (20.0, 80.0),
    'relative_humidity_sensor_2': (20.0, 80.0),
    'pressure_sensor_2': (950.0, 1050.0),
    'battery_level': (3.0, 4.2),
    'gas_sensor_2': (50000.0, 500000.0)
}

# Number of indices per sensor type
SENSOR_INDICES = {
    'temperature_sensor_1': [0],
    'relative_humidity_sensor_1': [0],
    'battery_level': [0],
    'temperature_sensor_2': list(range(10)),
    'relative_humidity_sensor_2': list(range(10)),
    'pressure_sensor_2': list(range(10)),
    'gas_sensor_2': list(range(10))
}

NODE_STATE_TYPES = ['online', 'alarm', 'role']
# Ruoli allineati alla dashboard (normalizzati come Router/Parent/Child)
NODE_ROLES = ['Router', 'Parent', 'Child']
ALARM_STATES = ['none', 'warning', 'critical']

PARAMETER_TYPES = ['polling_frequency', 'sleep_policy', 'longitude', 'latitude']
SLEEP_POLICIES = ['always_awake', 'duty_cycle', 'deep_sleep']

LINK_DIAGNOSTIC_TYPES = ['rssi', 'next-hop', 'link_quality']
NETWORK_DIAGNOSTIC_TYPES = [
    'connected_components',
    'leader_online',
    'traffic_level',
    'congestion_level'
]
TRAFFIC_LEVELS = ['low', 'medium', 'high']
CONGESTION_LEVELS = ['none', 'mild', 'moderate', 'severe']
EVENT_SCOPES = ['network', 'node']


class NodeProfile:
    """Simple container for synthetic node metadata and state."""

    def __init__(self, eui, version, comment, latitude, longitude, role):
        self.eui = eui
        self.version = version
        self.comment = comment
        self.latitude = latitude
        self.longitude = longitude
        self.role = role
        self.online = True
        self.alarm = 'none'
        self.polling_frequency = random.choice([30.0, 60.0, 120.0, 300.0])
        self.sleep_policy = random.choice(SLEEP_POLICIES)
        self.preferred_next_hop = None


def generate_eui():
    """Generate a random 8-byte EUI as 16-char uppercase hex."""
    return ''.join(f'{random.randint(0, 255):02X}' for _ in range(8))


def create_node_profiles(num_devices):
    """Create stable synthetic nodes used by the generator."""
    profiles = []

    for idx in range(num_devices):
        eui = generate_eui()
        version = f"v{random.randint(1, 3)}.{random.randint(0, 9)}"
        comment = f"Synthetic node {idx + 1} for dashboard testing"
        latitude, longitude = queries.random_parcel_lat_lon()
        role = queries.dashboard_role_for_index(idx)
        profiles.append(NodeProfile(eui, version, comment, latitude, longitude, role))

    return profiles


def create_database(db_path):
    """Create the database schema."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT NOT NULL,
        timestamp REAL NOT NULL,
        data BLOB NOT NULL
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS node (
        eui TEXT PRIMARY KEY,
        created_at REAL,
        label TEXT,
        version TEXT,
        comment TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS sensor_reading (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_id INTEGER NOT NULL,
        eui TEXT NOT NULL,
        timestamp REAL NOT NULL,
        sensor_name TEXT NOT NULL,
        sensor_index INTEGER NOT NULL,
        value REAL NOT NULL,
        FOREIGN KEY (data_id) REFERENCES data(id),
        FOREIGN KEY (eui) REFERENCES node(eui)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS event (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        scope TEXT NOT NULL,
        eui TEXT,
        event_type TEXT NOT NULL,
        severity TEXT,
        message TEXT,
        data_id INTEGER,
        FOREIGN KEY (data_id) REFERENCES data(id),
        FOREIGN KEY (eui) REFERENCES node(eui)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS node_state_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        eui TEXT NOT NULL,
        state_name TEXT NOT NULL,
        value_text TEXT,
        value_num REAL,
        data_id INTEGER,
        FOREIGN KEY (data_id) REFERENCES data(id),
        FOREIGN KEY (eui) REFERENCES node(eui)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS node_state (
        eui TEXT NOT NULL,
        state_name TEXT NOT NULL,
        timestamp REAL NOT NULL,
        value_text TEXT,
        value_num REAL,
        data_id INTEGER,
        PRIMARY KEY (eui, state_name),
        FOREIGN KEY (data_id) REFERENCES data(id),
        FOREIGN KEY (eui) REFERENCES node(eui)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS node_parameter_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        eui TEXT NOT NULL,
        parameter_name TEXT NOT NULL,
        value_text TEXT,
        value_num REAL,
        data_id INTEGER,
        FOREIGN KEY (data_id) REFERENCES data(id),
        FOREIGN KEY (eui) REFERENCES node(eui)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS node_parameter (
        eui TEXT NOT NULL,
        parameter_name TEXT NOT NULL,
        timestamp REAL NOT NULL,
        value_text TEXT,
        value_num REAL,
        data_id INTEGER,
        PRIMARY KEY (eui, parameter_name),
        FOREIGN KEY (data_id) REFERENCES data(id),
        FOREIGN KEY (eui) REFERENCES node(eui)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS link_diagnostic (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        src_eui TEXT NOT NULL,
        dst_eui TEXT NOT NULL,
        metric_name TEXT NOT NULL,
        value_text TEXT,
        value_num REAL,
        data_id INTEGER,
        FOREIGN KEY (data_id) REFERENCES data(id),
        FOREIGN KEY (src_eui) REFERENCES node(eui),
        FOREIGN KEY (dst_eui) REFERENCES node(eui)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS network_diagnostic (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        metric_name TEXT NOT NULL,
        value_text TEXT,
        value_num REAL,
        data_id INTEGER,
        FOREIGN KEY (data_id) REFERENCES data(id)
    )
    """)

    conn.execute("""
    CREATE TRIGGER IF NOT EXISTS trg_node_state_log_to_current
    AFTER INSERT ON node_state_log
    BEGIN
        INSERT INTO node_state (eui, state_name, timestamp, value_text, value_num, data_id)
        VALUES (NEW.eui, NEW.state_name, NEW.timestamp, NEW.value_text, NEW.value_num, NEW.data_id)
        ON CONFLICT(eui, state_name) DO UPDATE SET
            timestamp = excluded.timestamp,
            value_text = excluded.value_text,
            value_num = excluded.value_num,
            data_id = excluded.data_id
        WHERE excluded.timestamp >= node_state.timestamp;
    END;
    """)

    conn.execute("""
    CREATE TRIGGER IF NOT EXISTS trg_node_parameter_log_to_current
    AFTER INSERT ON node_parameter_log
    BEGIN
        INSERT INTO node_parameter (eui, parameter_name, timestamp, value_text, value_num, data_id)
        VALUES (NEW.eui, NEW.parameter_name, NEW.timestamp, NEW.value_text, NEW.value_num, NEW.data_id)
        ON CONFLICT(eui, parameter_name) DO UPDATE SET
            timestamp = excluded.timestamp,
            value_text = excluded.value_text,
            value_num = excluded.value_num,
            data_id = excluded.data_id
        WHERE excluded.timestamp >= node_parameter.timestamp;
    END;
    """)

    conn.execute("""
    CREATE VIEW IF NOT EXISTS unified_log AS
    SELECT
        'sensor_reading' AS record_type,
        timestamp,
        eui AS subject_eui,
        NULL AS object_eui,
        sensor_name AS name,
        CAST(value AS TEXT) AS value,
        data_id
    FROM sensor_reading
    UNION ALL
    SELECT
        'event',
        timestamp,
        eui,
        NULL,
        event_type,
        COALESCE(message, severity),
        data_id
    FROM event
    UNION ALL
    SELECT
        'node_state',
        timestamp,
        eui,
        NULL,
        state_name,
        COALESCE(value_text, CAST(value_num AS TEXT)),
        data_id
    FROM node_state_log
    UNION ALL
    SELECT
        'node_parameter',
        timestamp,
        eui,
        NULL,
        parameter_name,
        COALESCE(value_text, CAST(value_num AS TEXT)),
        data_id
    FROM node_parameter_log
    UNION ALL
    SELECT
        'link_diagnostic',
        timestamp,
        src_eui,
        dst_eui,
        metric_name,
        COALESCE(value_text, CAST(value_num AS TEXT)),
        data_id
    FROM link_diagnostic
    UNION ALL
    SELECT
        'network_diagnostic',
        timestamp,
        NULL,
        NULL,
        metric_name,
        COALESCE(value_text, CAST(value_num AS TEXT)),
        data_id
    FROM network_diagnostic
    """)

    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_data_timestamp ON data(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_sensor_timestamp ON sensor_reading(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_sensor_eui_timestamp ON sensor_reading(eui, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_event_timestamp ON event(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_event_eui_timestamp ON event(eui, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_node_state_log_eui_timestamp ON node_state_log(eui, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_node_state_current_ts ON node_state(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_node_parameter_log_eui_timestamp ON node_parameter_log(eui, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_node_parameter_current_ts ON node_parameter(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_link_diag_src_dst_ts ON link_diagnostic(src_eui, dst_eui, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_network_diag_metric_ts ON network_diagnostic(metric_name, timestamp)",
    ]

    for stmt in indexes:
        conn.execute(stmt)

    conn.commit()
    return conn


def register_nodes(conn, profiles, created_at):
    """Insert node metadata rows."""
    cursor = conn.cursor()
    for idx, profile in enumerate(profiles, start=1):
        cursor.execute(
            """
            INSERT OR IGNORE INTO node (eui, created_at, label, version, comment)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                profile.eui,
                created_at,
                f"node-{idx}",
                profile.version,
                profile.comment,
            )
        )
        # assicuriamoci che la tabella node_state contenga le coordinate iniziali
        if profile.latitude is not None and profile.longitude is not None:
            queries.update_node_coordinates(conn, profile.eui, profile.latitude, profile.longitude)
    conn.commit()


def generate_sensor_value(sensor_name, time_offset=0.0):
    """Generate a realistic sensor value with some temporal variation."""
    min_val, max_val = SENSOR_RANGES[sensor_name]
    base_value = (min_val + max_val) / 2.0
    amplitude = (max_val - min_val) / 4.0

    time_factor = (time_offset / 3600.0) * 2.0 * 3.14159
    variation = amplitude * (0.5 + 0.5 * random.random()) * (
        0.3 * random.random() + 0.7 * (1.0 + 0.5 * (time_factor % (2.0 * 3.14159)))
    )
    noise = random.gauss(0.0, amplitude * 0.1)

    value = base_value + variation + noise
    value = max(min_val, min(max_val, value))
    return round(value, 2)


def insert_raw_data(cursor, timestamp, ordinal):
    """Insert a raw packet row and return its id."""
    uuid_value = f"test-uuid-{ordinal:06d}-{uuid.uuid4().hex[:8]}"
    payload = os.urandom(32)
    cursor.execute(
        "INSERT INTO data (uuid, timestamp, data) VALUES (?, ?, ?)",
        (uuid_value, timestamp, payload)
    )
    return cursor.lastrowid


def insert_sensor_readings(cursor, profile, data_id, timestamp, time_offset):
    active_sensors = random.sample(
        SENSOR_TYPES,
        k=random.randint(max(1, len(SENSOR_TYPES) - 2), len(SENSOR_TYPES))
    )
    readings_count = 0
    battery_value = None

    for sensor_name in active_sensors:
        for sensor_index in SENSOR_INDICES[sensor_name]:
            value = generate_sensor_value(sensor_name, time_offset)
            cursor.execute(
                """
                INSERT INTO sensor_reading
                (data_id, eui, timestamp, sensor_name, sensor_index, value)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (data_id, profile.eui, timestamp, sensor_name, sensor_index, value)
            )
            readings_count += 1
            if sensor_name == 'battery_level' and sensor_index == 0:
                battery_value = value

    return readings_count, battery_value


def maybe_insert_event(cursor, profile, data_id, timestamp, battery_value):
    """Insert occasional node/network events."""
    inserted = 0

    if battery_value is not None and battery_value < 3.25 and random.random() < 0.50:
        cursor.execute(
            """
            INSERT INTO event (timestamp, scope, eui, event_type, severity, message, data_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                'node',
                profile.eui,
                'battery_low',
                'warning',
                f'Battery low on node {profile.eui}: {battery_value:.2f}V',
                data_id,
            )
        )
        inserted += 1

    if random.random() < 0.12:
        scope = random.choice(EVENT_SCOPES)
        eui = profile.eui if scope == 'node' else None
        event_type = random.choice(['manual_annotation', 'maintenance_note', 'topology_review'])
        severity = random.choice(['info', 'warning'])
        message = (
            f'User inserted {event_type.replace("_", " ")} for {profile.eui}'
            if eui else
            f'User inserted network-wide {event_type.replace("_", " ")}'
        )
        cursor.execute(
            """
            INSERT INTO event (timestamp, scope, eui, event_type, severity, message, data_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (timestamp, scope, eui, event_type, severity, message, data_id)
        )
        inserted += 1

    return inserted


def maybe_insert_node_states(cursor, profile, data_id, timestamp, battery_value):
    inserted = 0

    if random.random() < 0.06:
        profile.online = random.random() > 0.10
        cursor.execute(
            """
            INSERT INTO node_state_log (timestamp, eui, state_name, value_text, value_num, data_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (timestamp, profile.eui, 'online', 'true' if profile.online else 'false', 1.0 if profile.online else 0.0, data_id)
        )
        inserted += 1

    if battery_value is not None:
        if battery_value < 3.2:
            profile.alarm = 'critical'
        elif battery_value < 3.4:
            profile.alarm = 'warning'
        elif random.random() < 0.20:
            profile.alarm = 'none'

    if random.random() < 0.08:
        cursor.execute(
            """
            INSERT INTO node_state_log (timestamp, eui, state_name, value_text, value_num, data_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (timestamp, profile.eui, 'alarm', profile.alarm, None, data_id)
        )
        inserted += 1

    if random.random() < 0.03:
        profile.role = random.choice(NODE_ROLES)
        cursor.execute(
            """
            INSERT INTO node_state_log (timestamp, eui, state_name, value_text, value_num, data_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (timestamp, profile.eui, 'role', profile.role, None, data_id)
        )
        inserted += 1

    return inserted


def maybe_insert_node_parameters(cursor, profile, data_id, timestamp):
    inserted = 0

    if random.random() < 0.05:
        profile.polling_frequency = random.choice([15.0, 30.0, 60.0, 120.0, 300.0])
        cursor.execute(
            """
            INSERT INTO node_parameter_log (timestamp, eui, parameter_name, value_text, value_num, data_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (timestamp, profile.eui, 'polling_frequency', None, profile.polling_frequency, data_id)
        )
        inserted += 1

    if random.random() < 0.03:
        profile.sleep_policy = random.choice(SLEEP_POLICIES)
        cursor.execute(
            """
            INSERT INTO node_parameter_log (timestamp, eui, parameter_name, value_text, value_num, data_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (timestamp, profile.eui, 'sleep_policy', profile.sleep_policy, None, data_id)
        )
        inserted += 1

    if random.random() < 0.02:
        profile.longitude += random.uniform(-0.0005, 0.0005)
        cursor.execute(
            """
            INSERT INTO node_parameter_log (timestamp, eui, parameter_name, value_text, value_num, data_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (timestamp, profile.eui, 'longitude', None, round(profile.longitude, 6), data_id)
        )
        inserted += 1

    if random.random() < 0.02:
        profile.latitude += random.uniform(-0.0005, 0.0005)
        cursor.execute(
            """
            INSERT INTO node_parameter_log (timestamp, eui, parameter_name, value_text, value_num, data_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (timestamp, profile.eui, 'latitude', None, round(profile.latitude, 6), data_id)
        )
        inserted += 1

    return inserted


def insert_link_diagnostics(cursor, profiles, source_profile, data_id, timestamp):
    if len(profiles) < 2:
        return 0

    inserted = 0
    peers = [p for p in profiles if p.eui != source_profile.eui]
    sampled_peers = random.sample(peers, k=min(len(peers), random.randint(1, min(3, len(peers)))))

    for peer in sampled_peers:
        rssi = round(random.uniform(-110.0, -45.0), 1)
        link_quality = round(random.uniform(0.05, 1.00), 3)
        next_hop = random.choice(peers).eui if peers else peer.eui
        source_profile.preferred_next_hop = next_hop

        rows = [
            ('rssi', None, rssi),
            ('next-hop', next_hop, None),
            ('link_quality', None, link_quality),
        ]
        for metric_name, value_text, value_num in rows:
            cursor.execute(
                """
                INSERT INTO link_diagnostic
                (timestamp, src_eui, dst_eui, metric_name, value_text, value_num, data_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (timestamp, source_profile.eui, peer.eui, metric_name, value_text, value_num, data_id)
            )
            inserted += 1

    return inserted


def insert_network_diagnostics(cursor, profiles, data_id, timestamp):
    inserted = 0
    online_count = sum(1 for p in profiles if p.online)
    leader_online = any(p.role == 'Router' and p.online for p in profiles)
    connected_components = 1 if online_count >= max(1, len(profiles) - 1) else random.randint(1, min(3, len(profiles)))
    traffic_level = random.choice(TRAFFIC_LEVELS)
    congestion_level = random.choice(CONGESTION_LEVELS)

    rows = [
        ('connected_components', None, float(connected_components)),
        ('leader_online', 'true' if leader_online else 'false', 1.0 if leader_online else 0.0),
        ('traffic_level', traffic_level, None),
        ('congestion_level', congestion_level, None),
    ]

    for metric_name, value_text, value_num in rows:
        cursor.execute(
            """
            INSERT INTO network_diagnostic (timestamp, metric_name, value_text, value_num, data_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (timestamp, metric_name, value_text, value_num, data_id)
        )
        inserted += 1

    return inserted


def initialize_node_history(conn, profiles, timestamp):
    """Insert baseline node states and parameters so current tables are populated via triggers."""
    cursor = conn.cursor()
    for idx, profile in enumerate(profiles):
        data_id = insert_raw_data(cursor, timestamp, -(idx + 1))
        baseline_states = [
            ('online', 'true', 1.0),
            ('alarm', profile.alarm, None),
            ('role', profile.role, None),
        ]
        for state_name, value_text, value_num in baseline_states:
            cursor.execute(
                """
                INSERT INTO node_state_log (timestamp, eui, state_name, value_text, value_num, data_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (timestamp, profile.eui, state_name, value_text, value_num, data_id)
            )

        baseline_params = [
            ('polling_frequency', None, profile.polling_frequency),
            ('sleep_policy', profile.sleep_policy, None),
            ('longitude', None, round(profile.longitude, 6)),
            ('latitude', None, round(profile.latitude, 6)),
        ]
        for parameter_name, value_text, value_num in baseline_params:
            cursor.execute(
                """
                INSERT INTO node_parameter_log (timestamp, eui, parameter_name, value_text, value_num, data_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (timestamp, profile.eui, parameter_name, value_text, value_num, data_id)
            )
        # aggiorniamo subito anche la tabella "node_state" con le coordinate generate
        if profile.latitude is not None and profile.longitude is not None:
            queries.update_node_coordinates(conn, profile.eui, profile.latitude, profile.longitude)
    conn.commit()


def populate_database(conn, num_points=100, num_devices=2, hours_back=2.0):
    """Populate the database with synthetic historical data."""
    profiles = create_node_profiles(num_devices)
    print(f"Generated {num_devices} devices with EUIs: {[p.eui for p in profiles]}")

    current_time = time.time()
    start_time = current_time - (3600.0 * float(hours_back))

    register_nodes(conn, profiles, start_time)
    initialize_node_history(conn, profiles, start_time)

    cursor = conn.cursor()
    print(f"\nGenerating {num_points} data points...")

    total_sensor_readings = 0
    total_events = 0
    total_states = 0
    total_parameters = 0
    total_link_diags = 0
    total_network_diags = 0

    for i in range(num_points):
        timestamp = start_time + (current_time - start_time) * (i / max(1, num_points))
        time_offset = timestamp - start_time
        profile = random.choice(profiles)

        data_id = insert_raw_data(cursor, timestamp, i)
        readings_count, battery_value = insert_sensor_readings(cursor, profile, data_id, timestamp, time_offset)
        total_sensor_readings += readings_count
        total_events += maybe_insert_event(cursor, profile, data_id, timestamp, battery_value)
        total_states += maybe_insert_node_states(cursor, profile, data_id, timestamp, battery_value)
        total_parameters += maybe_insert_node_parameters(cursor, profile, data_id, timestamp)
        total_link_diags += insert_link_diagnostics(cursor, profiles, profile, data_id, timestamp)

        if i % max(1, num_devices) == 0:
            total_network_diags += insert_network_diagnostics(cursor, profiles, data_id, timestamp)

        if (i + 1) % 50 == 0:
            print(f"  Generated {i + 1}/{num_points} data points...")

    conn.commit()
    print(f"\n✓ Successfully generated {num_points} data points!")
    print(
        "  Inserted: "
        f"{total_sensor_readings} sensor readings, "
        f"{total_events} events, "
        f"{total_states} node states, "
        f"{total_parameters} parameters, "
        f"{total_link_diags} link diagnostics, "
        f"{total_network_diags} network diagnostics"
    )


def print_statistics(conn):
    """Print database statistics."""
    cursor = conn.cursor()

    table_counts = {}
    for table in ['data', 'node', 'sensor_reading', 'event', 'node_state_log', 'node_state', 'node_parameter_log', 'node_parameter', 'link_diagnostic', 'network_diagnostic']:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        table_counts[table] = cursor.fetchone()[0]

    cursor.execute("SELECT DISTINCT sensor_name FROM sensor_reading ORDER BY sensor_name")
    sensors = [row[0] for row in cursor.fetchall()]

    cursor.execute("SELECT eui, label, version FROM node ORDER BY eui")
    nodes = cursor.fetchall()

    cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM unified_log")
    min_time, max_time = cursor.fetchone()

    print("\n" + "=" * 72)
    print("DATABASE STATISTICS")
    print("=" * 72)
    for table_name, count in table_counts.items():
        print(f"{table_name:<20} {count}")
    print(f"Unique sensor types:   {len(sensors)} - {', '.join(sensors)}")
    print(f"Unique devices:        {len(nodes)}")
    for eui, label, version in nodes:
        print(f"  - {eui} | {label} | {version}")
    if min_time is not None and max_time is not None:
        print(f"Time range:            {datetime.fromtimestamp(min_time).strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"                    to {datetime.fromtimestamp(max_time).strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Duration:              {(max_time - min_time) / 60:.1f} minutes")
    print("=" * 72)

    print("\nSAMPLE SENSOR READINGS (last 10):")
    cursor.execute(
        """
        SELECT eui, datetime(timestamp, 'unixepoch', 'localtime') AS dt,
               sensor_name, sensor_index, value
        FROM sensor_reading
        ORDER BY timestamp DESC
        LIMIT 10
        """
    )
    print(f"{'EUI':<18} {'Timestamp':<20} {'Sensor':<28} {'Idx':<5} {'Value':<10}")
    print("-" * 95)
    for row in cursor.fetchall():
        print(f"{row[0]:<18} {row[1]:<20} {row[2]:<28} {row[3]:<5} {row[4]:<10.2f}")

    print("\nSAMPLE EVENTS (last 5):")
    cursor.execute(
        """
        SELECT datetime(timestamp, 'unixepoch', 'localtime'), scope,
               COALESCE(eui, 'NETWORK'), event_type, severity, message
        FROM event
        ORDER BY timestamp DESC
        LIMIT 5
        """
    )
    for row in cursor.fetchall():
        print(f"  {row[0]} | {row[1]:<7} | {row[2]:<16} | {row[3]:<18} | {row[4] or '-':<7} | {row[5]}")


def load_or_create_profiles(conn, requested_devices):
    """Load nodes from DB if present, otherwise create and register them."""
    cursor = conn.cursor()
    cursor.execute("SELECT eui, version, comment, label FROM node ORDER BY created_at, eui")
    rows = cursor.fetchall()

    if rows:
        profiles = []
        for i, (eui, version, comment, _label) in enumerate(rows):
            latitude, longitude = queries.random_parcel_lat_lon()
            role = queries.dashboard_role_for_index(i)
            profile = NodeProfile(eui, version or 'v1.0', comment or 'Synthetic node', latitude, longitude, role)
            profiles.append(profile)
        return profiles

    profiles = create_node_profiles(requested_devices)
    register_nodes(conn, profiles, time.time())
    initialize_node_history(conn, profiles, time.time())
    return profiles


def add_single_datapoint(conn, profiles, base_time_offset=0.0, ordinal=0):
    """Add one current-time packet plus associated readings and events."""
    cursor = conn.cursor()
    timestamp = time.time()
    profile = random.choice(profiles)

    data_id = insert_raw_data(cursor, timestamp, ordinal)
    readings_count, battery_value = insert_sensor_readings(cursor, profile, data_id, timestamp, base_time_offset)
    event_count = maybe_insert_event(cursor, profile, data_id, timestamp, battery_value)
    state_count = maybe_insert_node_states(cursor, profile, data_id, timestamp, battery_value)
    parameter_count = maybe_insert_node_parameters(cursor, profile, data_id, timestamp)
    link_diag_count = insert_link_diagnostics(cursor, profiles, profile, data_id, timestamp)
    network_diag_count = insert_network_diagnostics(cursor, profiles, data_id, timestamp)

    conn.commit()
    return {
        'data_id': data_id,
        'timestamp': timestamp,
        'eui': profile.eui,
        'sensor_readings': readings_count,
        'events': event_count,
        'states': state_count,
        'parameters': parameter_count,
        'link_diagnostics': link_diag_count,
        'network_diagnostics': network_diag_count,
    }


def continuous_mode(conn, num_devices=DEFAULT_NUM_DEVICES, interval=DEFAULT_CONTINUOUS_INTERVAL):
    """Continuously add new rows at regular intervals."""
    print("\n" + "=" * 72)
    print(f"CONTINUOUS MODE - Adding data every {interval} seconds")
    print("Press Ctrl+C to stop")
    print("=" * 72 + "\n")

    profiles = load_or_create_profiles(conn, num_devices)
    print(f"Using {len(profiles)} device(s): {[p.eui for p in profiles]}\n")

    try:
        counter = 0
        start_time = time.time()
        while True:
            result = add_single_datapoint(
                conn,
                profiles,
                base_time_offset=time.time() - start_time,
                ordinal=counter,
            )
            counter += 1
            dt_str = datetime.fromtimestamp(result['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
            print(
                f"[{counter:4d}] {dt_str} | data_id={result['data_id']} | "
                f"EUI={result['eui']} | sensor={result['sensor_readings']} | "
                f"events={result['events']} | states={result['states']} | "
                f"params={result['parameters']} | link_diag={result['link_diagnostics']} | "
                f"net_diag={result['network_diagnostics']}"
            )
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n\n" + "=" * 72)
        print("Stopped by user")
        print("=" * 72)
        print_statistics(conn)


def main():
    parser = argparse.ArgumentParser(description="Generate dashboard-ready SQLite sensor network test data")
    parser.add_argument("--output", "-o", default=DEFAULT_DB_NAME, help="SQLite DB file path (default: data.db)")
    parser.add_argument("--points", "-p", type=int, default=DEFAULT_NUM_DATA_POINTS, help="Initial data points to generate")
    parser.add_argument("--devices", "-d", type=int, default=DEFAULT_NUM_DEVICES, help="Number of unique EUIs")
    parser.add_argument("--hours", type=float, default=DEFAULT_HOURS_BACK, help="How many hours back to spread generated timestamps")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducible datasets")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing DB without prompt")
    parser.add_argument("--continuous", "-c", action="store_true", help="Continuously append new rows")
    parser.add_argument("--interval", type=int, default=DEFAULT_CONTINUOUS_INTERVAL, help="Seconds between rows in continuous mode")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    if args.points <= 0:
        raise ValueError("--points must be > 0")
    if args.devices <= 0:
        raise ValueError("--devices must be > 0")
    if args.hours <= 0:
        raise ValueError("--hours must be > 0")
    if args.interval <= 0:
        raise ValueError("--interval must be > 0")

    db_path = os.path.abspath(args.output)
    continuous = args.continuous

    print("=" * 72)
    print("SENSOR NETWORK TEST GENERATOR")
    print("=" * 72)

    if os.path.exists(db_path):
        if continuous:
            print(f"\nUsing existing database: {db_path}")
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA foreign_keys = ON")
        else:
            if not args.overwrite:
                response = input(f"\n⚠️  Database '{db_path}' already exists. Overwrite? (y/N): ")
                if response.lower() != 'y':
                    print("Aborted.")
                    print("\nTip: use --overwrite to skip prompts")
                    return
            os.remove(db_path)
            print("Removed existing database.")

            print(f"\nCreating database: {db_path}")
            conn = create_database(db_path)
            populate_database(conn, args.points, args.devices, args.hours)
            print_statistics(conn)
    else:
        print(f"\nCreating database: {db_path}")
        conn = create_database(db_path)
        if not continuous:
            populate_database(conn, args.points, args.devices, args.hours)
            print_statistics(conn)

    if continuous:
        continuous_mode(conn, num_devices=args.devices, interval=args.interval)
    else:
        print("\n✓ Test database ready!")
        print("\nCreate a dataset:")
        print(f"  python generate_test_data.py --output {args.output} --overwrite")
        print("\nAppend live-ish rows:")
        print(f"  python generate_test_data.py --output {args.output} --continuous --interval {args.interval}")

    conn.close()


if __name__ == "__main__":
    main()
