"""Atribución al bot y conversión (N-7): las consultas viven en `db/metrics/attribution.py`, que comparte
con `core` (el reporte semanal de N-10). Este módulo las reexporta para las vistas del panel (N-8)."""
from db.metrics.attribution import *  # noqa: F403
