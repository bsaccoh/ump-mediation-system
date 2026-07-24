
import os
import shutil
from django.conf import settings
from streams.msc.models import MSCRecord
from streams.pgw.models import PGWRecord
from streams.sgw.models import SGWRecord
from streams.sgsn.models import SGSNRecord
from collection.models import CDRFile

def clean_data():
    print("Cleaning database records...")
    
    msc_count = MSCRecord.objects.all().delete()[0]
    print(f"Deleted {msc_count} MSC records.")
    
    pgw_count = PGWRecord.objects.all().delete()[0]
    print(f"Deleted {pgw_count} PGW records.")
    
    sgw_count = SGWRecord.objects.all().delete()[0]
    print(f"Deleted {sgw_count} SGW records.")
    
    sgsn_count = SGSNRecord.objects.all().delete()[0]
    print(f"Deleted {sgsn_count} SGSN records.")
    
    file_count = CDRFile.objects.all().delete()[0]
    print(f"Deleted {file_count} CDR file entries.")
    
    print("\nCleaning physical files...")
    data_dir = settings.DATA_DIR
    subdirs = ['incoming', 'processed', 'decoded', 'failed', 'archive']
    
    for subdir in subdirs:
        path = os.path.join(data_dir, subdir)
        if os.path.exists(path):
            print(f"Cleaning {path}...")
            for item in os.listdir(path):
                item_path = os.path.join(path, item)
                try:
                    if os.path.isfile(item_path):
                        os.unlink(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                except Exception as e:
                    print(f"Error deleting {item_path}: {e}")
        else:
            print(f"Directory {path} does not exist.")

if __name__ == "__main__":
    clean_data()
