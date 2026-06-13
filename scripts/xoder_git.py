"""Xoder Git Archaeology — standalone script replacing python -c command."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
import subprocess

def main():
    parser = argparse.ArgumentParser(description="Xoder Git Archaeology")
    parser.add_argument("--workspace", "-w", default=".", help="Project root directory")
    parser.add_argument("--output", "-o", required=True, help="Output JSON file path")
    args = parser.parse_args()

    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "git_timeline.py")
    r = subprocess.run(
        [sys.executable, script, "--mode", "full", "--repo", args.workspace],
        capture_output=True, text=True, cwd=args.workspace
    )

    if r.returncode != 0:
        print(f"FAIL: git_timeline exited with {r.returncode}")
        print(r.stderr[:500] if r.stderr else "no stderr")
        sys.exit(1)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(r.stdout)
    print(f"OK: {len(r.stdout)} bytes -> {args.output}")

if __name__ == "__main__":
    main()
