"""Xoder Architecture Pattern Detection — standalone script replacing python -c command."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
from ast_parser import detect_architecture_pattern, discover_modules

def main():
    parser = argparse.ArgumentParser(description="Xoder Architecture Pattern Detection")
    parser.add_argument("--workspace", "-w", default=".", help="Project root directory")
    parser.add_argument("--output-arch", "-a", required=True, help="Architecture pattern output JSON")
    parser.add_argument("--output-modules", "-m", required=True, help="Module discovery output JSON")
    args = parser.parse_args()

    arch = detect_architecture_pattern(args.workspace)
    os.makedirs(os.path.dirname(os.path.abspath(args.output_arch)), exist_ok=True)
    with open(args.output_arch, "w", encoding="utf-8") as f:
        json.dump(arch, f, ensure_ascii=False, indent=2)
    print(f"OK: {arch.get('pattern','?')} (conf={arch.get('confidence',0)}) -> {args.output_arch}")

    modules = discover_modules(args.workspace)

    # Filter noise modules
    noise_prefixes = ("scripts", "skills", "dashboard", ".xoder", ".xoder-local", "__pycache__")
    if "modules" in modules:
        raw = modules["modules"]
        if isinstance(raw, dict):
            filtered = {}
            for mod_name, files in raw.items():
                parts = mod_name.replace('\\', '/').split('/')
                if parts[0] in noise_prefixes:
                    continue
                filtered[mod_name] = files
            modules["modules"] = filtered
            modules["module_count"] = len(filtered)
        elif isinstance(raw, list):
            filtered = [m for m in raw if m.get("name","").replace('\\','/').split('/')[0] not in noise_prefixes]
            modules["modules"] = filtered
            modules["module_count"] = len(filtered)

    # Post-process: split mixed Java modules into java/resources/templates/static
    if "modules" in modules and isinstance(modules["modules"], dict):
        raw = modules["modules"]
        refined = {}
        for mod_name, files in raw.items():
            java_files = [f for f in files if f.endswith(".java")]
            resource_files = [f for f in files if not f.endswith(".java")]
            if java_files and resource_files and len(files) > 20:
                refined[mod_name + "/java"] = java_files
                templates = [f for f in resource_files if "templates" in f]
                static_f = [f for f in resource_files if "static" in f]
                other_res = [f for f in resource_files if f not in templates and f not in static_f]
                if templates:
                    refined[mod_name + "/templates"] = templates
                if static_f:
                    refined[mod_name + "/static"] = static_f
                if other_res:
                    refined[mod_name + "/resources"] = other_res
            else:
                refined[mod_name] = files
        modules["modules"] = refined
        modules["module_count"] = len(refined)

    os.makedirs(os.path.dirname(os.path.abspath(args.output_modules)), exist_ok=True)
    with open(args.output_modules, "w", encoding="utf-8") as f:
        json.dump(modules, f, ensure_ascii=False, indent=2)
    print(f"OK: {modules.get('module_count',0)} modules -> {args.output_modules}")

if __name__ == "__main__":
    main()
