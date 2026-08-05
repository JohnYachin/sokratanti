"""
CAIOS — Run all database migrations via Supabase REST API
"""
import urllib.request
import urllib.error
import json
import os
import sys

SUPABASE_URL = "https://zrvsuwdlhnnfvqxxohex.supabase.co"
SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpydnN1d2RsaG5uZnZxeHhvaGV4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4NTUxMTQwMCwiZXhwIjoyMTAxMDg3NDAwfQ.19YNUSRWeJknVytkfQjvnzsjT0LmvqkWUX0eRRDSGJY"

MIGRATIONS_DIR = r"C:\Users\GPD\.gemini\antigravity\scratch\caios\database\migrations"
SEED_DIR = r"C:\Users\GPD\.gemini\antigravity\scratch\caios\database\seed"

def run_sql(sql: str, label: str) -> bool:
    endpoint = f"{SUPABASE_URL}/rest/v1/rpc/exec_sql"
    # Use the pg REST endpoint for raw SQL via service role
    # Supabase allows raw SQL via the /rest/v1/ with RPC or via the SQL editor API
    # We'll use the management API approach via POST to the query endpoint
    query_url = f"https://api.supabase.com/v1/projects/zrvsuwdlhnnfvqxxohex/database/query"
    
    payload = json.dumps({"query": sql}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
    }
    req = urllib.request.Request(query_url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read().decode())
            print(f"  ✅ {label} — OK")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  ❌ {label} — HTTP {e.code}: {body[:200]}")
        return False
    except Exception as e:
        print(f"  ❌ {label} — {e}")
        return False

def run_sql_direct(sql: str, label: str) -> bool:
    """Run SQL directly via Supabase service role using the db/query endpoint."""
    import urllib.request, urllib.error, json

    url = f"{SUPABASE_URL}/rest/v1/rpc/query"
    # Try alternative: use psql if available
    return False

files = [
    (MIGRATIONS_DIR, "001_extensions.sql"),
    (MIGRATIONS_DIR, "002_core_tables.sql"),
    (MIGRATIONS_DIR, "003_indexes.sql"),
    (MIGRATIONS_DIR, "004_rls.sql"),
    (MIGRATIONS_DIR, "005_seed_agents.sql"),
    (SEED_DIR, "coins_top50.sql"),
]

print("🚀 CAIOS Database Migration Runner")
print("=" * 50)

# Check if supabase CLI is available
import subprocess
try:
    result = subprocess.run(["supabase", "--version"], capture_output=True, text=True, timeout=5)
    print(f"✅ Supabase CLI found: {result.stdout.strip()}")
    has_cli = True
except Exception:
    print("⚠️  Supabase CLI not found — checking psql...")
    has_cli = False

# Check psql
try:
    result = subprocess.run(["psql", "--version"], capture_output=True, text=True, timeout=5)
    print(f"✅ psql found: {result.stdout.strip()}")
    has_psql = True
except Exception:
    print("⚠️  psql not found")
    has_psql = False

print()

if not has_psql and not has_cli:
    print("📋 MANUAL MIGRATION REQUIRED")
    print("=" * 50)
    print("Open: https://supabase.com/dashboard/project/zrvsuwdlhnnfvqxxohex/sql/new")
    print()
    print("Run these files IN ORDER in the SQL Editor:")
    for d, f in files:
        path = os.path.join(d, f)
        print(f"  {f}")
        with open(path, "r", encoding="utf-8") as fp:
            content = fp.read()
        print(f"  ({len(content)} chars, {content.count(chr(10))} lines)")
    print()
    print("Saving combined migration file for easy copy-paste...")
    combined_path = r"C:\Users\GPD\.gemini\antigravity\scratch\caios\database\FULL_MIGRATION.sql"
    with open(combined_path, "w", encoding="utf-8") as out:
        for d, f in files:
            path = os.path.join(d, f)
            out.write(f"-- ============================================================\n")
            out.write(f"-- {f}\n")
            out.write(f"-- ============================================================\n\n")
            with open(path, "r", encoding="utf-8") as fp:
                out.write(fp.read())
            out.write("\n\n")
    print(f"✅ Combined file saved: {combined_path}")
    sys.exit(0)
