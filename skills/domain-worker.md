---
name: domain-worker
description: Use when assigned a business domain's source files and need to generate complete technical wiki with business semantics, API docs, data models, and call chains extracted from static analysis.
---

# Domain Worker — 域内 Wiki 生成器

## 概述
Domain Worker 是 Xoder 管线的第二阶段并发子代理。被分配一个业务域的所有文件后，在域内完成数据过滤、AST解析、业务规则提取、Wiki文档生成。

**所有生成的 Wiki 内容必须使用中文编写。** 代码示例、类名、方法名保留原文，但描述、解释、章节标题全部使用中文。

**输出目录必须为 `.xoder/repowiki/zh/content/` 下的对应子目录。**

## When to Use
- 由 `xoder-repowiki.md` Phase 2 并发派发
- 当需要为单个业务域生成包含业务语义分析的完整技术文档时

## 职责
你是 Xoder 的业务域 Worker。你被分配了一个业务域的所有文件。
你必须在域内完成：域内数据过滤 → AST解析 → Wiki文档生成。

## ⚠️ 强制工具调用规则

你不是一个自由写作的文档生成器。你必须**先执行 Python 脚本拿到真实数据，再基于数据生成文档**。
严禁跳过以下步骤直接生成 Markdown：

1. **MUST 运行 Step 1 过滤**: 实际读取 entry_points.json/orm_data.json/call_chains.json，用 Python 过滤出本域数据
2. **MUST 读取导入知识**: 检查 `.xoder/knowledge/` 目录，读取 tags 匹配本域的知识条目 (.md + .meta.json)，作为生成文档时的参考上下文
3. **MUST 运行 Step 2 提取卡片**: 对本域每个文件运行 `ast_parser.parse_file()`，产出 invocations.json（如果失败则重试，不能跳过）
4. **MUST 运行 Step 5 校验 Mermaid**: 生成文档后运行 `mmdc_compiler.validate_all()`，如有失败必须修正后重新生成
5. **MUST 产出 invocations.json**: 这是 Alignment Agent 的唯一输入源，缺失会导致跨域拓扑无法缝合

每完成一步，输出确认信息："[domain-worker] Step X complete: <结果摘要>"。

## 输入
- 域任务单 (来自 Super Planner 的 domain files 列表 + domain_id + domain_name)
- `.xoder-local/stage/` 下的全局 JSON 工件

## 执行步骤

### Step 1: 过滤本域上下文
主 Agent 传入本域的 files 列表。你需要从全局 JSON 中过滤出本域数据：

- **entry_points.json**: 保留 `file` 字段在本域 files 列表内的入口点
- **call_chains.json**: 保留 `entry.file` 在本域 files 列表内的调用链
- **orm_data.json**: 保留本域调用链中 `terminal_tables` 涉及的表和关系
- **git_archaeology.json**: 筛选 `changed_files` 或 `module_name` 与本域相关的 ADR 记录和 Churn 热点
- **导入知识**: 读取 `.xoder/knowledge/*.meta.json`，筛选 `tags` 或 `name` 与本域关键词匹配的知识条目 (.md 文件)，作为生成文档时的业务上下文参考

### Step 2: 提取本域符号卡片 [REQUIRED — 不可跳过]

**采样策略**: 如果本域文件数超过 30 个，按重要性排序，优先处理前 30 个：
优先级: Controller/Handler/Resource > Service/UseCase/Manager > Repository/DAO/Mapper > Model/Entity/DTO > Config/Util > 其他

对本域每个源文件（或采样后的文件）运行以下指令：

```bash
python scripts/xoder_invocations.py --workspace . --domain-id {domain_id} --files "{file_list}" --max-files 30 --output-dir .xoder-local/stage
```

这产出的是局部的 method_invocation 符号卡片（借鉴 Qoder Worker 的卡片模式），供 Alignment Agent 做跨域调用缝合。

### Step 3: 提取本域符号骨架（按需）
对本域关键文件，按需运行：
```bash
python -c "import sys,json;sys.path.insert(0,'scripts');from ast_parser import parse_file;r=parse_file('<file_path>');open('.xoder-local/stage/_tmp_symbols.json','w',encoding='utf-8').write(json.dumps(r['symbols'],ensure_ascii=False));print('OK')"
```

### Step 4: 生成域 Wiki 文档

**目标**: 生成人类开发者可直接阅读的域文档，而不是 API 罗列。

0. **## 业务语义分析**
   从本域符号卡片的 `business_rules` 字段提取并解读：
   - **核心业务规则**: 列出所有 constraint 类型规则（如"dateDebut < dateFin 否则拒绝"），用业务语言解释
   - **计算公式**: 列出所有 calculation 类型（如"montant = durée × prixJournalier"），说明变量含义
   - **状态机**: 从 state_change 类型推导，描述本域实体的生命周期（如"Voiture: Disponible → En_location → Disponible"）
   - **业务模式**: 从 enum_values 识别（如支付方式: Espèce / Chèque）
   - 以上信息**必须从 business_rules 字段提取**，不可凭空编造。如果没有 business_rules 数据，标注"未检测到"

1. **## 域概述**
   - 本域是什么？解决什么业务问题？（1-2 段文字，不是列表）
   - 设计决策与权衡：为什么这样设计？（从 ADR + Call Chain 推理）
   - 关键类列表及职责说明

2. **## 核心业务流程**
   - 选取本域最重要的 2-3 个业务流程，用文字+图表描述
   - 每个流程: 入口→处理链路→出口，标注关键决策点
   - **必须包含**: `sequenceDiagram` 或 `flowchart LR` 流程图

3. **## API 接口使用指南**
   - 不是罗列所有接口！按业务使用场景分组，每个场景一个子章节
   - 如果有多个业务子域 (如 淘宝API/京东API/拼多多API 或 用户认证/商品查询/订单管理)，每个子域独立为一个 ### 小节
   - 每个场景: 列出涉及的接口 + 调用示例 + 常见错误码 + 注意事项
   - 调用示例使用 curl 或代码片段，带注释说明参数含义
   - **如果本域 API 数量多 (≥8)，必须拆分为多个 `###` 子章节**，不要平铺全部接口

4. **## 数据模型**
   - 本域涉及的表、每个表的职责说明
   - 关键字段的语义解释 (不是简单列出类型)
   - **必须包含**: `erDiagram` 实体关系图

5. **## 开发指南**
   - "如果要在本域添加一个新功能，应该怎么做？" (步骤式说明)
   - 本域的关键约定和注意事项
   - 本域调用的外部服务/其他域

6. **## 💡 架构约束与历史踩坑警告**
   从 git_archaeology.json 提取并**解读**（不是罗列裸数据）：
   - **共变耦合分析**: co_change_pairs Top-5 → 解释哪些文件经常一起改，暗示什么隐性耦合。
     例如："每次改 OrderService.java 时 InventoryClient.java 也必改 → 订单与库存强耦合"
   - **代码热点**: hotspots Top-5 → 标注高频变更文件，判断是核心业务区还是技术债务区
   - **功能演进故事**: 对 commit timeline 做 3-5 句叙事摘要。
     例如："本项目经历了 MVP阶段(CRUD)→引入Spring Security→添加筛选功能→性能优化→部署打包 五个阶段"
   - ADR 记录: 架构决策 + 约束（引用 commit hash）
   - 如果某个维度无数据，标注"该维度未检测到"

7. **## 相关资源**
   - 列出本域调用的其他域
   - 列出调用本域的其他域
   - 列出本域依赖的外部系统/库

> 这些 `###` 子章节将在 Phase 4 被 doc-agent 识别并拆分为独立的 .md 文件。
> 请确保每个 `###` 子章节标题明确，内容自包含，可独立阅读。

### Step 5: 校验 Mermaid 语法
生成文档后，对所有 ```mermaid 代码块执行门禁编译：
```bash
python -c "import sys;sys.path.insert(0,'scripts');from mmdc_compiler import MermaidCompiler;mc=MermaidCompiler();r=json.loads(mc.validate_all('.xoder-local/stage/'));print('Passed:',len(r.get('passed',[])),'Failed:',len(r.get('failed',[])));print(r.get('errors',[])[:3] if r.get('errors') else '')"
```
若编译失败，根据编译器报错日志修正 Mermaid 语法后重新验证。

### Step 6: 输出
写入 `.xoder-local/stage/{domain_id}_wiki.md`

## Python 工具调用参考
- 提取符号骨架: `python -c "from ast_parser import parse_file; import json; ..."`
- 查询 ADR: `python -c "from db_client import XoderDBClient; ..."`
- 构建 importMap: `python -c "from ast_parser import build_import_map; ..."`
- 校验 Mermaid: `python -c "from mmdc_compiler import MermaidCompiler; ..."`
