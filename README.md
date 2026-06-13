# Xoder — Offline Self-Healing Code Repository Wiki Engine

> **X** → Cross-analysis / Multi-dimensional | **oder** → Order (German)  
> Cross-analyze code, bring order to repositories.

---

## What is Xoder?

Xoder is a **fully offline**, **self-healing** Wiki auto-generation system for code repositories. It performs deep static analysis on repositories of any scale (≤10,000 files, ≤200 modules), combines Git archaeology with multi-agent collaboration, and automatically produces high-quality technical Wiki knowledge bases — all without sending code to the cloud.

## Philosophy

| Principle | Description |
|-----------|-------------|
| **Fully Offline** | No external LLM APIs or cloud services. Code never leaves your machine. |
| **Multi-Agent Pipeline** | Qoder-style 4-stage agent collaboration: Plan → Concur → Stitch → Publish |
| **Self-Healing** | Incremental hash tracking reprocesses only changed parts. Supports reverse sync (human-edited docs → knowledge engine update). |
| **Business Semantics** | Extracts not just symbol skeletons but also business rules (constraints, calculations, state machines, business patterns) from method bodies. |
| **Multi-Language** | Python / Java / Go / TypeScript / JavaScript / C++ / Protobuf and more. |
| **Architecture-Aware** | Auto-detects MVC, microservices, layered architectures, etc. |
| **Git Archaeology** | Semantic washing + co-change coupling analysis + code hotspots + ADR reverse engineering. |
| **Bilingual Wiki** | Generates both English and Chinese documentation with cross-language linking. |

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│                     Xoder 4-Stage Pipeline                     │
├──────────┬────────────┬──────────────┬────────────────────────┤
│ Phase 1  │  Phase 2   │   Phase 3    │        Phase 4         │
│  Super   │   Domain   │  Alignment   │       Doc Agent        │
│  Planner │  Workers   │    Agent     │                        │
│          │     ×N     │              │                        │
│ ┌──────┐ │ ┌────────┐ │ ┌──────────┐ │ ┌────────────────────┐ │
│ │Domain │→│ │Per-Dom.│ │ │Cross-Dom │→│ │Chapter Gen+Polish │ │
│ │Slice  │ │ │Wiki    │→│ │Topology  │ │ │+Atomic Publish    │ │
│ └──────┘ │ └────────┘ │ └──────────┘ │ └────────────────────┘ │
└──────────┴────────────┴──────────────┴────────────────────────┘
         ↑                      ↑
    Phase 0.5 Archaeology   Phase 0.1 Knowledge Import
   (Static Analysis + Git)  (PDF / DOCX / HTML → MD)
```

## Project Structure

```
xoder/
├── scripts/                 # Python tool scripts
│   ├── xoder-cli.py         # CLI management (init/status/clean)
│   ├── xoder_entry.py       # Entry point detection
│   ├── xoder_arch.py        # Architecture pattern detection + module discovery
│   ├── xoder_orm.py         # ORM penetration (database schema inference)
│   ├── xoder_callchain.py   # Call chain tracing
│   ├── xoder_git.py         # Git archaeology (wash/co-change/ADR)
│   ├── xoder_importmap.py   # Import map builder
│   ├── xoder_invocations.py # Method invocation symbol card extraction
│   ├── xoder_match.py       # Cross-domain call-site matching
│   ├── xoder_outline.py     # Wiki TOC outline generator
│   ├── xoder_dbstatus.py    # Database status updater
│   ├── xoder_knowledge.py   # External knowledge import (PDF/DOCX/HTML→MD)
│   ├── xoder_spring.py      # Spring DI dependency inference
│   ├── ast_parser.py        # Multi-language AST parser engine
│   ├── git_timeline.py      # Git history analysis engine
│   ├── hash_tracker.py      # Hash tracker (incremental/reverse sync)
│   ├── mmdc_compiler.py     # Mermaid compiler gateway
│   ├── db_client.py         # SQLite database client
│   ├── token_gateway.py     # Token gateway
│   ├── git_operator.py      # Git operation wrapper
│   ├── config.py            # Global configuration center
│   └── setup_env.ps1        # Environment setup script
├── skills/                  # Agent skill definitions (for opencode)
│   ├── xoder-repowiki.md    # Main orchestration pipeline
│   ├── super-planner.md     # Phase 1: Domain slicing
│   ├── domain-worker.md     # Phase 2: Per-domain Wiki generation
│   ├── alignment-agent.md   # Phase 3: Topology stitching
│   ├── doc-agent.md         # Phase 4: Doc assembly & publish
│   └── knowledge-import.md  # Knowledge import guide
├── dashboard/               # Web dashboard
│   ├── server.py            # HTTP server (zero external dependencies)
│   ├── index.html           # SPA with Mermaid rendering
│   └── run_dashboard.ps1    # Launch script
├── tests/                   # Unit tests
│   ├── test_ast_parser.py
│   ├── test_git_timeline.py
│   └── test_hash_tracker.py
└── README.md
```

## Requirements

| Dependency | Version | Notes |
|------------|---------|-------|
| Python | >= 3.9 | Core runtime |
| (Optional) Node.js | >= 18 | Mermaid diagram rendering |
| (Optional) mmdc | latest | `npm install -g @mermaid-js/mermaid-cli` |
| (Optional) markitdown | latest | `pip install 'markitdown[all]'` for PDF/DOCX/XLSX import |

## Quick Start

### 1. Environment Setup

```bash
# Windows
powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1

# Linux / macOS
python3 -m pip install 'markitdown[all]'
```

### 2. Initialize Directory Structure

```bash
python scripts/xoder-cli.py init
```

### 3. Run the Full Pipeline

Full Wiki generation is driven by opencode agents guided by `skills/xoder-repowiki.md`. The static analysis phase runs independently:

```bash
# Phase 0.5: Archaeology — Static Analysis
python scripts/xoder_entry.py --workspace . --output .xoder-local/stage/entry_points.json
python scripts/xoder_orm.py --workspace . --output .xoder-local/stage/orm_data.json
python scripts/xoder_callchain.py --workspace . --entries .xoder-local/stage/entry_points.json --output .xoder-local/stage/call_chains.json
python scripts/xoder_arch.py --workspace . --output-arch .xoder-local/stage/architecture_pattern.json --output-modules .xoder-local/stage/super_planner_modules.json

# Phase 0.5: Archaeology — Git History
python scripts/xoder_git.py --workspace . --output .xoder-local/stage/git_archaeology.json

# Phase 0.1: Knowledge Import (if you have external docs)
python scripts/xoder_knowledge.py --workspace . --auto
```

### 4. Launch the Dashboard

```bash
python dashboard/server.py
# Open http://127.0.0.1:8920
```

### 5. CLI Management

```bash
python scripts/xoder-cli.py status   # View task status
python scripts/xoder-cli.py clean    # Clear cache
```

### 6. Incremental Updates

```bash
python scripts/hash_tracker.py --mode diff --workspace .
python scripts/hash_tracker.py --mode propagate --workspace .
```

## Generated Wiki Output

After the pipeline completes, the Wiki is generated under `.xoder/repowiki/`. Xoder supports **bilingual output** — both English and Chinese documentation with cross-language references:

```
.xoder/repowiki/
├── en/                          # English Wiki (primary)
│   └── content/
│       ├── README.md
│       ├── overview.md
│       ├── quickstart.md
│       ├── backend-architecture/
│       ├── api-docs/
│       ├── database-design/
│       ├── frontend-architecture/   # (if detected)
│       ├── testing-strategy/        # (if detected)
│       ├── development-guide.md
│       ├── deployment.md
│       ├── troubleshooting.md
│       ├── diagrams/
│       └── meta/
├── zh/                          # Chinese Wiki (alternate)
│   └── content/
│       ├── README.md                # 阅读指南
│       ├── 项目概述.md
│       ├── 快速开始.md
│       ├── 后端架构设计/
│       ├── API接口文档/
│       ├── 数据库设计/
│       ├── ...
│       └── diagrams/
├── wiki_sync_metadata.db        # Sync state database
    └── meta/
```

Each English page includes a cross-reference link to its Chinese counterpart and vice versa, allowing readers to switch languages seamlessly.

## Incremental & Reverse Sync

```bash
# Incremental update — only reprocess changed files
python scripts/hash_tracker.py --mode diff
python scripts/hash_tracker.py --mode propagate

# Reverse sync — human-edited Wiki changes → knowledge engine
python scripts/hash_tracker.py --mode watch
```

## Supported Languages

| Language | Parser | Coverage |
|----------|--------|----------|
| Python | Native AST | Full (classes/methods/calls/business rules) |
| Java | Regex AST | Full (including annotations / Spring semantics) |
| Go | Regex AST | Full (structs/interfaces/methods) |
| TypeScript/JavaScript | Regex AST | Full (classes/interfaces/arrow functions) |
| C/C++ | Regex AST | Class/method signatures |
| Protobuf | Regex AST | Service/RPC definitions |
| Other languages | Fallback Lexer | Class/method signatures |

## Configuration

Global configuration resides in `scripts/config.py`:

- **Repo Breaker**: 10,000 file limit, 200 module limit, 10 MB per-file limit
- **Language Routing**: 20+ file extension mappings
- **Git Washing**: Noise/signal regex patterns for commit filtering
- **Token Gateway**: Context window constraints and reduction ratios

## How Xoder Works with opencode

Xoder is designed as a set of **opencode skills** (`skills/*.md`) that orchestrate multi-agent pipelines. The `skills/xoder-repowiki.md` skill is the entry point that:

1. Invokes Python scripts for static analysis (Phase 0.5)
2. Dispatches **Super Planner** sub-agent for domain slicing (Phase 1)
3. Dispatches **N Domain Workers** in parallel for per-domain Wiki generation (Phase 2)
4. Dispatches **Alignment Agent** for cross-domain topology stitching (Phase 3)
5. Dispatches **Doc Agents** for final assembly and atomic publish (Phase 4)

Each agent loads its skill definition, runs required Python commands, and produces artifacts for the next stage.

## License

[MIT](LICENSE)

## Contributing

See `skills/xoder-repowiki.md` for pipeline design. PRs must include corresponding tests.

## Related Projects

- [opencode](https://opencode.ai) — The agent runtime that drives Xoder pipelines
- [DeepWiki](https://github.com/nicepkg/DeepWiki) — Inspiration for deep Wiki generation
- [Understand-Anything](https://github.com/nicepkg/Understand-Anything) — importMap technique inspiration
>>>>>>> 120adb4 (Initial commit: Xoder — offline self-healing repo wiki engine)
