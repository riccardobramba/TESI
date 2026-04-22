from dash import dcc, html
import dash_bootstrap_components as dbc


def build_layout(store_payload=None):
    """store_payload: dict da queries.load_dashboard_store_payload() per primo paint già popolato."""
    p = store_payload or {}
    return dbc.Container([
    dcc.Store(id='nodes-data-store', data=p.get('nodes')),
    dcc.Store(id='links-data-store', data=p.get('links')),
    dcc.Store(id='general-stats-store', data=p.get('stats')),
    dcc.Store(id='selected-node-store'),
    dcc.Store(id='full-trend-data-store'),
    dcc.Store(id='last-update-timestamp-store', data=p.get('last_update')),
    dcc.Store(id='refresh-trigger-store'),     # Usato per forzare un aggiornamento da altre callback
    dcc.Store(id='edit-mode-store', data={'is_editing': False, 'node_id': None}), # Tiene traccia se siamo in modalità "modifica posizione"
    dcc.Store(id='settings-store', storage_type='session', data={'refresh_interval_ms': 15000, 'downsample_rule': 'h'}), # Salva le impostazioni (intervallo, downsample)
    dcc.Store(id='active-graph-store', data='temperature'), # Salva quale grafico mostrare (temp, hum, batt)
    dcc.Store(id='quick-time-range-store', data='24h'),     # Filtro rapido trend: 1h/6h/24h/all
    dcc.Store(id='last_trend_timestamp_store'),
    dcc.Store(id='avg-temp-trend-store', data=p.get('avg_temp')),
    dcc.Store(id='avg-humidity-trend-store', data=p.get('avg_hum')),
    dcc.Store(id='avg-battery-trend-store', data=p.get('avg_batt')),
    dcc.Store(id='point-index-to-id-map-store'), # Dizionario che mappa l'indice del punto sulla mappa al node_id
    dcc.Store(id='graph-refresh-trigger'),     # Trigger specifico per aggiornare il grafico piccolo
    dcc.Store(id='node-health-store'),         # Snapshot stato semafori (sincronizzazione UI)
    dcc.Store(id='modal-metric-switch-targets'),

    # Timer principale per il polling dei dati (es. ogni 15 sec)
    dcc.Interval(id='interval-component', interval=15 * 1000, n_intervals=0, disabled=True),
    # Timer secondario (ogni sec) per aggiornamenti UI veloci
    dcc.Interval(id='timer-update-interval', interval=1 * 1000, n_intervals=0),
    
    # Riga per il Titolo principale
    dbc.Row(dbc.Col(html.H1("Dashboard", className="text-center text-primary mb-4 mt-4"), width=12)),
    
    # Riga principale che divide l'app in due colonne (Sinistra e Destra)
    dbc.Row([
        
        # Colonna di SINISTRA (Mappa e Controlli)
        dbc.Col([
            
            dbc.Card([
                # Intestazione della Card
                dbc.CardHeader(
                    dbc.Row([
                        dbc.Col(html.H5("Mappa della rete", className="mb-0")),
                        # Icona 'info' cliccabile
                        dbc.Col(
                            html.I(className="bi bi-info-circle-fill", id="map-help-icon", n_clicks=0),
                            width="auto",
                            style={'cursor': 'pointer'}
                        ),
                    ], justify="between", align="center")
                ),
                # Corpo della Card
                dbc.CardBody(
                    html.Div([
                        # Qui viene disegnata la mappa (gestita dalla Callback 5)
                        dcc.Graph(id='network-map', style={'height': '62vh'}), 
                        
                        # Mirino
                        html.Div(
                            "+", 
                            id='map-crosshair', 
                            style={
                                'position': 'absolute', 
                                'top': '50%', 
                                'left': '50%', 
                                'transform': 'translate(-50%, -50%)', # Centra perfettamente
                                'fontSize': '30px', 
                                'color': 'yellow', 
                                'pointerEvents': 'none', # Fa sì che i click "passino attraverso"
                                'display': 'none' # Nascosto di default
                            })], 
                        style={'position': 'relative'}
                    )
                )
            ]),
            
            # Pop-up di aiuto per la mappa
            dbc.Popover(
                [
                    dbc.PopoverHeader("Guida all'uso della mappa"),
                    dbc.PopoverBody([
                        html.P("- Seleziona un nodo con un singolo click per vederne i dettagli. Stessa procedura per deselezionare"),
                        html.P("- Seleziona più nodi usando gli strumenti (lazo o rettangolo) dalla barra che appare in alto a destra sulla mappa."),
                        html.P("- Deseleziona tutto cliccando due volte su un'area vuota della mappa."),
                        html.P("- Per modificare la posizione di un nodo, selezionalo, clicca 'Cambia posizione', sposta la mappa e clicca 'Salva'."),
                    ]),
                ],
                id="map-help-popover",
                target="map-help-icon", # Si collega all'icona 'info'
                trigger="click", # Appare al click
            ),
            
            # Riga per i controlli sotto la mappa
            dbc.Row([
                # Pulsante di aggiornamento manuale
                dbc.Col(
                    dbc.Button("Poll", id="refresh-button", color="primary", className="w-100"), width=5, className="mt-3"), 
                # Colonna per il countdown e la checkbox
                dbc.Col([
                    # Testo del countdown (aggiornato da JS)
                    html.Div(
                        id='countdown-timer-output', 
                        className="text-start fw-bold ps-2"
                    ), 
                    # Checkbox per abilitare/disabilitare il polling automatico
                    dbc.Checkbox(
                        id='auto-refresh-checkbox', 
                        label="Polling automatico", 
                        value=False, className="mt-1 ps-2"
                    ), 
                ], 
                width=7, 
                className="mt-3 border-start" 
                ), 
            ], 
            align="center"
            )
        ], width=6), # Fine colonna sinistra
        
        # Colonna di DESTRA (Schede e Grafici)
        dbc.Col([
            # Contenitore principale per le schede
            dbc.Tabs(
                id="right-panel-tabs",
                active_tab="tab-generale", # Scheda visualizzata di default
                children=[
                    
                    # Scheda 1: "Generale"
                    dbc.Tab(
                        label="Generale",
                        tab_id="tab-generale",
                        children=[
                            # Riga per le statistiche
                            dbc.Row([
                                # Card per le statistiche (Temp, Hum, Batt)
                                dbc.Col(dbc.Card([dbc.CardHeader("Statistiche generali"), dbc.CardBody(id='general-stats-container')]), width=6),
                                # Card test per lo stato server
                                dbc.Col(dbc.Card([dbc.CardHeader("Stato server"), dbc.CardBody(id='node-health-container')]), width=6),
                            ], className="mt-3"),
                            dbc.Row(
                                dbc.Col(
                                    dbc.Card(
                                        dbc.CardBody(
                                            dbc.Row(
                                                [
                                                    dbc.Col(html.Small("Attuale", className="text-muted")),
                                                    dbc.Col(html.Small("Massimo (24h)", className="text-muted")),
                                                    dbc.Col(html.Small("Minimo (24h)", className="text-muted")),
                                                ],
                                                id='trend-kpi-row',
                                                className="g-2",
                                            )
                                        )
                                    ),
                                    width=12,
                                ),
                                className="mt-3",
                            ),
                            
                            # Sezione del grafico piccolo
                            dbc.Card(
                                [
                                    # Titolo del grafico (es. "Temperatura Media (Oggi)")
                                    dbc.CardHeader(
                                        dbc.Row(
                                            [
                                                dbc.Col(html.Span(id='trend-graph-title'), width=True),
                                                dbc.Col(
                                                    dcc.Dropdown(
                                                        id='graph-metric-selector',
                                                        options=[
                                                            {'label': 'Temperatura', 'value': 'temperature'},
                                                            {'label': 'Umidità', 'value': 'humidity'},
                                                            {'label': 'Batteria', 'value': 'battery'},
                                                        ],
                                                        value='temperature',
                                                        clearable=False,
                                                        style={'minWidth': '180px'},
                                                    ),
                                                    width="auto"
                                                ),
                                                dbc.Col(
                                                    html.Small(
                                                        id='trend-graph-updated-badge',
                                                        className="text-muted"
                                                    ),
                                                    width="auto"
                                                ),
                                            ],
                                            justify="between",
                                            align="center",
                                            className="g-2",
                                        )
                                    ),
                                    dbc.CardBody(
                                        html.Div(
                                            id='temp-trend-card-clickable',
                                            n_clicks=0,
                                            style={'cursor': 'pointer'},
                                            children=[
                                                # Grafico piccolo
                                                dcc.Graph(id='trend-graph', style={'height': '16vh'}, config={'displayModeBar': False})
                                            ]
                                        )
                                    )
                                ],
                                className="mb-3"
                            ),
                            # Riga per l'alert informativo e l'editor di posizione
                            dbc.Row([
                                # Alert che indica quale nodo è stato selezionato
                                dbc.Col(html.Div(id='node-info-output'), width=6),
                                # Contenitore per i pulsanti "Modifica Posizione" / "Salva" / "Annulla"
                                dbc.Col(html.Div(id='position-editor-container'), width=6)
                            ])
                        ]
                    ),
                    
                    # Scheda 2: "InfoNodo"
                    dbc.Tab(
                        label="InfoNodo",
                        tab_id="tab-infonodo",
                        children=[
                            # Div riempito (dalla Callback 7) con i dettagli
                            # del nodo o dei nodi selezionati.
                            html.Div(id='infonodo-tab-content', className="mt-3")
                        ]
                    ),
                    
                    # Scheda 2.5: "InfoArchi"
                    dbc.Tab(
                        label="Info Archi",
                        tab_id="tab-infoarchi",
                        children=[
                            html.Div(id='infoarchi-tab-content', className="mt-3")
                        ]
                    ),
                    dbc.Tab(
                        label="Allarmi",
                        tab_id="tab-allarmi",
                        children=[
                            dbc.Card(
                                [
                                    dbc.CardBody([
                                        html.H5("Allarmi smart (ultimi eventi)", className="card-title"),
                                        html.Div(id='smart-alerts-container')
                                    ])
                                ],
                                className="mt-3"
                            )
                        ]
                    ),
                    
                    # Scheda 3: "Settings"
                    dbc.Tab(
                        label="Settings",
                        tab_id="tab-settings",
                        children=[
                            # Impostazioni del polling
                            dbc.Card(
                                dbc.CardBody([
                                    html.H5("Polling automatico", className="card-title"),
                                    dbc.Row([
                                        dbc.Col(dbc.Label("Ore:"), width="auto"),
                                        dbc.Col(dbc.Input(id='hours-input', type='number', min=0, placeholder="H"), width=2),
                                        dbc.Col(dbc.Label("Min:"), width="auto"),
                                        dbc.Col(dbc.Input(id='minutes-input', type='number', min=0, max=59, placeholder="M"), width=2),
                                        dbc.Col(dbc.Label("Sec:"), width="auto"),
                                        dbc.Col(dbc.Input(id='seconds-input', type='number', min=0, max=59, step=5, placeholder="S"), width=2),
                                        dbc.Col(dbc.Button("Salva", id="save-interval-button", color="primary", className="w-100"), width="auto"),
                                    ], align="center", className="g-2"),
                                    # Spazio per i messaggi di stato
                                    html.Div(id='interval-save-status', className="mt-2")
                                ]),
                                className="mt-3"
                            ),
                            # Impostazioni di downsampling (grafico piccolo)
                            dbc.Card(
                                dbc.CardBody([
                                    html.H5("Dettagli Grafici (Downsampling)", className="card-title"),
                                    html.P("Scegli quanti punti visualizzare per i grafici a linee...", className="card-text small text-muted"),
                                    dbc.Row([
                                        dbc.Col(
                                            dcc.Dropdown(
                                                id='downsample-setting-dropdown',
                                                options=[
                                                    {'label': 'Ogni 15 minuti', 'value': '15min'},
                                                    {'label': 'Ogni 30 minuti', 'value': '30min'},
                                                    {'label': 'Ogni 1 ora', 'value': '1h'},
                                                    {'label': 'Ogni 2 ore', 'value': '2h'},
                                                    {'label': 'Ogni 4 ore', 'value': '4h'},
                                                    {'label': 'Nessun raggruppamento (Dati grezzi)', 'value': 'raw'}
                                                ],
                                                value='1h',
                                                clearable=False
                                            )
                                        ),
                                        dbc.Col(dbc.Button("Salva", id="save-downsample-button", color="primary"), width="auto")
                                    ], align="center"),
                                    html.Div(id='downsample-save-status', className="mt-2")
                                ]),
                                className="mt-3"
                            ),
                            # Log
                            dbc.Card(
                                dbc.CardBody([
                                    html.H5("Log Eventi", className="card-title"),
                                    html.P("Clicca sull'ultimo evento per vedere/nascondere lo storico.", className="card-text small text-muted"),
                                    
                                    # Div cliccabile per aprire/chiudere il log completo
                                    html.Div(
                                        id="log-toggler",
                                        children=[
                                            # Mostra solo l'ultimo log
                                            html.Pre(id='log-preview', className="p-2 bg-light text-dark rounded")
                                        ],
                                        style={'cursor': 'pointer'}
                                    ),
                                    
                                    # Componente 'Collapse' che mostra/nasconde il log completo
                                    dbc.Collapse(
                                        html.Pre(id='log-full-output', style={'maxHeight': '180px', 'overflowY': 'auto'}, className="p-2 bg-light text-dark rounded mt-2"),
                                        id='log-collapse',
                                        is_open=False # Nascosto di default
                                    )
                                ]),
                                className="mt-3"
                            ),
                        ]
                    ),
                ]
            )
        ], width=6, className="scrollable-column"), # Fine colonna destra
    ], className="main-content-row"),
    
    # Modale per il grafico ingrandito
    dbc.Modal([
        dbc.ModalHeader(
            dbc.Row(
                [
                    dbc.Col(dbc.ModalTitle(id='modal-title'), width=True),
                    dbc.Col(
                        dbc.ButtonGroup(
                            [
                                dbc.Button(id='modal-switch-btn-1', size='sm', color='secondary', outline=True, n_clicks=0),
                                dbc.Button(id='modal-switch-btn-2', size='sm', color='secondary', outline=True, n_clicks=0),
                            ]
                        ),
                        width='auto'
                    ),
                ],
                justify="between",
                align="center",
                className="g-2",
            )
        ), # Titolo del modale
        dbc.ModalBody([
            # Controlli giornalieri
            html.Div(id='daily-view-controls', children=[
                dbc.Row([
                    # Calendario per scegliere il giorno
                    dbc.Col(dcc.DatePickerSingle(id='date-picker-single', display_format='DD/MM/YYYY', placeholder='Seleziona una data...'), width=4), 
                    # Slider per scegliere l'intervallo orario
                    dbc.Col(
                        html.Div(
                            id='modal-slider-container', 
                            children=[dcc.RangeSlider(id='modal-time-range-slider', step=1800, tooltip={'style': {'display': 'none'}})]
                        ), width=8)
                ], align="center", className="mb-1"),
                # Testo che mostra l'orario selezionato
                html.P(id='slider-tooltip-output', className='text-center fw-bold'),
            ]),

            # Il grafico grande che appare nel modale
            dcc.Graph(id='modal-temp-graph', style={'height': '60vh'})
        ]),
    ], 
    id='modal-graph', # ID del modale
    size="xl", # Dimensioni
    is_open=False # Nascosto all'avvio
    ),
], fluid=True, style={"backgroundColor": "#ffffff", "minHeight": "100vh", "color": "#000000"})


layout = build_layout()