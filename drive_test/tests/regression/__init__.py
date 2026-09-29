"""
Golden-file regression suite for the drive-test ingestion pipeline.

These tests run against the real TRP corpus in data/drive-test/raw/*/trp/.
That directory is gitignored, so the files exist on development and production
machines but not in CI — every test here SKIPS rather than fails when the corpus
is absent, so CI stays green while local runs remain authoritative.

Run:
    python manage.py test drive_test.tests.regression --settings=config.test_settings

Regenerate the snapshots after a DELIBERATE behaviour change:
    UMP_REGEN_SNAPSHOTS=1 python manage.py test drive_test.tests.regression --settings=config.test_settings

Always review the resulting snapshot diff before committing it. An unexplained
diff means the pipeline changed when it should not have.
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

from django.conf import settings

SNAPSHOT_DIR = Path(__file__).parent / 'snapshots'

#: Set UMP_REGEN_SNAPSHOTS=1 to write snapshots instead of asserting against them.
REGEN = os.environ.get('UMP_REGEN_SNAPSHOTS') == '1'


def corpus_files() -> list[Path]:
    """Every TRP file in the corpus, in a stable order.

    Sorted deliberately: glob order is filesystem-dependent, and a regression
    suite that iterates in a different order on another machine is not a
    regression suite.
    """
    pattern = str(Path(settings.BASE_DIR) / 'data' / 'drive-test' / 'raw' / '*' / 'trp' / '*.trp')
    return sorted(Path(p) for p in glob.glob(pattern))


def snapshot_path(source: Path, suffix: str) -> Path:
    return SNAPSHOT_DIR / f'{source.stem}.{suffix}.json'


def compare_or_write(test, actual: dict, source: Path, suffix: str) -> None:
    """Assert `actual` matches the committed snapshot, or write it when regenerating."""
    path = snapshot_path(source, suffix)

    if REGEN:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        test.skipTest(f'regenerated {path.name}')

    if not path.exists():
        test.fail(
            f'No snapshot for {source.name}. Generate it with:\n'
            f'  UMP_REGEN_SNAPSHOTS=1 python manage.py test drive_test.tests.regression '
            f'--settings=config.test_settings'
        )

    expected = json.loads(path.read_text(encoding='utf-8'))

    # Field-by-field so a failure names the drifting key rather than dumping
    # two large dicts at the reader.
    for key in sorted(set(expected) | set(actual)):
        test.assertIn(key, expected, f'{source.name}: {key!r} is new — snapshot is stale')
        test.assertIn(key, actual, f'{source.name}: {key!r} disappeared from the pipeline output')
        test.assertEqual(
            actual[key], expected[key],
            f'{source.name}: {key!r} changed\n'
            f'  expected: {expected[key]!r}\n'
            f'  actual:   {actual[key]!r}',
        )
