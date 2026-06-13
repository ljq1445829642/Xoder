"""
Xoder 本地离线 Mermaid-CLI 编译器网关

负责 mermaid 代码块的提取、语法校验与 SVG 编译。
通过调用本地安装的 mmdc (mermaid-cli) 完成渲染，不依赖任何外部服务。

错误码:
  20002: MERMAID_SYNTAX_BROKEN - Mermaid 语法破碎或编译失败
"""

import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import os
import re
import subprocess
import tempfile
import logging
from typing import List, Tuple

from config import (
    MMDC_PATH_ENV,
    MMDC_DEFAULT_PATH,
    MMDC_TIMEOUT_SECONDS,
    MMDC_OUTPUT_FORMAT,
    get_error_message,
)

logger = logging.getLogger(__name__)

_ERROR_MERMAID_SYNTAX = 20002

_MERMAID_BLOCK_RE = re.compile(
    r"```mermaid\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE
)

_GRAPH_DIRECTIONS = {"TD", "TB", "BT", "RL", "LR"}
_EDGE_SYMBOLS = {"-->", "--->", "---", "==>", "==", "-.-", "-.->", "===>"}
_SUBGRAPH_RE = re.compile(r"\bsubgraph\s+\w+", re.IGNORECASE)
_END_RE = re.compile(r"\bend\b", re.IGNORECASE)
_NODE_RE = re.compile(r"^\s*[A-Za-z_]\w*(?:\[|\(|\{|>|\/|\[\[)", re.MULTILINE)


def _find_mmdc_executable() -> str:
    env_path = os.environ.get(MMDC_PATH_ENV)
    if env_path and os.path.isfile(env_path):
        return env_path
    if env_path:
        return env_path
    return MMDC_DEFAULT_PATH


class MermaidCompiler:
    """本地离线 Mermaid-CLI 编译器网关"""

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    @staticmethod
    def extract_mermaid_blocks(markdown_content: str) -> List[str]:
        """从 Markdown 文本中提取所有 ```mermaid 代码块"""
        if not markdown_content:
            return []
        blocks = []
        for match in _MERMAID_BLOCK_RE.finditer(markdown_content):
            body = match.group(1).strip()
            if body:
                blocks.append(body)
        return blocks

    @staticmethod
    def validate_mermaid_syntax(mermaid_source: str) -> Tuple[bool, str]:
        """快速语法结构检查，返回 (is_valid, error_message)。

        检查项:
          - 必须包含有效的 graph 方向声明 (graph TD/LR 等)
          - 节点定义语法完整性
          - 边连接符合法性
          - subgraph/end 配对闭合
        """
        src = mermaid_source.strip()
        if not src:
            return False, f"{get_error_message(_ERROR_MERMAID_SYNTAX)}: empty mermaid source"

        lines = [ln.strip() for ln in src.splitlines() if ln.strip()]
        if not lines:
            return False, f"{get_error_message(_ERROR_MERMAID_SYNTAX)}: no content lines"

        first_line = lines[0].lower()
        # All valid Mermaid diagram types
        _VALID_DIAGRAM_TYPES = (
            "graph ", "flowchart ", "sequencediagram", "erdiagram", "classdiagram",
            "gantt", "pie ", "statediagram", "gitgraph", "mindmap", "timeline",
            "journey", "quadrantchart", "xychart", "block", "sankey", "c4context",
            "c4container", "c4component", "c4dynamic", "c4deployment"
        )
        has_graph_decl = any(first_line.startswith(t) for t in _VALID_DIAGRAM_TYPES)

        if not has_graph_decl:
            return False, (
                f"{get_error_message(_ERROR_MERMAID_SYNTAX)}: "
                f"unrecognized diagram type '{lines[0].split()[0] if lines[0].split() else lines[0]}'. "
                f"Valid types: graph TD/LR, flowchart, sequenceDiagram, erDiagram, classDiagram, "
                f"gantt, pie, stateDiagram, gitGraph, mindmap, timeline, journey, etc."
            )

        non_flowchart = {"sequencediagram", "erdiagram", "classdiagram", "gantt", "pie",
                        "statediagram", "gitgraph", "mindmap", "timeline", "journey",
                        "quadrantchart", "xychart", "block", "sankey", "c4"}
        if any(first_line.startswith(t) for t in non_flowchart):
            return True, ""

        subgraph_depth = 0
        has_nodes = False
        has_edges = False

        for line in lines:
            lower = line.lower()

            if "graph " in lower or "flowchart " in lower or "sequenceDiagram" in lower or \
               "classDiagram" in lower or "gantt" in lower or "pie" in lower or \
               "erDiagram" in lower or "stateDiagram" in lower:
                continue

            if _SUBGRAPH_RE.search(line):
                subgraph_depth += 1
                continue
            if _END_RE.match(line) and subgraph_depth > 0:
                subgraph_depth -= 1
                continue

            if _NODE_RE.search(line):
                has_nodes = True

            if any(sep in line for sep in _EDGE_SYMBOLS):
                has_edges = True

        if subgraph_depth != 0:
            return False, (
                f"{get_error_message(_ERROR_MERMAID_SYNTAX)}: "
                f"unbalanced subgraph/end pairing (depth={subgraph_depth})"
            )

        if not has_nodes and not has_edges:
            return False, (
                f"{get_error_message(_ERROR_MERMAID_SYNTAX)}: "
                "no valid node definitions or edge connections detected"
            )

        return True, ""

    @staticmethod
    def compile(mermaid_source: str, output_dir: str) -> Tuple[bool, str]:
        """编译 mermaid 源码为 SVG 文件。

        Args:
            mermaid_source: mermaid 语法的源码文本。
            output_dir: 输出目录，SVG 文件将写入此目录。

        Returns:
            (success, error_message) - success 为 True 表示编译成功。
        """
        src = mermaid_source.strip()
        if not src:
            return False, f"{get_error_message(_ERROR_MERMAID_SYNTAX)}: empty mermaid source"

        is_valid, err = MermaidCompiler.validate_mermaid_syntax(src)
        if not is_valid:
            logger.warning("Mermaid pre-validation failed: %s", err)
            return False, err

        mmdc_path = _find_mmdc_executable()
        if mmdc_path == MMDC_DEFAULT_PATH and not _mmdc_available(mmdc_path):
            logger.warning(
                "mmdc not found at '%s'. Set env %s or install mermaid-cli (npm i -g @mermaid-js/mermaid-cli).",
                mmdc_path, MMDC_PATH_ENV,
            )
            return False, (
                f"{get_error_message(_ERROR_MERMAID_SYNTAX)}: "
                f"mmdc executable not found ('{mmdc_path}'). "
                f"Install via: npm install -g @mermaid-js/mermaid-cli"
            )

        os.makedirs(output_dir, exist_ok=True)

        fd, temp_mmd = tempfile.mkstemp(suffix=".mmd", prefix="xoder_mermaid_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(src)

            import hashlib
            name_hash = hashlib.md5(src.encode("utf-8")).hexdigest()[:12]
            output_file = os.path.join(output_dir, f"diagram_{name_hash}.{MMDC_OUTPUT_FORMAT}")

            cmd = [
                mmdc_path,
                "-i", temp_mmd,
                "-o", output_file,
                "-b", "transparent",
            ]

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=MMDC_TIMEOUT_SECONDS,
            )

            if proc.returncode != 0:
                stderr_text = proc.stderr.strip() or "unknown rendering error"
                logger.error("mmdc compilation failed (exit=%d): %s", proc.returncode, stderr_text)
                return False, (
                    f"{get_error_message(_ERROR_MERMAID_SYNTAX)}: "
                    f"mmdc exited {proc.returncode}: {stderr_text}"
                )

            if not os.path.isfile(output_file) or os.path.getsize(output_file) == 0:
                return False, (
                    f"{get_error_message(_ERROR_MERMAID_SYNTAX)}: "
                    "mmdc completed but output file is missing or empty"
                )

            logger.info("Mermaid diagram compiled: %s", output_file)
            return True, output_file

        except subprocess.TimeoutExpired:
            logger.error("mmdc compilation timed out after %ds", MMDC_TIMEOUT_SECONDS)
            return False, (
                f"{get_error_message(_ERROR_MERMAID_SYNTAX)}: "
                f"compilation timed out after {MMDC_TIMEOUT_SECONDS}s"
            )
        except FileNotFoundError:
            logger.error("mmdc executable not found: %s", mmdc_path)
            return False, (
                f"{get_error_message(_ERROR_MERMAID_SYNTAX)}: "
                f"mmdc command not found: '{mmdc_path}'"
            )
        except Exception as exc:
            logger.exception("Unexpected mmdc compilation error")
            return False, (
                f"{get_error_message(_ERROR_MERMAID_SYNTAX)}: "
                f"unexpected error: {exc}"
            )
        finally:
            _safe_remove(temp_mmd)

    @staticmethod
    def validate_all(stage_dir: str) -> str:
        """Validate all .mmd files and mermaid blocks in .md files in a directory.
        Called by main skill: mc.validate_all('.xoder-local/stage/')
        
        Returns: JSON string with validation results.
        """
        import json as _json
        from pathlib import Path as _Path
        results = {"passed": [], "failed": [], "errors": []}
        stage = _Path(stage_dir)
        if not stage.exists():
            return _json.dumps({"error": f"Directory not found: {stage_dir}"})
        
        # Check .mmd files
        for mmd_file in stage.glob("*.mmd"):
            try:
                content = mmd_file.read_text(encoding='utf-8')
                ok, err = MermaidCompiler.validate_mermaid_syntax(content)
                if ok:
                    results["passed"].append(str(mmd_file.name))
                else:
                    results["failed"].append({"file": str(mmd_file.name), "error": err})
                    results["errors"].append(err)
            except Exception as e:
                results["failed"].append({"file": str(mmd_file.name), "error": str(e)})
                results["errors"].append(str(e))
        
        # Check mermaid blocks in .md files
        for md_file in stage.glob("*.md"):
            try:
                content = md_file.read_text(encoding='utf-8')
                blocks = MermaidCompiler.extract_mermaid_blocks(content)
                for i, block in enumerate(blocks):
                    ok, err = MermaidCompiler.validate_mermaid_syntax(block)
                    if not ok:
                        results["failed"].append({"file": str(md_file.name), "block": i+1, "error": err})
                        results["errors"].append(err)
            except Exception as e:
                results["failed"].append({"file": str(md_file.name), "error": str(e)})
                results["errors"].append(str(e))
        
        results["summary"] = f"{len(results['passed'])} passed, {len(results['failed'])} failed"
        return _json.dumps(results, indent=2, ensure_ascii=False)


# ------------------------------------------------------------------
# 内部辅助
# ------------------------------------------------------------------

def _mmdc_available(mmdc_path: str) -> bool:
    try:
        proc = subprocess.run(
            [mmdc_path, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _safe_remove(path: str):
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass
