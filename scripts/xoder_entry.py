"""Xoder Entry Point Detection — standalone script replacing python -c command."""
import sys, os, json, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
from ast_parser import discover_entry_points
from config import ENTRY_NOISE_PATHS

def main():
    parser = argparse.ArgumentParser(description="Xoder Entry Point Detection")
    parser.add_argument("--workspace", "-w", default=".", help="Project root directory")
    parser.add_argument("--output", "-o", required=True, help="Output JSON file path")
    args = parser.parse_args()

    noise = {"scripts", "skills", "dashboard", ".xoder", ".xoder-local"}
    r = discover_entry_points(args.workspace)
    r["entry_points"] = [
        e for e in r["entry_points"]
        if e["file"].replace("\\", "/").lstrip("./").split("/")[0] not in noise
        and not any(seg in ENTRY_NOISE_PATHS for seg in e["file"].replace("\\", "/").split("/"))
    ]
    r["total"] = len(r["entry_points"])

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)
    print(f"OK: {r['total']} entry points -> {args.output}")

    # Write Task_Queue rows for status tracking
    try:
        db_path = os.path.join(args.workspace, ".xoder", "repowiki", "wiki_sync_metadata.db")
        if os.path.exists(db_path):
            from db_client import XoderDBClient
            db = XoderDBClient(db_path)
            db.connect()
            for m in r.get("entry_points", [])[:1]:  # one row for the archaeology phase
                tid = hashlib.md5(b"entry_detection").hexdigest()
                db.upsert_task(tid, "entry_detection", json.dumps(["entry_points.json"]),
                              hashlib.md5(json.dumps(r, ensure_ascii=False).encode()).hexdigest(), "SUCCESS")
            db.close()
    except Exception:
        pass

if __name__ == "__main__":
    main()
