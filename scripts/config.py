"""
Xoder 全局离线大模型路由、哈希忽略规则、大仓熔断阀值配置中心
"""

import os
import json
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional

# =============================================================================
# Xoder 命名空间常量
# =============================================================================
PRODUCT_NAME = "Xoder"
XODER_HIDDEN_DIR = ".xoder"
REPOWIKI_DIR = "repowiki"
XODER_LOCAL_STAGE_DIR = ".xoder-local"

# =============================================================================
# 错误码映射 (工业级异常状态码)
# =============================================================================
ERROR_CODE_MAP: Dict[int, str] = {
    0: "NO_ERROR",
    10001: "REPO_BREAKER_TRUNCATED",
    20002: "MERMAID_SYNTAX_BROKEN",
    30003: "OFFLINE_LLM_TIMEOUT_OOM",
    40004: "CODEGRAPH_TOPOLOGY_BROKEN",
    50005: "TREE_SITTER_PARSE_FAILURE",
    60006: "ORM_PENETRATION_FAILURE",
    70007: "GIT_TIMELINE_EMPTY",
    80008: "HASH_TRACKER_SYNC_CONFLICT",
    90009: "TOKEN_GATEWAY_CUTOFF",
}

ERROR_CODE_NAMES: Dict[int, str] = {v: k for k, v in ERROR_CODE_MAP.items()}

# =============================================================================
# 任务状态枚举
# =============================================================================
class TaskStatus:
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

ACTIVE_STATUSES = {TaskStatus.PENDING, TaskStatus.PROCESSING}
TERMINAL_STATUSES = {TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLED}

# =============================================================================
# 大仓熔断阀值
# =============================================================================
REPO_BREAKER_MAX_FILES = 10_000
REPO_BREAKER_MAX_MODULES = 200
REPO_BREAKER_MAX_FILE_SIZE_MB = 10

# =============================================================================
# Agent 博弈配置
# =============================================================================
MAX_RETRY_LIMIT = 3
MAX_VALIDATOR_LOOP = 3
SNAPSHOT_MAX_CHAT_TURNS = 50

# =============================================================================
# 哈希算法配置
# =============================================================================
HASH_ALGORITHM = "sha256"
HASH_CHUNK_SIZE = 65536  # 64KB chunks

# =============================================================================
# Git 语义洗涤配置
# =============================================================================
GIT_NOISE_PATTERNS = [
    r"(?i)\bformat\b.*\bcode\b",
    r"(?i)\bfix\b.*\btyp[oe]s?\b",
    r"(?i)\bmerge\b.*\bbranch\b",
    r"(?i)\bbump\b.*\bversion\b",
    r"(?i)\bupdate\b.*\bdeps?\b",
    r"(?i)\bupdate\b.*\bdependencies?\b",
    r"(?i)\bchore\b.*\bdeps?\b",
    r"(?i)\bminor\b.*\b(?:fix|tweak|adjust)\b",
    r"(?i)\bwhite\s*space\b",
    r"(?i)\bclean\s*up\b",
    r"(?i)\bremove\b.*\bTODO\b",
    r"(?i)\bremove\b.*\bFIXME\b",
    r"(?i)\bupdate\b.*\blicense\b",
    r"(?i)\bupdate\b.*\b\.gitignore\b",
    r"(?i)\bwip\b",
]

GIT_SIGNAL_PATTERNS = [
    r"(?i)\bfeat\s*[:(]",
    r"(?i)\bfeat\b",
    r"(?i)\bfix\s*[:(]",
    r"(?i)\bfix\b",
    r"(?i)\brefactor\s*[:(]",
    r"(?i)\brefactor\b",
    r"(?i)\bperf\s*[:(]",
    r"(?i)\bperf\b",
    r"(?i)\brevert\s*[:(]",
    r"(?i)\bsecurity\s*[:(]",
    r"(?i)\bbreaking\b",
    r"(?i)\bdeprecat(?:e|ed|ion)\b",
    r"(?i)\bJIRA[-\s]?\d+\b",
    r"(?i)\bBUG[-\s]?\d+\b",
    r"(?i)\bBUGZILLA[-\s]?\d+\b",
    r"(?i)\bGH[-\s]?#\d+\b",
    r"(?i)\bPR[-\s]?#\d+\b",
    r"(?i)\bissue[-\s]?#\d+\b",
    r"(?i)\bresolv(?:e|ed|es)\b.*\b#\d+\b",
]

# =============================================================================
# 时间衰减算法配置
# =============================================================================
TIME_DECAY_HALF_LIFE_DAYS = 180
TIME_DECAY_LAMBDA = 0.693147 / TIME_DECAY_HALF_LIFE_DAYS  # ln(2) / half_life

# =============================================================================
# 默认忽略目录和文件
# =============================================================================
DEFAULT_EXCLUDED_DIRS: List[str] = [
    ".git", ".svn", ".hg", ".bzr",
    "node_modules", "bower_components", "jspm_packages",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", "env", "virtualenv",
    "dist", "build", "out", "target", "bin", "obj",
    ".idea", ".vscode", ".vs", ".eclipse", ".settings",
    ".xoder", ".xoder-local",
    "node_modules", "logs", "log", "tmp", "temp",
]

DEFAULT_EXCLUDED_FILES: List[str] = [
    "*.min.js", "*.min.css", "*.bundle.js", "*.bundle.css",
    "*.map", "*.gz", "*.zip", "*.tar", "*.tgz", "*.rar",
    "*.exe", "*.dll", "*.so", "*.dylib", "*.jar", "*.war",
    "*.pyc", "*.pyd", "*.pyo", "*.class",
    "yarn.lock", "pnpm-lock.yaml", "package-lock.json",
    "poetry.lock", "Pipfile.lock", "Cargo.lock",
    ".DS_Store", "Thumbs.db", "desktop.ini",
    ".env", ".env.*", "*.lock",
]

# =============================================================================
# Mermaid 编译器配置
# =============================================================================
MMDC_PATH_ENV = "MMDC_PATH"
MMDC_DEFAULT_PATH = "mmdc"
MMDC_TIMEOUT_SECONDS = 30
MMDC_OUTPUT_FORMAT = "svg"

# =============================================================================
# Token 网关配置
# =============================================================================
TOKEN_GATEWAY_MAX_HOPS = 2
TOKEN_GATEWAY_MAX_CONTEXT_CHARS = 32000
TOKEN_GATEWAY_TARGET_REDUCTION_RATIO = 0.93

# =============================================================================
# LLM 路由配置
# =============================================================================
LOCAL_LLM_ROUTER: Dict[str, Dict[str, Any]] = {
    "light": {
        "model": "qwen2.5:14b",
        "endpoint": "http://localhost:11434",
        "max_tokens": 8192,
        "description": "小型本地模型，用于哈希对比、正则过滤等轻量任务"
    },
    "heavy": {
        "model": "qwen2.5:70b",
        "endpoint": "http://localhost:11434",
        "max_tokens": 32768,
        "description": "大型本地模型，用于Wiki文档生成、ADR反推等重度任务"
    },
    "local": {
        "model": "qwen2.5:32b",
        "endpoint": "http://localhost:11434",
        "max_tokens": 16384,
        "description": "默认中等级别本地模型"
    },
}

DEFAULT_LLM_TIER = "local"

# =============================================================================
# 支持的文件扩展名
# =============================================================================
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
    ".cpp", ".c", ".h", ".hpp", ".cs", ".swift", ".kt", ".scala",
    ".rb", ".php", ".vue", ".svelte", ".html", ".sql",
}

# Paths to exclude from entry point scanning (static resources, vendored libs)
ENTRY_NOISE_PATHS = {
    "static", "plugins", "fonts", "images", "img", "assets", "public",
    "css", "scss", "sass", "less", "js/lib", "js/vendor", "vendor",
    "node_modules", "bower_components",
}

CONFIG_EXTENSIONS = {
    ".xml", ".yml", ".yaml", ".json", ".toml", ".prisma",
    ".properties", ".ini", ".cfg", ".conf",
}

DOC_EXTENSIONS = {
    ".md", ".rst", ".txt", ".adoc",
}

# =============================================================================
# 辅助函数
# =============================================================================

def get_xoder_dir(workspace_dir: str) -> str:
    return os.path.join(workspace_dir, XODER_HIDDEN_DIR, REPOWIKI_DIR)

def get_stage_dir(workspace_dir: str) -> str:
    return os.path.join(workspace_dir, XODER_LOCAL_STAGE_DIR, "stage")

def get_db_path(workspace_dir: str) -> str:
    return os.path.join(get_xoder_dir(workspace_dir), "wiki_sync_metadata.db")

def get_error_message(error_code: int) -> str:
    return ERROR_CODE_MAP.get(error_code, f"UNKNOWN_ERROR_{error_code}")

def get_error_code(name: str) -> int:
    return ERROR_CODE_NAMES.get(name, 0)

SUPPORTED_LANGUAGES = {
    "zh": {
        "name": "中文",
        "dir": "zh",
    },
    "en": {
        "name": "English",
        "dir": "en",
    },
}

DEFAULT_LANGUAGE = "zh"
