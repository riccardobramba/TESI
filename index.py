import database
from app import app, server

__all__ = ["app", "server", "main"]


def main():
    # 1. Inizializzazione Database
    database.init_db()

    # 2. Layout con dati già negli store (mappa e statistiche al primo caricamento)
    import queries
    import layout as layout_mod

    payload = queries.load_dashboard_store_payload()
    app.layout = layout_mod.build_layout(payload)

    import callbacks  # registra callback dopo il layout
    _ = callbacks

    print("--- Dashboard Ready ---")
    # Il parametro dev_tools_props_check=False aiuta a evitare avvisi fastidiosi
    app.run(debug=True, port=8050)

if __name__ == '__main__':
    main()