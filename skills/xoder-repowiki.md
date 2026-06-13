---
name: xoder-repowiki
description: Use when a code repository needs comprehensive wiki documentation generated from static analysis, git history, and multi-agent collaboration. Triggers on generating or updating project wiki.
---

# Xoder Repo Wiki

## 概述
Xoder Repo Wiki 是一套完全离线、自愈的代码仓库 Wiki 自动生成系统。
采用 Qoder 式 4 阶段多 Agent 流水线架构：
1. Super Planner — 全局审判与业务域动态切片
2. Domain Workers ×N — 并发执行域内全流程 (借鉴 DeepWiki 三层迭代提示词)
3. Alignment Agent — 跨域调用拓扑缝合 (借鉴 Understand-Anything importMap)
4. Doc Agent — 全局润色 + ADR 注入 + 新手指南 + 原子落盘

## When to Use
- 首次为代码仓库生成完整 Wiki 文档
- 代码变更后增量更新 Wiki
- 人类手改 Markdown 后反向同步到知识引擎

## When NOT to Use
- 单纯查询单个 API 或类的文档 → 直接用 opencode 问答
- 非代码仓库项目（纯文档/纯配置仓库）→ 产出意义不大

## 核心约束
- 所有子 Agent 通过 opencode Task tool 派发，由 `skills/xoder-repowiki.md` 统一编排
- 子 Agent 加载各自的 skill 文件，按步骤执行 Python 脚本后再生成内容

## 前置条件
- Python >= 3.9
- (可选) mmdc (Mermaid CLI)

> LLM 能力由 opencode 自身提供，无需额外启动 Ollama 或其他本地模型服务。

## 命令规范
- 超过 3 行的 Python 逻辑**必须**写临时 `.py` 文件 (`scripts/_temp_xxx.py`)，用 `python scripts/_temp_xxx.py` 执行后删除
- Phase 0/0.5 已提供独立脚本，直接调用，不需改写

## 工作流

### Phase 0: 初始化
```bash
python scripts/xoder-cli.py init
```

### Phase 0.1: 知识导入 (自动)

如果 `.xoder/knowledge/` 目录下有用户手动放入的文档 (PDF/Word/Excel/HTML/Markdown 等)，
自动扫描并转换为 .md 供后续 Agent 参考。已有文件仅在内容变更时重新转换。

```bash
python scripts/xoder_knowledge.py --workspace . --auto
```

### Phase 0.5: 考古层 — Git + 静态解构
```bash
python scripts/xoder_git.py --workspace . --output .xoder-local/stage/git_archaeology.json
```

### Phase 0.5: 考古层 — 全局静态解构
一次性产出 6 份全局 JSON 工件，供后续所有 Agent 消费：
符号解析从此阶段起即提取业务规则 (business_rules: 约束/计算公式/状态变更/业务模式)，贯穿全管线。

**Step 0.5.1: Entry Point 检测（过滤噪声目录）**
```bash
python scripts/xoder_entry.py --workspace . --output .xoder-local/stage/entry_points.json
```

**Step 0.5.2: ORM 穿透**
```bash
python scripts/xoder_orm.py --workspace . --output .xoder-local/stage/orm_data.json
```

**Step 0.5.3: 调用链追踪**
```bash
python scripts/xoder_callchain.py --workspace . --entries .xoder-local/stage/entry_points.json --output .xoder-local/stage/call_chains.json
```

**Step 0.5.4: 架构模式识别**
```bash
python scripts/xoder_arch.py --workspace . --output-arch .xoder-local/stage/architecture_pattern.json --output-modules .xoder-local/stage/super_planner_modules.json
```

**Step 0.5.5: Spring DI 依赖推断 (如果检测到 Java/Spring 项目)**
```bash
python scripts/xoder_spring.py --workspace . --output .xoder-local/stage/spring_di_mapping.json
```
Spring 项目大量使用 @Autowired/@Resource 注入，静态 AST 无法追踪这些依赖。
此脚本通过扫描注解建立 field→class 映射，产出虚拟调用边供 callgraph 使用。
(非 Spring 项目可跳过此步)

### Phase 1: Super Planner — 域切片
Load skill `skills/super-planner.md` as sub-agent:
```
prompt: |
  Read skills/super-planner.md and follow its execution steps.
  1. Read pom.xml (or build.gradle/package.json)
  2. Run detect_architecture_pattern and discover_modules
  3. Analyze modules and divide into business domains
  4. Output super_planner_domains.json to .xoder-local/stage/
```
**Wait for Super Planner to complete.** Read `.xoder-local/stage/super_planner_domains.json`.

### Phase 2: Domain Workers — 并发执行
Based on super_planner_domains.json, dispatch ONE domain-worker sub-agent PER domain.
ALL domain-workers run IN PARALLEL using opencode Task tool:

```
For each domain in super_planner_domains.json:
  Task tool:
    subagent_type: general
    description: "Generate wiki for {domain_name}"
    prompt: |
      Load skill from skills/domain-worker.md.
      You are assigned the {domain_name} domain with {file_count} files.
      Files: {file_list}
      
      ⚠️ CRITICAL: You MUST execute EVERY Python command listed in domain-worker.md.
      Do NOT summarize or skip any step. Each step produces a required artifact.
      After each step, report: "[domain-worker:{domain_id}] Step N complete: <summary>"
      If a Python command fails, retry with PYTHONPATH fix or alternative approach.
      Do NOT generate the final wiki until ALL steps are complete.
      
      Required outputs:
      - .xoder-local/stage/{domain_id}_wiki.md
      - .xoder-local/stage/{domain_id}_invocations.json
      
      Read these context files:
        - .xoder-local/stage/entry_points.json
        - .xoder-local/stage/orm_data.json
        - .xoder-local/stage/call_chains.json
        - .xoder-local/stage/git_archaeology.json
      Output: .xoder-local/stage/{domain_id}_wiki.md
```
**Wait for ALL domain-workers to complete.**

### Phase 3: Alignment Agent — 拓扑缝合
```
Task tool:
  subagent_type: general
  description: "Stitch cross-domain topology"
  prompt: |
    Load skill from skills/alignment-agent.md.
    Read .xoder-local/stage/super_planner_domains.json, all .xoder-local/stage/*_wiki.md and .xoder-local/stage/*_invocations.json.
    Build importMap, detect cross-domain calls, match call-sites to definitions, stitch topology.
    
    ⚠️ CRITICAL: You MUST run the Python commands in alignment-agent.md.
    Output REQUIRED: .xoder-local/stage/global_topology.json and .xoder-local/stage/import_map.json
```

### Phase 4: Wiki 大纲生成 + Doc Agent — 动态章节 + 分层发布

#### Step 4.0: 生成 Wiki 目录大纲
```bash
python scripts/xoder_outline.py --workspace . --output .xoder-local/stage/wiki_outline.json
```

#### Step 4.1: Doc Agents — 并发分章节生成

读取 wiki_outline.json 确定需生成的章节。按类别分组，**并发派发多个 Doc Agent 子代理**，
避免单 Agent 上下文超限。

**分组策略**：
- Doc Agent A: universal 章节 (项目概述 / 快速开始 / 部署与运维 / 开发规范 / 扩展开发 / 故障排除)
- Doc Agent B: 后端 + 数据 章节 (后端架构设计 / API接口文档 / 数据库设计 / 第三方集成 if detected)
- Doc Agent C: 前端 + 测试 章节 (前端架构设计 / 管理后台系统 / 测试策略 if detected)

每个 Doc Agent 读取各自需要的域 Wiki 文件，独立生成。

```
For each group (A, B, C):
  Task tool (parallel):
    subagent_type: general
    description: "Generate group {group_name} wiki sections"
    prompt: |
      Load skill from skills/doc-agent.md.
      You are assigned the {group_name} section group.
      Sections to generate: {section_list}
      Read .xoder-local/stage/wiki_outline.json for section details.
      Read ONLY the .xoder-local/stage/*_wiki.md files relevant to your sections.
      PRESERVE all `## 业务语义分析` and enhanced ADR chapters.
      Output each section as independent .md files to .xoder-local/stage/_publish/
```
Wait for ALL Doc Agents to complete.

#### Step 4.2: Final Assembly — 阅读指南 + 图表 + 元数据 + 落盘
```
Task tool:
  subagent_type: general
  description: "README assembly + diagrams + meta + atomic publish"
  prompt: |
    Load skill from skills/doc-agent.md, Steps 3-8 only.
    Read all .xoder-local/stage/_publish/*.md and wiki_outline.json.
    Generate README.md with reading guide (新手入门/进阶开发/运维部署).
    VERIFY all links in README.md point to actual files. Links must use correct subdirectory paths (e.g. voiture-location API/voiture-location API 总览.md not bare 车辆管理 API.md). If a target is in a subdirectory, the link must include the full relative path.
    Collect Mermaid diagrams to diagrams/.
    Generate knowledge_cards.json and adr_records.json.
    Atomic write to .xoder/repowiki/zh/.
    Run hash_tracker.py --mode register.
    Run xoder_dbstatus.py.
```

## 增量运行
```bash
python scripts/hash_tracker.py --mode diff --workspace .
python scripts/hash_tracker.py --mode propagate --workspace .
# Re-run only affected domains
```

## 反向同步
```bash
python scripts/hash_tracker.py --mode watch --workspace .
```

## Common Mistakes

| 错误 | 正确做法 |
|------|---------|
| 跳过 Phase 0.5 直接跑 Phase 2 | Phase 0.5 产出 entry_points/orm_data/call_chains，是后续的输入 |
| 用 `python -c "..." > file` 管道 | 所有 Phase 0/0.5 已提供独立脚本，直接用 `python scripts/xoder_xxx.py` |
| Sub-Agent 不执行 Python 命令直接生成文档 | domain-worker.md 开头有 `⚠️ 强制工具调用规则`，必须遵守 |
| Domain Worker 跳过 invocations.json | Alignment Agent 依赖它做跨域缝合，缺失会导致拓扑断裂 |
| Doc Agent 丢弃业务语义章节 | 必须保留 `## 业务语义分析` 和增强 ADR 章节 |
