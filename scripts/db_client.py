"""
Xoder 本地持久化 SQLite 状态机客户端

完整表结构定义：
1. Task_Queue - 断点续传任务队列
2. Session_Snapshot - Agent 多轮会话快照
3. Hash_Fingerprint - 增量哈希指纹追踪
4. Reverse_Sync_Metadata - 反向同步元数据注入
5. CodeGraph_Topology - 物理调用关系图拓扑缓存
6. ADR_Records - 架构决策记录存储

错误码映射 (工业级异常状态码):
  0:      NO_ERROR (正常)
  10001:  REPO_BREAKER_TRUNCATED (大仓熔断截断)
  20002:  MERMAID_SYNTAX_BROKEN (Mermaid语法破碎)
  30003:  OFFLINE_LLM_TIMEOUT_OOM (离线大模型响应超时/OOM)
  40004:  CODEGRAPH_TOPOLOGY_BROKEN (CodeGraph拓扑断链)
  50005:  TREE_SITTER_PARSE_FAILURE (Tree-sitter解析失败)
  60006:  ORM_PENETRATION_FAILURE (ORM穿透失败)
  70007:  GIT_TIMELINE_EMPTY (Git历史为空)
  80008:  HASH_TRACKER_SYNC_CONFLICT (哈希追踪同步冲突)
  90009:  TOKEN_GATEWAY_CUTOFF (Token网关裁剪截断)
"""

import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import os
import sqlite3
import json
import logging
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger(__name__)

# =============================================================================
# 完整 DDL 表结构定义
# =============================================================================

DDL_CREATE_TASK_QUEUE = '''
CREATE TABLE IF NOT EXISTS Task_Queue (
    task_id               TEXT PRIMARY KEY,
    module_name           TEXT NOT NULL,
    file_paths            TEXT NOT NULL,
    file_hash             TEXT NOT NULL DEFAULT '',
    status                TEXT NOT NULL DEFAULT 'PENDING'
                          CHECK(status IN ('PENDING', 'PROCESSING', 'SUCCESS', 'FAILED', 'CANCELLED')),
    retry_count           INTEGER NOT NULL DEFAULT 0,
    error_code            INTEGER NOT NULL DEFAULT 0
                          CHECK(error_code IN (0, 10001, 20002, 30003, 40004, 50005, 60006, 70007, 80008, 90009)),
    error_log             TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at          TEXT
)
'''

DDL_CREATE_SESSION_SNAPSHOT = '''
CREATE TABLE IF NOT EXISTS Session_Snapshot (
    task_id               TEXT PRIMARY KEY,
    chat_history_json     TEXT NOT NULL DEFAULT '[]',
    agent_role            TEXT NOT NULL DEFAULT 'unknown',
    snapshot_at           TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (task_id) REFERENCES Task_Queue(task_id) ON DELETE CASCADE
)
'''

DDL_CREATE_HASH_FINGERPRINT = '''
CREATE TABLE IF NOT EXISTS Hash_Fingerprint (
    file_path             TEXT PRIMARY KEY,
    sha256_hash           TEXT NOT NULL,
    file_size_bytes       INTEGER NOT NULL DEFAULT 0,
    last_modified         TEXT NOT NULL DEFAULT (datetime('now')),
    sync_status           TEXT NOT NULL DEFAULT 'UP_TO_DATE'
                          CHECK(sync_status IN ('UP_TO_DATE', 'OUTDATED', 'CONFLICT')),
    affected_task_ids     TEXT DEFAULT '[]'
)
'''

DDL_CREATE_REVERSE_SYNC_META = '''
CREATE TABLE IF NOT EXISTS Reverse_Sync_Metadata (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    target_file_path      TEXT NOT NULL,
    module_name           TEXT NOT NULL,
    human_annotation_json TEXT NOT NULL DEFAULT '{}',
    diff_snapshot         TEXT,
    injected_at           TEXT NOT NULL DEFAULT (datetime('now')),
    consumed_by_task_id   TEXT
)
'''

DDL_CREATE_CODEGRAPH_TOPOLOGY = '''
CREATE TABLE IF NOT EXISTS CodeGraph_Topology (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    source_node           TEXT NOT NULL,
    target_node           TEXT NOT NULL,
    edge_type             TEXT NOT NULL
                          CHECK(edge_type IN ('CALLS', 'INHERITS', 'IMPLEMENTS', 'DEPENDS_ON', 'ANNOTATED_BY', 'IMPORTS', 'CONTAINS')),
    source_file           TEXT NOT NULL,
    target_file           TEXT NOT NULL,
    weight                REAL NOT NULL DEFAULT 1.0,
    metadata_json         TEXT DEFAULT '{}',
    UNIQUE(source_node, target_node, edge_type)
)
'''

DDL_CREATE_ADR_RECORDS = '''
CREATE TABLE IF NOT EXISTS ADR_Records (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    commit_hash           TEXT NOT NULL,
    module_name           TEXT NOT NULL,
    title                 TEXT NOT NULL,
    context_problem       TEXT NOT NULL DEFAULT '',
    decision_compromise   TEXT NOT NULL DEFAULT '',
    architecture_constraint TEXT NOT NULL DEFAULT '',
    co_change_files       TEXT DEFAULT '[]',
    weight                REAL NOT NULL DEFAULT 1.0,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(commit_hash, module_name)
)
'''

DDL_CREATE_MODULE_INDEX = '''
CREATE TABLE IF NOT EXISTS Module_Index (
    module_name           TEXT PRIMARY KEY,
    module_type           TEXT NOT NULL DEFAULT 'unknown',
    source_dir            TEXT NOT NULL,
    file_count            INTEGER NOT NULL DEFAULT 0,
    dependencies_json     TEXT DEFAULT '[]',
    status                TEXT NOT NULL DEFAULT 'PENDING',
    wiki_generated_at     TEXT,
    last_hash             TEXT
)
'''

DDL_CREATE_INDEXES = [
    'CREATE INDEX IF NOT EXISTS idx_task_queue_status ON Task_Queue(status)',
    'CREATE INDEX IF NOT EXISTS idx_task_queue_module ON Task_Queue(module_name)',
    'CREATE INDEX IF NOT EXISTS idx_task_queue_error ON Task_Queue(error_code)',
    'CREATE INDEX IF NOT EXISTS idx_hash_fingerprint_sync ON Hash_Fingerprint(sync_status)',
    'CREATE INDEX IF NOT EXISTS idx_cg_topology_source ON CodeGraph_Topology(source_node)',
    'CREATE INDEX IF NOT EXISTS idx_cg_topology_target ON CodeGraph_Topology(target_node)',
    'CREATE INDEX IF NOT EXISTS idx_cg_topology_edge ON CodeGraph_Topology(edge_type)',
    'CREATE INDEX IF NOT EXISTS idx_adr_module ON ADR_Records(module_name)',
    'CREATE INDEX IF NOT EXISTS idx_reverse_sync_module ON Reverse_Sync_Metadata(module_name)',
    'CREATE INDEX IF NOT EXISTS idx_session_task ON Session_Snapshot(task_id)',
]

# =============================================================================
# 数据库客户端
# =============================================================================

class XoderDBClient:
    """Xoder 本地 SQLite 数据库客户端"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    # =========================================================================
    # 连接管理
    # =========================================================================

    def connect(self):
        if self._conn is None:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def connection(self):
        self.connect()
        try:
            yield self._conn
        finally:
            pass  # Keep connection alive for reuse

    @contextmanager
    def transaction(self):
        self.connect()
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # =========================================================================
    # 数据库初始化
    # =========================================================================

    def initialize_database(self):
        """创建所有核心表与索引"""
        self.connect()
        cursor = self._conn.cursor()

        cursor.execute(DDL_CREATE_TASK_QUEUE)
        cursor.execute(DDL_CREATE_SESSION_SNAPSHOT)
        cursor.execute(DDL_CREATE_HASH_FINGERPRINT)
        cursor.execute(DDL_CREATE_REVERSE_SYNC_META)
        cursor.execute(DDL_CREATE_CODEGRAPH_TOPOLOGY)
        cursor.execute(DDL_CREATE_ADR_RECORDS)
        cursor.execute(DDL_CREATE_MODULE_INDEX)

        for index_sql in DDL_CREATE_INDEXES:
            cursor.execute(index_sql)

        self._conn.commit()
        logger.info("Xoder database initialized successfully at %s", self.db_path)

    # =========================================================================
    # Task_Queue 操作
    # =========================================================================

    def upsert_task(self, task_id: str, module_name: str, file_paths: str,
                    file_hash: str, status: str = "PENDING") -> int:
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO Task_Queue (task_id, module_name, file_paths, file_hash, status, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(task_id) DO UPDATE SET
                    file_paths = excluded.file_paths,
                    file_hash = excluded.file_hash,
                    status = excluded.status,
                    retry_count = CASE WHEN excluded.status = 'PROCESSING'
                                  THEN retry_count ELSE retry_count END,
                    updated_at = datetime('now')
            ''', (task_id, module_name, file_paths, file_hash, status))
            return cursor.rowcount

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        self.connect()
        cursor = self._conn.cursor()
        cursor.execute('SELECT * FROM Task_Queue WHERE task_id = ?', (task_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_task_status(self, task_id: str, status: str,
                           error_code: int = 0, error_log: str = None):
        with self.transaction() as conn:
            cursor = conn.cursor()
            completed = datetime.now().isoformat() if status in ("SUCCESS", "FAILED") else None
            cursor.execute('''
                UPDATE Task_Queue
                SET status = ?, error_code = ?, error_log = ?,
                    updated_at = datetime('now'),
                    completed_at = COALESCE(?, completed_at)
                WHERE task_id = ?
            ''', (status, error_code, error_log, completed, task_id))

    def increment_retry(self, task_id: str) -> int:
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE Task_Queue
                SET retry_count = retry_count + 1, updated_at = datetime('now')
                WHERE task_id = ?
            ''', (task_id,))
            cursor.execute('SELECT retry_count FROM Task_Queue WHERE task_id = ?', (task_id,))
            row = cursor.fetchone()
            return row[0] if row else 0

    def get_tasks_by_status(self, status: str) -> List[Dict[str, Any]]:
        self.connect()
        cursor = self._conn.cursor()
        cursor.execute('SELECT * FROM Task_Queue WHERE status = ?', (status,))
        return [dict(row) for row in cursor.fetchall()]

    def get_all_tasks(self) -> List[Dict[str, Any]]:
        self.connect()
        cursor = self._conn.cursor()
        cursor.execute('SELECT * FROM Task_Queue ORDER BY updated_at DESC')
        return [dict(row) for row in cursor.fetchall()]

    def clean_completed_sessions(self):
        with self.transaction() as conn:
            conn.execute('''
                DELETE FROM Session_Snapshot
                WHERE task_id IN (SELECT task_id FROM Task_Queue WHERE status = 'SUCCESS')
            ''')

    # =========================================================================
    # Session_Snapshot 操作
    # =========================================================================

    def save_session(self, task_id: str, chat_history: List[Dict],
                     agent_role: str = "unknown"):
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO Session_Snapshot (task_id, chat_history_json, agent_role, snapshot_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(task_id) DO UPDATE SET
                    chat_history_json = excluded.chat_history_json,
                    agent_role = excluded.agent_role,
                    snapshot_at = datetime('now')
            ''', (task_id, json.dumps(chat_history, ensure_ascii=False), agent_role))

    def load_session(self, task_id: str) -> Optional[List[Dict]]:
        self.connect()
        cursor = self._conn.cursor()
        cursor.execute(
            'SELECT chat_history_json FROM Session_Snapshot WHERE task_id = ?',
            (task_id,)
        )
        row = cursor.fetchone()
        if row and row[0]:
            return json.loads(row[0])
        return None

    def delete_session(self, task_id: str):
        with self.transaction() as conn:
            conn.execute('DELETE FROM Session_Snapshot WHERE task_id = ?', (task_id,))

    # =========================================================================
    # Hash_Fingerprint 操作
    # =========================================================================

    def upsert_hash(self, file_path: str, sha256_hash: str, file_size_bytes: int):
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO Hash_Fingerprint (file_path, sha256_hash, file_size_bytes, last_modified, sync_status)
                VALUES (?, ?, ?, datetime('now'), 'UP_TO_DATE')
                ON CONFLICT(file_path) DO UPDATE SET
                    sha256_hash = excluded.sha256_hash,
                    file_size_bytes = excluded.file_size_bytes,
                    last_modified = datetime('now'),
                    sync_status = 'UP_TO_DATE'
            ''', (file_path, sha256_hash, file_size_bytes))

    def get_hash(self, file_path: str) -> Optional[Dict[str, Any]]:
        self.connect()
        cursor = self._conn.cursor()
        cursor.execute(
            'SELECT * FROM Hash_Fingerprint WHERE file_path = ?', (file_path,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def mark_hash_outdated(self, file_path: str):
        with self.transaction() as conn:
            conn.execute('''
                UPDATE Hash_Fingerprint SET sync_status = 'OUTDATED'
                WHERE file_path = ?
            ''', (file_path,))

    def get_outdated_hashes(self) -> List[Dict[str, Any]]:
        self.connect()
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM Hash_Fingerprint WHERE sync_status = 'OUTDATED'")
        return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # Reverse_Sync_Metadata 操作
    # =========================================================================

    def inject_human_annotation(self, target_file_path: str, module_name: str,
                                 annotation: Dict, diff_snapshot: str = None):
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO Reverse_Sync_Metadata
                    (target_file_path, module_name, human_annotation_json, diff_snapshot)
                VALUES (?, ?, ?, ?)
            ''', (target_file_path, module_name,
                  json.dumps(annotation, ensure_ascii=False), diff_snapshot))

    def get_human_annotations(self, module_name: str) -> List[Dict[str, Any]]:
        self.connect()
        cursor = self._conn.cursor()
        cursor.execute('''
            SELECT * FROM Reverse_Sync_Metadata
            WHERE module_name = ? AND consumed_by_task_id IS NULL
            ORDER BY injected_at DESC
        ''', (module_name,))
        return [dict(row) for row in cursor.fetchall()]

    def mark_annotation_consumed(self, annotation_id: int, task_id: str):
        with self.transaction() as conn:
            conn.execute('''
                UPDATE Reverse_Sync_Metadata
                SET consumed_by_task_id = ?
                WHERE id = ?
            ''', (task_id, annotation_id))

    # =========================================================================
    # CodeGraph_Topology 操作
    # =========================================================================

    def insert_graph_edge(self, source_node: str, target_node: str,
                          edge_type: str, source_file: str, target_file: str,
                          weight: float = 1.0, metadata: Dict = None):
        with self.transaction() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO CodeGraph_Topology
                    (source_node, target_node, edge_type, source_file, target_file,
                     weight, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (source_node, target_node, edge_type, source_file, target_file,
                  weight, json.dumps(metadata or {}, ensure_ascii=False)))

    def query_downstream(self, node: str, max_hops: int = 2) -> List[Dict[str, Any]]:
        self.connect()
        results = []
        visited = set()
        current_level = {node}

        for hop in range(max_hops):
            next_level = set()
            for current in current_level:
                cursor = self._conn.cursor()
                cursor.execute('''
                    SELECT * FROM CodeGraph_Topology WHERE source_node = ?
                ''', (current,))
                for row in cursor.fetchall():
                    edge = dict(row)
                    if edge['target_node'] not in visited:
                        results.append(edge)
                        next_level.add(edge['target_node'])
            visited.update(current_level)
            current_level = next_level - visited

        return results

    def query_upstream(self, node: str, max_hops: int = 2) -> List[Dict[str, Any]]:
        self.connect()
        results = []
        visited = set()
        current_level = {node}

        for hop in range(max_hops):
            next_level = set()
            for current in current_level:
                cursor = self._conn.cursor()
                cursor.execute('''
                    SELECT * FROM CodeGraph_Topology WHERE target_node = ?
                ''', (current,))
                for row in cursor.fetchall():
                    edge = dict(row)
                    if edge['source_node'] not in visited:
                        results.append(edge)
                        next_level.add(edge['source_node'])
            visited.update(current_level)
            current_level = next_level - visited

        return results

    def clear_topology(self):
        with self.transaction() as conn:
            conn.execute('DELETE FROM CodeGraph_Topology')

    # =========================================================================
    # ADR_Records 操作
    # =========================================================================

    def insert_adr(self, commit_hash: str, module_name: str, title: str,
                   context_problem: str = "", decision_compromise: str = "",
                   architecture_constraint: str = "",
                   co_change_files: List[str] = None, weight: float = 1.0):
        with self.transaction() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO ADR_Records
                    (commit_hash, module_name, title, context_problem,
                     decision_compromise, architecture_constraint,
                     co_change_files, weight)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (commit_hash, module_name, title, context_problem,
                  decision_compromise, architecture_constraint,
                  json.dumps(co_change_files or [], ensure_ascii=False), weight))

    def get_adrs_for_module(self, module_name: str) -> List[Dict[str, Any]]:
        self.connect()
        cursor = self._conn.cursor()
        cursor.execute('''
            SELECT * FROM ADR_Records
            WHERE module_name = ?
            ORDER BY weight DESC, created_at DESC
        ''', (module_name,))
        return [dict(row) for row in cursor.fetchall()]

    def get_all_adrs(self) -> List[Dict[str, Any]]:
        self.connect()
        cursor = self._conn.cursor()
        cursor.execute('SELECT * FROM ADR_Records ORDER BY weight DESC')
        return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # Module_Index 操作
    # =========================================================================

    def upsert_module(self, module_name: str, module_type: str,
                      source_dir: str, file_count: int = 0,
                      dependencies: List[str] = None):
        with self.transaction() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO Module_Index
                    (module_name, module_type, source_dir, file_count,
                     dependencies_json, status)
                VALUES (?, ?, ?, ?, ?, 'PENDING')
            ''', (module_name, module_type, source_dir, file_count,
                  json.dumps(dependencies or [], ensure_ascii=False)))

    def get_module(self, module_name: str) -> Optional[Dict[str, Any]]:
        self.connect()
        cursor = self._conn.cursor()
        cursor.execute('SELECT * FROM Module_Index WHERE module_name = ?', (module_name,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_all_modules(self) -> List[Dict[str, Any]]:
        self.connect()
        cursor = self._conn.cursor()
        cursor.execute('SELECT * FROM Module_Index ORDER BY module_name')
        return [dict(row) for row in cursor.fetchall()]

    def update_module_wiki_status(self, module_name: str, status: str, last_hash: str = None):
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE Module_Index
                SET status = ?, wiki_generated_at = datetime('now'),
                    last_hash = COALESCE(?, last_hash)
                WHERE module_name = ?
            ''', (status, last_hash, module_name))

    # =========================================================================
    # 清理操作
    # =========================================================================

    def reset_all_task_states(self):
        with self.transaction() as conn:
            conn.execute("UPDATE Task_Queue SET status = 'PENDING', error_code = 0, error_log = NULL, retry_count = 0, completed_at = NULL")

    def clear_all_data(self):
        self.connect()
        with self.transaction() as conn:
            tables = ['Task_Queue', 'Session_Snapshot', 'Hash_Fingerprint',
                      'Reverse_Sync_Metadata', 'CodeGraph_Topology',
                      'ADR_Records', 'Module_Index']
            for table in tables:
                conn.execute(f'DELETE FROM {table}')
        logger.warning("All Xoder database data cleared")

    def get_database_stats(self) -> Dict[str, int]:
        self.connect()
        stats = {}
        tables = ['Task_Queue', 'Session_Snapshot', 'Hash_Fingerprint',
                  'Reverse_Sync_Metadata', 'CodeGraph_Topology',
                  'ADR_Records', 'Module_Index']
        for table in tables:
            cursor = self._conn.cursor()
            cursor.execute(f'SELECT COUNT(*) FROM {table}')
            stats[table] = cursor.fetchone()[0]
        return stats
