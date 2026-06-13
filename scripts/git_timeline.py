"""
Git history semantic washing, churn hotspot analysis, NLP classification,
and ADR reverse-engineering.

Modes:
  wash  – Basic commit washing (noise filter + signal rank + time decay)
  full  – Full analysis: wash + co-change + churn + NLP classify + ADR
  churn – Churn hotspot analysis only
  adr   – ADR reverse engineering from a commits JSON file
"""

import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import argparse
import collections
import json
import logging
import math
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from config import (
    GIT_NOISE_PATTERNS,
    GIT_SIGNAL_PATTERNS,
    TIME_DECAY_HALF_LIFE_DAYS,
    TIME_DECAY_LAMBDA,
    HASH_ALGORITHM,
    ERROR_CODE_MAP,
)

logger = logging.getLogger(__name__)


def _find_git_root(start_path: str) -> Optional[str]:
    path = os.path.abspath(start_path)
    for _ in range(10):
        if os.path.isdir(os.path.join(path, '.git')):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return None

# =============================================================================
# ADR Reverse Prompt Matrix
# =============================================================================

ADR_Reverse_Prompt_Matrix = """
你是资深架构师。基于以下清洗后的 Git 提交记录与共变文件图谱，
请逆向推导出架构决策记录 (ADR)。

对每一个架构相关的提交，需要提取：
1. context_problem（背景问题）：系统当时面临什么约束或瓶颈？
2. decision_compromise（决策与妥协）：团队做了什么取舍？为什么选择该方案？
3. architecture_constraint（架构约束）：此决策带来了什么长期约束？

提交记录：
{given_commits_json}

共变文件图谱：
{co_change_json}

输出格式（JSON 数组）：
[
  {{
    "commit_hash": "...",
    "title": "...",
    "context_problem": "...",
    "decision_compromise": "...",
    "architecture_constraint": "...",
    "weight": 1.0
  }}
]

请仅返回 JSON 数组，不要包含额外解释文字。
"""

# =============================================================================
# NLP classification helpers (module-level)
# =============================================================================

_SEVERITY_CRITICAL_WORDS = {
    "critical", "crash", "deadlock", "overflow", "oom",
    "data loss", "security", "exploit",
}
_SEVERITY_HIGH_WORDS = {"fix", "bug", "issue"}
_SEVERITY_MEDIUM_WORDS = {"improve", "tweak"}

_TYPE_PATTERNS = [
    (re.compile(r"(?i)\bfix\s*[:(]"), "FIX"),
    (re.compile(r"(?i)\bbug\s*[:(]"), "FIX"),
    (re.compile(r"(?i)\bhotfix\s*[:(]"), "FIX"),
    (re.compile(r"(?i)\bfeat\s*[:(]"), "FEATURE"),
    (re.compile(r"(?i)\bfeature\s*[:(]"), "FEATURE"),
    (re.compile(r"(?i)\brefactor\s*[:(]"), "REFACTOR"),
    (re.compile(r"(?i)\bperf\s*[:(]"), "PERFORMANCE"),
    (re.compile(r"(?i)\bperformance\s*[:(]"), "PERFORMANCE"),
    (re.compile(r"(?i)\bsecurity\s*[:(]"), "SECURITY"),
    (re.compile(r"(?i)\bbreaking\b"), "BREAKING"),
    (re.compile(r"(?i)\brevert\s*[:(]"), "REVERT"),
]


def _classify_commit_type(message: str) -> str:
    for pattern, ctype in _TYPE_PATTERNS:
        if pattern.search(message):
            return ctype
    return "OTHER"


def _detect_severity(message: str) -> str:
    lower = message.lower()
    for w in _SEVERITY_CRITICAL_WORDS:
        if w in lower:
            return "CRITICAL"
    for w in _SEVERITY_HIGH_WORDS:
        if w in lower:
            return "HIGH"
    for w in _SEVERITY_MEDIUM_WORDS:
        if w in lower:
            return "MEDIUM"
    return "LOW"


# =============================================================================
# GitTimelineWasher
# =============================================================================

class GitTimelineWasher:

    def __init__(self):
        self._noise_patterns = [re.compile(p) for p in GIT_NOISE_PATTERNS]
        self._signal_patterns = [re.compile(p) for p in GIT_SIGNAL_PATTERNS]

    # =========================================================================
    # wash_commits
    # =========================================================================

    def wash_commits(
        self,
        repo_path: str,
        file_paths: Optional[List[str]] = None,
        max_commits: int = 500,
    ) -> List[Dict]:
        if not os.path.isdir(os.path.join(repo_path, ".git")):
            logger.error("Not a git repository: %s", repo_path)
            raise ValueError(f"Not a git repository: {repo_path}")

        raw = self._run_git_log(repo_path, file_paths, max_commits)
        if not raw:
            return []

        cleaned: List[Dict] = []
        for commit in raw:
            if not self._is_noise(commit["message"]):
                commit["is_signal"] = self._is_signal(commit["message"])
                commit["weight"] = self.compute_time_decay_weight(commit["date"])
                cleaned.append(commit)

        cleaned.sort(key=lambda c: (c.get("is_signal", False), c.get("weight", 0)), reverse=True)
        return cleaned

    def _run_git_log(
        self, repo_path: str, file_paths: Optional[List[str]], max_commits: int
    ) -> List[Dict]:
        fmt = "%H%x00%an%x00%ae%x00%aI%x00%s"
        cmd = ["git", "log", f"--format={fmt}", f"--max-count={max_commits}", "--no-merges"]
        if file_paths:
            cmd.append("--")
            cmd.extend(file_paths)

        try:
            result = subprocess.run(
                cmd, cwd=repo_path, capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                logger.warning("git log failed: %s", result.stderr)
                return []
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning("git log error: %s", exc)
            return []

        commits: List[Dict] = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\x00")
            if len(parts) >= 5:
                commits.append({
                    "hash": parts[0],
                    "author": parts[1],
                    "email": parts[2],
                    "date": parts[3],
                    "message": parts[4],
                })
        return commits

    def _is_noise(self, message: str) -> bool:
        for pat in self._noise_patterns:
            if pat.search(message):
                return True
        return False

    def _is_signal(self, message: str) -> bool:
        for pat in self._signal_patterns:
            if pat.search(message):
                return True
        return False

    # =========================================================================
    # compute_time_decay_weight
    # =========================================================================

    def compute_time_decay_weight(
        self, commit_date_str: str, current_date: Optional[str] = None
    ) -> float:
        try:
            commit_dt = datetime.fromisoformat(commit_date_str).replace(tzinfo=None)
        except (ValueError, TypeError):
            return 0.1

        if current_date:
            try:
                now = datetime.fromisoformat(current_date).replace(tzinfo=None)
            except (ValueError, TypeError):
                now = datetime.now().replace(tzinfo=None)
        else:
            now = datetime.now().replace(tzinfo=None)

        age_days = (now - commit_dt).total_seconds() / 86400.0
        if age_days < 0:
            age_days = 0

        weight = math.exp(-TIME_DECAY_LAMBDA * age_days)
        return round(weight, 6)

    # =========================================================================
    # detect_co_change
    # =========================================================================

    def detect_co_change(
        self, repo_path: str, min_co_occurrences: int = 3
    ) -> Dict[str, List[str]]:
        if not os.path.isdir(os.path.join(repo_path, ".git")):
            return {}

        commits_with_files = self._run_git_log_with_files(repo_path)
        if not commits_with_files:
            return {}

        pair_counts: Dict[Tuple[str, str], int] = collections.defaultdict(int)
        for files_in_commit in commits_with_files:
            sorted_files = sorted(set(files_in_commit))
            for i in range(len(sorted_files)):
                for j in range(i + 1, len(sorted_files)):
                    pair = (sorted_files[i], sorted_files[j])
                    pair_counts[pair] += 1

        adjacency: Dict[str, List[str]] = collections.defaultdict(list)
        for (a, b), count in pair_counts.items():
            if count >= min_co_occurrences:
                adjacency[a].append(b)
                adjacency[b].append(a)

        return dict(adjacency)

    def _run_git_log_with_files(self, repo_path: str) -> List[List[str]]:
        try:
            result = subprocess.run(
                ["git", "log", "--name-only", "--format=commit %H",
                 "--max-count=500", "--no-merges"],
                cwd=repo_path, capture_output=True, text=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

        commits: List[List[str]] = []
        current_files: List[str] = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line.startswith("commit "):
                if current_files:
                    commits.append(current_files)
                current_files = []
            elif line:
                current_files.append(line)
        if current_files:
            commits.append(current_files)
        return commits

    # =========================================================================
    # analyze_churn_hotspots (NEW – P1)
    # =========================================================================

    def analyze_churn_hotspots(
        self, repo_path: str, max_commits: int = 1000
    ) -> List[Dict]:
        commits = self._run_git_log_with_numstat(repo_path, max_commits)
        if not commits:
            return []

        file_stats: Dict[str, Dict] = {}
        for c in commits:
            author = c.get("author", "unknown")
            for fstat in c.get("files", []):
                fp = fstat["path"]
                if fp not in file_stats:
                    file_stats[fp] = {
                        "file": fp,
                        "commit_count": 0,
                        "lines_added": 0,
                        "lines_deleted": 0,
                        "first_commit_date": c["date"],
                        "recent_commits": 0,
                        "recent_added": 0,
                        "recent_deleted": 0,
                        "authors": collections.Counter(),
                    }
                fs = file_stats[fp]
                fs["commit_count"] += 1
                fs["lines_added"] += fstat.get("added", 0)
                fs["lines_deleted"] += fstat.get("deleted", 0)
                fs["authors"][author] += 1

                # track date range: earliest commit
                if c["date"] < fs["first_commit_date"]:
                    fs["first_commit_date"] = c["date"]

                # recent churn (last 90 days)
                try:
                    cd = datetime.fromisoformat(c["date"]).replace(tzinfo=None)
                except (ValueError, TypeError):
                    cd = datetime.min
                cutoff = datetime.now().replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)
                if cd >= cutoff - __import__("datetime").timedelta(days=90):
                    fs["recent_commits"] += 1
                    fs["recent_added"] += fstat.get("added", 0)
                    fs["recent_deleted"] += fstat.get("deleted", 0)

        now = datetime.now()
        for fp, fs in file_stats.items():
            try:
                first_dt = datetime.fromisoformat(fs["first_commit_date"]).replace(tzinfo=None)
            except (ValueError, TypeError):
                first_dt = now
            days = max((now - first_dt).total_seconds() / 86400.0, 1.0)
            churn = fs["lines_added"] + fs["lines_deleted"]
            fs["churn_score"] = round(churn * fs["commit_count"] / days, 4)
            r_churn = fs["recent_added"] + fs["recent_deleted"]
            fs["recent_churn"] = round(r_churn * fs["recent_commits"] / max(days, 90.0), 4)
            # top authors
            fs["top_authors"] = [a for a, _ in fs["authors"].most_common(3)]
            del fs["authors"]
            del fs["first_commit_date"]
            del fs["recent_commits"]
            del fs["recent_added"]
            del fs["recent_deleted"]

        if not file_stats:
            return []

        # percentile rank by churn_score
        scores = sorted(fs["churn_score"] for fs in file_stats.values())
        n = len(scores)
        for fs in file_stats.values():
            idx = sum(1 for s in scores if s < fs["churn_score"])
            percentile = (idx / n) * 100 if n > 1 else 50
            if percentile >= 90:
                fs["rank"] = "HOTSPOT"
            elif percentile >= 70:
                fs["rank"] = "WARM"
            else:
                fs["rank"] = "COLD"

        return sorted(file_stats.values(), key=lambda x: x["churn_score"], reverse=True)

    def _run_git_log_with_numstat(
        self, repo_path: str, max_commits: int = 1000
    ) -> List[Dict]:
        """Return commits with --numstat file-level stats."""
        fmt = "%H %ai %an"
        try:
            result = subprocess.run(
                ["git", "log", f"--format={fmt}", f"--max-count={max_commits}",
                 "--numstat", "--no-merges"],
                cwd=repo_path, capture_output=True, text=True, timeout=60,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

        commits: List[Dict] = []
        current: Optional[Dict] = None
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                if current is not None:
                    commits.append(current)
                current = None
                continue
            if current is None:
                # new commit header: hash date time tz author
                parts = line.split(" ", 3)
                if len(parts) >= 3:
                    current = {
                        "hash": parts[0],
                        "date": f"{parts[1]} {parts[2]}",
                        "author": parts[3] if len(parts) > 3 else "unknown",
                        "files": [],
                    }
            else:
                # numstat line: added\deleted\tpath  or  -\t-\tpath (binary)
                toks = line.split("\t")
                if len(toks) >= 3:
                    try:
                        added = int(toks[0]) if toks[0] != "-" else 0
                        deleted = int(toks[1]) if toks[1] != "-" else 0
                    except ValueError:
                        added = deleted = 0
                    current["files"].append({
                        "added": added, "deleted": deleted, "path": toks[2],
                    })
        if current is not None:
            commits.append(current)
        return commits

    # =========================================================================
    # classify_commits_nlp (NEW – P1)
    # =========================================================================

    def classify_commits_nlp(
        self, repo_path: str, max_commits: int = 500
    ) -> List[Dict]:
        commits = self._run_git_log_with_numstat(repo_path, max_commits)
        if not commits:
            return []

        classified: List[Dict] = []
        for c in commits:
            msg = c.get("message", c.get("subject", ""))
            if self._is_noise(c.get("message", c.get("subject", ""))):
                continue
            classified.append({
                "hash": c["hash"],
                "author": c["author"],
                "date": c["date"],
                "type": _classify_commit_type(c.get("message", c.get("subject", ""))),
                "severity": _detect_severity(c.get("message", c.get("subject", ""))),
                "message": c.get("message", c.get("subject", "")),
                "changed_files": [f["path"] for f in c.get("files", [])],
            })
        return classified

    # =========================================================================
    # reverse_engineer_adr
    # =========================================================================

    def reverse_engineer_adr(
        self, cleaned_commits: List[Dict], co_change_files: Dict[str, List[str]]
    ) -> List[Dict]:
        if not cleaned_commits:
            return []

        adr_records: List[Dict] = []
        seen_titles: Set[str] = set()
        relevant = [c for c in cleaned_commits if c.get("is_signal")]
        if not relevant:
            relevant = cleaned_commits[:20]

        for commit in relevant:
            title = self._derive_adr_title(commit["message"])[0:120]
            if title in seen_titles:
                continue
            seen_titles.add(title)

            co_files = co_change_files.get(commit.get("hash", ""), [])

            adr_records.append({
                "commit_hash": commit["hash"],
                "title": title,
                "context_problem": self._infer_context(commit["message"], co_files),
                "decision_compromise": commit["message"][0:200],
                "architecture_constraint": self._infer_constraint(commit["message"]),
                "co_change_files": co_files,
                "weight": commit.get("weight", 1.0),
            })

        return adr_records

    def _derive_adr_title(self, message: str) -> str:
        first_line = message.split("\n")[0].strip()
        patterns = [
            (r'^feat\s*[:(]\s*(.*)', r'\1'),
            (r'^fix\s*[:(]\s*(.*)', r'\1'),
            (r'^refactor\s*[:(]\s*(.*)', r'\1'),
            (r'^perf\s*[:(]\s*(.*)', r'\1'),
        ]
        for pat, repl in patterns:
            m = re.match(pat, first_line, re.IGNORECASE)
            if m:
                prefix = pat.split(r'\s')[0].lstrip("^")
                return f"[{prefix.upper()}] {m.group(1)}"
        return first_line

    def _infer_context(self, message: str, co_files: List[str]) -> str:
        parts: List[str] = []
        if co_files:
            parts.append(f"涉及文件: {', '.join(co_files[:5])}")
        if "break" in message.lower() or "breaking" in message.lower():
            parts.append("存在不兼容变更")
        if "deprecat" in message.lower():
            parts.append("标记了废弃接口")
        if not parts:
            parts.append("常规迭代与维护")
        return "; ".join(parts)

    def _infer_constraint(self, message: str) -> str:
        constraints: List[str] = []
        if "compat" in message.lower() or "backward" in message.lower():
            constraints.append("需保持向后兼容")
        if "api" in message.lower():
            constraints.append("影响外部 API 契约")
        if "schema" in message.lower() or "migrat" in message.lower():
            constraints.append("涉及数据模型迁移")
        if "security" in message.lower():
            constraints.append("安全加固要求，需持续审计")
        if "perf" in message.lower() or "performance" in message.lower():
            constraints.append("性能敏感路径，变更需基准测试验证")
        return "; ".join(constraints) if constraints else "无明确长期约束记录"


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Git Timeline – wash, churn, classify, and ADR reverse-engineer",
    )
    parser.add_argument("--mode", required=True,
                        choices=["wash", "full", "churn", "adr"],
                        help="Operation mode")
    parser.add_argument("--repo", default=os.getcwd(),
                        help="Path to git repository (default: cwd)")
    parser.add_argument("--files", default=None,
                        help="Comma-separated file paths for wash mode")
    parser.add_argument("--max", type=int, default=500,
                        help="Max commits to process (default: 500)")
    parser.add_argument("--commits-file", default=None,
                        help="JSON file with commits for ADR mode")
    parser.add_argument("--output", default=None,
                        help="Write JSON output to file instead of stdout")

    args = parser.parse_args()
    repo_path = _find_git_root(args.repo or '.') or (args.repo or '.')
    if not _find_git_root(repo_path):
        logger.warning("No .git found in %s or parents; git features skipped", repo_path)
    washer = GitTimelineWasher()

    if args.mode == "wash":
        file_list = None
        if args.files:
            file_list = [f.strip() for f in args.files.split(",") if f.strip()]
        washed = washer.wash_commits(repo_path, file_list, args.max)
        output = {"washed_commits": washed}

    elif args.mode == "churn":
        hotspots = washer.analyze_churn_hotspots(repo_path, args.max)
        output = {"hotspots": hotspots}

    elif args.mode == "adr":
        if not args.commits_file or not os.path.isfile(args.commits_file):
            print("Error: --commits-file required for ADR mode", file=sys.stderr)
            sys.exit(1)
        with open(args.commits_file, "r", encoding="utf-8") as f:
            commits_data = json.load(f)
        if isinstance(commits_data, dict) and "washed_commits" in commits_data:
            commits_data = commits_data["washed_commits"]
        if not isinstance(commits_data, list):
            print("Error: commits JSON must be a list or {washed_commits: [...]}", file=sys.stderr)
            sys.exit(1)
        co_change = washer.detect_co_change(repo_path, 3)
        adr_records = washer.reverse_engineer_adr(commits_data, co_change)
        output = {"adr_records": adr_records}

    elif args.mode == "full":
        file_list = None
        if args.files:
            file_list = [f.strip() for f in args.files.split(",") if f.strip()]
        washed = washer.wash_commits(repo_path, file_list, args.max)
        co_change = washer.detect_co_change(repo_path, 3)
        hotspots = washer.analyze_churn_hotspots(repo_path, args.max)
        classified = washer.classify_commits_nlp(repo_path, args.max)
        adr_records = washer.reverse_engineer_adr(washed, co_change)
        output = {
            "washed_commits": washed,
            "co_change_pairs": co_change,
            "hotspots": hotspots,
            "classified_commits": classified,
            "adr_records": adr_records,
        }

    json_str = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(json_str)
        print(f"Output written to {args.output}")
    else:
        print(json_str)


if __name__ == "__main__":
    main()
