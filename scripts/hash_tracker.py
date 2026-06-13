"""
Local incremental hash comparison, bidirectional sync monitoring,
and graph-level downstream propagation.

Modes:
  register  – Register all file hashes into the DB
  diff      – Find changed files vs DB
  watch     – Watch wiki dir for human edits
  propagate – Graph-level downstream propagation from changed files
"""

import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import argparse
import collections
import difflib
import hashlib
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from config import (
    HASH_ALGORITHM,
    HASH_CHUNK_SIZE,
    ERROR_CODE_MAP,
    XODER_HIDDEN_DIR,
    REPOWIKI_DIR,
    CODE_EXTENSIONS,
)
from db_client import XoderDBClient

logger = logging.getLogger(__name__)

XODERIGNORE = ".xoderignore"


class HashTracker:

    SRC_DIR_NAME = "src"
    WIKI_DIR_NAME = "wiki"

    # =========================================================================
    # __init__
    # =========================================================================

    def __init__(self):
        self._hash_algorithm = HASH_ALGORITHM
        self._chunk_size = HASH_CHUNK_SIZE

    # =========================================================================
    # compute_file_hash
    # =========================================================================

    def compute_file_hash(self, file_path: str) -> str:
        hasher = hashlib.new(HASH_ALGORITHM)
        try:
            with open(file_path, "rb") as fh:
                while True:
                    chunk = fh.read(HASH_CHUNK_SIZE)
                    if not chunk:
                        break
                    hasher.update(chunk)
            return hasher.hexdigest()
        except (IOError, OSError) as exc:
            logger.error("Failed to hash %s: %s", file_path, exc)
            return ""

    # =========================================================================
    # compute_combined_hash
    # =========================================================================

    def compute_combined_hash(self, file_paths: List[str]) -> str:
        sorted_paths = sorted(set(file_paths))
        hasher = hashlib.new(HASH_ALGORITHM)
        for fp in sorted_paths:
            file_hash = self.compute_file_hash(fp)
            hasher.update(file_hash.encode("utf-8"))
            hasher.update(fp.encode("utf-8"))
        return hasher.hexdigest()

    # =========================================================================
    # compute_fingerprints
    # =========================================================================

    def compute_fingerprints(self, workspace_dir: str) -> str:
        """Compute SHA-256 fingerprints for all source files in workspace.
        Called by archaeologist skill: HashTracker().compute_fingerprints('.')
        
        Returns: JSON string with {file_path: sha256_hash, ...}
        """
        import json as _json
        from pathlib import Path as _Path
        results = {}
        root = _Path(workspace_dir)
        exts = CODE_EXTENSIONS if hasattr(self, '_exts') or True else {'.py','.java','.go','.ts','.js'}
        # Use the module-level CODE_EXTENSIONS
        from config import CODE_EXTENSIONS as _CE, DEFAULT_EXCLUDED_DIRS as _DED
        for ext in _CE:
            for fp in root.rglob(f'*{ext}'):
                rel = str(fp.relative_to(root))
                if any(d in rel.split('/') or d in rel.split('\\') for d in _DED):
                    continue
                try:
                    results[rel] = self.compute_file_hash(str(fp))
                except Exception:
                    pass
        return _json.dumps(results, indent=2)

    # =========================================================================
    # scan_and_compare
    # =========================================================================

    def scan_and_compare(
        self,
        workspace_dir: str,
        db_client: XoderDBClient,
        file_paths: List[str],
    ) -> Dict[str, str]:
        result: Dict[str, str] = {}
        ignore_patterns = self._load_ignore_patterns(workspace_dir)
        existing = set(file_paths)
        db_tracked: Dict[str, Dict] = {}

        db_client.connect()
        cursor = db_client._conn.cursor()
        for fp in existing:
            cursor.execute(
                "SELECT * FROM Hash_Fingerprint WHERE file_path = ?", (fp,)
            )
            row = cursor.fetchone()
            if row:
                db_tracked[fp] = dict(row)

        for fp in existing:
            if self._should_ignore(fp, ignore_patterns):
                continue

            if not os.path.isfile(fp):
                result[fp] = "deleted"
                db_client.mark_hash_outdated(fp)
                continue

            current_hash = self.compute_file_hash(fp)
            if not current_hash:
                result[fp] = "unchanged"
                continue

            if fp in db_tracked:
                stored_hash = db_tracked[fp].get("sha256_hash", "")
                if current_hash == stored_hash:
                    result[fp] = "unchanged"
                else:
                    result[fp] = "changed"
                    _update_hash_record(db_client, fp, current_hash)
            else:
                result[fp] = "new"
                _update_hash_record(db_client, fp, current_hash)

        for fp in db_tracked:
            if fp not in existing:
                result[fp] = "deleted"
                db_client.mark_hash_outdated(fp)

        return result

    # =========================================================================
    # watch_wiki_dir
    # =========================================================================

    def watch_wiki_dir(
        self, workspace_dir: str, db_client: XoderDBClient
    ) -> List[Dict]:
        wiki_root = os.path.join(workspace_dir, XODER_HIDDEN_DIR, REPOWIKI_DIR)
        if not os.path.isdir(wiki_root):
            logger.info("No repowiki directory found at %s", wiki_root)
            return []

        annotations: List[Dict] = []
        ignore_patterns = self._load_ignore_patterns(workspace_dir)

        for root, dirs, files in os.walk(wiki_root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(root, fname)
                if self._should_ignore(fpath, ignore_patterns):
                    continue

                annotation = self._extract_wiki_annotation(fpath, workspace_dir)
                if annotation:
                    db_client.inject_human_annotation(
                        target_file_path=fpath,
                        module_name=annotation.get("module_name", "unknown"),
                        annotation=annotation,
                        diff_snapshot=annotation.get("diff", ""),
                    )
                    annotations.append(annotation)

        return annotations

    def _extract_wiki_annotation(
        self, wiki_path: str, workspace_dir: str
    ) -> Optional[Dict]:
        try:
            with open(wiki_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except (IOError, OSError):
            return None

        relative_path = os.path.relpath(wiki_path, workspace_dir)
        module_name = self._infer_module_name(relative_path)

        source_file = self._resolve_source_file(workspace_dir, module_name)
        diff = ""
        if source_file and os.path.isfile(source_file):
            diff = self._compute_wiki_diff(source_file, content)

        sections = self._parse_markdown_sections(content)

        return {
            "wiki_path": wiki_path,
            "module_name": module_name,
            "relative_path": relative_path,
            "content_hash": hashlib.new(HASH_ALGORITHM, content.encode()).hexdigest(),
            "diff": diff,
            "sections": sections,
            "has_changes": bool(diff),
        }

    @staticmethod
    def _infer_module_name(relative_path: str) -> str:
        parts = Path(relative_path).parts
        for i, p in enumerate(parts):
            if p in (REPOWIKI_DIR, "repowiki"):
                if i + 1 < len(parts):
                    return parts[i + 1]
        return os.path.splitext(Path(relative_path).name)[0]

    @staticmethod
    def _resolve_source_file(workspace_dir: str, module_name: str) -> Optional[str]:
        candidates = [
            os.path.join(workspace_dir, module_name),
            os.path.join(workspace_dir, "src", module_name),
            os.path.join(workspace_dir, module_name, "__init__.py"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        return None

    @staticmethod
    def _compute_wiki_diff(source_file: str, wiki_content: str) -> str:
        try:
            with open(source_file, "r", encoding="utf-8", errors="replace") as fh:
                source_lines = fh.readlines()
        except (IOError, OSError):
            return ""

        wiki_lines = wiki_content.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(source_lines, wiki_lines, fromfile=source_file, tofile="wiki")
        )
        return "".join(diff_lines) if diff_lines else ""

    @staticmethod
    def _parse_markdown_sections(content: str) -> Dict[str, str]:
        sections: Dict[str, str] = {}
        current_heading: Optional[str] = None
        current_body: List[str] = []

        for line in content.split("\n"):
            if line.startswith("## "):
                if current_heading:
                    sections[current_heading] = "\n".join(current_body).strip()
                current_heading = line[3:].strip()
                current_body = []
            elif line.startswith("# "):
                if current_heading:
                    sections[current_heading] = "\n".join(current_body).strip()
                current_heading = line[2:].strip()
                current_body = []
            else:
                current_body.append(line)

        if current_heading:
            sections[current_heading] = "\n".join(current_body).strip()

        return sections

    # =========================================================================
    # sync_back
    # =========================================================================

    def sync_back(
        self, workspace_dir: str, db_client: XoderDBClient
    ) -> Dict:
        report: Dict = {"synced": [], "conflicts": [], "skipped": []}

        db_client.connect()
        cursor = db_client._conn.cursor()
        cursor.execute(
            "SELECT * FROM Reverse_Sync_Metadata WHERE consumed_by_task_id IS NULL"
        )
        rows = cursor.fetchall()

        for row in rows:
            annotation = dict(row)
            wiki_path = annotation["target_file_path"]

            if not os.path.isfile(wiki_path):
                report["skipped"].append({
                    "annotation_id": annotation["id"],
                    "reason": "Wiki file not found",
                })
                continue

            source_file = self._resolve_source_file(
                workspace_dir, annotation["module_name"]
            )
            if not source_file or not os.path.isfile(source_file):
                report["skipped"].append({
                    "annotation_id": annotation["id"],
                    "reason": "Source file not found",
                })
                continue

            try:
                applied = self._apply_wiki_to_source(wiki_path, source_file)
                if applied:
                    db_client.mark_annotation_consumed(annotation["id"], "SYNC_BACK")
                    logger.info("Synced wiki changes from %s to %s", wiki_path, source_file)
                    report["synced"].append({
                        "annotation_id": annotation["id"],
                        "source_file": source_file,
                        "wiki_path": wiki_path,
                    })
                else:
                    report["conflicts"].append({
                        "annotation_id": annotation["id"],
                        "error_code": 80008,
                        "reason": "Sync conflict — manual resolution required",
                        "source_file": source_file,
                        "wiki_path": wiki_path,
                    })
                    db_client.mark_hash_outdated(source_file)
            except Exception as exc:
                logger.error("Sync back failed for %s: %s", wiki_path, exc)
                report["conflicts"].append({
                    "annotation_id": annotation["id"],
                    "error_code": 80008,
                    "reason": str(exc),
                })

        return report

    def _apply_wiki_to_source(self, wiki_path: str, source_path: str) -> bool:
        try:
            with open(wiki_path, "r", encoding="utf-8", errors="replace") as fh:
                wiki_content = fh.read()
            with open(source_path, "r", encoding="utf-8", errors="replace") as fh:
                source_content = fh.read()
        except (IOError, OSError):
            return False

        wiki_hash = hashlib.new(HASH_ALGORITHM, wiki_content.encode()).hexdigest()
        source_hash = hashlib.new(HASH_ALGORITHM, source_content.encode()).hexdigest()

        if wiki_hash == source_hash:
            return True

        diff_ratio = difflib.SequenceMatcher(None, source_content, wiki_content).ratio()
        if diff_ratio < 0.5:
            logger.warning(
                "Sync conflict: %s and %s differ by %.1f%% — manual review needed",
                source_path, wiki_path, (1 - diff_ratio) * 100,
            )
            return False

        try:
            with open(source_path, "w", encoding="utf-8") as fh:
                fh.write(wiki_content)
            return True
        except (IOError, OSError):
            return False

    # =========================================================================
    # graph_propagate (NEW – P2)
    # =========================================================================

    def graph_propagate(
        self,
        workspace_dir: str,
        db_client: XoderDBClient,
        changed_files: List[str],
    ) -> Dict:
        db_client.connect()
        conn = db_client._conn

        normalized = {os.path.normpath(f).replace("\\", "/") for f in changed_files}

        starting_nodes: Set[str] = set()
        if normalized:
            placeholders = ",".join("?" * len(normalized))
            rows = conn.execute(
                f"SELECT DISTINCT source_node FROM CodeGraph_Topology "
                f"WHERE source_file IN ({placeholders})",
                list(normalized),
            ).fetchall()
            starting_nodes = {r[0] for r in rows}

        visited_nodes: Set[str] = set(starting_nodes)
        affected_files: Set[str] = set()
        queue = collections.deque(starting_nodes)

        edge_types = ("CALLS", "DEPENDS_ON", "IMPORTS")

        while queue:
            node = queue.popleft()
            rows = conn.execute(
                "SELECT target_node, target_file, edge_type FROM CodeGraph_Topology "
                "WHERE source_node = ?",
                (node,),
            ).fetchall()
            for target_node, target_file, edge_type in rows:
                if edge_type not in edge_types:
                    continue
                if target_file:
                    norm_target = os.path.normpath(target_file).replace("\\", "/")
                    affected_files.add(norm_target)
                if target_node and target_node not in visited_nodes:
                    visited_nodes.add(target_node)
                    queue.append(target_node)

        all_affected = sorted(affected_files)

        # Mark Hash_Fingerprint entries as OUTDATED
        for fpath in all_affected:
            conn.execute(
                "UPDATE Hash_Fingerprint SET sync_status = 'OUTDATED' WHERE file_path = ?",
                (fpath,),
            )

        # Mark Task_Queue entries as PENDING for affected files
        modules_to_regenerate: List[str] = []
        for fpath in all_affected:
            rows = conn.execute(
                "SELECT module_name FROM Task_Queue WHERE file_paths LIKE ?",
                (f"%{fpath}%",),
            ).fetchall()
            for (mod,) in rows:
                if mod not in modules_to_regenerate:
                    modules_to_regenerate.append(mod)
            conn.execute(
                "UPDATE Task_Queue SET status = 'PENDING', error_code = 0, "
                "updated_at = datetime('now') WHERE file_paths LIKE ?",
                (f"%{fpath}%",),
            )

        conn.commit()

        return {
            "directly_changed": changed_files,
            "downstream_affected": all_affected,
            "modules_to_regenerate": modules_to_regenerate,
        }

    # =========================================================================
    # register_all_files
    # =========================================================================

    def register_all_files(
        self, workspace_dir: str, db_client: XoderDBClient
    ) -> Dict[str, int]:
        db_client.connect()
        ignore_patterns = self._load_ignore_patterns(workspace_dir)
        file_paths: List[str] = []

        for root, dirs, files in os.walk(workspace_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                fpath = os.path.join(root, fname)
                if self._should_ignore(fpath, ignore_patterns):
                    continue
                file_paths.append(fpath)

        registered = 0
        for fp in file_paths:
            fhash = self.compute_file_hash(fp)
            if fhash:
                _update_hash_record(db_client, fp, fhash)
                registered += 1

        return {"files_scanned": len(file_paths), "registered": registered}

    # =========================================================================
    # .xoderignore support
    # =========================================================================

    def _load_ignore_patterns(self, workspace_dir: str) -> List[str]:
        ignore_file = os.path.join(workspace_dir, XODERIGNORE)
        patterns: List[str] = [
            os.path.join(XODER_HIDDEN_DIR, "*"),
            "__pycache__/**",
            "*.pyc",
        ]
        if os.path.isfile(ignore_file):
            try:
                with open(ignore_file, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        stripped = line.strip()
                        if stripped and not stripped.startswith("#"):
                            patterns.append(stripped)
            except (IOError, OSError):
                pass
        return patterns

    @staticmethod
    def _should_ignore(file_path: str, patterns: List[str]) -> bool:
        normalized = file_path.replace("\\", "/")
        for pattern in patterns:
            pattern_norm = pattern.replace("\\", "/")
            regex = re.compile(
                "^" + re.escape(pattern_norm).replace(r"\*", ".*").replace(r"\?", ".") + "$"
            )
            if regex.match(normalized) or regex.match(Path(normalized).name):
                return True
        return False


# =============================================================================
# Helpers
# =============================================================================

def _update_hash_record(db_client: XoderDBClient, file_path: str, file_hash: str):
    file_size = os.path.getsize(file_path) if os.path.isfile(file_path) else 0
    db_client.upsert_hash(file_path, file_hash, file_size)


def _collect_source_files(workspace_dir: str, ignore_patterns: List[str]) -> List[str]:
    files: List[str] = []
    for root, dirs, fnames in os.walk(workspace_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in fnames:
            fpath = os.path.join(root, fname)
            if HashTracker._should_ignore(fpath, ignore_patterns):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext in CODE_EXTENSIONS:
                files.append(fpath)
    return files


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Hash Tracker – register, diff, watch, and propagate",
    )
    parser.add_argument("--mode", required=True,
                        choices=["register", "diff", "watch", "propagate"],
                        help="Operation mode")
    parser.add_argument("--workspace", default=os.getcwd(),
                        help="Workspace directory (default: cwd)")
    parser.add_argument("--db", required=True,
                        help="Path to Xoder SQLite database")
    parser.add_argument("--changed", default=None,
                        help="Comma-separated list of changed files (for propagate mode)")
    parser.add_argument("--output", default=None,
                        help="Write JSON output to file instead of stdout")

    args = parser.parse_args()
    db = XoderDBClient(args.db)
    tracker = HashTracker()

    if args.mode == "register":
        result = tracker.register_all_files(args.workspace, db)
        output = result

    elif args.mode == "diff":
        ignore_pats = tracker._load_ignore_patterns(args.workspace)
        all_files = _collect_source_files(args.workspace, ignore_pats)
        status_map = tracker.scan_and_compare(args.workspace, db, all_files)
        changed = [fp for fp, s in status_map.items() if s != "unchanged"]
        output = {"total_files": len(all_files), "changes": len(changed), "status": status_map}

    elif args.mode == "watch":
        annotations = tracker.watch_wiki_dir(args.workspace, db)
        output = {"annotations": annotations, "count": len(annotations)}

    elif args.mode == "propagate":
        if not args.changed:
            print("Error: --changed required for propagate mode", file=sys.stderr)
            sys.exit(1)
        changed_list = [f.strip() for f in args.changed.split(",") if f.strip()]
        result = tracker.graph_propagate(args.workspace, db, changed_list)
        output = result

    json_str = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(json_str)
        print(f"Output written to {args.output}")
    else:
        print(json_str)

    db.close()


if __name__ == "__main__":
    main()
