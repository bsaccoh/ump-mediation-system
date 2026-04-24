import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from collection.models import CDRFile, DataSource
from streams.pgw.processor import PGWProcessor
import traceback

# Get or create a test source
source, _ = DataSource.objects.get_or_create(
    name='TEST_PGW',
    defaults={
        'source_type': DataSource.SourceType.LOCAL,
    }
)

# Create a CDRFile record
test_file = r'c:\Users\Saccoh1629182\Documents\Babah\BS\OCS\project\babah\ump-mediation-system\data\incoming\pgw\20260417_224318_bFTPGWCDR264946150687020251228153941.dat'

cdr_file = CDRFile.objects.create(
    source=source,
    filename='test_pgw.dat',
    file_size=2479007,
    file_path=test_file,
    file_hash='test'
)

print(f"Processing file: {cdr_file.filename}")
processor = PGWProcessor()
try:
    success, message = processor.process(cdr_file.pk)
    print(f"Result: {success} - {message}")
except Exception as e:
    print(f"Exception: {e}")
    traceback.print_exc()

# Check results
cdr_file.refresh_from_db()
print(f"\nFile Status: {cdr_file.status}")
print(f"Total Records: {cdr_file.records_total}")
print(f"Error Message: {cdr_file.error_message}")
