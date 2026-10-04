"""Siembra seis meses de historia sintética para la demo (N-9): cambios de estado, leads con su interés por
las propiedades, búsquedas sin resultados y cierres, sobre el inventario de C-9.

Sin historia, la vista de negocio del panel (N-8) y el reporte semanal (N-10) salen en cero. Todo lo que
escribe lleva `synthetic = true`, y esas vistas solo lo cuentan con `demo=1` (o con
WEEKLY_REPORT_INCLUDE_SYNTHETIC=1). La cola, la ficha, el funnel, el reparto de leads y las alertas no lo ven
nunca: no son personas a las que atender.

Qué muestra (propuestas.md §2.2):
- Zonas que rotan rápido (las rentas de Chapalita y Providencia, en días) y lento (las ventas y la preventa
  de Tlaquepaque y Andares, en meses).
- Estancadas con consultas y sin visitas (prop_00231: ¿el precio?) y sin ninguna consulta (prop_00232 y
  prop_00233: ¿la visibilidad?).
- Cierres atribuibles al bot (el lead consultó o visitó esa propiedad con el bot) y no atribuibles.
- Un tiempo de atención distinto por asesor (B-12): cada lead calificado trae `qualified_at` y `claimed_at`.

Reglas:
- Los estados pasan por `db/property_history.record_past`: el historial termina en el estado que la
  propiedad ya tiene, así que el seed no cambia el inventario. Una propiedad con historial real, o que ya
  no está en el estado del guion, no se toca: la historia sembrada podría contradecirla.
- Los leads no tienen teléfono ni correo, y su `telegram_user_id` empieza con `synthetic-`, que no choca
  con uno real (los de Telegram son números). No escribe en `funnel_events`: es append-only (DT-17) y esos
  leads ya no se podrían borrar.
- Determinista e idempotente: semilla fija, y cada corrida borra la historia anterior y la vuelve a sembrar
  en una sola transacción. Las fechas se cuentan hacia atrás desde hoy, así que dos corridas del mismo día
  dejan la base igual; con `--hoy` se fija el día.

Uso:
  python scripts/seed_history.py                    # siembra, o vuelve a sembrar, seis meses hasta ayer
  python scripts/seed_history.py --hoy 2026-10-03   # la misma historia, contada desde ese día
  python scripts/seed_history.py --borrar           # borra la historia sintética y nada más
"""
from __future__ import annotations

import argparse
import math
import random
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.metrics.inventory import LOCAL_TZ  # noqa: E402
from db.models import (  # noqa: E402
    Advisor,
    Deal,
    Lead,
    LeadScore,
    Property,
    PropertyInterest,
    PropertyStatusEvent,
    UnmetSearch,
)
from db.property_history import record_past  # noqa: E402
from db.session import get_engine, session_scope  # noqa: E402
from services.catalog.app.data import ADVISORS, PROPERTIES  # noqa: E402

SEED = 9
DAYS = 180
ACTOR = "seed"
LEAD_PREFIX = "synthetic-"
# Espacio de nombres de los ids de leads y cierres: los mismos en cada corrida.
IDS = uuid.UUID("5e1d0009-0000-4000-8000-000000000009")
BACKGROUND_LEADS = 160
UNMET_SEARCHES = 90

# El guion de cada propiedad: (días atrás, hora local, estado). Termina en el estado del catálogo.
TIMELINES: dict[str, list[tuple[int, int, str]]] = {
    # Providencia, venta: se cayó el apartado y lleva más de 90 días disponible, con consultas y sin visitas.
    "prop_00231": [(170, 10, "disponible"), (120, 16, "apartado"), (112, 11, "disponible")],
    # Providencia y Zapopan, venta: disponibles hace más de 30 y de 90 días, sin una sola consulta.
    "prop_00232": [(45, 10, "disponible")],
    "prop_00233": [(100, 10, "disponible")],
    "prop_00234": [(130, 10, "disponible"), (9, 13, "apartado")],
    # Chapalita y Providencia, renta: se rentan en menos de dos semanas y vuelven al mercado.
    "prop_00235": [(175, 10, "disponible"), (166, 17, "apartado"), (163, 12, "cerrado"), (3, 9, "disponible")],
    "prop_00236": [(80, 10, "disponible"), (70, 18, "apartado"), (67, 12, "cerrado"), (10, 9, "disponible"),
                   (2, 16, "apartado")],
    "prop_00240": [(60, 10, "disponible"), (54, 17, "apartado"), (52, 12, "cerrado"), (6, 9, "disponible")],
    # Andares, preventa: se vende una unidad y la propiedad sigue disponible con las demás.
    "prop_00237": [(180, 10, "disponible"), (100, 12, "cerrado"), (100, 13, "disponible")],
    "prop_00238": [(150, 10, "disponible"), (40, 16, "apartado"), (5, 12, "cerrado"), (5, 13, "disponible")],
    # Tlaquepaque, venta: casi cinco meses en el mercado.
    "prop_00239": [(160, 10, "disponible"), (30, 15, "apartado"), (20, 12, "cerrado")],
    # Recién publicadas.
    "prop_00241": [(25, 10, "disponible")],
    "prop_00242": [(20, 10, "disponible")],
}
# Un cierre por cada paso a `cerrado`: (asesor, si el lead habló con el bot de esa propiedad antes, DT-41).
DEALS: dict[str, tuple[str, bool]] = {
    "prop_00235": ("adv_001", True),
    "prop_00236": ("adv_001", False),  # el inquilino llegó por otro lado
    "prop_00240": ("adv_003", True),
    "prop_00237": ("adv_003", False),
    "prop_00238": ("adv_003", True),
    "prop_00239": ("adv_002", True),
}
NO_INTEREST = {"prop_00232", "prop_00233"}  # las estancadas "sin consultas"
NO_VISITS = {"prop_00231"}  # la estancada "consultas sin visitas"

ZONES = {"Providencia": 30, "Zapopan": 25, "Chapalita": 15, "Andares": 15, "Tlaquepaque": 10, None: 5}
CAMPAIGNS = {"Providencia": "camp_fb_providencia", "Andares": "camp_ig_andares", "Zapopan": "camp_google_zapopan"}
BANDS = {"COLD": 45, "WARM": 35, "HOT": 20}
BAND_TOTALS = {"COLD": (12, 40), "WARM": (41, 70), "HOT": (71, 94)}
# Mediana de lo que tarda cada asesor en tomar un lead calificado (B-12): uno rápido, uno lento. Son los
# asesores de scripts/seed_advisors.py, y todos los cierres son de ellos.
CLAIM_MINUTES = {"adv_001": 12, "adv_002": 150, "adv_003": 35}
# Búsquedas sin resultado: (zona, tipo, recámaras, presupuesto máximo, peso). La zona la escribe el lead.
UNMET = [
    ("Providencia", "renta", 3, 25000, 20),
    ("providencia", "renta", 3, 25000, 5),  # la misma zona escrita distinto: la vista las junta
    ("Chapalita", "venta", 2, 3000000, 12),
    ("Zapopan", "renta", 2, 15000, 10),
    ("Tlajomulco", "venta", 3, 2500000, 8),
    (None, "renta", 1, 8000, 6),
    ("Andares", "renta", 2, 30000, 6),
    ("Tlaquepaque", "preventa", None, None, 3),
]

_CATALOG = {prop["id"]: prop for prop in PROPERTIES}
_ZONE_ADVISORS = {zone: [a["id"] for a in ADVISORS if zone in a["zones"]] for zone in ZONES if zone}


@dataclass(frozen=True)
class Summary:
    leads: int
    events: int
    deals: int
    interest: int
    unmet: int
    skipped: dict[str, str]  # propiedad -> por qué no se tocó


# --- borrar ------------------------------------------------------------------------------------------

def purge(session: Session) -> dict[str, int]:
    """Borra todo lo sintético. Los puntajes, el interés y los cierres de esos leads se van en cascada."""
    removed = {}
    for name, model in (("deals", Deal), ("interest", PropertyInterest), ("unmet", UnmetSearch),
                        ("events", PropertyStatusEvent), ("leads", Lead)):
        removed[name] = session.execute(delete(model).where(model.synthetic.is_(True))).rowcount
    return removed


# --- sembrar -----------------------------------------------------------------------------------------

def _at(anchor: datetime, days_ago: int, hour: float) -> datetime:
    return anchor - timedelta(days=days_ago) + timedelta(hours=hour)


def _status_at(steps: list[tuple[datetime, str]], when: datetime) -> str | None:
    status = None
    for at, to in steps:
        if at > when:
            break
        status = to
    return status


def _score(rng: random.Random, lead_id: uuid.UUID, band: str) -> LeadScore:
    """Un puntaje BANT-E verosímil para la banda: cinco componentes de 0 a 20 que suman el total."""
    total = rng.randint(*BAND_TOTALS[band])
    parts = [0] * 5
    for _ in range(total):
        parts[rng.choice([i for i in range(5) if parts[i] < 20])] += 1
    need_fit, budget, authority, timing, eligibility = parts
    return LeadScore(lead_id=lead_id, need_fit=need_fit, budget=budget, authority=authority, timing=timing,
                     eligibility=eligibility, total=total, band=band, reasons=[])


def _claim_wait(rng: random.Random, advisor: str) -> timedelta:
    return timedelta(minutes=CLAIM_MINUTES[advisor] * math.exp(rng.gauss(0, 0.7)))


class _History:
    """Lo que se va a escribir, generado siempre en el mismo orden con la misma semilla."""

    def __init__(self, anchor: datetime):
        self.anchor = anchor
        self.rng = random.Random(SEED)
        self.timelines = {pid: [(_at(anchor, d, h), s) for d, h, s in steps] for pid, steps in TIMELINES.items()}
        self.leads: list[Lead] = []
        self.scores: list[LeadScore] = []
        self.interest: list[PropertyInterest] = []
        self.deals: list[Deal] = []
        self.unmet: list[UnmetSearch] = []

    def _lead(self, *, created: datetime, zone: str | None, band: str, advisor: str | None,
              qualified: datetime | None, claimed: datetime | None, status: str, campaign: str | None) -> Lead:
        n = len(self.leads) + 1
        lead = Lead(id=uuid.uuid5(IDS, f"lead-{n}"), telegram_user_id=f"{LEAD_PREFIX}{n:04d}", zone=zone,
                    status=status, advisor_id=advisor, qualified_at=qualified, claimed_at=claimed,
                    utm_campaign=campaign, synthetic=True, created_at=created,
                    updated_at=max(t for t in (created, qualified, claimed) if t is not None))
        score = _score(self.rng, lead.id, band)
        score.updated_at = lead.updated_at  # sin la hora de la corrida: dos corridas dejan lo mismo
        self.leads.append(lead)
        self.scores.append(score)
        return lead

    def _touch(self, lead: Lead, property_id: str, kind: str, at: datetime) -> None:
        if at < self.anchor:
            self.interest.append(PropertyInterest(lead_id=lead.id, property_id=property_id, kind=kind,
                                                  created_at=at, synthetic=True))

    def closings(self) -> None:
        """Un lead por cierre, que llega antes de la operación y termina en `closed_won`."""
        rng = self.rng
        for property_id, (advisor, attributed) in DEALS.items():
            steps = self.timelines[property_id]
            closed = next(at for at, status in steps if status == "cerrado")
            listed = steps[0][0]
            gap = min((closed - listed) * rng.uniform(0.3, 0.7), timedelta(days=50))
            created = closed - gap
            qualified = created + timedelta(minutes=rng.uniform(10, 360))
            prop = _CATALOG[property_id]
            lead = self._lead(created=created, zone=prop["zone"], band=rng.choices(["HOT", "WARM"], [8, 2])[0],
                              advisor=advisor, qualified=qualified, claimed=qualified + _claim_wait(rng, advisor),
                              status="closed_won", campaign=CAMPAIGNS.get(prop["zone"]) if rng.random() < 0.4 else None)
            if attributed:
                self._touch(lead, property_id, "anuncio" if lead.utm_campaign else "consulta",
                            created + timedelta(minutes=5))
                self._touch(lead, property_id, "visita", created + gap * rng.uniform(0.4, 0.8))
            price = Decimal(str(prop["price"]))
            if prop["type"] != "renta":
                price = (price * Decimal("0.97")).quantize(Decimal("10000"))
            self.deals.append(Deal(id=uuid.uuid5(IDS, f"deal-{property_id}"), property_id=property_id,
                                   lead_id=lead.id, advisor_id=advisor,
                                   kind="renta" if prop["type"] == "renta" else "venta",
                                   price=price, closed_at=closed, synthetic=True))

    def background(self) -> None:
        """Leads que llegan a lo largo de los seis meses: buscan, consultan, a veces visitan, y no cierran."""
        rng = self.rng
        for _ in range(BACKGROUND_LEADS):
            created = _at(self.anchor, rng.randint(1, DAYS), rng.uniform(8, 22))
            zone = rng.choices(list(ZONES), list(ZONES.values()))[0]
            campaign = CAMPAIGNS.get(zone) if rng.random() < 0.5 else None
            band = rng.choices(list(BANDS), list(BANDS.values()))[0]
            qualified = claimed = advisor = None
            status = rng.choices(["nuevo", "closed_lost"], [60, 40])[0]
            if band != "COLD":
                qualified = created + timedelta(minutes=rng.uniform(5, 240))
                advisor = rng.choice(_ZONE_ADVISORS.get(zone) or list(CLAIM_MINUTES))
                claimed = qualified + _claim_wait(rng, advisor)
                status = rng.choices(["sql", "visit_scheduled", "closed_lost"], [40, 25, 35])[0]
                if rng.random() < 0.08 or claimed >= self.anchor:  # nadie lo tomó (todavía)
                    claimed, status = None, "mql"
                if qualified >= self.anchor:
                    qualified = advisor = None
                    status = "nuevo"
            lead = self._lead(created=created, zone=zone, band=band, advisor=advisor, qualified=qualified,
                              claimed=claimed, status=status, campaign=campaign)

            on_market = sorted(pid for pid, prop in _CATALOG.items()
                               if pid in self.timelines and pid not in NO_INTEREST
                               and (zone is None or prop["zone"] == zone)
                               and _status_at(self.timelines[pid], created) == "disponible")
            for property_id in rng.sample(on_market, k=min(len(on_market), rng.randint(1, 3))):
                self._touch(lead, property_id, "busqueda", created + timedelta(minutes=rng.uniform(1, 5)))
                if campaign or rng.random() < 0.7:
                    self._touch(lead, property_id, "anuncio" if campaign else "consulta",
                                created + timedelta(minutes=rng.uniform(5, 30)))
                if status == "visit_scheduled" and property_id not in NO_VISITS:
                    self._touch(lead, property_id, "visita", created + timedelta(days=rng.uniform(1, 7)))

    def searches(self) -> None:
        rng = self.rng
        for _ in range(UNMET_SEARCHES):
            zone, kind, bedrooms, budget, _weight = rng.choices(UNMET, [u[-1] for u in UNMET])[0]
            self.unmet.append(UnmetSearch(
                zone=zone, type=kind, bedrooms=bedrooms,
                budget_max=Decimal(budget) if budget and rng.random() < 0.7 else None,
                created_at=_at(self.anchor, rng.randint(1, DAYS), rng.uniform(7, 23)), synthetic=True))


def _why_not(session: Session, property_id: str, final_status: str) -> str | None:
    """Por qué la historia de esta propiedad no se puede sembrar; None si se puede."""
    prop = session.get(Property, property_id)
    if prop is None:
        return "no está en la base"
    if prop.status != final_status:
        return f"está en {prop.status!r} y el guion termina en {final_status!r}"
    real = session.execute(select(exists().where(PropertyStatusEvent.property_id == property_id,
                                                 PropertyStatusEvent.synthetic.is_(False)))).scalar()
    if real:
        return "tiene historial real"
    return None


def _sow(session: Session, anchor: datetime) -> Summary:
    missing = sorted(set(CLAIM_MINUTES) - set(session.execute(select(Advisor.id)).scalars()))
    if missing:
        raise RuntimeError(f"faltan los asesores {', '.join(missing)}: correr antes scripts/seed_advisors.py")
    if session.get(Property, next(iter(TIMELINES))) is None:
        raise RuntimeError("el inventario está vacío: correr antes scripts/seed_catalog.py")

    history = _History(anchor)
    history.closings()
    history.background()
    history.searches()

    skipped = {pid: reason for pid, steps in TIMELINES.items()
               if (reason := _why_not(session, pid, steps[-1][2])) is not None}
    events = 0
    for property_id, steps in history.timelines.items():
        if property_id not in skipped:
            events += len(record_past(session, property_id, steps, ACTOR, synthetic=True))
    deals = [d for d in history.deals if d.property_id not in skipped]
    dropped = {d.lead_id for d in history.deals if d.property_id in skipped}  # el lead existía por el cierre
    leads = [lead for lead in history.leads if lead.id not in dropped]
    interest = [i for i in history.interest if i.property_id not in skipped and i.lead_id not in dropped]

    session.add_all(leads)
    session.flush()
    session.add_all([s for s in history.scores if s.lead_id not in dropped])
    session.add_all(deals + interest + history.unmet)
    session.flush()
    return Summary(leads=len(leads), events=events, deals=len(deals), interest=len(interest),
                   unmet=len(history.unmet), skipped=skipped)


def seed(engine=None, *, today: date | None = None) -> Summary:
    """Borra la historia sintética anterior y siembra seis meses que terminan el día antes de `today`."""
    today = today or datetime.now(LOCAL_TZ).date()
    anchor = datetime.combine(today, time.min, tzinfo=LOCAL_TZ)
    with session_scope(engine or get_engine()) as session:
        purge(session)
        return _sow(session, anchor)


def remove(engine=None) -> dict[str, int]:
    """Borra la historia sintética. No toca leads, cierres, eventos ni búsquedas reales."""
    with session_scope(engine or get_engine()) as session:
        return purge(session)


def main() -> None:
    parser = argparse.ArgumentParser(description="Historia sintética de seis meses para la demo (N-9).")
    parser.add_argument("--hoy", type=date.fromisoformat, help="el día de la demo (AAAA-MM-DD); por defecto, hoy")
    parser.add_argument("--borrar", action="store_true", help="borra la historia sintética y no siembra nada")
    args = parser.parse_args()

    if args.borrar:
        removed = remove()
        print("Historia sintética borrada: " + ", ".join(f"{n} {name}" for name, n in removed.items()))
        return
    summary = seed(today=args.hoy)
    print(f"Historia sintética de {DAYS} días: {summary.leads} leads, {summary.events} cambios de estado, "
          f"{summary.deals} cierres, {summary.interest} interacciones y {summary.unmet} búsquedas sin resultado.")
    for property_id, reason in summary.skipped.items():
        print(f"  {property_id} no se tocó: {reason}")
    print("Se ve en el panel con /panel/<token>/negocio?demo=1")


if __name__ == "__main__":
    main()
