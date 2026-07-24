import django
import os
import sys
import traceback

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from collection.models import CDRFile
from streams.pgw.processor import PGWProcessor

file_id = 127
try:
    cdr_file = CDRFile.objects.get(pk=file_id)
    print(f"Processing file: {cdr_file.filename} (ID: {file_id})")
    
    processor = PGWProcessor()
    success, message = processor.process(file_id)
    
    print(f"Success: {success}")
    print(f"Message: {message}")
    
    cdr_file.refresh_from_db()
    print(f"Final Status: {cdr_file.status}")
    print(f"Records: {cdr_file.records_total}")
    
except Exception as e:
    print(f"Error: {e}")
    traceback.print_exc()
