import sys
from pathlib import Path

# Permite `from app.scoring import ...` sin instalar el paquete (la estructura de CI llega con CT-5).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
