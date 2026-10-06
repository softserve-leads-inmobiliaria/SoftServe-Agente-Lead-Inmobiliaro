"""#202: los valores de la base se muestran en inglés (`db/labels.py`)."""
import pytest

from db.labels import label
from db.models import (
    DEAL_KINDS,
    LEAD_STATUSES,
    NOTIFICATION_KINDS,
    PROPERTY_KINDS,
    PROPERTY_STATUSES,
    PROPERTY_TYPES,
)


@pytest.mark.parametrize("field,values", [
    ("property_type", PROPERTY_TYPES), ("property_kind", PROPERTY_KINDS), ("property_status", PROPERTY_STATUSES),
    ("deal_kind", DEAL_KINDS), ("lead_status", LEAD_STATUSES), ("notice_kind", NOTIFICATION_KINDS),
])
def test_every_value_of_the_database_has_its_label(field, values):
    # Un valor nuevo en `db/models.py` sin etiqueta se vería en español en el panel.
    labels = [label(field, value) for value in values]
    assert all(text != value for text, value in zip(labels, values, strict=True))
    assert len(set(labels)) == len(values)


def test_the_labels_read_like_the_agent_says_them():
    assert label("property_type", "renta") == "for rent"
    assert label("property_status", "apartado") == "reserved"
    assert label("lead_status", "sql") == "in progress"


def test_an_unknown_value_is_shown_as_it_is():
    assert label("property_type", "permuta") == "permuta"
    assert label("no_such_field", None) == "None"
