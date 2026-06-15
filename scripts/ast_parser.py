"""
AST-level symbol skeleton extraction and code topology analysis.
Multi-language: Python ast for .py, regex-based for Java/TS/Go/proto, FallbackLexer for others.
All parsers strip method bodies — extract only signatures, annotations, imports.

CLI modes: discover | symbols | entry | callgraph | trace
"""

import argparse
import ast as py_ast
import fnmatch
import hashlib
import json
import logging
import os
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from config import CODE_EXTENSIONS, DEFAULT_EXCLUDED_DIRS, DEFAULT_EXCLUDED_FILES  # noqa: E402
from config import HASH_ALGORITHM, HASH_CHUNK_SIZE, REPO_BREAKER_MAX_FILES  # noqa: E402
from config import REPO_BREAKER_MAX_MODULES, REPO_BREAKER_MAX_FILE_SIZE_MB  # noqa: E402

logger = logging.getLogger(__name__)

EXT_MAP = {".py":"python",".java":"java",".go":"go",".ts":"typescript",".tsx":"typescript",
           ".js":"javascript",".jsx":"javascript",".rs":"rust",".cpp":"cpp",".c":"c",
           ".h":"c",".hpp":"cpp",".cs":"csharp",".swift":"swift",".kt":"kotlin",
           ".scala":"scala",".rb":"ruby",".php":"php",".vue":"vue",".svelte":"svelte",
           ".sql":"sql",".proto":"protobuf"}

TERM_METHODS = {"save","insert","update","delete","remove","persist","merge",
                "flush","commit","rollback","execute","query","find","get",
                "create","drop","alter","truncate","bulkSave","batchInsert"}
TERM_SUFFIXES = ("Repository","DAO","Dao","Mapper","Repo","Gateway",
                 "EntityManager","Session","Connection")

# ---- File helpers ----
def _hash(file_path: str) -> str:
    h = hashlib.new(HASH_ALGORITHM)
    with open(file_path, "rb") as f:
        while True:
            c = f.read(HASH_CHUNK_SIZE)
            if not c: break
            h.update(c)
    return h.hexdigest()

def _skip_braces(s: str, i: int) -> int:
    d = 0
    while i < len(s):
        if s[i] == '{': d += 1
        elif s[i] == '}':
            d -= 1
            if d == 0: return i
        i += 1
    return len(s) - 1

def _strip_cmts(s: str) -> str:
    """Strip comments with fast state machine. O(n) single pass, no regex backtracking."""
    result = []
    i = 0
    n = len(s)
    while i < n:
        if i + 1 < n and s[i] == '/' and s[i+1] == '/':
            # Line comment — skip to end of line
            i += 2
            while i < n and s[i] != '\n':
                i += 1
            if i < n:
                result.append('\n')
                i += 1
        elif i + 1 < n and s[i] == '/' and s[i+1] == '*':
            # Block comment — skip to */
            i += 2
            while i + 1 < n and not (s[i] == '*' and s[i+1] == '/'):
                i += 1
            i += 2  # skip */
        else:
            result.append(s[i])
            i += 1
    return ''.join(result)

def _load_xoderignore(ws: str) -> List[str]:
    p = os.path.join(ws, ".xoderignore")
    if not os.path.isfile(p): return []
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]

def _ign_match(name: str, pats: List[str], is_dir: bool) -> bool:
    for p in pats:
        neg = p.startswith("!")
        c = p[1:] if neg else p
        if c.endswith("/") and not is_dir: continue
        if fnmatch.fnmatch(name, c.rstrip("/")): return not neg
    return False

def _exclude(name: str, is_dir: bool) -> bool:
    if is_dir: return name in DEFAULT_EXCLUDED_DIRS
    return any(fnmatch.fnmatch(name, p) for p in DEFAULT_EXCLUDED_FILES)

# ---- Annotation extraction ----
_ANN_RE = re.compile(r'@(\w+(?:\.\w+)*)\s*(?:\([^)]*\))?')

def _anns(s: str) -> List[str]:
    return [m.group(0) for m in _ANN_RE.finditer(s)]

# ---- Python AST parser ----
def _name_of(n: Any) -> str:
    if isinstance(n, py_ast.Name): return n.id
    if isinstance(n, py_ast.Attribute):
        p = []
        while isinstance(n, py_ast.Attribute):
            p.append(n.attr); n = n.value
        if isinstance(n, py_ast.Name): p.append(n.id)
        return ".".join(reversed(p))
    if isinstance(n, py_ast.Constant) and isinstance(n.value, str): return n.value
    return ""

def _py_calls(node: py_ast.FunctionDef) -> List[str]:
    calls: Set[str] = set()
    class V(py_ast.NodeVisitor):
        def visit_Call(self, n):
            if isinstance(n.func, py_ast.Name): calls.add(n.func.id)
            elif isinstance(n.func, py_ast.Attribute): calls.add(_name_of(n.func))
            self.generic_visit(n)
    V().visit(node)
    return sorted(calls)

def _py_parse(src: str, fp: str) -> Dict:
    tree = py_ast.parse(src, filename=fp)
    cls_list, funcs, imps = [], [], []
    for nd in py_ast.iter_child_nodes(tree):
        if isinstance(nd, py_ast.Import):
            for a in nd.names: imps.append(a.name)
        elif isinstance(nd, py_ast.ImportFrom):
            mod = nd.module or ""
            for a in nd.names: imps.append(f"{mod}.{a.name}" if mod else a.name)
        elif isinstance(nd, py_ast.ClassDef):
            c = {"class_name":nd.name,"modifiers":[],"annotations":[_name_of(d) for d in nd.decorator_list],
                 "extends":None,"implements":[],"methods":[],"fields":[],"dependencies":[]}
            bases = [_name_of(b) for b in nd.bases]
            if bases: c["extends"] = bases[0]; c["implements"] = bases[1:]
            for ch in nd.body:
                if isinstance(ch, py_ast.FunctionDef):
                    m = {"name":ch.name,"return_type":_name_of(ch.returns) if ch.returns else None,
                         "parameters":[{"name":a.arg,"type":_name_of(a.annotation) if a.annotation else None}
                                       for a in ch.args.args],
                         "annotations":[_name_of(d) for d in ch.decorator_list],"business_rules":[]}
                    deps = _py_calls(ch); m["dependencies"] = deps
                    body_text = py_ast.get_source_segment(src, ch) or ""
                    m["business_rules"] = _extract_biz_rules(body_text, "python")
                    c["methods"].append(m)
                    for d in deps:
                        if d not in c["dependencies"]: c["dependencies"].append(d)
                elif isinstance(ch, py_ast.Assign):
                    for t in ch.targets:
                        if isinstance(t, py_ast.Name) and isinstance(ch.value, py_ast.Call):
                            c["fields"].append({"name":t.id,"type":_name_of(ch.value.func)
                                                if hasattr(ch.value,'func') else "","annotations":[]})
            cls_list.append(c)
        elif isinstance(nd, py_ast.FunctionDef):
            m = {"name":nd.name,"return_type":_name_of(nd.returns) if nd.returns else None,
                 "parameters":[{"name":a.arg,"type":_name_of(a.annotation) if a.annotation else None}
                               for a in nd.args.args],
                 "annotations":[_name_of(d) for d in nd.decorator_list],"business_rules":[]}
            m["dependencies"] = _py_calls(nd)
            body_text = py_ast.get_source_segment(src, nd) or ""
            m["business_rules"] = _extract_biz_rules(body_text, "python")
            funcs.append(m)
    return {"classes":cls_list,"functions":funcs,"imports":imps,"entry_points":_detect_entries(src,"python",fp)}

# ---- C-family call extraction ----
_CF_DOT_CALL = re.compile(r'(\w+)\.(\w+)\s*\(')
_CF_BARE_CALL = re.compile(r'(?<![.\w])(\w+)\s*\(')
_CF_IGNORE = {"this", "super", "new", "return", "if", "else", "for", "while",
              "do", "switch", "case", "try", "catch", "finally", "throw", "throws",
              "class", "interface", "enum", "import", "package", "void", "int",
              "long", "float", "double", "boolean", "byte", "short", "char", "String"}

def _cf_extract_calls(body: str, keywords: set) -> List[str]:
    """Extract method call patterns from C-family method bodies.
    Captures: obj.method(...) and bare method(...)
    Returns unique sorted list of qualified call names."""
    calls: Set[str] = set()
    all_ignore = keywords | _CF_IGNORE
    for m in _CF_DOT_CALL.finditer(body):
        g1, g2 = m.group(1), m.group(2)
        if g1 not in all_ignore and g2 not in all_ignore:
            calls.add(f"{g1}.{g2}")
    for m in _CF_BARE_CALL.finditer(body):
        g = m.group(1)
        if g not in all_ignore:
            calls.add(g)
    cond_kw = {"if", "else", "return", "throw", "catch"}
    for kw in cond_kw:
        if re.search(r'\b' + kw + r'\b', body):
            calls.add(f"__{kw}__")
    return sorted(calls)

# ---- Business rule extraction ----
_BIZ_IF = re.compile(r'if\s*\((.*?)\)\s*(?:return|throw|{)\s*', re.IGNORECASE)
_BIZ_ASSIGN = re.compile(r'(\w[\w.]*)\s*=\s*(.+?)\s*;')
_BIZ_ARITH = re.compile(r'[+\-*/%]')
_BIZ_ENUM = re.compile(r'"([^"]+)"(?:\s*\|\s*"([^"]+)")+')

def _extract_biz_rules(body: str, lang: str = "java") -> List[Dict]:
    """Extract business rules from method bodies.
    Returns list of {type, desc, vars, line} dicts.
    types: constraint, calculation, state_change, workflow, enum_values
    """
    rules = []
    # constraints (if-condition → reject/return)
    for m in _BIZ_IF.finditer(body):
        cond = m.group(1)[:120].strip().replace('\n', ' ')
        rules.append({"type": "constraint", "desc": cond,
                      "vars": re.findall(r'\b(\w+)\b', cond)[:6]})
    # calculations (assignments with arithmetic)
    for m in _BIZ_ASSIGN.finditer(body):
        lhs = m.group(1).strip()
        rhs = m.group(2).strip()[:120].replace('\n', ' ')
        if _BIZ_ARITH.search(rhs):
            vars_in = re.findall(r'\b([a-zA-Z]\w+)\b', rhs)
            rules.append({"type": "calculation", "desc": f"{lhs} = {rhs}",
                          "vars": [v for v in vars_in if v not in ("if","for","while","new","return","null","true","false")][:8]})
    # state changes (field assignments: this.field = value / setField(value))
    sf = re.findall(r'(?:this\.)?(?:set)?(\w+)\s*\((?:new\s+)?(\w*)\)?|(\w+)\.set(\w+)\s*\(', body)
    for g in sf:
        flat = [x for x in g if x]
        if len(flat) >= 1 and flat[0][0].isupper() if flat and flat[0] else False:
            rules.append({"type": "state_change", "desc": f"状态变更: {'→'.join(flat[:2])}",
                          "vars": list(flat)})
    # enum values (string literals suggesting options)
    quoted = re.findall(r'"([^"]{1,40})"', body)
    if len(quoted) >= 2 and len(quoted) <= 10:
        rules.append({"type": "enum_values", "desc": " / ".join(quoted[:8]),
                      "vars": quoted[:8]})
    return rules

# ---- Java parser ----
_J_CLS = re.compile(r'(?:(?:public|private|protected|static|abstract|final)\s+)*'
                    r'(?:class|interface|enum)\s+(\w+)(?:\s+extends\s+([\w.<>,\s]+?))?'
                    r'(?:\s+implements\s+([\w.<>,\s]+?))?\s*\{')
_J_MET = re.compile(r'(?:(?:public|private|protected|static|abstract|final|synchronized|native'
                    r'|transient|volatile|default)\s+)*(?:<\w[\w\s,<>?\[\]]*>\s+)?'
                    r'([\w<>\[\].,\s]+?)\s+(\w+)\s*\(([^)]*)\)')
_J_FLD = re.compile(r'(?:(?:public|private|protected|static|final|transient|volatile)\s+)+'
                    r'([\w<>\[\].,\s]+?)\s+(\w+(?:\s*=\s*[^;]+)?)\s*;', re.MULTILINE)
_J_IMP = re.compile(r'import\s+(?:static\s+)?([\w.*]+)\s*;')
_J_KW = {"if","else","for","while","do","switch","case","default","try","catch","finally",
         "throw","throws","return","new","synchronized","assert","break","continue",
         "class","interface","enum","extends","implements","import","package",
         "public","private","protected","static","final","abstract","native",
         "transient","volatile","strictfp","const","goto","super","this",
         "instanceof","void","boolean","byte","short","int","long","float","double","char","String"}
_J_MOD_KW = {"public","private","protected","static","final","abstract","synchronized",
             "native","transient","volatile","default","strictfp"}

def _j_mods(raw: str) -> List[str]:
    return [kw for kw in ("public","private","protected","static","abstract","final") if kw in raw]

def _j_params(ps: str) -> List[Dict]:
    r = []
    for p in ps.split(","):
        p = p.strip()
        if not p: continue
        pts = p.split()
        r.append({"name":pts[-1],"type":" ".join(pts[:-1]) if len(pts)>1 else None})
    return r

def _j_members(body: str, cls: Dict) -> None:
    pos, pa = 0, []
    bi = -1
    be = -1
    while pos < len(body):
        am = _ANN_RE.match(body, pos)
        if am:
            pa.append(am.group(0).strip())
            pos = am.end()
            while pos < len(body) and body[pos] in ' \t\r\n': pos += 1
            continue
        mm = _J_MET.match(body, pos)
        if mm:
            ret, nm = mm.group(1).strip(), mm.group(2)
            ret = " ".join(w for w in ret.split() if w not in _J_MOD_KW)
            if nm not in _J_KW and not ret.startswith("//"):
                method = {"name":nm,"return_type":ret,
                    "parameters":_j_params(mm.group(3) or ""),"annotations":pa[:],"dependencies":[],"business_rules":[]}
                bi = body.find('{', mm.end())
                be = _skip_braces(body, bi) if bi != -1 else bi
                if bi != -1 and be != -1:
                    method["dependencies"] = _cf_extract_calls(body[bi+1:be], _J_KW)
                    method["business_rules"] = _extract_biz_rules(body[bi+1:be])
                elif bi != -1:
                    method["dependencies"] = _cf_extract_calls(body[bi:], _J_KW)
                cls["methods"].append(method)
            pa = []
            if bi != -1 and be != -1:
                pos = be + 1
            else:
                pos = mm.end()
            continue
        fm = _J_FLD.match(body, pos)
        if fm:
            cls["fields"].append({"name":fm.group(2).strip().split("=")[0].strip(),
                "type":fm.group(1).strip(),"annotations":pa[:]}); pa = []; pos = fm.end(); continue
        pos += 1

def _j_parse(src: str) -> Dict:
    clean = _strip_cmts(src)
    cl, im = [], [m.group(1) for m in _J_IMP.finditer(clean)]
    
    # Fast: use finditer to jump directly to class definitions
    for cm in _J_CLS.finditer(clean):
        # Look backwards for annotations (within 500 chars before class)
        before = clean[max(0, cm.start()-500):cm.start()]
        pa = _anns(before)
        c = {"class_name": cm.group(1),
             "modifiers": _j_mods(clean[max(0,cm.start()-30):cm.start()]),
             "annotations": pa,
             "extends": cm.group(2).strip() if cm.group(2) else None,
             "implements": [x.strip() for x in cm.group(3).split(",")] if cm.group(3) else [],
             "methods": [], "fields": [], "dependencies": [], "business_rules": []}
        bs = cm.end() - 1
        be = _skip_braces(clean, bs)
        _j_members(clean[bs+1:be], c)
        cl.append(c)
    
    return {"classes": cl, "functions": [], "imports": im, "entry_points": _detect_entries(src, "java")}

# ---- TypeScript/JS parser ----
_TS_CLS = re.compile(r'(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+(\w+)'
                     r'(?:\s+extends\s+([\w.<>,\s]+?))?(?:\s+implements\s+([\w.<>,\s]+?))?\s*\{')
_TS_IFACE = re.compile(r'(?:export\s+(?:default\s+)?)?interface\s+(\w+)(?:\s+extends\s+([\w.<>,\s]+?))?\s*\{')
_TS_FN = re.compile(r'(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)'
                    r'(?:\s*:\s*([\w<>\[\]|&\s,]+))?', re.MULTILINE)
_TS_MT = re.compile(r'(?:(?:public|private|protected|static|abstract|async)\s+)*(\w+)\s*\(([^)]*)\)'
                    r'(?:\s*:\s*([\w<>\[\]|&\s,]+))?', re.MULTILINE)
_TS_ARR = re.compile(r'(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(([^)]*)\)'
                     r'(?:\s*:\s*([\w<>\[\]|&\s,]+))?\s*=>', re.MULTILINE)
_TS_IMP = re.compile(r'import\s+.+?\s*from\s*[\'"]([^\'"]+)[\'"]', re.MULTILINE)
_TS_REQ = re.compile(r'(?:const|let|var)\s+.+?\s*=\s*require\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)', re.MULTILINE)
_TS_KW = {"if","else","for","while","switch","return","throw","new","try","catch","typeof","instanceof"}

def _ts_ps(ps: str) -> List[Dict]:
    r = []
    for p in ps.split(","):
        p = p.strip()
        if not p: continue
        if ":" in p:
            a = p.split(":",1); r.append({"name":a[0].strip(),"type":a[1].strip()})
        else: r.append({"name":p,"type":None})
    return r

def _ts_members(body: str, cls: Dict) -> None:
    pos = 0
    while pos < len(body):
        mm = _TS_MT.match(body, pos)
        if mm and mm.group(1) not in _TS_KW:
            method = {"name":mm.group(1),"return_type":mm.group(3).strip() if mm.group(3) else None,
                                   "parameters":_ts_ps(mm.group(2)),"annotations":[],"dependencies":[],"business_rules":[]}
            bi = body.find('{', mm.end())
            be = _skip_braces(body, bi) if bi != -1 else bi
            if bi != -1 and be != -1:
                method["dependencies"] = _cf_extract_calls(body[bi+1:be], _TS_KW)
                method["business_rules"] = _extract_biz_rules(body[bi+1:be], lang)
            cls["methods"].append(method)
            if bi != -1 and be != -1:
                pos = be + 1
            else:
                pos = mm.end()
            continue
        pos += 1

def _ts_iface_members(body: str, cls: Dict) -> None:
    for m in _TS_MT.finditer(body):
        cls["methods"].append({"name":m.group(1),"return_type":m.group(3).strip() if m.group(3) else None,
                               "parameters":_ts_ps(m.group(2)),"annotations":[]})

def _ts_parse(src: str, lang: str = "typescript") -> Dict:
    clean = _strip_cmts(src)
    cl, fn, imps, sn = [], [], [], set()
    for m in _TS_IMP.finditer(clean): imps.append(m.group(1))
    for m in _TS_REQ.finditer(clean): imps.append(m.group(1))

    for m in _TS_IFACE.finditer(clean):
        c = {"class_name":m.group(1),"modifiers":["export"] if "export" in m.group(0) else [],
             "annotations":[],"extends":m.group(2).strip() if m.group(2) else None,"implements":[],
             "methods":[],"fields":[],"dependencies":[]}
        bs = m.end()-1; _ts_iface_members(clean[bs+1:_skip_braces(clean,bs)], c)
        cl.append(c); sn.add(m.group(1))

    for m in _TS_CLS.finditer(clean):
        c = {"class_name":m.group(1),"modifiers":["export"] if "export" in m.group(0) else [],
             "annotations":[],"extends":m.group(2).strip() if m.group(2) else None,
             "implements":[x.strip() for x in m.group(3).split(",")] if m.group(3) else [],
             "methods":[],"fields":[],"dependencies":[]}
        bs = m.end()-1; _ts_members(clean[bs+1:_skip_braces(clean,bs)], c)
        cl.append(c); sn.add(m.group(1))

    for m in _TS_FN.finditer(clean):
        fn.append({"name":m.group(1),"return_type":m.group(3).strip() if m.group(3) else None,
                   "parameters":_ts_ps(m.group(2)),"annotations":[]})
    for m in _TS_ARR.finditer(clean):
        if m.group(1) not in sn:
            fn.append({"name":m.group(1),"return_type":m.group(3).strip() if m.group(3) else None,
                       "parameters":_ts_ps(m.group(2)),"annotations":[]})

    return {"classes":cl,"functions":fn,"imports":imps,"entry_points":_detect_entries(src,lang)}

# ---- Go parser ----
_G_STRUCT = re.compile(r'type\s+(\w+)\s+struct\s*\{')
_G_IFACE = re.compile(r'type\s+(\w+)\s+interface\s*\{')
_G_FN = re.compile(r'func\s+(?:\((\w+)\s+\*?(\w+)\)\s+)?(\w+)\s*\(([^)]*)\)\s*(?:\(?([^)]*)\)?)?\s*\{?', re.MULTILINE)
_G_IMP_BLK = re.compile(r'import\s*\((.*?)\)', re.DOTALL)
_G_IMP = re.compile(r'"([^"]+)"')

def _g_ps(ps: str) -> List[Dict]:
    r = []
    for p in ps.split(","):
        p = p.strip()
        if not p: continue
        pts = p.split()
        r.append({"name":pts[0],"type":" ".join(pts[1:]) if len(pts)>1 else None})
    return r

def _g_parse(src: str) -> Dict:
    clean = _strip_cmts(src)
    cl, fn, imps = [], [], []
    for bm in _G_IMP_BLK.finditer(clean):
        for m in _G_IMP.finditer(bm.group(1)): imps.append(m.group(1))

    for m in _G_STRUCT.finditer(clean):
        bs = m.end()-1; be = _skip_braces(clean, bs)
        body = clean[bs+1:be]; fld = []
        for l in body.split('\n'):
            l = l.strip()
            if not l or l.startswith('//'): continue
            pts = l.split()
            if len(pts) >= 2: fld.append({"name":pts[0],"type":pts[1].rstrip(','),"annotations":[]})
        cl.append({"class_name":m.group(1),"modifiers":[],"annotations":[],"extends":None,
                    "implements":[],"methods":[],"fields":fld,"dependencies":[]})

    for m in _G_IFACE.finditer(clean):
        bs = m.end()-1; be = _skip_braces(clean, bs)
        body = clean[bs+1:be]; mtds = []
        for l in body.split('\n'):
            l = l.strip()
            if not l or l.startswith('//'): continue
            fm = re.match(r'(\w+)\s*\(([^)]*)\)(?:\s*\(?([^)]*)\)?)?', l)
            if fm: mtds.append({"name":fm.group(1),"return_type":fm.group(3).strip() if fm.group(3) else None,
                                "parameters":_g_ps(fm.group(2)),"annotations":[]})
        cl.append({"class_name":m.group(1),"modifiers":[],"annotations":[],"extends":None,
                    "implements":[],"methods":mtds,"fields":[],"dependencies":[]})

    for m in _G_FN.finditer(clean):
        rn, rt = m.group(1), m.group(2)
        fnm, ps, ret = m.group(3), m.group(4) or "", m.group(5) or ""
        rec = {"name":fnm,"return_type":ret.strip() or None,"parameters":_g_ps(ps),"annotations":[]}
        if rn and rt:
            rc = rt.strip('*')
            c = next((x for x in cl if x["class_name"] == rc), None)
            if not c:
                c = {"class_name":rc,"modifiers":[],"annotations":[],"extends":None,
                     "implements":[],"methods":[],"fields":[],"dependencies":[]}; cl.append(c)
            del rec["name"]; rec["name"] = fnm; c["methods"].append(rec)
        else: fn.append(rec)

    return {"classes":cl,"functions":fn,"imports":imps,"entry_points":_detect_entries(src,"go")}

# ---- Proto parser ----
_PROTO_SVC = re.compile(r'service\s+(\w+)\s*\{')
_PROTO_RPC = re.compile(r'rpc\s+(\w+)\s*\((\w+)\)\s*returns\s*\((\w+)\)', re.MULTILINE)

def _proto_parse(src: str) -> Dict:
    cl, eps = [], []
    for sm in _PROTO_SVC.finditer(src):
        bs = sm.end()-1; body = src[bs+1:_skip_braces(src,bs)]; mtds = []
        for rm in _PROTO_RPC.finditer(body):
            mtds.append({"name":rm.group(1),"return_type":rm.group(3),
                         "parameters":[{"name":"request","type":rm.group(2)}],"annotations":[]})
        cl.append({"class_name":sm.group(1),"modifiers":[],"annotations":[],"extends":None,
                    "implements":[],"methods":mtds,"fields":[],"dependencies":[]})
    for rm in _PROTO_RPC.finditer(src):
        eps.append({"file":"","line":0,"type":"rpc","method":None,
                    "path":rm.group(1),"handler":rm.group(1),"annotations":[]})
    return {"classes":cl,"functions":[],"imports":[],"entry_points":eps}

# ---- C/C++ parser ----
_CPP_INC = re.compile(r'#include\s+[<"]([^>"]+)[>"]')
_CPP_FN = re.compile(r'(?:virtual\s+)?(?:static\s+)?(?:inline\s+)?'
                     r'(?:const\s+)?(?:unsigned\s+)?(?:signed\s+)?'
                     r'[\w:<>*&\s]+?\s+(\w+)\s*\(([^)]*)\)\s*(?:const\s*)?\{?',
                     re.MULTILINE)
_CPP_CLS = re.compile(r'(?:class|struct)\s+(\w+)(?:\s*:\s*(?:public|private|protected)\s+([\w:<>,\s]+?))?\s*\{')
_CPP_KW = {"if","else","for","while","do","switch","case","default","try","catch",
           "throw","return","new","delete","sizeof","typedef","using","namespace",
           "template","typename","class","struct","enum","union","public","private",
           "protected","virtual","static","const","inline","explicit","friend",
           "operator","void","bool","char","short","int","long","float","double",
           "auto","extern","volatile","mutable","register","signed","unsigned"}

def _cpp_parse(src: str) -> Dict:
    clean = _strip_cmts(src)
    cl, fn, imps = [], [], [m.group(1) for m in _CPP_INC.finditer(clean)]

    for cm in _CPP_CLS.finditer(clean):
        bs = cm.end() - 1
        be = _skip_braces(clean, bs)
        body = clean[bs+1:be]
        mtds = []
        for fm in _CPP_FN.finditer(body):
            nm = fm.group(1)
            if nm not in _CPP_KW:
                mtds.append({"name": nm, "return_type": None,
                            "parameters": [{"name": p.strip().split()[-1] if p.strip() else "",
                                           "type": " ".join(p.strip().split()[:-1]) if len(p.strip().split())>1 else None}
                                          for p in (fm.group(2) or "").split(",") if p.strip()],
                            "annotations": [], "dependencies": []})
        cl.append({"class_name": cm.group(1), "modifiers": [],
                   "annotations": [], "extends": cm.group(2).strip() if cm.group(2) else None,
                   "implements": [], "methods": mtds, "fields": [], "dependencies": []})

    for fm in _CPP_FN.finditer(clean):
        nm = fm.group(1)
        if nm not in _CPP_KW:
            already_in_class = any(nm in [m["name"] for m in c["methods"]] for c in cl)
            if not already_in_class:
                fn.append({"name": nm, "return_type": None,
                          "parameters": [{"name": p.strip().split()[-1] if p.strip() else "",
                                         "type": " ".join(p.strip().split()[:-1]) if len(p.strip().split())>1 else None}
                                        for p in (fm.group(2) or "").split(",") if p.strip()],
                          "annotations": [], "dependencies": []})

    return {"classes": cl, "functions": fn, "imports": imps, "entry_points": _detect_entries(src, "cpp")}

# ---- FallbackLexer ----
_FB_CLS = re.compile(r'(?:class|interface|struct|enum)\s+(\w+)', re.MULTILINE)
_FB_FN = re.compile(r'(?:def|func|fn|fun|function|sub)\s+(\w+)\s*\(', re.MULTILINE)

def _fb_parse(src: str) -> Dict:
    cl = [{"class_name":m.group(1),"modifiers":[],"annotations":[],"extends":None,
           "implements":[],"methods":[],"fields":[],"dependencies":[]} for m in _FB_CLS.finditer(src)]
    fn = [{"name":m.group(1),"return_type":None,"parameters":[],"annotations":[]} for m in _FB_FN.finditer(src)]
    return {"classes":cl,"functions":fn,"imports":[],"entry_points":[]}

# ---- Entry point detection ----
_EP_PATS: List[Tuple[re.Pattern, str, Optional[str], Optional[int]]] = [
    (re.compile(r'@GetMapping\s*\(\s*(?:value\s*=)?\s*["\']([^"\']+)',re.I),"http","GET",1),
    (re.compile(r'@PostMapping\s*\(\s*(?:value\s*=)?\s*["\']([^"\']+)',re.I),"http","POST",1),
    (re.compile(r'@PutMapping\s*\(\s*(?:value\s*=)?\s*["\']([^"\']+)',re.I),"http","PUT",1),
    (re.compile(r'@DeleteMapping\s*\(\s*(?:value\s*=)?\s*["\']([^"\']+)',re.I),"http","DELETE",1),
    (re.compile(r'@PatchMapping\s*\(\s*(?:value\s*=)?\s*["\']([^"\']+)',re.I),"http","PATCH",1),
    (re.compile(r'@RequestMapping\s*\([^)]*?(?:value|path)\s*=\s*["\']([^"\']+)',re.I),"http",None,1),
    (re.compile(r'@\w+\.route\s*\(\s*["\']([^"\']+)',re.I),"http",None,1),
    (re.compile(r'@router\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)',re.I),"http",None,2),
    (re.compile(r'@(?:Get|Post|Put|Delete|Patch)\s*\(\s*["\']([^"\']+)',re.I),"http",None,1),
    (re.compile(r'@Controller\s*\(\s*["\']([^"\']*)',re.I),"http",None,1),
    (re.compile(r'(?:router|app)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)',re.I),"http",None,2),
    (re.compile(r'(?:router|e|r|c)\.(GET|POST|PUT|DELETE|PATCH)\s*\(\s*["\']([^"\']+)',re.I),"http",None,2),
    (re.compile(r'rpc\s+(\w+)\s*\((\w+)\)',re.I),"rpc",None,1),
    (re.compile(r'@(?:Query|Mutation)\s*\(\s*',re.I),"http",None,None),
    (re.compile(r'@Resolver\s*\(',re.I),"http",None,None),
    (re.compile(r'@click\.command|@cli\.command\s*\(',re.I),"cli",None,None),
    (re.compile(r'@Command\s*\(\s*\{',re.I),"cli",None,None),
    (re.compile(r'\.add_parser\s*\(\s*["\'](\w+)',re.I),"cli",None,1),
    (re.compile(r'cobra\.Command\s*\{',re.I),"cli",None,None),
    (re.compile(r'@EventHandler|@EventListener|@Subscribe|@RabbitListener|@KafkaListener',re.I),"event",None,None),
    (re.compile(r'\.on\s*\(\s*["\'](\w+)',re.I),"event",None,1),
    (re.compile(r'@Scheduled\s*\(|@Cron\s*\(|@Schedule\s*\(',re.I),"cron",None,None),
    (re.compile(r'@SubscribeMessage\s*\(\s*["\']([^"\']+)',re.I),"event",None,1),
]

def _detect_entries(src: str, lang: str = "unknown", fp: str = "") -> List[Dict]:
    if lang is None:
        lang = "unknown"
    # Detect class-level @RequestMapping for path prefix
    class_prefix = ""
    cls_req = re.search(r'@RequestMapping\s*\(\s*["\']([^"\']+)["\']', src[:2000])
    if cls_req:
        class_prefix = cls_req.group(1).rstrip('/')
    entries, lines = [], src.split('\n')
    for ln, line in enumerate(lines, 1):
        for pat, etype, method_or, pidx in _EP_PATS:
            m = pat.search(line)
            if not m: continue
            path, meth = None, method_or
            if pidx and pidx <= (m.lastindex or 0): path = m.group(pidx)
            entry: Dict = {"file":fp,"line":ln,"type":etype,"method":meth,"path":path,"handler":"","annotations":[]}
            if not entry["method"] and etype == "http":
                for v in ("GET","POST","PUT","DELETE","PATCH"):
                    if v.lower() in pat.pattern.lower(): entry["method"] = v; break
            # resolve handler
            # try next line first (annotations precede their handler), then current
            for hl in [lines[ln] if ln < len(lines) else "", line]:
                if lang == "python": hm = re.search(r'def\s+(\w+)\s*\(', hl)
                elif lang == "go": hm = re.search(r'func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(', hl)
                elif lang in ("typescript","javascript"): hm = re.search(r'(?:function|async)\s+(\w+)\s*\(', hl)
                else: hm = re.search(r'(\w+)\s*\(', hl)
                if hm: entry["handler"] = hm.group(1); break
            if class_prefix and entry.get("path"):
                ep = entry["path"]
                if not ep.startswith('/'):
                    ep = '/' + ep
                if not ep.startswith(class_prefix):
                    ep = class_prefix.rstrip('/') + ep
                entry["path"] = ep
            entries.append(entry); break
    return entries

# ========================================================================
# Main parse dispatch
# ========================================================================

def parse_file(file_path: str) -> Dict:
    if not os.path.isfile(file_path):
        return {"file_path":file_path,"hash":"","language":"unknown","error":"File not found",
                "symbols":{"classes":[],"imports":[],"entry_points":[]}}
    sfx = Path(file_path).suffix.lower()
    lang = EXT_MAP.get(sfx, "unknown")
    sz = os.path.getsize(file_path) / (1024*1024)
    fh = _hash(file_path)
    if sz > REPO_BREAKER_MAX_FILE_SIZE_MB:
        return {"file_path":file_path,"hash":fh,"language":lang,
                "warning":f"File too large ({sz:.1f}MB)","symbols":{"classes":[],"imports":[],"entry_points":[]}}
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f: src = f.read()
        if sfx == ".py": syms = _py_parse(src, file_path)
        elif sfx == ".java": syms = _j_parse(src)
        elif sfx in (".ts",".tsx",".js",".jsx"):
            try: syms = _ts_parse(src, lang or "typescript")
            except Exception as exc:
                logger.info("TS/JS parse error %s: %s, fallback", file_path, exc)
                syms = _fb_parse(src)
        elif sfx == ".go": syms = _g_parse(src)
        elif sfx == ".proto": syms = _proto_parse(src)
        elif sfx in (".cpp", ".c", ".h", ".hpp"): syms = _cpp_parse(src)
        else:
            logger.info("No parser for %s, fallback", file_path)
            syms = _fb_parse(src)
        for ep in syms.get("entry_points",[]):
            if not ep.get("file"): ep["file"] = file_path
        return {"file_path":file_path,"hash":fh,"language":lang,"symbols":syms}
    except SyntaxError:
        logger.warning("Syntax error in %s, fallback", file_path)
        with open(file_path, "r", encoding="utf-8", errors="replace") as f: src = f.read()
        return {"file_path":file_path,"hash":fh,"language":lang,"source":"fallback","symbols":_fb_parse(src)}
    except Exception as exc:
        logger.error("Parse error %s: %s", file_path, exc)
        return {"file_path":file_path,"hash":fh,"language":lang,"error":str(exc),
                "error_code":50005,"symbols":{"classes":[],"imports":[],"entry_points":[]}}

# ========================================================================
# Module discovery
# ========================================================================

def discover_modules(ws_dir: str) -> Dict:
    if not os.path.isdir(ws_dir):
        print(json.dumps({"error":f"Directory not found: {ws_dir}"}, ensure_ascii=False)); sys.exit(1)
    ipats = _load_xoderignore(ws_dir)
    af, mm = [], defaultdict(list)
    for root, dirs, files in os.walk(ws_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.') and not _exclude(d,True)
                     and not _ign_match(d, ipats, True)]
        rel_root = os.path.relpath(root, ws_dir)
        if rel_root == ".": rel_root = ""
        for f in files:
            if f.startswith('.') or _exclude(f,False) or _ign_match(f, ipats, False): continue
            if Path(f).suffix.lower() not in CODE_EXTENSIONS: continue
            rp = os.path.join(rel_root, f).replace('\\','/')
            af.append(rp)
            parts = rp.split('/')
            mn = "root" if len(parts)==1 else parts[0] if len(parts)==2 else f"{parts[0]}/{parts[1]}"
            mm[mn].append(rp)
            if len(af) >= REPO_BREAKER_MAX_FILES: break
        if len(af) >= REPO_BREAKER_MAX_FILES: break
    if len(mm) > REPO_BREAKER_MAX_MODULES:
        sm = sorted(mm.items(), key=lambda x: len(x[1]), reverse=True)
        mm = dict(sm[:REPO_BREAKER_MAX_MODULES])
    # Auto-subdivide large modules (80K+ line projects)
    refined = {}
    for mod_name, files in mm.items():
        if len(files) > 30:
            subgroups = defaultdict(list)
            for f in files:
                parts = f.split('/')
                if len(parts) >= 4:
                    sub = f"{parts[0]}/{parts[1]}/{parts[2]}"
                elif len(parts) >= 3:
                    sub = f"{parts[0]}/{parts[1]}"
                else:
                    sub = mod_name
                subgroups[sub].append(f)
            for sub, sub_files in subgroups.items():
                refined[sub] = sub_files
        else:
            refined[mod_name] = files
    mm = refined
    if not af:
        print(json.dumps({"error":"No code files found"}, ensure_ascii=False)); sys.exit(0)
    mods = []
    for mn, mf in sorted(mm.items()):
        lgs = list({EXT_MAP.get(Path(x).suffix.lower(),"unknown") for x in mf})
        mods.append({"name":mn,"dir":mn.replace('/','\\') if mn!="root" else ".",
                     "files":sorted(mf),"file_count":len(mf),"languages":lgs})
    return {"modules":mods,"total_files":len(af),"module_count":len(mods)}

# ========================================================================
# Call graph
# ========================================================================

def build_callgraph(ws_dir: str) -> Dict:
    disc = discover_modules(ws_dir)
    af = []
    for m in disc.get("modules",[]):
        for f in m.get("files",[]): af.append(os.path.join(ws_dir, f))
    nodes, edges, sids, sedges, perr = [], [], set(), set(), []
    
    def _process_file(fp):
        try:
            res = parse_file(fp)
            if res.get("error"):
                return None, fp, True
            return res, fp, False
        except Exception:
            return None, fp, True
    
    results = []
    with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 4))) as executor:
        futures = {executor.submit(_process_file, fp): fp for fp in af}
        for future in as_completed(futures):
            results.append(future.result())
    
    for res, fp, is_err in results:
        if is_err or res is None:
            perr.append(fp)
            continue
        rp = os.path.relpath(fp, ws_dir).replace('\\', '/')
        # Skip noise directories for large projects
        skip_patterns = ('native/', 'conf/db/migration/', 'node_modules/', 'vendor/', 
                        '.xoder/', '.xoder-local/', 'skills/', 'scripts/', 'dashboard/', '__pycache__/')
        if any(rp.startswith(p) or ('/' + p) in rp for p in skip_patterns):
            continue
        res = parse_file(fp)
        if res.get("error"): perr.append(fp); continue
        rp = os.path.relpath(fp, ws_dir).replace('\\','/')
        fid = f"file:{rp}"
        if fid not in sids: sids.add(fid); nodes.append({"id":fid,"type":"file","name":rp,"file_path":rp})
        syms = res.get("symbols",{})
        for imp in syms.get("imports",[]):
            ek = (fid, imp, "IMPORTS")
            if ek not in sedges: sedges.add(ek); edges.append({"source":fid,"target":imp,"type":"IMPORTS"})
        for cls in syms.get("classes",[]):
            cn = cls.get("class_name",""); cid = f"class:{rp}::{cn}"
            if cid not in sids: sids.add(cid); nodes.append({"id":cid,"type":"class","name":cn,"file_path":rp})
            if cls.get("extends") and cls["extends"] != "None":
                ek = (cid, cls["extends"], "INHERITS")
                if ek not in sedges: sedges.add(ek); edges.append({"source":cid,"target":cls["extends"],"type":"INHERITS"})
            for im in cls.get("implements",[]):
                im = im.strip()
                if im:
                    ek = (cid, im, "IMPLEMENTS")
                    if ek not in sedges: sedges.add(ek); edges.append({"source":cid,"target":im,"type":"IMPLEMENTS"})
            for dep in cls.get("dependencies",[]):
                ek = (cid, dep, "DEPENDS_ON")
                if ek not in sedges: sedges.add(ek); edges.append({"source":cid,"target":dep,"type":"DEPENDS_ON"})
            for mt in cls.get("methods",[]):
                mn = mt.get("name",""); mid = f"function:{rp}::{cn}.{mn}"
                if mid not in sids: sids.add(mid); nodes.append({"id":mid,"type":"function","name":f"{cn}.{mn}","file_path":rp})
                for dep in mt.get("dependencies",[]):
                    ek = (mid, dep, "CALLS")
                    if ek not in sedges: sedges.add(ek); edges.append({"source":mid,"target":dep,"type":"CALLS"})
        for fn in syms.get("functions",[]):
            fnm = fn.get("name",""); fid2 = f"function:{rp}::{fnm}"
            if fid2 not in sids: sids.add(fid2); nodes.append({"id":fid2,"type":"function","name":fnm,"file_path":rp})
            for dep in fn.get("dependencies",[]):
                ek = (fid2, dep, "CALLS")
                if ek not in sedges: sedges.add(ek); edges.append({"source":fid2,"target":dep,"type":"CALLS"})
    # Inject Spring DI virtual edges
    di_path = os.path.join(ws_dir, ".xoder-local", "stage", "spring_di_mapping.json")
    if os.path.isfile(di_path):
        try:
            di_data = json.load(open(di_path, "r", encoding="utf-8"))
            for ve in di_data.get("virtual_edges", []):
                src_fp = ve["source_file"]
                tgt_fp = ve["target_file"]
                src_id = f"file:{src_fp}"
                tgt_id = f"file:{tgt_fp}"
                ek = (src_id, tgt_id, "SPRING_DI")
                if ek not in sedges:
                    sedges.add(ek)
                    edges.append({"source": src_id, "target": tgt_id, "type": "SPRING_DI",
                                  "field": ve.get("field_name", ""), "actual_class": ve.get("actual_class", "")})
        except Exception:
            pass
    return {"nodes":nodes,"edges":edges,"node_count":len(nodes),"edge_count":len(edges),
            "parse_errors":perr if perr else None}

# ========================================================================
# Call chain tracing
# ========================================================================

def _is_terminal(cn: str, mn: str) -> bool:
    if mn.lower() in TERM_METHODS: return True
    if any(cn.endswith(s) for s in TERM_SUFFIXES): return True
    return False

def _guess_table(cn: str) -> str:
    for s in TERM_SUFFIXES:
        if cn.endswith(s): cn = cn[:-len(s)]; break
    r = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', cn)
    r = re.sub(r'([a-z\d])([A-Z])', r'\1_\2', r)
    return "t_" + r.lower()

def trace_call_chains(ws_dir: str, entry_file: str) -> Dict:
    if not os.path.isfile(entry_file): return {"error":f"Entry file not found: {entry_file}"}
    with open(entry_file, "r", encoding="utf-8") as f: ed = json.load(f)
    eps = ed.get("entry_points", ed)
    if not isinstance(eps, list): eps = [eps]
    cg = build_callgraph(ws_dir)
    n2n: Dict[str, List[str]] = defaultdict(list)
    for nd in cg.get("nodes",[]): n2n[nd["name"]].append(nd["id"])
    ebs: Dict[str, List[Dict]] = defaultdict(list)
    for e in cg.get("edges",[]):
        if e.get("type") == "CALLS": ebs[e["source"]].append(e)
    
    # Load import_map for cross-reference resolution
    import_map = None
    im_path = os.path.join(ws_dir, ".xoder-local", "stage", "import_map.json")
    if os.path.isfile(im_path):
        try:
            with open(im_path, "r", encoding="utf-8") as f:
                import_map = json.load(f).get("importMap", {})
        except Exception:
            pass
    
    def _resolve_via_imports(target: str, caller_file: str) -> List[str]:
        """Use import_map to find target definition in imported files."""
        if not import_map or not caller_file:
            return []
        # Find files imported by the caller
        caller_rp = caller_file.replace('\\', '/')
        imported = []
        for k, v in import_map.items():
            if k.replace('\\', '/') == caller_rp:
                imported = v
                break
        if not imported:
            return []
        method = target.rsplit('.', 1)[-1] if '.' in target else target
        results = []
        for imp_file in imported:
            imp_rp = imp_file.replace('\\', '/')
            for fnm, ids in n2n.items():
                if imp_rp in fnm and fnm.endswith(f".{method}"):
                    results.extend(ids)
        return results
    
    def _fuzzy_n2n(target: str) -> List[str]:
        """Match target name in n2n: exact → ends-with → contains → method-only."""
        direct = n2n.get(target, [])
        if direct: return direct
        results = []
        for fnm, ids in n2n.items():
            if fnm == target or fnm.endswith(f".{target}") or fnm.endswith(f"::{target}"):
                results.extend(ids)
        if results: return results
        # Step 3: word-level contains
        for fnm, ids in n2n.items():
            if f".{target}" in fnm or fnm.endswith(target):
                results.extend(ids)
        if results: return results
        # Step 4: method-name-only match (e.g. clientService.getClientById → *.getClientById)
        if '.' in target:
            method = target.rsplit('.', 1)[-1]
            for fnm, ids in n2n.items():
                if fnm.endswith(f".{method}") or fnm.endswith(f"::{method}"):
                    results.extend(ids)
        return results
    
    chains = []
    for ep in eps:
        h = ep.get("handler","")
        if not h: continue
        snodes = n2n.get(h, [])
        if not snodes:
            for fnm, ids in n2n.items():
                if fnm.endswith(f".{h}") or fnm == h: snodes = ids; break
        if not snodes:
            for fnm, ids in n2n.items():
                if f".{h}" in fnm or fnm.endswith(h): snodes = ids; break
        for sn in snodes:
            visited = set(); q = [(sn, [sn.rsplit("::",1)[-1]], [], [])]
            while q:
                cur, path, anns, tbls = q.pop(0)
                if cur in visited or len(path) > 15 or len(visited) >= 500:
                    if cur not in visited and len(visited) < 500:
                        visited.add(cur)
                    continue
                visited.add(cur)
                np = path[-1].split(".")
                cn = ".".join(np[:-1]) if len(np)>1 else ""
                mn = np[-1] if np else ""
                if _is_terminal(cn, mn):
                    t = _guess_table(cn)
                    if t not in tbls: tbls = tbls + [t]
                    chains.append({"entry":ep,"path":path,"annotations_found":anns,"terminal_tables":tbls}); continue
                for ne in ebs.get(cur, []):
                    target_name = ne.get("target", "")
                    matched = _fuzzy_n2n(target_name)
                    # Fallback: use import_map to resolve via imported files
                    if not matched and import_map:
                        caller_file = cur.split("::", 1)[0].replace("function:", "")
                        matched = _resolve_via_imports(target_name, caller_file)
                    for tn in matched:
                        q.append((tn, path + [tn.rsplit("::",1)[-1]], anns[:], tbls[:]))
    if not chains:
        for ep in eps:
            h = ep.get("handler", "")
            if not h:
                continue
            chains.append({
                "entry": ep,
                "path": [h],
                "annotations_found": [],
                "terminal_tables": [],
                "trace_note": "no_call_edges_available",
            })
    return {"call_chains":chains,"chain_count":len(chains)}

# ========================================================================
# High-level API wrappers (called by .md skill files)
# ========================================================================

ENTRY_PATTERNS = [
    (r'@(?:GetMapping|PostMapping|PutMapping|DeleteMapping|RequestMapping)\s*\(\s*["\']([^"\']+)["\']', 'http', 'Spring'),
    (r'@(?:app|router)\.(?:route|get|post|put|patch|delete)\s*\(\s*["\']([^"\']+)["\']', 'http', 'Flask/FastAPI'),
    (r'(?:app|router|server)\.(?:get|post|put|patch|delete|all|use)\s*\(\s*["\']([^"\']+)["\']', 'http', 'Express/Koa'),
    (r'@(?:Get|Post|Put|Patch|Delete|All)\s*\(\s*["\']([^"\']+)["\']', 'http', 'NestJS'),
    (r'router\.(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s*\(\s*["\']([^"\']+)["\']', 'http', 'Gin/Echo'),
    (r'@(?:Query|Mutation|Subscription|Resolver)\s*\(', 'http', 'GraphQL'),
    (r'^service\s+(\w+)', 'rpc', 'gRPC'),
    (r'@click\.(?:command|group)\s*\(', 'cli', 'Click'),
    (r'@Command\s*\(\s*["\']([^"\']+)["\']', 'cli', 'Cobra'),
    (r'\.add_parser\s*\(\s*["\']([^"\']+)["\']', 'cli', 'Argparse'),
    (r'@(?:EventHandler|Subscribe|Listener)\s*\(', 'event', 'Event'),
    (r'\.on\s*\(\s*["\']([^"\']+)["\']', 'event', 'Listener'),
    (r'@(?:Cron|Schedule|Scheduled)\s*\(\s*["\']([^"\']+)["\']', 'cron', 'Cron'),
    (r'@PatchMapping\s*\(\s*["\']([^"\']+)["\']', 'http', 'Spring'),
    (r'@Bean\s*(?:\([^)]*\))?', 'config', 'Spring Bean'),
    (r'@EventListener\s*(?:\([^)]*\))?', 'event', 'Spring Event'),
    (r'@MessageMapping\s*\(\s*["\']([^"\']+)["\']', 'http', 'Spring WebSocket'),
]

def discover_entry_points(workspace_dir: str) -> dict:
    """Detect all entry points (HTTP routes, RPC, CLI, events) in workspace."""
    if not os.path.isdir(workspace_dir):
        return {"error": f"Directory not found: {workspace_dir}", "entry_points": [], "total": 0, "by_type": {}}
    disc = discover_modules(workspace_dir)
    all_entries = []
    for m in disc.get("modules", []):
        for f in m.get("files", []):
            res = parse_file(os.path.join(workspace_dir, f))
            all_entries.extend(res.get("symbols", {}).get("entry_points", []))
    by_type = defaultdict(int)
    for ep in all_entries:
        t = ep.get("type", "unknown")
        if t:
            by_type[t] += 1
    return {"entry_points": all_entries, "total": len(all_entries), "by_type": dict(by_type)}

def trace_from_entries(workspace_dir: str, entry_points_file: str) -> dict:
    """Trace call chains from entry points JSON file down to terminal DB operations."""
    if not os.path.isfile(entry_points_file):
        return {"error": f"Entry file not found: {entry_points_file}"}
    ep_path = entry_points_file
    if not os.path.isabs(ep_path):
        ep_path = os.path.join(workspace_dir, ep_path)
    if not os.path.isfile(ep_path):
        ep_path = os.path.abspath(entry_points_file)
    result = trace_call_chains(workspace_dir, ep_path)
    cg = build_callgraph(workspace_dir)
    cross_module = 0
    for chain in result.get("call_chains", []):
        paths = chain.get("path", [])
        modules = set()
        for p in paths:
            parts = p.split(".")
            if len(parts) > 1:
                modules.add(parts[0])
        if len(modules) > 1:
            cross_module += 1
            chain["cross_module"] = True
        else:
            chain["cross_module"] = False
    result["graph_stats"] = {
        "node_count": cg.get("node_count", 0),
        "edge_count": cg.get("edge_count", 0),
        "total_chains": len(result.get("call_chains", [])),
        "cross_module_chains": cross_module,
    }
    return result

def extract_symbols(workspace_dir: str, file_paths: list) -> dict:
    """Extract symbol skeletons from specific files."""
    results = {}
    for fp in file_paths:
        full_path = os.path.join(workspace_dir, fp) if not os.path.isabs(fp) else fp
        results[fp] = parse_file(full_path)
    return results

# ========================================================================
# CLI
# ========================================================================

def main():
    ap = argparse.ArgumentParser(prog="ast_parser",
        description="Multi-language AST symbol extractor and code topology analyzer")
    ap.add_argument("--mode", required=True,
                    choices=["discover","symbols","entry","callgraph","trace"])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--files", default=None, help="Comma-separated file list (for symbols)")
    ap.add_argument("--entry", default=None, help="Entry-points JSON file (for trace)")
    ap.add_argument("--pretty", action="store_true", default=False)
    args = ap.parse_args()
    ws = os.path.abspath(args.workspace)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    if args.mode == "discover":
        result = discover_modules(ws)
    elif args.mode == "symbols":
        if not args.files:
            print(json.dumps({"error":"--files required"}, ensure_ascii=False)); sys.exit(1)
        fps = [os.path.join(ws, x.strip()) for x in args.files.split(",")]
        result = {"files":[parse_file(fp) for fp in fps],"count":len(fps)}
    elif args.mode == "entry":
        disc = discover_modules(ws); ae = []
        for m in disc.get("modules",[]):
            for f in m.get("files",[]):
                ae.extend(parse_file(os.path.join(ws, f)).get("symbols",{}).get("entry_points",[]))
        result = {"entry_points":ae,"count":len(ae)}
    elif args.mode == "callgraph":
        result = build_callgraph(ws)
    elif args.mode == "trace":
        if not args.entry:
            print(json.dumps({"error":"--entry required"}, ensure_ascii=False)); sys.exit(1)
        result = trace_call_chains(ws, os.path.abspath(args.entry))
    else:
        print(json.dumps({"error":f"Unknown mode: {args.mode}"}, ensure_ascii=False)); sys.exit(1)

    indent = 2 if args.pretty else None
    print(json.dumps(result, ensure_ascii=False, indent=indent, default=str))

def detect_architecture_pattern(workspace_dir: str) -> dict:
    """Detect the architecture pattern (DDD/MVC/Hexagonal/Layered) from code graph topology.
    
    Strategy:
    1. Discover all modules and their files
    2. Parse each file for annotations and imports
    3. Classify each file by annotation pattern:
       - @RestController/@Controller/@GetMapping/@PostMapping → "controller"
       - @Service/@Component/@Injectable → "service"  
       - @Repository/@Mapper/@DAO → "repository"
       - @Entity/@Document/@Table → "domain"
       - @Configuration → "config"
    4. Analyze import direction: controller imports service? service imports repository? etc.
    5. Check directory naming: domain/application/infrastructure → DDD; ports/adapters → Hexagonal
    6. Classify the overall pattern
    
    Returns: {
        "pattern": "MVC" | "DDD" | "Hexagonal" | "Layered" | "Unknown",
        "confidence": 0.0-1.0,
        "layers": {
            "controller": {"files": [...], "count": int},
            "service": {"files": [...], "count": int},
            "repository": {"files": [...], "count": int},
            "domain": {"files": [...], "count": int},
            "config": {"files": [...], "count": int}
        },
        "analysis": {
            "total_files": int,
            "classified_files": int,
            "unclassified_files": int,
            "import_direction": "controller→service→repository" | "domain←application→infrastructure" | ...,
            "directory_hints": ["domain/", "application/", "infrastructure/"] | ["controllers/", "services/", "models/"] | [],
            "fan_analysis": {"high_fan_in_modules": [...], "high_fan_out_modules": [...]}
        },
        "recommendation": "建议在 architecture.md 中按 {pattern} 架构描述本项目"
    }
    """
    import json as _json
    from pathlib import Path as _Path
    from collections import Counter, defaultdict
    
    root = _Path(workspace_dir)
    
    # 1. Discover modules
    modules_result = discover_modules(workspace_dir)
    all_modules = modules_result.get("modules", [])
    
    # 2. Annotation-based layer classification
    layer_patterns = {
        "controller": [
            "RestController", "Controller", "GetMapping", "PostMapping", "PutMapping", 
            "DeleteMapping", "RequestMapping", "@Get(", "@Post(", "@Put(", "@Delete(",
            "router.get", "router.post", "app.get", "app.post", "router.GET", "router.POST"
        ],
        "service": [
            "Service", "Component", "Injectable", "@UseCase", "UseCase", 
            "Interactor", "Handler"
        ],
        "repository": [
            "Repository", "Mapper", "DAO", "Dao", "JpaRepository",
            "MongoRepository", "CrudRepository"
        ],
        "domain": [
            "Entity", "Document", "Table", "@Model", "AggregateRoot",
            "ValueObject", "Domain", "prisma.model"
        ],
        "config": [
            "Configuration", "Config", "@Bean", "@Module", "Properties"
        ]
    }
    
    layers = {k: {"files": [], "count": 0} for k in layer_patterns}
    classified = set()
    
    for module in all_modules:
        for file_path in module.get("files", []):
            # Skip noise directories
            rp_norm = file_path.replace('\\', '/')
            if any(rp_norm.startswith(nd) for nd in ('scripts/', 'skills/', 'dashboard/', '.xoder/', '.xoder-local/')):
                continue
            abs_path = root / file_path
            if not abs_path.exists():
                continue
            try:
                content = abs_path.read_text(encoding='utf-8', errors='replace')
                ext = os.path.splitext(file_path)[1].lower()
                if ext not in (".java", ".py", ".go", ".ts", ".tsx", ".js", ".jsx",
                              ".cpp", ".c", ".h", ".hpp", ".cs", ".kt", ".swift", ".rb", ".php"):
                    continue
                file_classified = False
                for layer, patterns in layer_patterns.items():
                    for pat in patterns:
                        if pat.lower() in content.lower():
                            layers[layer]["files"].append(file_path)
                            layers[layer]["count"] += 1
                            classified.add(file_path)
                            file_classified = True
                            break
                    if file_classified:
                        break
            except Exception:
                pass
    
    total = sum(len(m.get("files", [])) for m in all_modules)
    unclassified = total - len(classified)
    
    # 3. Analyze directory naming hints
    dir_hints = []
    for module in all_modules:
        dir_name = module.get("dir", "").lower()
        module_name = module.get("name", "").lower()
        if any(d in dir_name for d in ["domain", "model", "entity", "entities"]):
            dir_hints.append(f"{dir_name} (domain)")
        if any(d in dir_name for d in ["application", "usecase", "use_case", "interactor"]):
            dir_hints.append(f"{dir_name} (application)")
        if any(d in dir_name for d in ["infrastructure", "infra", "persistence", "repository", "dao"]):
            dir_hints.append(f"{dir_name} (infrastructure)")
        if any(d in dir_name for d in ["interface", "api", "controller", "handler", "router", "presentation"]):
            dir_hints.append(f"{dir_name} (interface)")
        if any(d in dir_name for d in ["port", "adapter", "gateway"]):
            dir_hints.append(f"{dir_name} (port/adapter)")
    
    # 4. Determine pattern
    has_controller = layers["controller"]["count"] > 0
    has_service = layers["service"]["count"] > 0
    has_repository = layers["repository"]["count"] > 0
    has_domain = layers["domain"]["count"] > 0
    
    ddd_dirs = any("domain" in d.lower() and "application" in d.lower() and "infrastructure" in d.lower() for d in dir_hints)
    hex_dirs = any("port" in d.lower() or "adapter" in d.lower() for d in dir_hints)
    mvc_dirs = any("controller" in d.lower() or "service" in d.lower() or "model" in d.lower() for d in dir_hints)
    
    pattern = "Unknown"
    confidence = 0.0
    import_direction = "unknown"
    
    if ddd_dirs and (has_domain or has_repository):
        pattern = "DDD (Domain-Driven Design)"
        confidence = 0.7
        import_direction = "interfaces → application → domain ← infrastructure"
    elif hex_dirs:
        pattern = "Hexagonal (Ports & Adapters)"
        confidence = 0.65
        import_direction = "adapters → ports ← domain"
    elif has_controller and has_service:
        if has_repository or has_domain:
            pattern = "MVC (Model-View-Controller) / 三层架构"
            confidence = 0.75
            import_direction = "controller → service → repository"
        else:
            pattern = "Layered Architecture"
            confidence = 0.5
            import_direction = "上层 → 下层 (单向依赖)"
    elif has_service or has_repository:
        pattern = "Layered Architecture"
        confidence = 0.4
        import_direction = "推测为分层架构"
    
    # Boost confidence based on directory hints matching
    if pattern.startswith("DDD") and ddd_dirs:
        confidence = min(1.0, confidence + 0.15)
    if pattern.startswith("MVC") and mvc_dirs:
        confidence = min(1.0, confidence + 0.1)
    
    # Generate recommendation
    recommendations = {
        "DDD": "建议在 architecture.md 中按 DDD 四层架构 (interfaces/application/domain/infrastructure) 描述本项目各层的职责与依赖方向",
        "MVC": "建议在 architecture.md 中按 MVC 三层架构 (Controller/Service/Repository) 描述本项目的请求处理链路",
        "Hexagonal": "建议在 architecture.md 中按六边形架构 (ports/adapters) 描述本项目的端口-适配器模式",
        "Layered": "建议在 architecture.md 中按分层架构描述本项目的层级依赖关系",
        "Unknown": "建议在 architecture.md 中按模块分组描述本项目的组织结构"
    }
    
    rec_key = pattern.split(" ")[0] if pattern != "Unknown" else "Unknown"
    recommendation = recommendations.get(rec_key, recommendations["Unknown"])
    
    return {
        "pattern": pattern,
        "confidence": round(confidence, 2),
        "layers": layers,
        "analysis": {
            "total_files": total,
            "classified_files": len(classified),
            "unclassified_files": unclassified,
            "import_direction": import_direction,
            "directory_hints": dir_hints[:10],
            "fan_analysis": {"note": "Fan-in/out analysis requires full CodeGraph build; run --mode callgraph first"}
        },
        "recommendation": recommendation
    }


def build_import_map(workspace_dir: str) -> dict:
    """Build deterministic importMap: {file_path: [imported_file_paths]}.

    Borrowed from Understand-Anything's extract-import-map pattern.
    Pure static analysis, zero LLM cost. Every input file gets an entry.
    External packages (npm/pip/cargo) are dropped — only project-internal imports.

    Returns: {
        "importMap": {"src/foo.py": ["src/bar.py", "src/utils.py"], ...},
        "stats": {"files_scanned": int, "files_with_imports": int, "total_edges": int}
    }
    """
    import json as _json
    from pathlib import Path as _Path
    from collections import defaultdict as _dd

    root = _Path(workspace_dir).resolve()
    modules_data = discover_modules(workspace_dir)
    all_files = []
    for m in modules_data.get("modules", []):
        all_files.extend(m.get("files", []))

    # Build file set for resolving relative imports
    file_set = set(all_files)

    # Build suffix index for dotted-name resolution (Java/Kotlin/C#)
    suffix_index = _dd(list)
    for f in all_files:
        base = f.replace('\\', '/').rsplit('.', 1)[0]
        suffix_index[base.replace('/', '.')].append(f)
        parts = base.split('/')
        for i in range(len(parts)):
            suffix_index['.'.join(parts[i:])].append(f)

    import_map = {}
    files_scanned = 0
    files_with_imports = 0
    total_edges = 0

    NOISE = {'scripts', 'skills', 'dashboard', '.xoder', '.xoder-local'}

    for file_path in all_files:
        parts = file_path.replace('\\', '/').split('/')
        if parts[0] in NOISE:
            import_map[file_path] = []
            continue

        abs_path = root / file_path
        if not abs_path.exists():
            import_map[file_path] = []
            continue

        try:
            result = parse_file(str(abs_path))
        except Exception:
            import_map[file_path] = []
            continue

        files_scanned += 1
        syms = result.get("symbols", {})
        raw_imports = syms.get("imports", [])

        # Resolve imports to project file paths
        resolved = []
        file_dir = str(_Path(file_path).parent).replace('\\', '/')
        ext = _Path(file_path).suffix.lower()

        for imp in raw_imports:
            targets = []

            # Python-style imports (dotted or relative)
            if ext == '.py':
                if imp.startswith('.'):
                    # Relative import: walk up from file_dir
                    depth = len(imp) - len(imp.lstrip('.'))
                    rest = imp.lstrip('.')
                    up = file_dir
                    for _ in range(depth - 1):
                        up = '/'.join(up.split('/')[:-1]) if '/' in up else ''
                    candidate_base = f"{up}/{rest.replace('.', '/')}" if up else rest.replace('.', '/')
                else:
                    # Absolute dotted import: try suffix matching
                    candidate_base = imp.replace('.', '/')

                candidates = [
                    f"{candidate_base}.py",
                    f"{candidate_base}/__init__.py",
                ]
                for c in candidates:
                    c = c.lstrip('/')
                    if c in file_set:
                        targets.append(c)
                        break

            # Java-style imports (fully qualified dotted names)
            elif ext == '.java':
                # Try suffix index
                dotted = imp
                if dotted in suffix_index:
                    targets.extend(suffix_index[dotted][:1])
                else:
                    # Try path-based: com.x.y.Foo -> com/x/y/Foo.java
                    path_version = imp.replace('.', '/') + '.java'
                    if path_version in file_set:
                        targets.append(path_version)

            # TS/JS imports (filesystem paths)
            elif ext in ('.ts', '.tsx', '.js', '.jsx'):
                # import '...' is already a path
                clean = imp.lstrip('.').lstrip('/')
                if not clean.startswith('@') and '/' in clean:
                    # Relative path
                    candidate = f"{file_dir}/{clean}"
                    candidate = candidate.replace('\\', '/')
                    candidates = [candidate, candidate + '.ts', candidate + '.tsx',
                                 candidate + '.js', candidate + '/index.ts']
                    for c in candidates:
                        c = c.lstrip('/')
                        if c in file_set:
                            targets.append(c)
                            break

            # Go imports (package-level)
            elif ext == '.go':
                # Go imports reference packages, not files
                # Map package path to directory, find .go files there
                clean = imp.strip('"').split('/')[-1]  # last segment as package name
                # Too complex for minimal implementation, skip for now
                pass

            if targets:
                resolved.extend(targets)

        resolved = sorted(set(resolved))
        import_map[file_path] = resolved
        if resolved:
            files_with_imports += 1
            total_edges += len(resolved)

    return {
        "importMap": import_map,
        "stats": {
            "files_scanned": files_scanned,
            "files_with_imports": files_with_imports,
            "total_edges": total_edges
        }
    }


if __name__ == "__main__":
    main()
