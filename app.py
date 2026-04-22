import pkgutil
import importlib.util

# Python 3.14 removed pkgutil.find_loader; Dash 3.x still expects it.
# Provide a small compatibility shim so the app can run on Python 3.14+.
if not hasattr(pkgutil, "find_loader"):
    def find_loader(name, path=None):
        spec = importlib.util.find_spec(name)
        return spec.loader if spec else None

    pkgutil.find_loader = find_loader

import dash
import dash_bootstrap_components as dbc
from flask_compress import Compress

# Token Mapbox
MAPBOX_TOKEN = "pk.eyJ1Ijoidmlyb254IiwiYSI6ImNtZmF5NXV4NTBwOTAyd3FvOXRxdDR5bGgifQ.8ftkMDrhE7Wel0tGCZ2PCA"

# Inizializza l'app Dash
app = dash.Dash(__name__, 
                external_stylesheets=[dbc.themes.LUX, dbc.icons.BOOTSTRAP], 
                suppress_callback_exceptions=True, 
                update_title=None)

# Abilita la compressione dei dati inviati
compress = Compress()
compress.init_app(app.server)

server = app.server