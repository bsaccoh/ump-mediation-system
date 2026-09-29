"""
Import Orange Sierra Leone reference data from the GEO-DIM Excel file.

Usage:
    python manage.py import_orange_geodim --file /path/to/Geo-Dimension_Newsites_Upgrade_2026_V1.xlsx
    python manage.py import_orange_geodim --file ... --dry-run
    python manage.py import_orange_geodim --file ... --clear
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

TECH_MAP = {
    '2G_HUAWEI': '2G',
    '3G_HUAWEI': '3G',
    '4G_HUAWEI': 'LTE',
    '5G_HUAWEI': 'NR',
}

ORANGE_MCC = '619'
ORANGE_MNC = '01'


class Command(BaseCommand):
    help = 'Import Orange Sierra Leone sites, sectors, and cells from GEO-DIM Excel'

    def add_arguments(self, parser):
        parser.add_argument('--file', required=True, help='Path to GEO-DIM Excel file')
        parser.add_argument('--dry-run', action='store_true',
                            help='Validate and report — no DB writes')
        parser.add_argument('--clear', action='store_true',
                            help='Delete existing Orange sites/sectors/cells before import')

    def handle(self, *args, **options):
        try:
            import openpyxl
        except ImportError:
            raise CommandError('openpyxl is required: pip install openpyxl')

        path = Path(options['file'])
        if not path.exists():
            raise CommandError(f'File not found: {path}')

        dry_run = options['dry_run']
        do_clear = options['clear']

        self.stdout.write(f'Loading workbook: {path.name} …')
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)

        from reference.models import Operator
        from drive_test.models import Site, Sector, Cell

        try:
            operator = Operator.objects.get(home_mcc=ORANGE_MCC, home_mnc=ORANGE_MNC)
        except Operator.DoesNotExist:
            raise CommandError(
                f'Orange operator (MCC={ORANGE_MCC} MNC={ORANGE_MNC}) not found in database.'
            )
        self.stdout.write(f'Operator: {operator.name} (id={operator.id})')

        # ── Read Sheet 1: Physical Sites ───────────────────────────────────
        sites_sheet = wb['OSL_Physical Sites']
        sites_data = self._read_sites(sites_sheet)
        self.stdout.write(f'Read {len(sites_data)} physical sites')

        # ── Read Sheet 2: GEO-DIM cells ───────────────────────────────────
        cells_sheet = wb['GEO-DIM 2G_3G_4G_5G']
        cells_data = self._read_cells(cells_sheet)
        self.stdout.write(f'Read {len(cells_data)} cell rows')

        # Validate that all cell site IDs reference a known site
        known_site_ids = {r['site_id'] for r in sites_data}
        missing = {r['site_id'] for r in cells_data} - known_site_ids
        if missing:
            self.stdout.write(self.style.WARNING(
                f'  {len(missing)} cell site IDs not in physical-sites sheet: '
                f'{sorted(missing)[:10]}{"…" if len(missing) > 10 else ""}'
            ))

        if dry_run:
            self.stdout.write(self.style.SUCCESS('Dry run — no changes written.'))
            return

        with transaction.atomic():
            if do_clear:
                n_cells = Cell.objects.filter(operator=operator).delete()[0]
                # Sectors and Sites cascade if configured, otherwise delete explicitly
                site_ids = list(Site.objects.filter(operator=operator).values_list('id', flat=True))
                n_sectors = Sector.objects.filter(site_id__in=site_ids).delete()[0]
                n_sites = Site.objects.filter(operator=operator).delete()[0]
                self.stdout.write(
                    f'Cleared: {n_cells} cells, {n_sectors} sectors, {n_sites} sites'
                )

            # ── Insert / update Sites ──────────────────────────────────────
            created_sites, updated_sites = self._import_sites(sites_data, operator, Site)

            # Build lookup: site_id → Site pk
            site_map = {
                s.site_id: s
                for s in Site.objects.filter(operator=operator)
            }

            # ── Insert Sectors and Cells ───────────────────────────────────
            created_sectors, created_cells, skipped = self._import_cells(
                cells_data, operator, site_map, Sector, Cell
            )

        self.stdout.write(self.style.SUCCESS(
            f'\nDone.\n'
            f'  Sites:   {created_sites} created, {updated_sites} updated\n'
            f'  Sectors: {created_sectors} created\n'
            f'  Cells:   {created_cells} created, {skipped} skipped'
        ))

    # ── Sheet readers ──────────────────────────────────────────────────────

    def _read_sites(self, ws):
        rows = ws.iter_rows(values_only=True)
        header = next(rows)  # skip header
        # Expected columns (0-indexed): S/N, SITE ID, SITE NAME, Tower Height,
        #   LATITUDE, LONGITUDE, Technology, Classification, NAtCa Sites Classification,
        #   OWNER, Region, District, Chiefdom, Location, Location_Updated, OnAir Date,
        #   Site Type, Swap Date
        results = []
        for row in rows:
            if not row or row[1] is None:
                continue
            site_id = str(row[1]).strip()
            if not site_id:
                continue
            try:
                lat = float(row[4]) if row[4] is not None else None
                lon = float(row[5]) if row[5] is not None else None
            except (TypeError, ValueError):
                lat = lon = None

            site_type_raw = str(row[16]).strip() if row[16] else 'Unknown'
            # Truncate to 20 chars (field max_length)
            site_type = site_type_raw[:20]

            address = ''
            if row[14]:
                address = str(row[14]).strip()[:500]
            elif row[13]:
                address = str(row[13]).strip()[:500]

            commissioned = None
            if row[15] is not None:
                from datetime import datetime, date
                if isinstance(row[15], (datetime, date)):
                    commissioned = row[15].date() if isinstance(row[15], datetime) else row[15]
                else:
                    try:
                        from dateutil.parser import parse as dateparse
                        commissioned = dateparse(str(row[15])).date()
                    except Exception:
                        pass

            results.append({
                'site_id': site_id,
                'name': str(row[2]).strip()[:200] if row[2] else site_id,
                'site_type': site_type,
                'latitude': lat,
                'longitude': lon,
                'address': address,
                'commissioned_date': commissioned,
                'metadata': {
                    'tower_height_m': row[3],
                    'technology': str(row[6]) if row[6] else None,
                    'classification': str(row[7]) if row[7] else None,
                    'natca_classification': str(row[8]) if row[8] else None,
                    'owner': str(row[9]) if row[9] else None,
                    'region': str(row[10]) if row[10] else None,
                    'district': str(row[11]) if row[11] else None,
                    'chiefdom': str(row[12]) if row[12] else None,
                },
            })
        return results

    def _read_cells(self, ws):
        rows = ws.iter_rows(values_only=True)
        next(rows)  # skip header
        # Expected columns (0-indexed):
        # 0=Site ID, 1=BTS Name, 2=NE Name, 3=CellName, 4=LocalCellID,
        # 5=BTS ID/eNodeBID, 6=MCC, 7=MNC, 8=LAC, 9=Cell Id, 10=CGI,
        # 11=Longitude, 12=Latitude, 13=BSC Name, 14=Technology
        results = []
        for row in rows:
            if not row or row[0] is None:
                continue
            site_id = str(row[0]).strip()
            cell_name = str(row[3]).strip() if row[3] else ''
            if not site_id or not cell_name:
                continue

            tech_raw = str(row[14]).strip() if row[14] else ''
            tech = TECH_MAP.get(tech_raw)
            if tech is None:
                continue  # unknown technology — skip

            try:
                local_cell_id = int(row[4]) if row[4] is not None else None
            except (TypeError, ValueError):
                local_cell_id = None

            try:
                enodeb_id = int(row[5]) if row[5] is not None else None
            except (TypeError, ValueError):
                enodeb_id = None

            mcc = str(row[6]).strip() if row[6] else ORANGE_MCC
            mnc = str(row[7]).strip().zfill(2) if row[7] else ORANGE_MNC

            try:
                lac_val = int(row[8]) if row[8] is not None else None
            except (TypeError, ValueError):
                lac_val = None

            try:
                cell_id_val = int(row[9]) if row[9] is not None else None
            except (TypeError, ValueError):
                cell_id_val = None

            cgi_val = str(int(row[10])) if row[10] is not None else ''

            try:
                lon = float(row[11]) if row[11] is not None else None
                lat = float(row[12]) if row[12] is not None else None
            except (TypeError, ValueError):
                lon = lat = None

            # Derive sector digit from CellName suffix
            sector_digit = self._sector_digit(cell_name)

            results.append({
                'site_id': site_id,
                'cell_name': cell_name,
                'sector_digit': sector_digit,
                'tech': tech,
                'mcc': mcc[:3],
                'mnc': mnc[:3],
                'lac_val': lac_val,
                'cell_id_val': cell_id_val,
                'cgi_val': cgi_val[:30],
                'lat': lat,
                'lon': lon,
                'local_cell_id': local_cell_id,
                'enodeb_id': enodeb_id,
            })
        return results

    # ── DB writers ─────────────────────────────────────────────────────────

    def _import_sites(self, sites_data, operator, Site):
        created = 0
        updated = 0
        for d in sites_data:
            site, is_new = Site.objects.get_or_create(
                operator=operator,
                site_id=d['site_id'],
                defaults={
                    'name': d['name'],
                    'site_type': d['site_type'],
                    'latitude': d['latitude'],
                    'longitude': d['longitude'],
                    'address': d['address'],
                    'commissioned_date': d['commissioned_date'],
                    'metadata': d['metadata'],
                    'is_active': True,
                },
            )
            if is_new:
                created += 1
            else:
                # Update coordinates and metadata if not already set
                changed = False
                if site.latitude is None and d['latitude'] is not None:
                    site.latitude = d['latitude']
                    changed = True
                if site.longitude is None and d['longitude'] is not None:
                    site.longitude = d['longitude']
                    changed = True
                if not site.metadata:
                    site.metadata = d['metadata']
                    changed = True
                if changed:
                    site.save()
                    updated += 1
        return created, updated

    def _import_cells(self, cells_data, operator, site_map, Sector, Cell):
        from django.db import IntegrityError

        created_sectors = 0
        created_cells = 0
        skipped = 0

        # Cache sectors by (site_pk, sector_id)
        sector_cache = {}

        for d in cells_data:
            site = site_map.get(d['site_id'])
            if site is None:
                skipped += 1
                continue

            sector_id = f"{d['site_id']}-S{d['sector_digit']}"
            cache_key = (site.pk, sector_id)
            if cache_key not in sector_cache:
                sector, created = Sector.objects.get_or_create(
                    site=site,
                    sector_id=sector_id,
                    defaults={'is_active': True, 'metadata': {}},
                )
                if created:
                    created_sectors += 1
                sector_cache[cache_key] = sector
            sector = sector_cache[cache_key]

            # Determine cell-type-specific IDs
            tech = d['tech']
            lac = rac = tac = ci = eci = nci = None
            if tech == '2G':
                lac = d['lac_val']
                ci = d['cell_id_val']
                cgi = d['cgi_val']
                ecgi = ''
            elif tech == '3G':
                lac = d['lac_val']
                ci = d['cell_id_val']
                cgi = d['cgi_val']
                ecgi = ''
            elif tech == 'LTE':
                tac = d['lac_val']
                eci = d['cell_id_val']
                cgi = ''
                ecgi = d['cgi_val']
            elif tech == 'NR':
                tac = d['lac_val']
                nci = d['cell_id_val']
                cgi = ''
                ecgi = d['cgi_val']
            else:
                skipped += 1
                continue

            cell_id = d['cell_name'][:50]
            try:
                _, is_new = Cell.objects.get_or_create(
                    cell_id=cell_id,
                    operator=operator,
                    defaults={
                        'sector': sector,
                        'technology': tech,
                        'mcc': d['mcc'],
                        'mnc': d['mnc'],
                        'lac': lac,
                        'tac': tac,
                        'ci': ci,
                        'eci': eci,
                        'nci': nci,
                        'cgi': cgi,
                        'ecgi': ecgi,
                        'latitude': d['lat'],
                        'longitude': d['lon'],
                        'is_active': True,
                        'metadata': {
                            'local_cell_id': d['local_cell_id'],
                            'enodeb_id': d['enodeb_id'],
                        },
                    },
                )
                if is_new:
                    created_cells += 1
                else:
                    skipped += 1
            except IntegrityError:
                skipped += 1

        return created_sectors, created_cells, skipped

    @staticmethod
    def _sector_digit(cell_name: str) -> str:
        """Extract sector digit from CellName suffix after last hyphen."""
        if '-' not in cell_name:
            return '1'
        suffix = cell_name.rsplit('-', 1)[-1].strip()
        if not suffix:
            return '1'
        # First character of suffix is the sector digit
        c = suffix[0]
        return c if c.isdigit() else '1'
