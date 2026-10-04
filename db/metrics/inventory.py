"""Inventario y rotación (N-6). Solo lee de Postgres, con SQL explícito, como `attribution.py`.

Para el gerente: cuánto se colocó en un periodo, qué tan rápido rota cada zona, qué propiedades están
estancadas y qué piden los leads que no hay. Vista agregada, como la de N-7: no devuelve leads ni texto
de la conversación, solo propiedades y conteos.

Reglas de conteo:
- **Colocada** es una operación de `deals` con `closed_at` dentro del periodo `[start, end)`. El "tipo" es
  el de la propiedad (`venta`, `renta`, `preventa`), igual en todas las consultas.
- **Días en el mercado:** de la primera vez que la propiedad entró a `disponible` (`property_status_events`)
  al cierre. Una propiedad sin ese evento no se puede medir y no cuenta.
- **Estancada:** `disponible` a la fecha de corte (`as_of`), desde hace más de 30, 60 o 90 días. El estado al
  corte es el del último evento de `property_status_events` hasta esa fecha, no el de hoy: el reporte semanal
  corta el domingo anterior, y una propiedad que entonces estaba rentada no estaba estancada aunque hoy haya
  vuelto al mercado (#177). Una propiedad sin historial no se puede medir y no aparece: hoy es el caso del
  inventario del *seed* sin la historia sintética de N-9, que es la única que lo tiene.
- **Consultas** son leads distintos con `anuncio` o `consulta` en `property_interest`; **visitas**, leads
  distintos con `visita`. Se cuentan leads, no filas: la misma consulta en dos turnos es un lead (N-4).

`include_synthetic=False` deja fuera la historia sembrada por N-9. `properties` no tiene esa marca: cuenta la
de la fila que se lee (la operación, el evento, el interés o la búsqueda).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

LOCAL_TZ = timezone(timedelta(hours=-6))  # hora de Guadalajara, la misma de la agenda (DT-32)
STAGNANT_DAYS = (30, 60, 90)
INQUIRY_KINDS = ("anuncio", "consulta")
VISIT_KIND = "visita"
NO_INQUIRIES = "sin consultas"
INQUIRIES_WITHOUT_VISITS = "consultas sin visitas"
RANKING_LIMIT = 10
UNMET_LIMIT = 10

# Las tres agrupaciones de las colocadas. Los nombres de columna son fijos: nunca vienen del usuario.
_PLACED_GROUPS = {"zone": "p.zone", "type": "p.type", "advisor": "coalesce(a.name, d.advisor_id)"}

_PLACED = """
    SELECT {group} AS name, count(*) AS deals, coalesce(sum(d.price), 0) AS amount
    FROM deals d
    JOIN properties p ON p.id = d.property_id
    LEFT JOIN advisors a ON a.id = d.advisor_id
    WHERE d.closed_at >= :start AND d.closed_at < :end AND (:synthetic OR NOT d.synthetic)
    GROUP BY {group}
    ORDER BY deals DESC, name
"""

# El primer `disponible` de la propiedad antes del cierre: el inicio de su tiempo en el mercado.
_DAYS_ON_MARKET = """
    SELECT {group} AS name,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM d.closed_at - listed.at)) AS p50,
           count(*) AS deals
    FROM deals d
    JOIN properties p ON p.id = d.property_id
    JOIN LATERAL (
        SELECT min(e.changed_at) AS at FROM property_status_events e
        WHERE e.property_id = d.property_id AND e.to_status = 'disponible' AND e.changed_at <= d.closed_at
    ) AS listed ON listed.at IS NOT NULL
    WHERE d.closed_at >= :start AND d.closed_at < :end AND (:synthetic OR NOT d.synthetic)
    GROUP BY {group}
    ORDER BY name
"""

# El estado de cada propiedad al corte: su último evento hasta esa fecha.
_STAGNANT = text("""
    WITH at_cutoff AS (
        SELECT DISTINCT ON (e.property_id) e.property_id, e.to_status, e.changed_at
        FROM property_status_events e
        WHERE e.changed_at <= :as_of AND (:synthetic OR NOT e.synthetic)
        ORDER BY e.property_id, e.changed_at DESC, e.id DESC
    ),
    interest AS (
        SELECT pi.property_id,
               count(DISTINCT pi.lead_id) FILTER (WHERE pi.kind = ANY(:inquiry_kinds)) AS inquiries,
               count(DISTINCT pi.lead_id) FILTER (WHERE pi.kind = :visit_kind) AS visits
        FROM property_interest pi
        WHERE pi.created_at >= :start AND pi.created_at < :end AND (:synthetic OR NOT pi.synthetic)
        GROUP BY pi.property_id
    )
    SELECT p.id, p.title, p.zone, p.type, p.price, s.changed_at AS since,
           coalesce(i.inquiries, 0) AS inquiries, coalesce(i.visits, 0) AS visits
    FROM properties p
    JOIN at_cutoff s ON s.property_id = p.id
    LEFT JOIN interest i ON i.property_id = p.id
    WHERE s.to_status = 'disponible' AND s.changed_at <= :as_of - make_interval(days => :min_days)
    ORDER BY s.changed_at, p.id
""")

_RANKING = text("""
    WITH interest AS (
        SELECT pi.property_id,
               count(DISTINCT pi.lead_id) FILTER (WHERE pi.kind = ANY(:inquiry_kinds)) AS inquiries,
               count(DISTINCT pi.lead_id) FILTER (WHERE pi.kind = :visit_kind) AS visits
        FROM property_interest pi
        WHERE pi.created_at >= :start AND pi.created_at < :end AND (:synthetic OR NOT pi.synthetic)
        GROUP BY pi.property_id
    ),
    closes AS (
        SELECT d.property_id, count(*) AS deals
        FROM deals d
        WHERE d.closed_at >= :start AND d.closed_at < :end AND (:synthetic OR NOT d.synthetic)
        GROUP BY d.property_id
    )
    SELECT p.id, p.title, p.zone, p.type,
           coalesce(i.inquiries, 0) AS inquiries, coalesce(i.visits, 0) AS visits,
           coalesce(c.deals, 0) AS deals
    FROM properties p
    LEFT JOIN interest i ON i.property_id = p.id
    LEFT JOIN closes c ON c.property_id = p.id
    WHERE i.property_id IS NOT NULL OR c.property_id IS NOT NULL
    ORDER BY deals DESC, visits DESC, inquiries DESC, p.id
    LIMIT :limit
""")

# La zona la escribe el LLM o el lead: "providencia" y "Providencia" son la misma, y se muestra la forma
# más repetida.
_UNMET = text("""
    SELECT mode() WITHIN GROUP (ORDER BY trim(u.zone)) AS zone, u.type, u.bedrooms, count(*) AS searches
    FROM unmet_searches u
    WHERE u.created_at >= :start AND u.created_at < :end AND (:synthetic OR NOT u.synthetic)
    GROUP BY lower(trim(u.zone)), u.type, u.bedrooms
    ORDER BY searches DESC, zone NULLS LAST, u.type NULLS LAST, u.bedrooms NULLS LAST
    LIMIT :limit
""")


@dataclass(frozen=True)
class PlacedGroup:
    name: str  # la zona, el tipo o el asesor
    deals: int
    amount: Decimal  # suma de `deals.price`


@dataclass(frozen=True)
class Placements:
    deals: int
    amount: Decimal
    by_zone: list[PlacedGroup]
    by_type: list[PlacedGroup]
    by_advisor: list[PlacedGroup]


@dataclass(frozen=True)
class DaysOnMarket:
    name: str  # la zona o el tipo
    median_days: float
    deals: int  # cierres medidos: los que tienen un `disponible` previo en el historial


@dataclass(frozen=True)
class StagnantProperty:
    property_id: str
    title: str
    zone: str
    type: str
    price: Decimal
    available_since: datetime
    days: int
    threshold: int  # 30, 60 o 90: el mayor que supera
    inquiries: int  # del periodo
    visits: int  # del periodo

    @property
    def demand(self) -> str | None:
        """"sin consultas", "consultas sin visitas", o None si tuvo visitas en el periodo."""
        if self.visits:
            return None
        return INQUIRIES_WITHOUT_VISITS if self.inquiries else NO_INQUIRIES


@dataclass(frozen=True)
class RankedProperty:
    property_id: str
    title: str
    zone: str
    type: str
    inquiries: int
    visits: int
    deals: int


@dataclass(frozen=True)
class UnmetDemand:
    zone: str | None  # None: la búsqueda no filtró por ese campo
    type: str | None
    bedrooms: int | None
    searches: int


@dataclass(frozen=True)
class InventoryReport:
    start: datetime
    end: datetime
    as_of: datetime
    include_synthetic: bool
    placements: Placements
    days_on_market_by_zone: list[DaysOnMarket]
    days_on_market_by_type: list[DaysOnMarket]
    stagnant: list[StagnantProperty]
    ranking: list[RankedProperty]
    unmet_demand: list[UnmetDemand]


# --- periodos ----------------------------------------------------------------------------------------

def _local_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=LOCAL_TZ)


def week_of(day: date) -> tuple[datetime, datetime]:
    """La semana de `day`, de lunes a lunes, en hora local."""
    monday = day - timedelta(days=day.weekday())
    return _local_midnight(monday), _local_midnight(monday + timedelta(days=7))


def month_of(day: date) -> tuple[datetime, datetime]:
    """El mes de `day`, del día 1 al día 1 del mes siguiente, en hora local."""
    first = day.replace(day=1)
    following = (first + timedelta(days=32)).replace(day=1)
    return _local_midnight(first), _local_midnight(following)


def _params(start: datetime, end: datetime, include_synthetic: bool) -> dict:
    if end <= start:
        raise ValueError("el periodo termina antes de empezar")
    return {"start": start, "end": end, "synthetic": include_synthetic}


# --- consultas ---------------------------------------------------------------------------------------

def placements(session: Session, start: datetime, end: datetime, *,
               include_synthetic: bool = False) -> Placements:
    """Las operaciones cerradas del periodo, en total y por zona, tipo y asesor. Más cierres primero."""
    params = _params(start, end, include_synthetic)

    def grouped(key: str) -> list[PlacedGroup]:
        query = text(_PLACED.format(group=_PLACED_GROUPS[key]))
        return [PlacedGroup(name=row.name, deals=row.deals, amount=Decimal(row.amount))
                for row in session.execute(query, params)]

    by_zone = grouped("zone")
    return Placements(deals=sum(g.deals for g in by_zone), amount=sum((g.amount for g in by_zone), Decimal(0)),
                      by_zone=by_zone, by_type=grouped("type"), by_advisor=grouped("advisor"))


def days_on_market(session: Session, start: datetime, end: datetime, by: str = "zone", *,
                   include_synthetic: bool = False) -> list[DaysOnMarket]:
    """Mediana de días en el mercado de lo que se cerró en el periodo, por `zone` o por `type`."""
    if by not in ("zone", "type"):
        raise ValueError("by debe ser 'zone' o 'type'")
    query = text(_DAYS_ON_MARKET.format(group=_PLACED_GROUPS[by]))
    return [DaysOnMarket(name=row.name, median_days=round(float(row.p50) / 86400, 1), deals=row.deals)
            for row in session.execute(query, _params(start, end, include_synthetic))]


def stagnant(session: Session, start: datetime, end: datetime, *, as_of: datetime | None = None,
             include_synthetic: bool = False) -> list[StagnantProperty]:
    """Las propiedades `disponible` hace más de 30 días a `as_of` (por defecto, el fin del periodo), con
    sus consultas y visitas del periodo. Las más antiguas primero."""
    cutoff = as_of or end
    params = _params(start, end, include_synthetic) | {
        "as_of": cutoff, "min_days": STAGNANT_DAYS[0], "inquiry_kinds": list(INQUIRY_KINDS),
        "visit_kind": VISIT_KIND}
    found = []
    for row in session.execute(_STAGNANT, params):
        days = (cutoff - row.since).days
        found.append(StagnantProperty(
            property_id=row.id, title=row.title, zone=row.zone, type=row.type, price=Decimal(row.price),
            available_since=row.since, days=days,
            threshold=max(t for t in STAGNANT_DAYS if days >= t),
            inquiries=row.inquiries, visits=row.visits))
    return found


def ranking(session: Session, start: datetime, end: datetime, *, limit: int = RANKING_LIMIT,
            include_synthetic: bool = False) -> list[RankedProperty]:
    """Las propiedades con más cierres, visitas y consultas del periodo, en ese orden de desempate."""
    params = _params(start, end, include_synthetic) | {
        "limit": limit, "inquiry_kinds": list(INQUIRY_KINDS), "visit_kind": VISIT_KIND}
    return [RankedProperty(property_id=row.id, title=row.title, zone=row.zone, type=row.type,
                           inquiries=row.inquiries, visits=row.visits, deals=row.deals)
            for row in session.execute(_RANKING, params)]


def unmet_demand(session: Session, start: datetime, end: datetime, *, limit: int = UNMET_LIMIT,
                 include_synthetic: bool = False) -> list[UnmetDemand]:
    """Las combinaciones de zona, tipo y recámaras más buscadas sin resultado en el periodo (N-5)."""
    params = _params(start, end, include_synthetic) | {"limit": limit}
    return [UnmetDemand(zone=row.zone, type=row.type, bedrooms=row.bedrooms, searches=row.searches)
            for row in session.execute(_UNMET, params)]


def inventory_report(session: Session, start: datetime, end: datetime, *, as_of: datetime | None = None,
                     include_synthetic: bool = False) -> InventoryReport:
    """Todas las consultas del periodo, para la vista de N-8 y el reporte de N-10."""
    options = {"include_synthetic": include_synthetic}
    return InventoryReport(
        start=start, end=end, as_of=as_of or end, include_synthetic=include_synthetic,
        placements=placements(session, start, end, **options),
        days_on_market_by_zone=days_on_market(session, start, end, "zone", **options),
        days_on_market_by_type=days_on_market(session, start, end, "type", **options),
        stagnant=stagnant(session, start, end, as_of=as_of, **options),
        ranking=ranking(session, start, end, **options),
        unmet_demand=unmet_demand(session, start, end, **options),
    )
