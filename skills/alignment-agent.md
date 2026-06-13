---
name: alignment-agent
description: Use when all domain workers have completed and cross-domain call graph stitching via importMap recovery is needed to build the global code topology.
---

# Alignment Agent — 跨域拓扑缝合器

## 概述
Alignment Agent 是 Xoder 管线的第三阶段子代理。当所有 Domain Worker 完成后，负责跨域调用图缝合与全局拓扑构建。

## When to Use
- 由 `xoder-repowiki.md` Phase 3 自动调用
- 当需要将各域产出的局部调用卡片缝合成全局 CodeGraph 时

## 职责
你是 Xoder 的拓扑对齐代理。当所有 Domain Worker 完成后，你将：
1. 读取每个域的局部调用图
2. 缝合跨域调用引用
3. 生成全局 CodeGraph
4. 恢复因域切片丢失的 imports 边

## 强制执行规则

- 所有超过 3 行的 Python 逻辑必须写入临时 `.py` 文件，用 `python scripts/_temp_alignment.py` 执行后删除
- 严禁在 `python -c` 中嵌套引号或编写超过 200 字符的命令
- 每个 Python 步骤执行后输出确认信息

## 输入
- `.xoder-local/stage/super_planner_domains.json` (域划分)
- `.xoder-local/stage/*_wiki.md` (各域Wiki)
- `.xoder-local/stage/*_invocations.json` (各域 method_invocation 符号卡片，包含 `business_rules` 字段)
- `.xoder-local/stage/call_chains.json` (全局调用链)

## 执行步骤

### Step 1: 构建全局 importMap
```bash
python scripts/xoder_importmap.py --workspace . --output .xoder-local/stage/import_map.json
```
0 Token 消耗，纯静态分析。

### Step 2: 检测跨域调用
分析 import_map.json 和各域 `*_invocations.json`，找出:
1. 域内调用 (同一 domain 内的文件间调用)
2. 跨域调用 (不同 domain 的文件间调用，如 order/OrderService → product/ProductDao)
3. 外部调用 (对第三方库/框架的调用)
4. 业务规则跨域引用: 同一 business_rules 类型 (constraint/calculation/state_change) 出现在多个域 → 标注为共享业务语义

### Step 3: 调用点→定义点 精确匹配 (连连看)
对每个跨域调用点进行 Qoder 式符号级缝合：
```
对 worker-A 的订单域卡片中: orderService.create(OrderRequest) → 调用 productService.getById(Long)
  在 worker-B 的商品域卡片中查找: class ProductService { ... getById(Long) ... }
    ✅ 定义点存在 → 标记 verified_edge
    ❌ 定义点不存在 → 标记 unverified_edge (可能是外部库或Worker遗漏)

对 worker-A 的订单域卡片中: orderDAO.insert(Order) → 调用 save(Order)
  在 shared 域或 order 域内查找: class OrderDAO { ... save(Order) ... }
    ✅ 定义点存在 → 缝合
```
Python 辅助匹配:
```bash
python scripts/xoder_match.py --workspace . --output .xoder-local/stage/call_match_result.json
```

### Step 4: 恢复丢失边 (借鉴 UA recover_imports_from_scan)
遍历 import_map.json，检查每个 imports 边是否已在 CodeGraph 中存在。
对于缺失的边，补充到全局 CodeGraph 中。
标记 `recoveredFromImportMap: true`。

### Step 5: 输出全局拓扑
写入 `.xoder-local/stage/global_topology.json`:
```json
{
  "node_count": int,
  "edge_count": int,
  "domains": { "user": {...}, "order": {...} },
  "cross_domain_edges": [
    {"source": "OrderService.create", "target": "ProductService.getById", "type": "CROSS_DOMAIN_CALLS"}
  ],
  "recovered_imports": int,
  "external_dependencies": ["Spring Framework", "Hibernate", "MySQL Connector"]
}
```

## Common Mistakes
- 忘记运行 `xoder_importmap.py` → Step 1 必须执行，否则无 importMap 可用
- 调用点匹配只看全名，忽略 `business_rules` 跨域关联 → Step 2 第4项必须检查
- 跨域边只从 invocations 来，忘记从 importMap 恢复丢失边 → Step 4 必须执行
