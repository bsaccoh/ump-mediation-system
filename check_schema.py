#!/usr/bin/env python
import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection

cursor = connection.cursor()
cursor.execute("PRAGMA table_info(pgw_records)")
columns = cursor.fetchall()

for col in columns:
    print(f"Column: {col[1]:<25} Type: {col[2]}")
