"""Xoder Knowledge Importer — auto-scan .xoder/knowledge/ for new/changed files and convert to .md."""
import sys, os, json, hashlib, glob as _glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
from datetime import datetime

_MD_AVAILABLE = False
try:
    from markitdown import MarkItDown
    _MD_AVAILABLE = True
except ImportError:
    pass

SUPPORTED_EXT = {".md", ".txt", ".text", ".rst", ".adoc", ".html", ".htm",
                 ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv",
                 ".pptx", ".epub", ".ipynb", ".json"}


def file_hash(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk: break
            h.update(chunk)
    return h.hexdigest()


def convert_file(src: str) -> str | None:
    """Convert a file to markdown. Returns None on failure."""
    ext = os.path.splitext(src)[1].lower()
    if _MD_AVAILABLE:
        try:
            md = MarkItDown()
            result = md.convert(src)
            if result.markdown and result.markdown.strip():
                return result.markdown
        except Exception:
            pass
    # Fallback
    if ext in (".md", ".txt", ".text", ".rst", ".adoc", ".json"):
        try:
            with open(src, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return None
    return None


def main():
    parser = argparse.ArgumentParser(description="Xoder Knowledge Importer")
    parser.add_argument("--workspace", "-w", default=".", help="Project root")
    parser.add_argument("--auto", "-a", action="store_true", help="Auto-scan .xoder/knowledge/ for new/changed files")
    parser.add_argument("--input", "-i", help="Single file to import")
    parser.add_argument("--name", "-n", help="Knowledge entry name")
    parser.add_argument("--tags", "-t", default="", help="Comma-separated tags")
    args = parser.parse_args()

    if not _MD_AVAILABLE:
        print("markitdown not installed. Only .md / .txt / .json files supported.")
        print("To enable PDF/DOCX/XLSX support: pip install 'markitdown[all]'")
        print()

    knowledge_dir = os.path.join(args.workspace, ".xoder", "knowledge")
    os.makedirs(knowledge_dir, exist_ok=True)

    if args.auto:
        run_auto(args.workspace, knowledge_dir)
    elif args.input:
        run_single(args.input, args.name, args.tags, knowledge_dir)
    else:
        parser.print_help()


def run_single(src, name, tags_str, knowledge_dir):
    if not os.path.isfile(src):
        print(f"FAIL: file not found: {src}")
        sys.exit(1)
    ext = os.path.splitext(src)[1].lower()
    name = name or os.path.splitext(os.path.basename(src))[0]
    converted = convert_file(src)
    if not converted or not converted.strip():
        print(f"FAIL: cannot convert {ext}. Run: pip install 'markitdown[all]'")
        sys.exit(1)
    save_entry(knowledge_dir, name, os.path.basename(src), os.path.abspath(src),
               ext, [t.strip() for t in tags_str.split(",") if t.strip()], converted)
    print(f"OK: {len(converted.split())} words -> {os.path.join(knowledge_dir, name + '.md')}")


def run_auto(workspace, knowledge_dir):
    existing_meta = {}
    for mf in _glob.glob(os.path.join(knowledge_dir, "*.meta.json")):
        try:
            with open(mf, "r", encoding="utf-8") as f:
                meta = json.load(f)
            existing_meta[meta.get("name", "")] = meta
        except Exception:
            pass

    # Find all importable files in knowledge_dir
    all_files = []
    for fname in os.listdir(knowledge_dir):
        fpath = os.path.join(knowledge_dir, fname)
        if not os.path.isfile(fpath):
            continue
        if fname.endswith(".meta.json") or fname.startswith("."):
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext not in SUPPORTED_EXT:
            continue
        all_files.append(fpath)

    updated = 0
    skipped = 0
    failed = 0

    for fpath in sorted(all_files):
        fname = os.path.basename(fpath)
        name = os.path.splitext(fname)[0]
        fhash = file_hash(fpath)
        ext = os.path.splitext(fname)[1].lower()

        # Check if unchanged
        prev = existing_meta.get(name, {})
        if prev.get("content_hash") == fhash:
            skipped += 1
            continue

        # New or changed — convert
        tags = prev.get("tags", [])
        source_file = prev.get("source_file", fname)

        converted = convert_file(fpath)
        if not converted or not converted.strip():
            print(f"SKIP: {fname} — cannot convert {ext}")
            failed += 1
            continue

        save_entry(knowledge_dir, name, source_file, os.path.abspath(fpath), ext, tags, converted)
        updated += 1
        print(f"OK: {fname} ({len(converted.split())} words)")

    print(f"\nKnowledge scan: {updated} updated, {skipped} unchanged, {failed} failed")


def save_entry(knowledge_dir, name, source_file, source_path, fmt, tags, content):
    out_path = os.path.join(knowledge_dir, f"{name}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    meta = {
        "name": name,
        "source_file": source_file,
        "source_path": source_path,
        "format": fmt,
        "tags": tags,
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "imported_at": datetime.now().isoformat(),
    }
    meta_path = os.path.join(knowledge_dir, f"{name}.meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
