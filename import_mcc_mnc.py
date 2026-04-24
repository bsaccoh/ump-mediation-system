"""Import MCC/MNC data from Excel into reference table."""
import os
import django
import pandas as pd

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from reference.models import MccMnc

# Read Excel
df = pd.read_excel('C:/Users/Saccoh1629182/Desktop/mnn-mnc.xlsx', dtype=str)
df = df.fillna('')

# Clean column names
df.columns = [c.strip() for c in df.columns]

print(f"Reading {len(df)} rows from Excel...")

# Clear existing MCC/MNC entries
deleted = MccMnc.objects.all().delete()
print(f"Cleared {deleted[0]} existing MCC/MNC entries.")

# Home MCC list (Sierra Leone)
HOME_MCC = {'619'}

created = 0
errors = 0

records = []
for _, row in df.iterrows():
    mcc = str(row.get('MCC', '')).strip().zfill(3)
    mnc = str(row.get('MNC', '')).strip().zfill(2)
    country = str(row.get('COUNTRY', '')).strip()
    operator = str(row.get('Network Operator', '')).strip() or str(row.get('Brand', '')).strip()
    dial_code = str(row.get('Dial Code', '')).strip()
    iso = str(row.get('ISO', '')).strip().upper()
    is_home = mcc in HOME_MCC

    if not mcc or not mnc or not country:
        errors += 1
        continue

    records.append(MccMnc(
        mcc=mcc,
        mnc=mnc,
        country=country,
        iso=iso,
        dial_code=dial_code,
        operator=operator or 'Unknown',
        is_home=is_home,
        enabled=True,
        notes='',
    ))

# Bulk create in batches
batch_size = 200
for i in range(0, len(records), batch_size):
    batch = records[i:i + batch_size]
    try:
        MccMnc.objects.bulk_create(batch, ignore_conflicts=True)
        created += len(batch)
    except Exception as e:
        # Fall back to one-by-one if bulk fails
        for r in batch:
            try:
                r.save()
                created += 1
            except Exception:
                errors += 1

total = MccMnc.objects.count()
home = MccMnc.objects.filter(is_home=True).count()
print(f"\nImport complete:")
print(f"  Created : {created:,}")
print(f"  Errors  : {errors}")
print(f"  Total in DB: {total:,}")
print(f"  Home networks (MCC 619): {home}")

# Show Sierra Leone entries
print("\nSierra Leone (MCC 619) entries:")
for r in MccMnc.objects.filter(mcc='619').order_by('mnc'):
    print(f"  {r.mcc}-{r.mnc} | {r.operator} | Home={r.is_home}")
