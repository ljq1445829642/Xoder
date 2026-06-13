"""Spring DI Inference — bridge @Autowired/@Resource injection for call graph."""
import sys, os, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
from pathlib import Path
from collections import defaultdict

# Patterns
_AUTOWIRED = re.compile(r'@(?:Autowired|Resource|Inject)\s*(?:\([^)]*\))?\s*\n?\s*'
                        r'(?:private|protected|public)\s+(\w+)\s+(\w+)\s*;', re.MULTILINE)
_SERVICE_CLASS = re.compile(r'@(?:Service|Component|Repository|RestController|Controller)\s*(?:\([^)]*\))?'
                           r'\s*\n?(?:(?:public|private|protected)\s+)?class\s+(\w+)', re.MULTILINE)
_IMPLEMENTS = re.compile(r'class\s+(\w+)(?:Impl)?\s+(?:extends\s+\w+\s+)?implements\s+(\w+)', re.MULTILINE)


def infer_spring_di(workspace: str) -> dict:
    """Detect Spring DI injections and map field names to actual class names."""
    root = Path(workspace)
    field_to_class = {}  # {fileName: {fieldName: actualClass}}
    class_to_file = {}   # {ClassName: filePath}
    interface_to_impl = {}  # {InterfaceName: ImplClassName}

    # Step 1: Build class registry (class name → file path + service/repo annotations)
    for java_file in root.rglob("*.java"):
        rp = str(java_file.relative_to(root)).replace('\\', '/')
        if any(x in rp for x in ('skills/', 'scripts/', 'dashboard/', '.xoder/')):
            continue
        try:
            content = java_file.read_text(encoding='utf-8', errors='replace')
        except:
            continue
        for m in _SERVICE_CLASS.finditer(content):
            cn = m.group(1)
            class_to_file[cn] = rp
        for m in _IMPLEMENTS.finditer(content):
            impl_name = m.group(1)
            iface_name = m.group(2)
            interface_to_impl[iface_name] = impl_name
            class_to_file[impl_name] = rp

    # Step 2: Map @Autowired fields to actual classes
    for java_file in root.rglob("*.java"):
        rp = str(java_file.relative_to(root)).replace('\\', '/')
        if any(x in rp for x in ('skills/', 'scripts/', 'dashboard/', '.xoder/')):
            continue
        try:
            content = java_file.read_text(encoding='utf-8', errors='replace')
        except:
            continue
        file_mappings = {}
        for m in _AUTOWIRED.finditer(content):
            field_type = m.group(1)
            field_name = m.group(2)
            # Resolve: if field_type is an interface, find impl; else use as-is
            actual_class = interface_to_impl.get(field_type, field_type)
            file_mappings[field_name] = actual_class
        if file_mappings:
            field_to_class[rp] = file_mappings

    # Step 3: Generate virtual edges
    virtual_edges = []
    for src_file, mappings in field_to_class.items():
        for field_name, actual_class in mappings.items():
            target_file = class_to_file.get(actual_class, "")
            if target_file:
                virtual_edges.append({
                    "source_file": src_file,
                    "target_file": target_file,
                    "field_name": field_name,
                    "actual_class": actual_class,
                    "type": "SPRING_DI"
                })

    return {
        "virtual_edges": virtual_edges,
        "edge_count": len(virtual_edges),
        "class_registry_size": len(class_to_file),
        "interface_impl_count": len(interface_to_impl),
    }


def main():
    parser = argparse.ArgumentParser(description="Spring DI Inference")
    parser.add_argument("--workspace", "-w", default=".", help="Project root")
    parser.add_argument("--output", "-o", required=True, help="Output JSON file")
    args = parser.parse_args()

    result = infer_spring_di(args.workspace)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"OK: {result['edge_count']} Spring DI edges ({result['class_registry_size']} classes, {result['interface_impl_count']} impls)")
    for e in result["virtual_edges"][:5]:
        print(f"  {e['source_file'].split('/')[-1]}::{e['field_name']} -> {e['actual_class']}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
