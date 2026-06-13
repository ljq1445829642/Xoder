import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
"""
ORM framework static configuration penetration.

Scans project directories for ORM configurations and extracts a unified
data topology: tables, fields, primary/foreign keys, and entity relationships
across MyBatis, Prisma, Hibernate/JPA, and SQLAlchemy.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

from config import ERROR_CODE_MAP

logger = logging.getLogger(__name__)

# =============================================================================
# Regex patterns for each ORM framework
# =============================================================================

JPA_ENTITY_PATTERN = re.compile(
    r'@Entity\s*(?:\([^)]*\))?\s*(?:@Table\s*\(\s*(?:name\s*=\s*)"([^"]*)"\s*\))?'
    r'\s*(?:public\s+)?class\s+(\w+)',
    re.DOTALL,
)

JPA_COLUMN_PATTERN = re.compile(
    r'@(?:Id|Column|GeneratedValue|JoinColumn|ManyToOne|OneToMany|ManyToMany|OneToOne)'
    r'(?:\([^)]*\))?\s*'
    r'(?:private|protected|public)\s+(\w+(?:<[^>]+>)?)\s+(\w+)\s*;',
    re.DOTALL,
)

JPA_ENTITY_LOOSE = re.compile(
    r'@(?:Entity|Table|MappedSuperclass)\s*(?:\([^)]*\))?'
    r'(?:\s*@\w+\s*(?:\([^)]*\))?)*'  # allow 0-N other annotations
    r'\s*'
    r'(?:(?:public|private|protected)\s+)?(?:abstract\s+)?'
    r'(?:class|interface)\s+(\w+)',
    re.MULTILINE | re.DOTALL,
)

JPA_RELATION_PATTERN = re.compile(
    r'@(OneToMany|ManyToOne|ManyToMany|OneToOne)'
    r'(?:\([^)]*(?:mappedBy\s*=\s*"(\w+)"[^)]*)?\))?',
    re.DOTALL,
)

SQLALCHEMY_MODEL_PATTERN = re.compile(
    r'class\s+(\w+)\s*\(\s*(?:db\.Model|Base)',
    re.DOTALL,
)

SQLALCHEMY_COLUMN_PATTERN = re.compile(
    r'(\w+)\s*=\s*db\.Column\s*\(\s*'
    r'(?:db\.)?(\w+(?:\(\d+\))?)[,\s)]',
    re.DOTALL,
)

SQLALCHEMY_FK_PATTERN = re.compile(
    r'db\.ForeignKey\s*\(\s*[\'"]([^\'"]+)[\'"]',
    re.DOTALL,
)

SQLALCHEMY_RELATION_PATTERN = re.compile(
    r'(\w+)\s*=\s*db\.relationship\s*\(\s*[\'"]([^\'"]*)[\'"]',
    re.DOTALL,
)

PRISMA_MODEL_PATTERN = re.compile(
    r'model\s+(\w+)\s*\{([^}]*)\}',
    re.DOTALL,
)

PRISMA_FIELD_PATTERN = re.compile(
    r'(\w+)\s+(\w+(?:\[\])?)\s*(@id\b)?\s*(@default[^@\n]*)?'
    r'(?:@relation[^@\n]*references:\s*\[(\w+)\])?',
)


# =============================================================================
# ORMPenetrator
# =============================================================================

class ORMPenetrator:
    """Static analysis of ORM configurations to extract data topology."""

    RELATION_TYPE_MAP = {
        "OneToMany": "ONE_TO_MANY",
        "ManyToOne": "MANY_TO_ONE",
        "ManyToMany": "MANY_TO_MANY",
        "OneToOne": "ONE_TO_ONE",
    }

    def penetrate(self, project_dir: str) -> Dict:
        if not os.path.isdir(project_dir):
            return {
                "error_code": 60006,
                "error": f"Project directory not found: {project_dir}",
                "tables": [],
                "relations": [],
            }

        tables: List[Dict] = []
        relations: List[Dict] = []

        try:
            self._scan_mybatis(project_dir, tables, relations)
        except Exception as exc:
            logger.warning("MyBatis scan failed: %s", exc)

        try:
            self._scan_prisma(project_dir, tables, relations)
        except Exception as exc:
            logger.warning("Prisma scan failed: %s", exc)

        try:
            self._scan_jpa(project_dir, tables, relations)
        except Exception as exc:
            logger.warning("JPA scan failed: %s", exc)

        try:
            self._scan_sqlalchemy(project_dir, tables, relations)
        except Exception as exc:
            logger.warning("SQLAlchemy scan failed: %s", exc)

        if not tables:
            self._scan_sql_files(project_dir, tables, relations)

        table_map = {t["name"]: t for t in tables}

        normalized_relations: List[Dict] = []
        seen_relations: set = set()
        for rel in relations:
            fk = (rel["from_table"], rel["to_table"], rel.get("via_field", ""))
            if fk not in seen_relations:
                seen_relations.add(fk)
                normalized_relations.append(rel)

        return {
            "tables": tables,
            "relations": normalized_relations,
            "table_count": len(tables),
            "relation_count": len(normalized_relations),
        }

    # =========================================================================
    # MyBatis XML mappers
    # =========================================================================

    def _scan_mybatis(self, project_dir: str, tables: List[Dict], relations: List[Dict]):
        for root, _, files in os.walk(project_dir):
            for fname in files:
                if not fname.endswith(".xml"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    self._parse_mybatis_xml(fpath, tables)
                except ET.ParseError:
                    continue

    def _parse_mybatis_xml(self, file_path: str, tables: List[Dict]):
        tree = ET.parse(file_path)
        root_el = tree.getroot()
        namespace = root_el.attrib.get("namespace", "")

        result_maps = root_el.findall(".//resultMap")
        for rm in result_maps:
            rm_id = rm.attrib.get("id", "unknown")
            table_name = self._resolve_table_from_select(root_el, rm_id) or rm_id
            fields: List[Dict] = []

            for child in rm:
                tag = child.tag.lower() if "}" in child.tag else child.tag
                tag = tag.split("}")[-1] if "}" in tag else tag
                prop = child.attrib.get("property", "")
                col = child.attrib.get("column", prop)
                jdbc_type = child.attrib.get("jdbcType", "VARCHAR")

                is_pk = tag == "id"
                is_fk = False
                references = None

                association = child.find("association") or child
                if "select" in child.attrib:
                    is_fk = True

                fields.append({
                    "name": col,
                    "type": jdbc_type,
                    "is_primary_key": is_pk,
                    "is_foreign_key": is_fk,
                    "references": references,
                })

            if table_name:
                tables.append({
                    "name": table_name,
                    "fields": fields,
                    "entity_class": namespace,
                    "orm_type": "mybatis",
                })

    @staticmethod
    def _resolve_table_from_select(root_el, result_map_id: str) -> Optional[str]:
        for sel in root_el.findall(".//select"):
            rm_ref = sel.attrib.get("resultMap", "")
            if rm_ref == result_map_id:
                text = ET.tostring(sel, encoding="unicode").lower()
                m = re.search(r'from\s+(\w+)', text)
                if m:
                    return m.group(1)
        return None

    # =========================================================================
    # Prisma schema.prisma
    # =========================================================================

    def _scan_prisma(self, project_dir: str, tables: List[Dict], relations: List[Dict]):
        for root, _, files in os.walk(project_dir):
            for fname in files:
                if fname == "schema.prisma":
                    fpath = os.path.join(root, fname)
                    self._parse_prisma_schema(fpath, tables, relations)

    def _parse_prisma_schema(self, file_path: str, tables: List[Dict], relations: List[Dict]):
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()

        for match in PRISMA_MODEL_PATTERN.finditer(content):
            model_name = match.group(1)
            body = match.group(2)
            fields: List[Dict] = []

            for line in body.strip().split("\n"):
                line = line.strip()
                if not line or line.startswith("//") or line.startswith("@"):
                    continue

                m = PRISMA_FIELD_PATTERN.match(line)
                if not m:
                    continue

                field_name = m.group(1)
                field_type = m.group(2)
                is_pk = bool(m.group(3))
                references = m.group(5) if m.lastindex and m.lastindex >= 5 else None
                is_fk = references is not None

                fields.append({
                    "name": field_name,
                    "type": field_type,
                    "is_primary_key": is_pk,
                    "is_foreign_key": is_fk,
                    "references": references,
                })

                if is_fk and references:
                    relations.append({
                        "from_table": model_name,
                        "to_table": references,
                        "type": "MANY_TO_ONE",
                        "via_field": field_name,
                    })

            tables.append({
                "name": model_name,
                "fields": fields,
                "entity_class": model_name,
                "orm_type": "prisma",
            })

    # =========================================================================
    # Hibernate / JPA @Entity in Java files
    # =========================================================================

    def _scan_jpa(self, project_dir: str, tables: List[Dict], relations: List[Dict]):
        for root, _, files in os.walk(project_dir):
            for fname in files:
                if not fname.endswith(".java"):
                    continue
                fpath = os.path.join(root, fname)
                self._parse_jpa_file(fpath, tables, relations)

        # Third pass: JpaRepository extends — infer entities from generics (if still no tables)
        if not tables:
            seen_entities = set()
            for root, _, files in os.walk(project_dir):
                for fname in files:
                    if not fname.endswith(".java"):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                            content = fh.read()
                    except:
                        continue
                    for m in re.finditer(
                        r'extends\s+JpaRepository\s*<\s*(\w+)\s*,\s*\w+\s*>',
                        content, re.MULTILINE
                    ):
                        entity_name = m.group(1)
                        if entity_name in seen_entities:
                            continue
                        # Case-insensitive entity file match
                        matched = None
                        for subroot, _, subfiles in os.walk(project_dir):
                            for sf in subfiles:
                                if sf.lower() == f"{entity_name.lower()}.java":
                                    matched = sf
                                    break
                            if matched:
                                efpath = os.path.join(subroot, matched)
                                try:
                                    with open(efpath, "r", encoding="utf-8", errors="replace") as efh:
                                        econtent = efh.read()
                                except:
                                    continue
                                if re.search(rf"class\s+{re.escape(entity_name)}\b", econtent, re.IGNORECASE):
                                    seen_entities.add(entity_name)
                                    table_name = entity_name.lower()
                                    cm = re.search(rf"class\s+{re.escape(entity_name)}\b", econtent, re.IGNORECASE)
                                    if cm:
                                        self._extract_jpa_entity(
                                            econtent, cm, table_name, entity_name, tables, relations
                                        )
                                break

    def _parse_jpa_file(self, file_path: str, tables: List[Dict], relations: List[Dict]):
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()

        seen_entities: set = set()

        # First pass: strict pattern
        for entity_match in JPA_ENTITY_PATTERN.finditer(content):
            table_name = entity_match.group(1) or entity_match.group(2).lower()
            class_name = entity_match.group(2)
            seen_entities.add(class_name)
            self._extract_jpa_entity(content, entity_match, table_name, class_name, tables, relations)

        # Second pass: loose entity detection for classes missed by strict pattern
        for entity_match in JPA_ENTITY_LOOSE.finditer(content):
            class_name = entity_match.group(1)
            if class_name in seen_entities:
                continue
            seen_entities.add(class_name)
            table_name = class_name.lower()
            self._extract_jpa_entity(content, entity_match, table_name, class_name, tables, relations)

    def _extract_jpa_entity(self, content: str, entity_match: re.Match,
                            table_name: str, class_name: str,
                            tables: List[Dict], relations: List[Dict]):
        fields: List[Dict] = []
        seen_fields: set = set()

        class_body = self._extract_class_body(content, entity_match.start())

        # Try to extract @Table(name="xxx") for accurate table name
        table_match = re.search(r'@Table\s*\(\s*(?:name\s*=\s*)"([^"]+)"', class_body)
        if table_match:
            table_name = table_match.group(1)

        # Build column name mapping: @Column(name="xxx") → fieldName
        col_name_map = {}
        for col_m in re.finditer(r'@Column\s*\(\s*(?:name\s*=\s*)"([^"]+)"', class_body):
            db_col_name = col_m.group(1)
            after = class_body[col_m.end():col_m.end()+200]
            fm = re.search(r'(?:private|protected|public)\s+\w+\s+(\w+)\s*;', after)
            if fm:
                col_name_map[fm.group(1)] = db_col_name

        for col_match in JPA_COLUMN_PATTERN.finditer(class_body):
            col_type = col_match.group(1)
            col_name = col_match.group(2)
            seen_fields.add(col_name)
            is_pk = "@Id" in col_match.group(0)

            fields.append({
                "name": col_name_map.get(col_name, col_name),
                "type": col_type,
                "is_primary_key": is_pk,
                "is_foreign_key": "@JoinColumn" in col_match.group(0),
                "references": None,
            })

        # Bare field fallback for Lombok/@Data entities (no @Column on fields)
        if not fields:
            bare_field = re.compile(
                r'(?:private|protected|public)\s+(\w+(?:<[^>]+>)?)\s+(\w+)\s*;',
                re.MULTILINE
            )
            for fm in bare_field.finditer(class_body):
                ftype, fname = fm.group(1), fm.group(2)
                if fname in seen_fields or fname in ('serialVersionUID', 'log', 'logger'):
                    continue
                seen_fields.add(fname)
                fields.append({
                    "name": col_name_map.get(fname, fname),
                    "type": ftype,
                    "is_primary_key": fname.lower() == 'id',
                    "is_foreign_key": fname.lower().endswith('id') and fname.lower() != 'id',
                    "references": None,
                })

        for rel_match in JPA_RELATION_PATTERN.finditer(class_body):
            rel_type = self.RELATION_TYPE_MAP.get(rel_match.group(1), "ONE_TO_ONE")
            mapped_by = rel_match.group(2)

            relations.append({
                "from_table": table_name,
                "to_table": "",
                "type": rel_type,
                "via_field": mapped_by or "",
            })

        tables.append({
            "name": table_name,
            "fields": fields,
            "entity_class": class_name,
            "orm_type": "jpa",
        })

    @staticmethod
    def _extract_class_body(content: str, start: int) -> str:
        brace_count = 0
        in_class = False
        class_start = start
        for i in range(start, len(content)):
            if content[i] == "{":
                brace_count += 1
                in_class = True
            elif content[i] == "}":
                brace_count -= 1
                if in_class and brace_count == 0:
                    return content[class_start:i + 1]
        return content[start:]

    # =========================================================================
    # SQLAlchemy models in Python
    # =========================================================================

    def _scan_sqlalchemy(self, project_dir: str, tables: List[Dict], relations: List[Dict]):
        for root, _, files in os.walk(project_dir):
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(root, fname)
                self._parse_sqlalchemy_file(fpath, tables, relations)

    def _parse_sqlalchemy_file(self, file_path: str, tables: List[Dict], relations: List[Dict]):
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()

        for model_match in SQLALCHEMY_MODEL_PATTERN.finditer(content):
            class_name = model_match.group(1)
            table_name = getattr(self, "_sqlalchemy_table_name", class_name.lower())
            class_body = self._extract_class_body(content, model_match.start())
            fields: List[Dict] = []

            __tablename__pat = re.compile(r'__tablename__\s*=\s*[\'"]([^\'"]+)[\'"]')
            tn_match = __tablename__pat.search(class_body)
            if tn_match:
                table_name = tn_match.group(1)

            for col_match in SQLALCHEMY_COLUMN_PATTERN.finditer(class_body):
                col_name = col_match.group(1)
                col_type = col_match.group(2)
                is_pk = "primary_key=True" in class_body[col_match.start():col_match.end() + 100]

                fk_match = SQLALCHEMY_FK_PATTERN.search(
                    class_body, col_match.start(), col_match.end() + 120
                )
                is_fk = fk_match is not None
                references = fk_match.group(1) if fk_match else None

                fields.append({
                    "name": col_name,
                    "type": col_type,
                    "is_primary_key": is_pk,
                    "is_foreign_key": is_fk,
                    "references": references,
                })

                if is_fk and references:
                    parts = references.split(".")
                    to_table = parts[0] if len(parts) > 0 else references
                    relations.append({
                        "from_table": table_name,
                        "to_table": to_table,
                        "type": "MANY_TO_ONE",
                        "via_field": col_name,
                    })

            for rel_match in SQLALCHEMY_RELATION_PATTERN.finditer(class_body):
                backref_field = rel_match.group(1)
                target = rel_match.group(2)
                relations.append({
                    "from_table": table_name,
                    "to_table": target,
                    "type": "ONE_TO_MANY",
                    "via_field": backref_field,
                })

            tables.append({
                "name": table_name,
                "fields": fields,
                "entity_class": class_name,
                "orm_type": "sqlalchemy",
            })

    def _scan_sql_files(self, project_dir: str, tables: List[Dict], relations: List[Dict]):
        """Fallback: scan .sql files for CREATE TABLE statements when no ORM config found."""
        sql_create = re.compile(
            r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"\[]?(\w+)[`"\]]?\s*\((.*?)\);?',
            re.IGNORECASE | re.DOTALL
        )
        sql_col = re.compile(
            r'[`"\[]?(\w+)[`"\]]?\s+(\w+(?:\([^)]+\))?)\s*(PRIMARY\s+KEY)?'
            r'|(?:PRIMARY\s+KEY\s*\(([^)]+)\))'
            r'|(?:FOREIGN\s+KEY\s*\((\w+)\)\s*REFERENCES\s*(\w+)\s*\((\w+)\))',
            re.IGNORECASE
        )
        sql_fk = re.compile(
            r'FOREIGN\s+KEY\s*[`"\[]?(\w+)[`"\]]?\s*REFERENCES\s*[`"\[]?(\w+)[`"\]]?\s*\([`"\[]?(\w+)[`"\]]?\)',
            re.IGNORECASE
        )

        seen = set()
        for root, dirs, files in os.walk(project_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__')]
            for f in files:
                if f.lower().endswith('.sql'):
                    fp = os.path.join(root, f)
                    try:
                        with open(fp, 'r', encoding='utf-8', errors='replace') as fh:
                            src = fh.read()
                    except Exception:
                        continue
                    for tm in sql_create.finditer(src):
                        tname = tm.group(1).lower()
                        if tname in seen:
                            continue
                        seen.add(tname)
                        body = tm.group(2)
                        fields = []
                        pk_fields = []
                        for col_line in body.split(','):
                            col_line = col_line.strip()
                            cm = re.match(r'[`"\[]?(\w+)[`"\]]?\s+(\w+(?:\(\d+(?:,\d+)?\))?)', col_line, re.IGNORECASE)
                            if cm:
                                col_name = cm.group(1)
                                col_type = cm.group(2).upper()
                                is_pk = 'PRIMARY KEY' in col_line.upper()
                                fields.append({
                                    "name": col_name, "type": col_type,
                                    "is_primary_key": is_pk, "is_foreign_key": False,
                                    "references": None
                                })
                                if is_pk:
                                    pk_fields.append(col_name)
                        for fkm in sql_fk.finditer(body):
                            fields.append({
                                "name": fkm.group(1), "type": "FK",
                                "is_primary_key": False, "is_foreign_key": True,
                                "references": f"{fkm.group(2)}.{fkm.group(3)}"
                            })
                            relations.append({
                                "from_table": tname, "to_table": fkm.group(2).lower(),
                                "type": "MANY_TO_ONE", "via_field": fkm.group(1)
                            })
                        tables.append({
                            "name": tname, "fields": fields,
                            "entity_class": None, "orm_type": "sql_file_fallback"
                        })
        logger.info("SQL file fallback: found %d tables in .sql files", len(seen))
