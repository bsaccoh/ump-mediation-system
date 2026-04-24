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

def clean_database():
    print("Cleaning database records...")
    
    # Delete CDR files (this will cascade to processing logs if any exist)
    deleted_files, _ = CDRFile.objects.all().delete()
    print(f"- Deleted CDRFiles: {deleted_files}")
    
    # Delete stream records
    deleted_msc, _ = MSCRecord.objects.all().delete()
    print(f"- Deleted MSCRecords: {deleted_msc}")
    
    deleted_pgw, _ = PGWRecord.objects.all().delete()
    print(f"- Deleted PGWRecords: {deleted_pgw}")
    
    deleted_sgsn, _ = SGSNRecord.objects.all().delete()
    print(f"- Deleted SGSNRecords: {deleted_sgsn}")
    
    deleted_sgw, _ = SGWRecord.objects.all().delete()
    print(f"- Deleted SGWRecords: {deleted_sgw}")
    
    # Delete business logic execution logs
    deleted_rules, _ = RuleExecutionLog.objects.all().delete()
    print(f"- Deleted RuleExecutionLogs: {deleted_rules}")

def clean_data_directories():
    print("\nCleaning data directories...")
    data_dir = settings.DATA_DIR
    
    if not os.path.exists(data_dir):
        print("Data directory does not exist yet. Skipping.")
        return
        
    for subdir in ['incoming', 'decoded', 'processed', 'failed', 'archive']:
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
