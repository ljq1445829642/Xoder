---
name: knowledge-import
description: Use when importing external knowledge files (PDF, DOCX, XLSX, HTML, CSV, MD) into the Xoder wiki pipeline for augmentation during wiki generation
---

# Knowledge Import — 外部知识导入与转换

## 概述
将外部文档转换为 Markdown 格式，注入 Xoder 知识库，供 Wiki 生成时参考。
基于微软 MarkItDown 引擎，支持 20+ 文档格式的本地离线转换。

## 使用方式（用户只需一步）

**把文档文件直接放到 `.xoder/knowledge/` 目录下即可。**

支持的格式：`.md` `.pdf` `.docx` `.doc` `.xlsx` `.xls` `.csv` `.html` `.htm` `.pptx` `.epub` `.txt` `.json`

下次运行 `xoder-repowiki` 时，Phase 0.1 自动扫描并转换。

## 前置条件

**推荐安装（完整格式支持）：**
```bash
pip install 'markitdown[all]'
```
不安装时仍可导入 `.md` `.txt` `.json` 纯文本文件。

## 自动扫描逻辑

`python scripts/xoder_knowledge.py --workspace . --auto` 会：

1. 扫描 `.xoder/knowledge/` 下所有文件
2. 对比 `.meta.json` 中存储的 SHA-256 哈希
3. 新文件或内容变更的 → 自动转换为 `.md`，记录元数据
4. 未变更的 → 跳过

## 知识如何使用

导入后，Domain Worker 和 Doc Agent 在生成文档时自动读取 `.xoder/knowledge/` 中匹配 tag 的知识条目作为参考上下文。

## Common Mistakes
- 放文件后忘记运行 `xoder-repowiki` → 知识不会自动生效
- 未安装 markitdown 放 PDF/DOCX → 跳过，先 `pip install 'markitdown[all]'`
- 大文件 (>50页 PDF) → 建议拆分为多个小文件分别放入
