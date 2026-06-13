"""Xoder DB Status Updater — standalone script replacing doc-agent Step 8 python -c."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
from db_client import XoderDBClient


def main():
    parser = argparse.ArgumentParser(description="Xoder DB Status Updater")
    parser.add_argument("--workspace", "-w", default=".", help="Project root")
    parser.add_argument("--domains-file", "-d", default=".xoder-local/stage/super_planner_domains.json",
                        help="Path to super_planner_domains.json")
    args = parser.parse_args()

    db_path = os.path.join(args.workspace, ".xoder", "repowiki", "wiki_sync_metadata.db")
    db = XoderDBClient(db_path)
    db.connect()

    domains_path = os.path.join(args.workspace, args.domains_file)
    if os.path.isfile(domains_path):
        with open(domains_path, "r", encoding="utf-8") as f:
            domains_data = json.load(f)
        for d in domains_data.get("domains", []):
            db.update_module_wiki_status(d["domain_id"], "SUCCESS")
    db.close()
    print("OK: DB status updated")


if __name__ == "__main__":
    main()
