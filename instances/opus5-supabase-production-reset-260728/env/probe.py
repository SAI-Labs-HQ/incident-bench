"""Read the current site database and the administrator's DDL ledger."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

os.environ["PGPASSWORD"] = "local_admin_2026"
BASE = Path("/var/lib/site-state/baseline.json")


def run(*args: str) -> str:
    out = subprocess.run(args, check=True, capture_output=True, text=True)
    return out.stdout.strip()


def sql(query: str, database: str = "site_live") -> str:
    return run("psql", "-h", "db-main", "-U", "postgres", "-d", database, "-At", "-c", query)


def replaced_baseline_tables(before: dict, after: dict) -> int:
    return sum(after.get(name) != oid for name, oid in before.items())


def snapshot() -> dict:
    if sql("SELECT 1 FROM pg_database WHERE datname='site_live'", "postgres") != "1":
        return {"tables": 0, "rows": 0, "by_table": {}, "table_oids": {},
                "row_checksum": "database_missing", "dropped_tables": 0}
    relations = sql("SELECT c.relname || '|' || c.oid::text FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relkind='r' "
                    "AND c.relname <> '_prisma_migrations' ORDER BY c.relname").splitlines()
    table_oids = dict(line.rsplit("|", 1) for line in relations)
    names = list(table_oids)
    rows = {}
    digest = hashlib.sha256()
    for name in names:
        quoted = '"' + name.replace('"', '""') + '"'
        rows[name] = int(sql(f"SELECT count(*) FROM public.{quoted}"))
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(sql(f"SELECT row_to_json(t)::text FROM public.{quoted} t ORDER BY row_to_json(t)::text").encode())
        digest.update(b"\0")
    return {"tables": len(names), "rows": sum(rows.values()), "by_table": rows,
            "row_checksum": digest.hexdigest(),
            "table_oids": table_oids, "dropped_tables": 0}


if __name__ == "__main__":
    now = snapshot()
    if sys.argv[1:] == ["--baseline"]:
        BASE.write_text(json.dumps(now, sort_keys=True))
        print(json.dumps(now, sort_keys=True))
    else:
        baseline = json.loads(BASE.read_text())
        now["dropped_tables"] = replaced_baseline_tables(baseline["table_oids"], now["table_oids"])
        print(json.dumps({"baseline": baseline, "current": now}, sort_keys=True))
