"""
Reference Data Importer

Loads network reference data (regions, districts, chiefdoms, sites, cells)
from CSV / Excel / JSON files into the drive_test models.

Rules:
  - Existing rows are updated in-place (upsert by natural key)
  - Original observed data is never overwritten when update_coordinates=False
  - Bulk operations only — no row-by-row creates
  - Returns an ImportResult with counts and error details
"""

import csv
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.db import transaction

logger = logging.getLogger(__name__)


@dataclass
class ImportResult:
    source: str = ''
    entity: str = ''
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self):
        return self.created + self.updated + self.skipped

    def __str__(self):
        return (
            f'{self.entity} from {self.source}: '
            f'{self.created} created, {self.updated} updated, '
            f'{self.skipped} skipped, {len(self.errors)} errors'
        )


class ReferenceImporter:
    """
    Top-level importer.  Call the import_* methods directly or use
    import_file() to auto-detect by content headers.
    """

    def __init__(self, operator_id: int, update_coordinates: bool = False):
        self.operator_id = operator_id
        self.update_coordinates = update_coordinates

    # ------------------------------------------------------------------
    # Geography
    # ------------------------------------------------------------------

    def import_regions(self, rows: list[dict]) -> ImportResult:
        from drive_test.models import Region
        result = ImportResult(entity='Region')
        to_create, to_update = [], []
        existing = {r.code: r for r in Region.objects.all()}

        for row in rows:
            code = (row.get('code') or '').strip()
            name = (row.get('name') or '').strip()
            if not code or not name:
                result.errors.append(f'Skipping row with missing code/name: {row}')
                result.skipped += 1
                continue
            if code in existing:
                obj = existing[code]
                obj.name = name
                to_update.append(obj)
                result.updated += 1
            else:
                to_create.append(Region(code=code, name=name,
                                        country=row.get('country', 'SL')))
                result.created += 1

        with transaction.atomic():
            if to_create:
                Region.objects.bulk_create(to_create, ignore_conflicts=True)
            if to_update:
                Region.objects.bulk_update(to_update, ['name', 'country'])
        return result

    def import_districts(self, rows: list[dict]) -> ImportResult:
        from drive_test.models import District, Region
        result = ImportResult(entity='District')
        region_map = {r.code: r for r in Region.objects.all()}
        existing = {d.code: d for d in District.objects.select_related('region')}
        to_create, to_update = [], []

        for row in rows:
            code = (row.get('code') or '').strip()
            name = (row.get('name') or '').strip()
            region_code = (row.get('region_code') or '').strip()
            if not code or not name:
                result.skipped += 1
                continue
            region = region_map.get(region_code)
            if not region:
                result.errors.append(f'Unknown region_code={region_code!r} for district {code}')
                result.skipped += 1
                continue
            if code in existing:
                obj = existing[code]
                obj.name = name
                obj.region = region
                to_update.append(obj)
                result.updated += 1
            else:
                to_create.append(District(code=code, name=name, region=region))
                result.created += 1

        with transaction.atomic():
            if to_create:
                District.objects.bulk_create(to_create, ignore_conflicts=True)
            if to_update:
                District.objects.bulk_update(to_update, ['name', 'region'])
        return result

    def import_sites(self, rows: list[dict]) -> ImportResult:
        from drive_test.models import Site, Chiefdom
        from reference.models import Operator
        result = ImportResult(entity='Site')
        try:
            operator = Operator.objects.get(pk=self.operator_id)
        except Operator.DoesNotExist:
            result.errors.append(f'Operator id={self.operator_id} not found')
            return result

        chiefdom_map = {c.code: c for c in Chiefdom.objects.all()}
        existing = {s.site_id: s for s in Site.objects.filter(operator=operator)}
        to_create, to_update = [], []

        for row in rows:
            site_id = (row.get('site_id') or '').strip()
            name = (row.get('name') or '').strip()
            if not site_id:
                result.skipped += 1
                continue

            lat = _float(row.get('latitude'))
            lon = _float(row.get('longitude'))
            alt = _float(row.get('altitude_m'))
            chiefdom = chiefdom_map.get((row.get('chiefdom_code') or '').strip())

            if site_id in existing:
                obj = existing[site_id]
                obj.name = name or obj.name
                obj.chiefdom = chiefdom or obj.chiefdom
                if self.update_coordinates:
                    obj.latitude = lat
                    obj.longitude = lon
                    obj.altitude_m = alt
                to_update.append(obj)
                result.updated += 1
            else:
                to_create.append(Site(
                    site_id=site_id, operator=operator, name=name or site_id,
                    latitude=lat, longitude=lon, altitude_m=alt,
                    chiefdom=chiefdom,
                    site_type=row.get('site_type', 'macro'),
                ))
                result.created += 1

        coord_fields = ['name', 'chiefdom', 'latitude', 'longitude', 'altitude_m'] \
            if self.update_coordinates else ['name', 'chiefdom']
        with transaction.atomic():
            if to_create:
                Site.objects.bulk_create(to_create, ignore_conflicts=True)
            if to_update:
                Site.objects.bulk_update(to_update, coord_fields)
        return result

    def import_cells(self, rows: list[dict]) -> ImportResult:
        from drive_test.models import Cell, Sector, Site
        from reference.models import Operator
        result = ImportResult(entity='Cell')
        try:
            operator = Operator.objects.get(pk=self.operator_id)
        except Operator.DoesNotExist:
            result.errors.append(f'Operator id={self.operator_id} not found')
            return result

        site_map = {s.site_id: s for s in Site.objects.filter(operator=operator)}
        existing = {c.cell_id: c for c in Cell.objects.filter(operator=operator)}
        to_create, to_update = [], []

        for row in rows:
            cell_id = (row.get('cell_id') or '').strip()
            if not cell_id:
                result.skipped += 1
                continue

            site_id = (row.get('site_id') or '').strip()
            site = site_map.get(site_id)
            if not site:
                result.errors.append(f'Unknown site_id={site_id!r} for cell {cell_id}')
                result.skipped += 1
                continue

            # Get or create sector
            sector_id = (row.get('sector_id') or 'S0').strip()
            sector, _ = Sector.objects.get_or_create(
                site=site, sector_id=sector_id,
                defaults={'azimuth_deg': _float(row.get('azimuth_deg'))},
            )

            kwargs = dict(
                operator=operator,
                sector=sector,
                technology=row.get('technology', '4G'),
                mcc=row.get('mcc', ''),
                mnc=row.get('mnc', ''),
                lac=_int(row.get('lac')),
                tac=_int(row.get('tac')),
                ci=_int(row.get('ci')),
                eci=_int(row.get('eci')),
                pci=_int(row.get('pci')),
                earfcn=_int(row.get('earfcn')),
                latitude=_float(row.get('latitude')),
                longitude=_float(row.get('longitude')),
                is_active=str(row.get('is_active', 'true')).lower() not in ('false', '0', 'no'),
            )

            if cell_id in existing:
                obj = existing[cell_id]
                for k, v in kwargs.items():
                    if v is not None or k in ('mcc', 'mnc'):
                        setattr(obj, k, v)
                to_update.append(obj)
                result.updated += 1
            else:
                to_create.append(Cell(cell_id=cell_id, **kwargs))
                result.created += 1

        update_fields = [
            'sector', 'technology', 'mcc', 'mnc', 'lac', 'tac', 'ci', 'eci',
            'pci', 'earfcn', 'latitude', 'longitude', 'is_active',
        ]
        with transaction.atomic():
            if to_create:
                Cell.objects.bulk_create(to_create, ignore_conflicts=True)
                # Re-save to trigger CGI/ECGI computation
                created_cells = Cell.objects.filter(
                    operator=operator, cell_id__in=[c.cell_id for c in to_create]
                )
                for cell in created_cells:
                    cell.save(update_fields=['cgi', 'ecgi'])
            if to_update:
                Cell.objects.bulk_update(to_update, update_fields)
                for cell in to_update:
                    cell.save(update_fields=['cgi', 'ecgi'])
        return result


def parse_csv(content: bytes | str) -> list[dict]:
    if isinstance(content, bytes):
        content = content.decode('utf-8-sig')  # handles BOM
    reader = csv.DictReader(io.StringIO(content))
    return [
        {k.strip().lower().replace(' ', '_'): (v or '').strip() for k, v in row.items()}
        for row in reader
    ]


def _float(val: Any) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _int(val: Any) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None
