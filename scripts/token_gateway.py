"""
Symbol-to-Token bidirectional mapping gateway with context pruning
and semantic vector indexing for dual-track retrieval.

Modes:
  index  – Build semantic vector index from symbol skeletons
  search – Semantic search over indexed vectors
  (import TokenGateway class for build_index / prune_context programmatic use)
"""

import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import argparse
import collections
import hashlib
import json
import logging
import math
import os
import sqlite3
import struct
import sys
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from config import (
    TOKEN_GATEWAY_MAX_HOPS,
    TOKEN_GATEWAY_MAX_CONTEXT_CHARS,
    TOKEN_GATEWAY_TARGET_REDUCTION_RATIO,
    HASH_ALGORITHM,
    ERROR_CODE_MAP,
)
from db_client import XoderDBClient

logger = logging.getLogger(__name__)

EXTERNAL_LLM_TIMEOUT_OOM = 30003

# DDL for semantic vector storage (created on-demand)
DDL_SEMANTIC_VECTORS = """
CREATE TABLE IF NOT EXISTS Semantic_Vectors (
    node_id       TEXT PRIMARY KEY,
    node_type     TEXT NOT NULL,
    summary_text  TEXT NOT NULL,
    embedding_blob BLOB,
    tfidf_vector  TEXT,
    dim           INTEGER DEFAULT 384
)
"""


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _blob_to_floats(blob: bytes, dim: int) -> List[float]:
    fmt = f"<{dim}f"
    return list(struct.unpack(fmt, blob))


def _floats_to_blob(vec: List[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


# =============================================================================
# TokenGateway
# =============================================================================

class TokenGateway:

    def __init__(self):
        self._symbol_to_cards: Dict[str, List[Dict]] = {}
        self._card_to_symbols: Dict[str, List[str]] = {}
        self._index_built = False

    # =========================================================================
    # build_index (knowledge cards)
    # =========================================================================

    def build_index(
        self, knowledge_cards_path: str, code_graph_db: XoderDBClient
    ) -> None:
        self._symbol_to_cards.clear()
        self._card_to_symbols.clear()

        if not os.path.isdir(knowledge_cards_path):
            logger.warning("Knowledge cards path not found: %s", knowledge_cards_path)
            self._index_built = False
            return

        for root, dirs, files in os.walk(knowledge_cards_path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(root, fname)
                symbol_name = os.path.splitext(fname)[0]
                rel_path = os.path.relpath(fpath, knowledge_cards_path)

                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                except (IOError, OSError):
                    continue

                card_entry = {
                    "path": fpath,
                    "relative_path": rel_path,
                    "symbol": symbol_name,
                    "content": content,
                    "size": len(content),
                    "hash": hashlib.new(HASH_ALGORITHM, content.encode()).hexdigest(),
                }

                if symbol_name not in self._symbol_to_cards:
                    self._symbol_to_cards[symbol_name] = []
                self._symbol_to_cards[symbol_name].append(card_entry)

                if fpath not in self._card_to_symbols:
                    self._card_to_symbols[fpath] = []
                self._card_to_symbols[fpath].append(symbol_name)

        self._index_built = len(self._symbol_to_cards) > 0
        logger.info(
            "TokenGateway index built: %d symbols, %d cards",
            len(self._symbol_to_cards),
            sum(len(v) for v in self._symbol_to_cards.values()),
        )

    # =========================================================================
    # prune_context
    # =========================================================================

    def prune_context(
        self,
        anchor_symbol: str,
        code_graph_db: XoderDBClient,
        max_hops: Optional[int] = None,
        max_chars: Optional[int] = None,
    ) -> str:
        if max_hops is None:
            max_hops = TOKEN_GATEWAY_MAX_HOPS
        if max_chars is None:
            max_chars = TOKEN_GATEWAY_MAX_CONTEXT_CHARS

        if not self._index_built:
            logger.warning("TokenGateway index not built; call build_index() first")
            return ""

        visited: Set[str] = set()
        collected_nodes: List[Tuple[str, str, float]] = []

        queue: deque = deque()
        queue.append((anchor_symbol, 0, 1.0))

        while queue:
            node, hop, weight = queue.popleft()
            if node in visited:
                continue
            if hop > max_hops:
                continue

            visited.add(node)

            card_content = self._lookup_card_content(node)
            if card_content:
                collected_nodes.append((node, card_content, weight))

            if hop < max_hops:
                edges = code_graph_db.query_downstream(node, 1)
                for edge in edges:
                    target = edge.get("target_node", "")
                    if target and target not in visited:
                        edge_weight = edge.get("weight", 1.0)
                        queue.append((target, hop + 1, weight * edge_weight))

        collected_nodes.sort(key=lambda x: x[2], reverse=True)
        pruned = self._pack_context(collected_nodes, max_chars)

        if len(collected_nodes) == 0:
            return ""

        original_chars = sum(len(c) for _, c, _ in collected_nodes)
        reduction = self.compute_reduction_ratio(original_chars, len(pruned))

        if len(pruned) < original_chars:
            logger.info(
                "TokenGateway cutoff: %d → %d chars (%.1f%% reduction)",
                original_chars, len(pruned), reduction * 100,
            )

        return pruned

    def _lookup_card_content(self, symbol: str) -> str:
        if symbol in self._symbol_to_cards:
            cards = self._symbol_to_cards[symbol]
            return "\n\n".join(c["content"] for c in cards)

        for key in self._symbol_to_cards:
            if symbol in key or key in symbol:
                cards = self._symbol_to_cards[key]
                return "\n\n".join(c["content"] for c in cards)

        return ""

    def _pack_context(
        self, nodes: List[Tuple[str, str, float]], max_chars: int
    ) -> str:
        result_parts: List[str] = []
        current_size = 0

        for node_name, content, weight in nodes:
            if current_size >= max_chars:
                break

            header = f"## {node_name} (weight={weight:.3f})\n\n"
            remaining = max_chars - current_size - len(header) - 2

            if remaining <= 0:
                break

            if len(content) <= remaining:
                result_parts.append(header + content)
                current_size += len(header) + len(content)
            else:
                trimmed = content[:remaining - 20] + "\n\n<!-- truncated -->"
                result_parts.append(header + trimmed)
                current_size += len(header) + len(trimmed)
                break

        return "\n\n".join(result_parts)

    # =========================================================================
    # compute_reduction_ratio
    # =========================================================================

    def compute_reduction_ratio(self, original_size: int, pruned_size: int) -> float:
        if original_size <= 0:
            return 0.0
        if pruned_size >= original_size:
            return 0.0
        return round((original_size - pruned_size) / original_size, 6)

    # =========================================================================
    # Utility lookups
    # =========================================================================

    def lookup_symbols_for_card(self, card_path: str) -> List[str]:
        return self._card_to_symbols.get(card_path, [])

    def lookup_cards_for_symbol(self, symbol: str) -> List[Dict]:
        return self._symbol_to_cards.get(symbol, [])

    def is_index_built(self) -> bool:
        return self._index_built

    def get_index_stats(self) -> Dict:
        total_symbols = len(self._symbol_to_cards)
        total_cards = sum(len(v) for v in self._symbol_to_cards.values())
        total_chars = sum(
            len(card["content"])
            for cards in self._symbol_to_cards.values()
            for card in cards
        )
        return {
            "symbols": total_symbols,
            "cards": total_cards,
            "total_content_chars": total_chars,
            "index_built": self._index_built,
        }

    # =========================================================================
    # prune_with_report
    # =========================================================================

    def prune_with_report(
        self,
        anchor_symbol: str,
        code_graph_db: XoderDBClient,
        max_hops: Optional[int] = None,
        max_chars: Optional[int] = None,
    ) -> Dict:
        if not self._index_built:
            return {
                "context": "",
                "report": {"error": "Index not built", "error_code": 90009},
            }

        original_total = sum(
            len(card["content"])
            for cards in self._symbol_to_cards.values()
            for card in cards
        )

        pruned = self.prune_context(
            anchor_symbol, code_graph_db, max_hops, max_chars
        )
        pruned_size = len(pruned)

        reduction = self.compute_reduction_ratio(original_total, pruned_size) if original_total > 0 else 0.0
        target_met = reduction >= TOKEN_GATEWAY_TARGET_REDUCTION_RATIO

        report = {
            "anchor_symbol": anchor_symbol,
            "max_hops": max_hops or TOKEN_GATEWAY_MAX_HOPS,
            "max_chars": max_chars or TOKEN_GATEWAY_MAX_CONTEXT_CHARS,
            "original_total_chars": original_total,
            "pruned_chars": pruned_size,
            "reduction_ratio": reduction,
            "target_reduction": TOKEN_GATEWAY_TARGET_REDUCTION_RATIO,
            "target_met": target_met,
        }

        if not target_met:
            report["warning"] = (
                f"Target reduction {TOKEN_GATEWAY_TARGET_REDUCTION_RATIO:.0%} "
                f"not met; achieved {reduction:.1%}"
            )

        result = {"context": pruned, "report": report}
        if pruned_size == 0 and original_total > 0:
            result["error_code"] = 90009

        return result

    # =========================================================================
    # build_semantic_index (NEW – P2)
    # =========================================================================

    def build_semantic_index(
        self, workspace_dir: str, db_client: XoderDBClient
    ) -> Dict:
        db_client.connect()
        conn = db_client._conn

        # Ensure Semantic_Vectors table exists
        conn.execute(DDL_SEMANTIC_VECTORS)
        conn.commit()

        # Collect symbol skeletons from knowledge cards
        knowledge_dir = os.path.join(workspace_dir, ".xoder", "repowiki")
        symbols: List[Dict] = []

        if os.path.isdir(knowledge_dir):
            for root, dirs, files in os.walk(knowledge_dir):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fname in files:
                    if not fname.endswith(".md"):
                        continue
                    fpath = os.path.join(root, fname)
                    symbol_name = os.path.splitext(fname)[0]
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                            content = fh.read()
                    except (IOError, OSError):
                        continue

                    summary = self._build_symbol_summary(symbol_name, content)
                    symbols.append({
                        "node_id": symbol_name,
                        "node_type": self._infer_node_type(content),
                        "summary_text": summary,
                    })

        if not symbols:
            logger.warning("No symbol skeletons found for semantic indexing")
            return {"indexed": 0, "embedding_method": "none", "dim": 0}

        summaries = [s["summary_text"] for s in symbols]

        # Try sentence-transformers first, fall back to TF-IDF
        embeddings: List[List[float]] = []
        method = "none"
        dim = 384

        # --- attempt sentence-transformers ---
        st_available = False
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("all-MiniLM-L6-v2")
            embeddings = model.encode(summaries, show_progress_bar=False).tolist()
            if embeddings and len(embeddings[0]) > 0:
                dim = len(embeddings[0])
                method = "sentence-transformers"
                st_available = True
                logger.info("Using sentence-transformers (all-MiniLM-L6-v2), dim=%d", dim)
        except Exception as exc:
            logger.info("sentence-transformers unavailable (%s), falling back to TF-IDF", exc)

        # --- TF-IDF fallback ---
        if not st_available:
            try:
                from sklearn.feature_extraction.text import TfidfVectorizer
                from sklearn.metrics.pairwise import cosine_similarity as sk_cosine

                tfidf = TfidfVectorizer(max_features=1000)
                tfidf_matrix = tfidf.fit_transform(summaries)
                # Store TF-IDF as sparse JSON: {"indices": [...], "values": [...], "dim": N}
                method = "tfidf"
                dim = tfidf_matrix.shape[1]
                logger.info("Using TF-IDF fallback, dim=%d", dim)
            except Exception as exc:
                logger.warning("TF-IDF also unavailable (%s); cannot build index", exc)
                return {"indexed": 0, "embedding_method": "none", "dim": 0}

        # Store into DB
        cursor = conn.cursor()
        stored = 0
        for i, sym in enumerate(symbols):
            node_id = sym["node_id"]
            node_type = sym["node_type"]
            summary_text = sym["summary_text"]
            embedding_blob = None
            tfidf_text = None

            if st_available and embeddings:
                emb = embeddings[i]
                embedding_blob = _floats_to_blob(emb)
            elif method == "tfidf":
                row = tfidf_matrix[i]
                coo = row.tocoo()
                tfidf_text = json.dumps({
                    "indices": coo.col.tolist(),
                    "values": coo.data.tolist(),
                    "dim": int(dim),
                })
                embedding_blob = None

            try:
                cursor.execute(
                    "INSERT OR REPLACE INTO Semantic_Vectors "
                    "(node_id, node_type, summary_text, embedding_blob, tfidf_vector, dim) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (node_id, node_type, summary_text, embedding_blob, tfidf_text, dim),
                )
                stored += 1
            except sqlite3.Error as exc:
                logger.error("Failed to insert vector for %s: %s", node_id, exc)

        conn.commit()
        return {"indexed": stored, "embedding_method": method, "dim": dim}

    # =========================================================================
    # semantic_search (NEW – P2)
    # =========================================================================

    def semantic_search(
        self, query: str, db_client: XoderDBClient, top_k: int = 5
    ) -> List[Dict]:
        db_client.connect()
        conn = db_client._conn

        rows = conn.execute(
            "SELECT node_id, node_type, summary_text, embedding_blob, tfidf_vector, dim "
            "FROM Semantic_Vectors"
        ).fetchall()

        if not rows:
            return []

        stored_embeddings: List[Tuple[str, str, str, List[float], str, int]] = []
        has_sentence_transformer = False

        for row in rows:
            node_id, node_type, summary_text, emb_blob, tfidf_vec, dim = row
            if emb_blob and dim:
                stored_embeddings.append(
                    (node_id, node_type, summary_text, _blob_to_floats(emb_blob, dim), dim)
                )
                has_sentence_transformer = True
            elif tfidf_vec and dim:
                stored_embeddings.append(
                    (node_id, node_type, summary_text, None, dim)
                )

        if not stored_embeddings:
            return []

        top_results: List[Tuple[float, str, str, str]] = []

        if has_sentence_transformer:
            try:
                from sentence_transformers import SentenceTransformer
                model = SentenceTransformer("all-MiniLM-L6-v2")
                query_emb = model.encode([query], show_progress_bar=False)[0].tolist()
            except Exception as exc:
                logger.warning("sentence-transformers failed for search: %s", exc)
                return []

            for entry in stored_embeddings:
                if len(entry) == 5 and entry[3] is not None:
                    node_id, node_type, summary_text, emb, dim = entry
                    sim = _cosine_similarity(query_emb, emb)
                    top_results.append((sim, node_id, node_type, summary_text))

        else:
            # TF-IDF fallback search
            try:
                from sklearn.feature_extraction.text import TfidfVectorizer
                from sklearn.metrics.pairwise import cosine_similarity as sk_cosine
            except Exception:
                return []

            # Rebuild TF-IDF matrix from stored vectors
            all_tfidf_data = []
            entry_info: List[Tuple[str, str, str, int]] = []
            for i, entry in enumerate(stored_embeddings):
                if len(entry) == 5 and entry[3] is None:
                    # Need original tfidf_vector text – but we only stored
                    # the blob list in stored_embeddings for TF-IDF case.
                    # Re-read from DB row.
                    pass

            # For TF-IDF fallback, re-read rows and reconstruct sparse vectors
            all_summaries = [r[2] for r in rows]
            all_ids = [r[0] for r in rows]
            all_types = [r[1] for r in rows]

            vectorizer = TfidfVectorizer(max_features=1000)
            try:
                tfidf_matrix = vectorizer.fit_transform(all_summaries)
                query_vec = vectorizer.transform([query])
                similarities = sk_cosine(query_vec, tfidf_matrix).flatten()
            except Exception:
                return []

            for i, sim in enumerate(similarities):
                if i < len(all_ids):
                    top_results.append((float(sim), all_ids[i], all_types[i], all_summaries[i]))

        top_results.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "node_id": nid,
                "node_type": ntype,
                "similarity_score": round(sim, 6),
                "summary_text": summary[:300],
            }
            for sim, nid, ntype, summary in top_results[:top_k]
        ]

    # =========================================================================
    # Symbol summary builder (helper)
    # =========================================================================

    def _build_symbol_summary(self, name: str, content: str) -> str:
        parts: List[str] = [f"Symbol: {name}"]
        extends = ""
        implements = ""
        methods: List[str] = []
        annotations: List[str] = []
        deps: List[str] = []

        for line in content.split("\n"):
            stripped = line.strip()
            low = stripped.lower()
            if low.startswith("extends:") or low.startswith("extends "):
                extends = stripped.split(":", 1)[-1].strip() if ":" in stripped else stripped[8:].strip()
            elif low.startswith("implements:") or low.startswith("implements "):
                implements = stripped.split(":", 1)[-1].strip() if ":" in stripped else stripped[11:].strip()
            elif low.startswith("## ") and ("method" in low or "方法" in low or "function" in low):
                methods.append(stripped[3:].strip())
            elif low.startswith("@") or "annotation" in low:
                annotations.append(stripped)
            elif "dependency" in low or "depends on" in low or "依赖" in low:
                deps.append(stripped)

        if extends:
            parts.append(f"Extends: {extends}")
        if implements:
            parts.append(f"Implements: {implements}")
        if methods:
            parts.append(f"Methods: {', '.join(methods[:20])}")
        if annotations:
            parts.append(f"Annotations: {', '.join(annotations[:10])}")
        if deps:
            parts.append(f"Dependencies: {', '.join(deps[:10])}")
        if not any([extends, implements, methods, annotations, deps]):
            parts.append(content[:500])

        return " ".join(parts)

    @staticmethod
    def _infer_node_type(content: str) -> str:
        low = content[:200].lower()
        if "class " in low or "interface " in low or "enum " in low:
            return "CLASS"
        if "function " in low or "def " in low:
            return "FUNCTION"
        if "module" in low:
            return "MODULE"
        return "SYMBOL"


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Token Gateway – build index, prune context, semantic search",
    )
    parser.add_argument("--mode", required=True,
                        choices=["index", "search"],
                        help="Operation mode")
    parser.add_argument("--workspace", default=os.getcwd(),
                        help="Workspace directory (default: cwd)")
    parser.add_argument("--db", required=True,
                        help="Path to Xoder SQLite database")
    parser.add_argument("--query", default=None,
                        help="Search query (for search mode)")
    parser.add_argument("--top", type=int, default=5,
                        help="Number of top results (for search mode)")
    parser.add_argument("--output", default=None,
                        help="Write JSON output to file instead of stdout")

    args = parser.parse_args()
    db = XoderDBClient(args.db)
    gateway = TokenGateway()

    if args.mode == "index":
        result = gateway.build_semantic_index(args.workspace, db)
        output = result

    elif args.mode == "search":
        if not args.query:
            print("Error: --query required for search mode", file=sys.stderr)
            sys.exit(1)
        results = gateway.semantic_search(args.query, db, args.top)
        output = {"query": args.query, "results": results, "count": len(results)}

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
