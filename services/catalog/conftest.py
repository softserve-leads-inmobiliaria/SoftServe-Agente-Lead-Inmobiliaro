"""Raíz de pytest para el catalog service.

Agrega `services/catalog/` al path de Python para que `from app import ...`
funcione al correr los tests desde la raíz del repo sin tener que setear
PYTHONPATH manualmente.
"""
import sys
from pathlib import Path

# services/catalog/ — la carpeta que contiene el paquete `app`
sys.path.insert(0, str(Path(__file__).parent))
