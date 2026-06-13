"""Xoder Call Chain Tracing — standalone script replacing python -c command."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
from ast_parser import trace_from_entries

def main():
    parser = argparse.ArgumentParser(description="Xoder Call Chain Tracing")
    parser.add_argument("--workspace", "-w", default=".", help="Project root directory")
    parser.add_argument("--entries", "-e", required=True, help="entry_points.json file")
    parser.add_argument("--output", "-o", required=True, help="Output JSON file path")
    args = parser.parse_args()

    if not os.path.isfile(args.entries):
        print(f"FAIL: entry_points.json not found at {args.entries}")
        sys.exit(1)
    if os.path.getsize(args.entries) == 0:
        print(f"FAIL: entry_points.json is empty at {args.entries}")
        sys.exit(1)
    try:
        with open(args.entries, 'r', encoding='utf-8') as f:
            json.load(f)
    except Exception as e:
        print(f"FAIL: entry_points.json is invalid JSON: {e}")
        sys.exit(1)

    r = trace_from_entries(args.workspace, args.entries)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)
    print(f"OK: {r.get('chain_count',0)} call chains -> {args.output}")

if __name__ == "__main__":
    main()
