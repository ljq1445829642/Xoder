"""
Xoder Git 操作器 —— 基于 Git 的仓库操作接口

提供提交日志查询、文件历史追踪、差异对比、分支管理以及 wiki 文件
的暂存-提交-推送自愈发布能力。所有操作通过 subprocess 调用 git CLI，
不依赖任何 Git Python 绑定库。

错误码引用:
  70007: GIT_TIMELINE_EMPTY - Git 历史为空
"""

import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import os
import re
import subprocess
import logging
from typing import List, Optional

from config import get_error_message

logger = logging.getLogger(__name__)

_ERROR_GIT_EMPTY = 70007
_ERROR_GIT_NOT_FOUND_MSG = "git executable not found or not on PATH"
_ERROR_NOT_REPO_MSG = "target path is not a git repository"


def _run_git(repo_path: str, cmd: List[str], timeout: int = 60,
             check: bool = False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git"] + cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=repo_path,
            check=check,
        )
    except FileNotFoundError:
        raise RuntimeError(_ERROR_GIT_NOT_FOUND_MSG)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git command timed out after {timeout}s: {' '.join(cmd)}")


def _is_git_repo(repo_path: str) -> bool:
    try:
        proc = _run_git(repo_path, ["rev-parse", "--git-dir"])
        return proc.returncode == 0
    except RuntimeError:
        return False


def _parse_changed_files(raw: str) -> List[str]:
    return [ln.strip() for ln in raw.splitlines() if ln.strip()]


class GitOperator:
    """基于 Git 命令行的仓库操作器，用于 wiki 自愈发布工作流"""

    # ------------------------------------------------------------------
    # 提交日志查询
    # ------------------------------------------------------------------

    @staticmethod
    def get_commit_log(repo_path: str, file_paths: List[str] = None,
                       max_count: int = 500) -> str:
        """获取仓库提交日志原始输出。

        Args:
            repo_path: 仓库根路径。
            file_paths: 可选，限定到特定文件的提交历史。
            max_count: 最大返回提交数。
        """
        if not _is_git_repo(repo_path):
            return _ERROR_NOT_REPO_MSG

        cmd = ["log", "--oneline", f"--max-count={max_count}"]
        if file_paths:
            cmd.append("--")
            cmd.extend(file_paths)

        try:
            proc = _run_git(repo_path, cmd)
            output = proc.stdout.strip()
            if not output:
                return get_error_message(_ERROR_GIT_EMPTY)
            return output
        except RuntimeError as exc:
            logger.error("get_commit_log failed: %s", exc)
            return str(exc)

    # ------------------------------------------------------------------
    # 文件历史
    # ------------------------------------------------------------------

    @staticmethod
    def get_file_history(repo_path: str, file_path: str,
                         max_count: int = 100) -> str:
        """追踪特定文件的提交历史（含重命名追踪）。

        Args:
            repo_path: 仓库根路径。
            file_path: 相对于仓库根的文件路径。
            max_count: 最大返回提交数。
        """
        if not _is_git_repo(repo_path):
            return _ERROR_NOT_REPO_MSG

        cmd = [
            "log", "--follow", "--oneline",
            f"--max-count={max_count}",
            "--", file_path,
        ]

        try:
            proc = _run_git(repo_path, cmd)
            output = proc.stdout.strip()
            if not output:
                return get_error_message(_ERROR_GIT_EMPTY)
            return output
        except RuntimeError as exc:
            logger.error("get_file_history failed: %s", exc)
            return str(exc)

    # ------------------------------------------------------------------
    # 提交差异
    # ------------------------------------------------------------------

    @staticmethod
    def get_diff(repo_path: str, commit_hash: str) -> str:
        """获取指定提交的完整 diff。

        Args:
            repo_path: 仓库根路径。
            commit_hash: 提交哈希值。
        """
        if not _is_git_repo(repo_path):
            return _ERROR_NOT_REPO_MSG

        cmd = ["show", "--pretty=medium", "--stat", commit_hash]

        try:
            proc = _run_git(repo_path, cmd)
            output = proc.stdout.strip()
            if not output:
                return f"no diff output for commit {commit_hash}"
            if proc.stderr.strip():
                return f"error: {proc.stderr.strip()}"
            return output
        except RuntimeError as exc:
            logger.error("get_diff failed: %s", exc)
            return str(exc)

    # ------------------------------------------------------------------
    # 变更文件列表
    # ------------------------------------------------------------------

    @staticmethod
    def get_changed_files(repo_path: str, commit_hash: str) -> List[str]:
        """列出指定提交中变更的所有文件。

        Args:
            repo_path: 仓库根路径。
            commit_hash: 提交哈希值。
        """
        if not _is_git_repo(repo_path):
            return []

        cmd = ["diff-tree", "--no-commit-id", "--name-only", "-r", commit_hash]

        try:
            proc = _run_git(repo_path, cmd)
            files = _parse_changed_files(proc.stdout)
            if proc.stderr.strip():
                logger.warning("get_changed_files stderr: %s", proc.stderr.strip())
            return files
        except RuntimeError as exc:
            logger.error("get_changed_files failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # 分支操作
    # ------------------------------------------------------------------

    @staticmethod
    def get_current_branch(repo_path: str) -> str:
        """获取当前检出的分支名称。

        Args:
            repo_path: 仓库根路径。
        """
        if not _is_git_repo(repo_path):
            return ""

        cmd = ["rev-parse", "--abbrev-ref", "HEAD"]

        try:
            proc = _run_git(repo_path, cmd)
            branch = proc.stdout.strip()
            if proc.returncode != 0:
                logger.error("get_current_branch failed: %s", proc.stderr.strip())
                return ""
            return branch
        except RuntimeError as exc:
            logger.error("get_current_branch failed: %s", exc)
            return ""

    @staticmethod
    def checkout_branch(repo_path: str, branch: str) -> bool:
        """检出一个已存在的分支。

        Args:
            repo_path: 仓库根路径。
            branch: 目标分支名称。
        """
        if not _is_git_repo(repo_path):
            logger.error(_ERROR_NOT_REPO_MSG)
            return False

        cmd = ["checkout", branch]

        try:
            proc = _run_git(repo_path, cmd)
            if proc.returncode != 0:
                logger.error("checkout_branch failed: %s", proc.stderr.strip())
                return False
            logger.info("Checked out branch '%s' in %s", branch, repo_path)
            return True
        except RuntimeError as exc:
            logger.error("checkout_branch failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # 暂存-提交-推送 (Wiki 自愈发布)
    # ------------------------------------------------------------------

    @staticmethod
    def commit_and_push(repo_path: str, message: str,
                        files: List[str]) -> bool:
        """暂存文件、提交并推送到远程仓库。

        操作流程:
          1. git add <files>
          2. git commit -m <message>
          3. git push

        Args:
            repo_path: 仓库根路径。
            message: 提交信息。
            files: 待暂存的文件路径列表（相对于 repo_path）。

        Returns:
            bool - 全部成功返回 True，任一环节失败返回 False。
        """
        if not _is_git_repo(repo_path):
            logger.error(_ERROR_NOT_REPO_MSG)
            return False

        if not files:
            logger.warning("commit_and_push called with empty file list")
            return False

        existing_files = []
        for f in files:
            full = os.path.join(repo_path, f)
            if os.path.isfile(full):
                existing_files.append(f)
            else:
                logger.warning("Skipping non-existent file: %s", f)

        if not existing_files:
            logger.warning("No existing files to commit")
            return False

        try:
            add_proc = _run_git(repo_path, ["add"] + existing_files)
            if add_proc.returncode != 0:
                logger.error("git add failed: %s", add_proc.stderr.strip())
                return False
        except RuntimeError as exc:
            logger.error("git add failed: %s", exc)
            return False

        try:
            commit_proc = _run_git(repo_path, ["commit", "-m", message])
            if commit_proc.returncode != 0:
                stderr = commit_proc.stderr.strip()
                logger.error("git commit failed: %s", stderr)
                return False
            logger.info("Committed %d files: %s", len(existing_files), message)
        except RuntimeError as exc:
            logger.error("git commit failed: %s", exc)
            return False

        try:
            push_proc = _run_git(repo_path, ["push"], timeout=120)
            if push_proc.returncode != 0:
                stderr = push_proc.stderr.strip()
                logger.warning("git push returned non-zero: %s", stderr)
                return False
            logger.info("Pushed to remote successfully")
        except RuntimeError as exc:
            logger.error("git push failed: %s", exc)
            return False

        return True
