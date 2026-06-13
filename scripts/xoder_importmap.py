"""Xoder ImportMap Builder — standalone script for alignment agent."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
from ast_parser import build_import_map

def main():
    parser = argparse.ArgumentParser(description="Xoder ImportMap Builder")
    parser.add_argument("--workspace", "-w", default=".", help="Project root directory")
    parser.add_argument("--output", "-o", required=True, help="Output JSON file path")
    args = parser.parse_args()

    r = build_import_map(args.workspace)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)
    print(f"OK: {r['stats']['files_scanned']} files scanned -> {args.output}")

if __name__ == "__main__":
    main()
