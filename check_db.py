#!/usr/bin/env python
"""Check database contents and sizes."""
import sqlite3
import os

db_path = 'db.sqlite3'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get file size
db_size = os.path.getsize(db_path)
print(f"Database file size: {db_size / (1024*1024):.2f} MB ({db_size:,} bytes)")
print()

# Get all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [row[0] for row in cursor.fetchall()]

print("Table row counts:")
print("-" * 50)
total_rows = 0
for table in tables:
    try:
        count = cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if count > 0:
            print(f"{table}: {count:,}")
            total_rows += count
    except Exception as e:
        print(f"{table}: ERROR - {e}")

print("-" * 50)
print(f"Total rows across all tables: {total_rows:,}")

# Check for large tables specifically
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
all_tables = [row[0] for row in cursor.fetchall()]
print("\nAll tables in database:")
for t in all_tables:
    print(f"  - {t}")

# Print tables with most rows first
print("\n" + "="*50)
print("TABLES WITH DATA (sorted by row count):")
print("="*50)
table_counts = []
for table in all_tables:
    try:
        count = cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        table_counts.append((table, count))
    except:
        pass

table_counts.sort(key=lambda x: x[1], reverse=True)
for table, count in table_counts:
    if count > 0:
        print(f"  {table}: {count:,}")

conn.close()
