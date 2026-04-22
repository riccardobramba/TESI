import sqlite3
import time
import random
import os

DB_FILE = "dashboard.db"
EVENT_SIMULATION_INTERVAL_SEC = 45

def get_sensors(cursor):
    try:
        cursor.execute("SELECT sensor_id, sensor_type, polling_frequency_sec FROM Sensors")
        return cursor.fetchall()
    except sqlite3.Error as e:
        print(f"Errore nel recuperare i sensori: {e}")
        return []

def get_node_ids(cursor):
    try:
        cursor.execute("SELECT node_id FROM Nodes")
        return [item[0] for item in cursor.fetchall()]
    except sqlite3.Error as e:
        print(f"Errore nel recuperare gli ID dei nodi: {e}")
        return []

def insert_reading(cursor, sensor_id, value):
    try:
        cursor.execute(
            "INSERT INTO SensorReadings (sensor_id, value) VALUES (?, ?)",
            (sensor_id, value)
        )
    except sqlite3.Error as e:
        print(f"Errore durante l'inserimento della lettura per il sensore {sensor_id}: {e}")

def simulate_and_insert_event(cursor, node_ids):
    if not node_ids:
        return

    event_types = ['PACKET_RX', 'PACKET_TX', 'BATTERY_LOW', 'DEVICE_OFF', 'DISCONNECTED', 'CONNECTED']
    
    random_node_id = random.choice(node_ids)
    random_event_type = random.choice(event_types)
    
    description = ""
    source_node_id = None
    
    other_nodes = [n for n in node_ids if n != random_node_id]
    if other_nodes:
        if random_event_type in ['PACKET_RX', 'CONNECTED']:
            source_node_id = random.choice(other_nodes)
            description = f"Connesso al nodo {source_node_id}" if random_event_type == 'CONNECTED' else f"Ricevuto pacchetto dal nodo {source_node_id}"
        elif random_event_type == 'PACKET_TX':
            source_node_id = random.choice(other_nodes)
            description = f"Inviato comando al nodo {source_node_id}"

    if random_event_type == 'BATTERY_LOW':
        description = f"Livello batteria critico: {random.randint(5, 15)}%"
    elif random_event_type == 'DEVICE_OFF':
        description = "Spegnimento improvviso per anomalia"
    elif random_event_type == 'DISCONNECTED':
        description = "Connessione persa con il parent"

    try:
        cursor.execute(
            "INSERT INTO Events (node_id, event_type, description, source_node_id) VALUES (?, ?, ?, ?)",
            (random_node_id, random_event_type, description, source_node_id)
        )
        print(f"  -> Evento generato: [Nodo: {random_node_id}, Tipo: {random_event_type}, Desc: '{description}']")
    except sqlite3.Error as e:
        print(f"Errore durante l'inserimento dell'evento: {e}")


def generate_mock_value(sensor_type, last_value=None):
    if sensor_type == 'temperature':
        return round(random.uniform(18.5, 23.5), 2)
    elif sensor_type == 'humidity':
        return round(random.uniform(45.0, 65.0), 2)
    elif sensor_type == 'battery':
        if last_value is None:
            return 100.0
        discharge = random.uniform(0.1, 0.5)
        new_value = max(0, last_value - discharge)
        return round(new_value, 2)
    else:
        return round(random.uniform(0.0, 100.0), 2)

def main():
    if not os.path.exists(DB_FILE):
        print(f"Errore: Il file del database '{DB_FILE}' non trovato.")
        print("Esegui prima 'database.py' per crearlo.")
        return

    conn = None
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        sensors_from_db = get_sensors(cursor)
        node_ids = get_node_ids(cursor)

        if not sensors_from_db or not node_ids:
            print("Database non popolato correttamente. Esegui 'database.py'.")
            return

        sensor_states = {
            s_id: {
                "type": s_type, "poll_freq": s_freq, "last_update": 0,
                "last_value": 100.0 if s_type == 'battery' else None
            } for s_id, s_type, s_freq in sensors_from_db
        }
        last_event_time = 0

        while True:
            current_time = time.time()
            something_updated = False
            
            print(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} --- Controllo operazioni...")

            for sensor_id, state in sensor_states.items():
                if current_time - state['last_update'] >= state['poll_freq']:
                    new_value = generate_mock_value(state['type'], state.get('last_value'))
                    insert_reading(cursor, sensor_id, new_value)
                    state['last_update'] = current_time
                    state['last_value'] = new_value
                    print(f"  -> Lettura: [SensorID: {sensor_id}, Tipo: {state['type'].capitalize()}, Valore: {new_value}]")
                    something_updated = True
            
            if current_time - last_event_time >= EVENT_SIMULATION_INTERVAL_SEC:
                simulate_and_insert_event(cursor, node_ids)
                last_event_time = current_time
                something_updated = True

            if something_updated:
                conn.commit()
                print("Modifiche salvate con successo nel database.")
            
            time.sleep(10)

    except sqlite3.Error as e:
        print(f"Si è verificato un errore con il database: {e}")
    except KeyboardInterrupt:
        print("\nSimulazione interrotta dall'utente.")
    finally:
        if conn:
            conn.close()
            print("Connessione al database chiusa.")

if __name__ == '__main__':
    main()