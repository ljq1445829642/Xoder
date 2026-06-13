"""Xoder ORM Penetration — standalone script replacing python -c command."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
from orm_penetrator import ORMPenetrator

def main():
    parser = argparse.ArgumentParser(description="Xoder ORM Penetration")
    parser.add_argument("--workspace", "-w", default=".", help="Project root directory")
    parser.add_argument("--output", "-o", required=True, help="Output JSON file path")
    args = parser.parse_args()

    p = ORMPenetrator()
    r = p.penetrate(args.workspace)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)
    print(f"OK: {len(r.get('tables',[]))} tables, {len(r.get('relations',[]))} relations -> {args.output}")

if __name__ == "__main__":
    main()
