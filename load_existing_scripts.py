import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from scripts.models import Script
from django.conf import settings

# Mapping of file paths to Script fields
# (filepath, name, category, script_type)
SCRIPTS_TO_ADD = [
    ('streams/msc/decoder.py', 'MSC Decoder', 'TRANSFORM', 'PYTHON'),
    ('streams/msc/processor.py', 'MSC Processor', 'OUTPUT_PROCESSING', 'PYTHON'),
    ('streams/pgw/decoder.py', 'PGW Decoder', 'TRANSFORM', 'PYTHON'),
    ('streams/pgw/processor.py', 'PGW Processor', 'OUTPUT_PROCESSING', 'PYTHON'),
    ('streams/sgsn/decoder.py', 'SGSN Decoder', 'TRANSFORM', 'PYTHON'),
    ('streams/sgsn/processor.py', 'SGSN Processor', 'OUTPUT_PROCESSING', 'PYTHON'),
    ('streams/sgw/decoder.py', 'SGW Decoder', 'TRANSFORM', 'PYTHON'),
    ('streams/sgw/processor.py', 'SGW Processor', 'OUTPUT_PROCESSING', 'PYTHON'),
    ('import_mcc_mnc.py', 'Import MCC/MNC', 'BATCH_REPAIR', 'PYTHON'),
    ('seed_reference.py', 'Seed Reference Data', 'TEST_DATA', 'PYTHON'),
    ('test_process.py', 'Test Processing', 'TEST_DATA', 'PYTHON'),
    ('check_schema.py', 'Check DB Schema', 'RECORD_VALIDATION', 'PYTHON'),
]

def run():
    print("Seeding scripts into the database...")
    base_dir = settings.BASE_DIR
    
    for filepath, name, category, script_type in SCRIPTS_TO_ADD:
        full_path = os.path.join(base_dir, filepath.replace('/', os.sep))
        if os.path.exists(full_path):
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            script, created = Script.objects.update_or_create(
                name=name,
                defaults={
                    'category': category,
                    'script_type': script_type,
                    'status': 'ACTIVE',
                    'content': content,
                    'description': f'Loaded from {filepath}'
                }
            )
            action = "Created" if created else "Updated"
            print(f"[{action}] {name}")
        else:
            print(f"[NOT FOUND] {full_path}")

if __name__ == '__main__':
    run()
