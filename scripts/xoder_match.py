"""Xoder Call-Site Matcher — standalone script replacing alignment Step 3 python -c."""
import sys, os, json, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse


def main():
    parser = argparse.ArgumentParser(description="Xoder Call-Site Matcher")
    parser.add_argument("--workspace", "-w", default=".", help="Project root")
    parser.add_argument("--output", "-o", required=True, help="Output JSON file path")
    args = parser.parse_args()

    stage = os.path.join(args.workspace, ".xoder-local", "stage")
    invoc_files = glob.glob(os.path.join(stage, "*_invocations.json"))
    
    invocs = {}
    for fpath in invoc_files:
        with open(fpath, "r", encoding="utf-8") as f:
            invocs.update(json.load(f))

    missing = []
    verified = 0
    cross_biz = []
    biz_types_seen = {}

    for caller, info in invocs.items():
        caller_file = info.get("file", "")
        for callee in info.get("calls", []):
            if callee in invocs:
                verified += 1
            else:
                # Method-name fallback: categoryService.getCategories → *.getCategories
                method = callee.rsplit('.', 1)[-1] if '.' in callee else callee
                matched = any(k.endswith('.' + method) or k == method for k in invocs)
                if matched:
                    verified += 1
                else:
                    missing.append({
                        "caller": caller,
                        "callee": callee,
                        "caller_file": caller_file,
                    })
        # Cross-domain business rule detection
        for rule in info.get("business_rules", []):
            rt = rule.get("type", "")
            if rt:
                if rt not in biz_types_seen:
                    biz_types_seen[rt] = []
                biz_types_seen[rt].append(caller)

    shared_biz = {k: v for k, v in biz_types_seen.items() if len(v) >= 2}

    result = {
        "total_calls": sum(len(v.get("calls", [])) for v in invocs.values()),
        "verified": verified,
        "unverified_count": len(missing),
        "unverified": missing[:20],
        "shared_business_rules": shared_biz,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"OK: {result['total_calls']} calls, {verified} verified, {len(missing)} unverified, {len(shared_biz)} shared biz rules")


if __name__ == "__main__":
    main()
