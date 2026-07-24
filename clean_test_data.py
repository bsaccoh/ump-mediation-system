import os
import shutil
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings
from collection.models import CDRFile
from streams.msc.models import MSCRecord
from streams.pgw.models import PGWRecord
from streams.sgsn.models import SGSNRecord
from streams.sgw.models import SGWRecord
from businesslogic.models import RuleExecutionLog

def delete_in_chunks(queryset, name):
    total = 0
    while True:
        pks = list(queryset.values_list('pk', flat=True)[:500])
        if not pks:
            break
        deleted, _ = queryset.filter(pk__in=pks).delete()
        total += deleted
    print(f"- Deleted {name}: {total}")

def clean_database():
    print("Cleaning database records...")
    
    # Delete business logic execution logs
    delete_in_chunks(RuleExecutionLog.objects.all(), "RuleExecutionLogs")

    # Delete stream records first to avoid massive cascade on CDRFile deletion
    delete_in_chunks(MSCRecord.objects.all(), "MSCRecords")
    delete_in_chunks(PGWRecord.objects.all(), "PGWRecords")
    delete_in_chunks(SGSNRecord.objects.all(), "SGSNRecords")
    delete_in_chunks(SGWRecord.objects.all(), "SGWRecords")
    
    # Now delete CDR files
    delete_in_chunks(CDRFile.objects.all(), "CDRFiles")

def clean_data_directories():
    print("\nCleaning data directories...")
    data_dir = settings.DATA_DIR
    
    if not os.path.exists(data_dir):
        print("Data directory does not exist yet. Skipping.")
        return
        
    for subdir in ['incoming', 'decoded', 'processed', 'failed', 'archive', 'output']:
        dir_path = os.path.join(data_dir, subdir)
        if os.path.exists(dir_path):
            # Iterate and remove all files and directories inside the subdir
            for filename in os.listdir(dir_path):
                file_path = os.path.join(dir_path, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    print(f"Failed to delete {file_path}. Reason: {e}")
            print(f"- Cleared directory: {subdir}")

if __name__ == '__main__':
    # Confirm execution to be safe, but since this is automated I'll just run it.
    print("=== TEST DATA CLEANUP ===")
    clean_database()
    clean_data_directories()
    print("=== CLEANUP COMPLETE ===")
