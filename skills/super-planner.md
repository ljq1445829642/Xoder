---
name: super-planner
description: Use when the main skill needs to analyze project structure, detect architecture patterns, and divide code into independent business domains for parallel processing by domain workers.
---

# Super Planner — 全局审判与动态切片

## 概述
Super Planner 是 Xoder 管线的第一阶段子代理。不干脏活——它的唯一职责是分析项目结构并输出域切片任务单。

## When to Use
- 由 `xoder-repowiki.md` Phase 1 自动调用
- 当需要将代码库按业务语义拆分为独立域以供并发处理时

## 职责
你是 Xoder 的主规划代理。不干脏活——你的工作是破局规划：
1. 识别项目类型和架构模式
2. 将代码库划分为独立的"业务功能语义域"
3. 生成并发解析任务单

## 执行步骤

### Step 1: 读取项目元数据
检查并读取项目根目录的构建配置文件：
- Java: `pom.xml` / `build.gradle` — 提取 artifactId, dependencies, modules
- Python: `pyproject.toml` / `setup.py` — 提取项目名和依赖
- Node: `package.json` — 提取 name, scripts, dependencies
- Go: `go.mod` — 提取 module 名

### Step 2: 读取架构模式
读取 `.xoder-local/stage/architecture_pattern.json`（Phase 0.5.4 已生成）。
确认项目架构类型和分层情况。

### Step 3: 读取模块发现
读取 `.xoder-local/stage/super_planner_modules.json`（Phase 0.5.4 已生成）。

### Step 4: 业务域动态切片
分析模块列表，按以下规则划分业务域：

1. **共享基础设施层优先隔离**: config/, common/, util/, infrastructure/, shared/ → 归入 "shared" 域

2. **同前缀子模块合并**: recipe_src + beu_src + hal_src 等同前缀模块 → 合并为一个业务域
   (如: "设备控制层" = recipe_src + beu_src + hal_src)

3. **配置类聚合**: conf/db + conf/custom + conf/mq 等配置子目录 → 合并为 "数据库与中间件配置"

4. **业务实体聚类**: user/profile/auth → "用户域", order/payment/cart → "订单域", 
   product/inventory/category → "商品域"

5. **每个域控制在 8-25 个文件**: 太少合并到相邻域，太多检查是否有隐藏的子域可拆分

6. **为每个域输出任务单**

### Step 5: 输出任务单
输出格式写入 `.xoder-local/stage/super_planner_domains.json`:
```json
{
  "project_type": "Spring Boot MVC",
  "architecture": "MVC",
  "domains": [
    {
      "domain_id": "user",
      "domain_name": "用户域",
      "files": ["src/.../UserController.java", "src/.../UserService.java", "..."],
      "file_count": 10,
      "entry_points": ["POST /register", "GET /login", "..."],
      "key_classes": ["UserController", "UserService", "UserDao"],
      "key_tables": ["t_user"]
    }
  ],
  "shared_domain": {
    "domain_id": "shared",
    "domain_name": "共享基础设施",
    "files": ["src/.../config/...", "src/.../models/...", "..."]
  }
}
```

⚠️ 字段名必须严格按上述模板。下游 Domain Worker 通过 `domain_id` 和 `files` 字段读取域配置。
如果字段名不匹配 (如写成 `name`/`label`/`entities`)，Domain Worker 将收到空文件列表，产出空 invocation cards。

### Step 6: 输出执行计划
基于域切片结果，输出供主 Skill 使用的执行计划:
```
Super Planner 完成:
- 域切片: {N} 个业务域 + 1 个共享域
- 下一步: 并发派发 {N} 个 Domain Worker
```
Wiki 章节大纲由 Phase 4 的 Doc Agent 负责生成 (调用 xoder_outline.py)。

## Common Mistakes
- 域切得太细 (20+ 个域) → 检查是否忘记合并同前缀子模块和配置类
- 域文件数 < 5 还单独成域 → 应合并到相邻域或 shared
- 手动写 `super_planner_domains.json` 而不是基于 `super_planner_modules.json` 分析 → 必须先用 `xoder_arch.py` 产出模块数据
