"""Xoder Invocation Card Extractor — standalone script replacing domain-worker Step 2 python -c."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
from ast_parser import parse_file


def main():
    parser = argparse.ArgumentParser(description="Xoder Invocation Card Extractor")
    parser.add_argument("--workspace", "-w", default=".", help="Project root")
    parser.add_argument("--domain-id", "-d", required=True, help="Domain ID for output filename")
    parser.add_argument("--files", "-f", required=True, help="Comma-separated list of file paths in this domain")
    parser.add_argument("--max-files", "-n", type=int, default=30, help="Max files to process (importance sampling)")
    parser.add_argument("--output-dir", "-o", default=".xoder-local/stage", help="Output directory")
    args = parser.parse_args()

    files = [f.strip() for f in args.files.split(",") if f.strip()]
    
    # Importance sampling: Controller > Service > Repository > Model > other
    def priority(fp: str) -> int:
        lower = fp.lower()
        if any(k in lower for k in ("controller", "handler", "resource")):
            return 0
        if any(k in lower for k in ("service", "usecase", "manager", "use_case")):
            return 1
        if any(k in lower for k in ("repository", "dao", "mapper", "repo")):
            return 2
        if any(k in lower for k in ("model", "entity", "domain", "dto")):
            return 3
        return 4

    files.sort(key=priority)
    files = files[:args.max_files]

    cards = {}
    for f in files:
        fp = os.path.join(args.workspace, f)
        if not os.path.isfile(fp):
            continue
        try:
            r = parse_file(fp)
        except Exception:
            continue
        syms = r.get("symbols", {})
        for cls in syms.get("classes", []):
            for mt in cls.get("methods", []):
                deps = mt.get("dependencies", [])
                rules = mt.get("business_rules", [])
                if deps or rules:
                    key = f"{cls['class_name']}.{mt['name']}"
                    cards[key] = {
                        "file": f,
                        "calls": deps,
                        "annotations": mt.get("annotations", []),
                        "business_rules": rules,
                    }

    os.makedirs(args.output_dir, exist_ok=True)
    output = os.path.join(args.output_dir, f"{args.domain_id}_invocations.json")
    with open(output, "w", encoding="utf-8") as fh:
        json.dump(cards, fh, ensure_ascii=False, indent=2)

    rule_count = sum(len(v.get("business_rules", [])) for v in cards.values())
    print(f"OK: {len(cards)} invocation cards ({len(files)} files, {rule_count} business rules) -> {output}")


if __name__ == "__main__":
    main()
