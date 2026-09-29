"""
Reference-quality helpers for the Cells page.

"Matchability" is derived from the strategies CellReferenceMatcher actually implements
(cell_matcher.py), so the page never claims more than the matcher can do:

  MATCHABLE  — has a CGI or ECGI (strategies 1/2: exact identifier match)
  LIMITED    — no exact identifier, but PCI+EARFCN (strategy 3) or its own GPS position
               (strategy 4) exists, so it can only be matched with lower confidence
  INCOMPLETE — none of the above; the matcher cannot use this cell at all

The Q builders (database side) and cell_state / cell_warnings (Python side) must agree;
tests assert this.
"""
from django.db.models import Q

MATCHABLE, LIMITED, INCOMPLETE = 'matchable', 'limited', 'incomplete'
STATE_LABELS = {MATCHABLE: 'MATCHABLE', LIMITED: 'LIMITED', INCOMPLETE: 'INCOMPLETE'}
STATE_HELP = {
    MATCHABLE: 'Has a CGI/ECGI: exact identifier matching is possible.',
    LIMITED: 'No CGI/ECGI. Only PCI+EARFCN or GPS-proximity matching (lower confidence).',
    INCOMPLETE: 'No usable identifier or position: the matcher cannot use this cell.',
}

# Technology -> identifiers a complete reference record is expected to carry.
EXPECTED_IDS = {
    '2G': (('lac', 'LAC'), ('ci', 'CI')),
    '3G': (('lac', 'LAC'), ('ci', 'CI')),
    '4G': (('tac', 'TAC'), ('eci', 'ECI')),
    '5G': (('tac', 'TAC'), ('nci', 'NCI')),
}

Q_EXACT = Q(cgi__gt='') | Q(ecgi__gt='')
Q_PHYSICAL = Q(pci__isnull=False, earfcn__isnull=False)
Q_OWN_GPS = (Q(latitude__isnull=False, longitude__isnull=False)
             & ~Q(latitude=0.0, longitude=0.0)
             & Q(latitude__gte=-90, latitude__lte=90, longitude__gte=-180, longitude__lte=180))
Q_LIMITED = ~Q_EXACT & (Q_PHYSICAL | Q_OWN_GPS)
Q_INCOMPLETE = ~Q_EXACT & ~Q_PHYSICAL & ~Q_OWN_GPS

STATE_Q = {MATCHABLE: Q_EXACT, LIMITED: Q_LIMITED, INCOMPLETE: Q_INCOMPLETE}


def has_valid_coords(lat, lon):
    return (lat is not None and lon is not None and not (lat == 0 and lon == 0)
            and -90 <= lat <= 90 and -180 <= lon <= 180)


def cell_state(cell):
    if cell.cgi or cell.ecgi:
        return MATCHABLE
    if (cell.pci is not None and cell.earfcn is not None) or has_valid_coords(cell.latitude, cell.longitude):
        return LIMITED
    return INCOMPLETE


def cell_warnings(cell, dup_ids=False):
    """Real, checkable reference problems for one cell (empty list = none found)."""
    w = []
    if not cell.technology:
        w.append('Missing technology')
    if not cell.mcc or not cell.mnc:
        w.append('Missing MCC/MNC')
    for field, label in EXPECTED_IDS.get(cell.technology, ()):
        if getattr(cell, field) is None:
            w.append(f'Missing {label}')
    if (cell.latitude is None) != (cell.longitude is None) or (
            cell.latitude is not None and not has_valid_coords(cell.latitude, cell.longitude)):
        w.append('Invalid coordinates')
    if cell.band_id and cell.technology and cell.band.technology != cell.technology:
        w.append('Band technology mismatch')
    if dup_ids:
        w.append('Duplicate identifier')
    return w


def primary_identifier(cell):
    """(label, value) of the identifier that matters most for this technology, or None."""
    order = {'4G': ('eci', 'nci', 'ci'), '5G': ('nci', 'eci', 'ci'),
             '3G': ('ci', 'eci', 'nci'), '2G': ('ci', 'eci', 'nci')}.get(cell.technology, ('ci', 'eci', 'nci'))
    for f in order:
        v = getattr(cell, f)
        if v is not None:
            return f.upper(), v
    if cell.ecgi:
        return 'ECGI', cell.ecgi
    if cell.cgi:
        return 'CGI', cell.cgi
    return None


def frequency_label(cell):
    """('4G Band 3 · 1800 MHz', 'EARFCN 1650') — either part may be empty."""
    band = ''
    if cell.band_id:
        b = cell.band
        band = f'{b.technology} Band {b.band_number} · {b.frequency_mhz:g} MHz'
    chan = ''
    if cell.technology == '5G' and cell.nrarfcn is not None:
        chan = f'NR-ARFCN {cell.nrarfcn}'
    elif cell.earfcn is not None:
        chan = f'EARFCN {cell.earfcn}'
    elif cell.nrarfcn is not None:
        chan = f'NR-ARFCN {cell.nrarfcn}'
    return band, chan


def snapshot(cell):
    """JSON-safe copy of a cell's reference state for CellHistory."""
    data = {}
    for f in cell._meta.concrete_fields:
        v = getattr(cell, f.attname)
        data[f.name] = v.isoformat() if hasattr(v, 'isoformat') else v
    data.pop('created_at', None)
    data.pop('updated_at', None)
    return data
