#!/usr/bin/env python3
"""Xoder Repo Wiki CLI — 轻量管理工具"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SUPPORTED_LANGUAGES, XODER_HIDDEN_DIR, REPOWIKI_DIR, XODER_LOCAL_STAGE_DIR
from db_client import XoderDBClient


def cmd_init(workspace):
    """创建 .xoder/repowiki/ 目录树并初始化数据库"""
    repowiki = os.path.join(workspace, XODER_HIDDEN_DIR, REPOWIKI_DIR)
    stage = os.path.join(workspace, XODER_LOCAL_STAGE_DIR, "stage")

    for lang in SUPPORTED_LANGUAGES.values():
        base = os.path.join(repowiki, lang["dir"])
        for sub in ("content/modules", "diagrams", "meta"):
            os.makedirs(os.path.join(base, sub), exist_ok=True)
    os.makedirs(stage, exist_ok=True)
    knowledge_dir = os.path.join(workspace, XODER_HIDDEN_DIR, "knowledge")
    os.makedirs(knowledge_dir, exist_ok=True)

    db_path = os.path.join(repowiki, "wiki_sync_metadata.db")
    db = XoderDBClient(db_path)
    db.initialize_database()
    db.close()

    print(f"[OK] Xoder initialized at {repowiki}")
    print(f"    Database: {db_path}")
    print(f"    Stage: {stage}")


def cmd_status(workspace):
    """打印状态看板"""
    db_path = os.path.join(workspace, XODER_HIDDEN_DIR, REPOWIKI_DIR, "wiki_sync_metadata.db")
    if not os.path.exists(db_path):
        print("No database found. Run 'xoder init' first.")
        return

    db = XoderDBClient(db_path)
    db.connect()
    tasks = db.get_all_tasks()
    stats = db.get_database_stats()
    db.close()

    print("=" * 60)
    print("  Xoder Wiki Status Board")
    print("=" * 60)

    status_icons = {"SUCCESS": "G", "PROCESSING": "Y", "FAILED": "R", "PENDING": "?", "CANCELLED": "X"}

    if tasks:
        for t in tasks:
            icon = status_icons.get(t.get("status", "?"), "?")
            print(f"  [{icon}] {t['module_name']:30s}  {t['status']:12s}  retries={t['retry_count']}")
    else:
        print("  No tasks yet. Run the main skill to generate wiki.")

    print("-" * 60)
    for table, count in stats.items():
        if count > 0:
            print(f"  {table}: {count} records")
    print("=" * 60)


def cmd_clean(workspace):
    """清空缓存和数据库"""
    db_path = os.path.join(workspace, XODER_HIDDEN_DIR, REPOWIKI_DIR, "wiki_sync_metadata.db")
    stage_dir = os.path.join(workspace, XODER_LOCAL_STAGE_DIR, "stage")

    if os.path.exists(db_path):
        os.remove(db_path)
        print(f"[OK] Removed {db_path}")
    if os.path.exists(stage_dir):
        import shutil
        shutil.rmtree(stage_dir)
        print(f"[OK] Removed {stage_dir}")


def main():
    parser = argparse.ArgumentParser(description="Xoder Repo Wiki CLI")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("init", help="Initialize .xoder/repowiki/ directory and database")
    sub.add_parser("status", help="Print status board")
    sub.add_parser("clean", help="Clear database and stage cache")

    args = parser.parse_args()
    workspace = os.getcwd()

    if args.command == "init":
        cmd_init(workspace)
    elif args.command == "status":
        cmd_status(workspace)
    elif args.command == "clean":
        cmd_clean(workspace)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
