"""Column vocabulary + row mapping for tabular drive-test logs.

Maps arbitrary vendor/export headers onto the canonical ParsedSample fields,
so CSV, Excel and flat JSON all normalise the same way. Unknown columns are
ignored (never invented), and a metric a row lacks stays None.
"""
from __future__ import annotations

import re

from .base import ParsedSample, to_float, to_int, to_str, to_timestamp

_INT_ATTRS = {'obs_pci', 'obs_psc', 'obs_bsic', 'obs_arfcn', 'channel'}
_STR_ATTRS = {'technology', 'mcc', 'mnc', 'plmn', 'operator_code', 'obs_cell_id',
              'band', 'event_type', 'event_status'}
_TS_ATTRS = {'timestamp'}

# normalized-synonym -> canonical ParsedSample attribute
SYNONYMS: dict[str, str] = {}


def _reg(attr: str, *names: str):
    for n in names:
        SYNONYMS[n] = attr


_reg('timestamp', 'time', 'timestamp', 'datetime', 'datetimeutc', 'utc', 'logtime', 'date', 'ts')
_reg('latitude', 'lat', 'latitude', 'gpslat', 'gpslatitude', 'ycoord', 'y')
_reg('longitude', 'lon', 'long', 'lng', 'longitude', 'gpslon', 'gpslongitude', 'xcoord', 'x')
_reg('altitude', 'alt', 'altitude', 'elevation', 'gpsalt', 'height')
_reg('speed', 'speed', 'velocity', 'speedkmh', 'gpsspeed')
_reg('heading', 'heading', 'bearing', 'course', 'direction')
_reg('hdop', 'hdop', 'gpsaccuracy', 'accuracy', 'gpshdop')
_reg('technology', 'technology', 'tech', 'rat', 'networktype', 'radiotech', 'radiotechnology', 'nettype', 'systemtype')
_reg('mcc', 'mcc')
_reg('mnc', 'mnc')
_reg('plmn', 'plmn', 'plmnid')
_reg('operator_code', 'operator', 'operatorname', 'carrier', 'network', 'networkname', 'plmnname')
_reg('obs_cell_id', 'cellid', 'cell', 'ci', 'eci', 'nci', 'ecgi', 'cgi', 'cellidentity', 'ecellid', 'servingcellid')
_reg('obs_pci', 'pci', 'physcellid', 'physicalcellid')
_reg('obs_psc', 'psc', 'scramblingcode', 'sc')
_reg('obs_bsic', 'bsic')
_reg('obs_arfcn', 'arfcn', 'earfcn', 'uarfcn', 'nrarfcn', 'dlarfcn', 'ssbarfcn')
_reg('frequency', 'frequency', 'freq', 'dlfreq', 'carrierfrequency')
_reg('band', 'band', 'frequencyband', 'operatingband')
_reg('channel', 'channel', 'chan', 'dlchannel', 'channelnumber')
_reg('rsrp', 'rsrp', 'rsrpdbm', 'lterserp', 'lterptp')
_reg('rsrq', 'rsrq', 'rsrqdb')
_reg('sinr', 'sinr', 'snr', 'rssinr', 'ltesinr')
_reg('rssi', 'rssi', 'signalstrength')
_reg('cqi', 'cqi')
_reg('ss_rsrp', 'ssrsrp', 'nrrsrp', 'ssbrsrp', 'nrssrsrp', 'ssrsrpdbm')
_reg('ss_rsrq', 'ssrsrq', 'nrrsrq', 'ssbrsrq')
_reg('ss_sinr', 'sssinr', 'nrsinr', 'ssbsinr', 'nrssinr')
_reg('rscp', 'rscp', 'cpichrscp', 'ecrscp')
_reg('ecno', 'ecno', 'ecio', 'cpichecno', 'ecn0')
_reg('rxlev', 'rxlev', 'rxlevel', 'rxlevsub', 'rxlevfull')
_reg('rxqual', 'rxqual', 'rxqualsub', 'rxqualfull')
_reg('dl_throughput', 'dlthroughput', 'downlinkthroughput', 'throughputdl', 'dl', 'appdlthroughput',
     'pdcpdlthroughput', 'dlkbps', 'dlmbps', 'downloadspeed', 'dlrate')
_reg('ul_throughput', 'ulthroughput', 'uplinkthroughput', 'throughputul', 'ul', 'appulthroughput',
     'ulkbps', 'ulmbps', 'uploadspeed', 'ulrate')
_reg('latency_ms', 'latency', 'ping', 'rtt', 'latencyms', 'pingms', 'rttms')
_reg('packet_loss', 'packetloss', 'loss', 'plr', 'packetlosspct', 'packetlossrate')
_reg('event_type', 'event', 'eventtype', 'eventname')
_reg('event_status', 'eventstatus', 'outcome', 'result', 'callresult')

_TECH_MAP = [
    (('5g', 'nr', '5gnr', 'newradio'), 'NR'),
    (('4g', 'lte', 'ltea', 'lteadvanced', 'ltefdd', 'ltetdd'), 'LTE'),
    (('3g', 'umts', 'wcdma', 'hspa', 'hspa+', 'hsdpa', 'hsupa'), 'UMTS'),
    (('2g', 'gsm', 'gprs', 'edge'), 'GSM'),
]


def normalize_header(h: str) -> str:
    return re.sub(r'[^a-z0-9]', '', str(h).strip().lower())


def normalize_technology(value: str) -> str:
    n = normalize_header(value)
    if not n:
        return ''
    for keys, canon in _TECH_MAP:
        if n in keys:
            return canon
    for keys, canon in _TECH_MAP:
        if any(k in n for k in keys):
            return canon
    return ''


def infer_technology(s: ParsedSample) -> str:
    """Infer RAT from which metrics a sample carries (only when not stated)."""
    if s.ss_rsrp is not None or s.ss_sinr is not None or s.ss_rsrq is not None:
        return 'NR'
    if s.rsrp is not None or s.rsrq is not None or s.sinr is not None:
        return 'LTE'
    if s.rscp is not None or s.ecno is not None:
        return 'UMTS'
    if s.rxlev is not None or s.rxqual is not None:
        return 'GSM'
    return ''


class ColumnMapper:
    """Resolves a header row once, then maps each data row to a ParsedSample."""

    def __init__(self, headers):
        self.mapping: dict[str, tuple[str, bool]] = {}
        # value: (canonical_attr, is_mbps) — mbps flag scales throughput to kbps.
        for h in headers:
            norm = normalize_header(h)
            attr = SYNONYMS.get(norm)
            if attr:
                self.mapping[h] = (attr, 'mbps' in norm)

    @property
    def matched_attrs(self) -> set[str]:
        return {attr for attr, _ in self.mapping.values()}

    def to_sample(self, row: dict) -> ParsedSample:
        s = ParsedSample()
        for header, (attr, is_mbps) in self.mapping.items():
            raw = row.get(header)
            if raw is None or (isinstance(raw, str) and raw.strip() == ''):
                continue
            if attr in _TS_ATTRS:
                setattr(s, attr, to_timestamp(raw))
            elif attr in _STR_ATTRS:
                setattr(s, attr, to_str(raw))
            elif attr in _INT_ATTRS:
                setattr(s, attr, to_int(raw))
            else:
                val = to_float(raw)
                if val is not None and is_mbps and attr in ('dl_throughput', 'ul_throughput'):
                    val *= 1000.0  # store throughput in kbps
                setattr(s, attr, val)

        if s.technology:
            s.technology = normalize_technology(s.technology) or ''
        if not s.technology:
            s.technology = infer_technology(s)
        if s.plmn and not (s.mcc and s.mnc) and len(s.plmn) >= 5:
            s.mcc, s.mnc = s.plmn[:3], s.plmn[3:]
        return s
