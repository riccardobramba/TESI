import sqlite3
import os
import logging

# --- CONFIGURAZIONE LOGGING (Invariata) ---
LOG_FILE = "dashboard.log"
if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, "w") as f:
        f.write("")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def system_log(message, level="INFO"):
    if level.upper() == "ERROR":
        logging.error(message)
    elif level.upper() == "WARNING":
        logging.warning(message)
    else:
        logging.info(message)

# --- CONFIGURAZIONE PERCORSI ---
base_dir = os.path.dirname(os.path.abspath(__file__))
# CAMBIATO: Puntamento al nuovo database del prof
DB_FILE = os.path.join(base_dir, "data.db") 

def get_connection():
    """Ritorna una connessione al nuovo database"""
    return sqlite3.connect(DB_FILE)

def init_db():
    """
    Verifica solo se il database esiste. 
    Non creiamo più le tabelle qui perché usiamo quelle del prof.
    """
    if not os.path.exists(DB_FILE):
        print(f"ATTENZIONE: Il file {DB_FILE} non esiste!")
        print("Assicurati di aver copiato il file data.db nella cartella o di aver eseguito generate_test_data.py")
    else:
        print("Database data.db trovato e pronto all'uso.")

if __name__ == '__main__':
    init_db()