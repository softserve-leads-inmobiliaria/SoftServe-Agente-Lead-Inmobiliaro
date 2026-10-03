"""Inventario y rotación (N-6): las consultas viven en `db/metrics/inventory.py`, que comparte con `core`
(el reporte semanal de N-10). Este módulo las reexporta para las vistas del panel (N-8)."""
from db.metrics.inventory import *  # noqa: F403
