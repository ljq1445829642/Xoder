"""Xoder Wiki Outline Generator — dynamic section detection for Wiki output."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
from pathlib import Path


def detect_sections(workspace: str) -> dict:
    """Analyze workspace to determine which wiki sections are relevant."""
    root = Path(workspace)
    sections = []
    evidence = {}

    # === Universal sections (always present) ===
    sections.append({"id": "overview", "title": "项目概述", "universal": True})
    sections.append({"id": "quickstart", "title": "快速开始", "universal": True})
    evidence["overview"] = "universal"
    evidence["quickstart"] = "universal"

    # === Detect backend architecture ===
    backend_files = list(root.rglob("pom.xml")) + list(root.rglob("build.gradle")) + \
                    list(root.rglob("go.mod")) + list(root.rglob("requirements.txt")) + \
                    list(root.rglob("pyproject.toml")) + list(root.rglob("Cargo.toml")) + \
                    list(root.rglob("package.json"))
    # Filter out node_modules/.xoder/skills/scripts
    backend_files = [f for f in backend_files 
                     if not any(x in str(f) for x in ("node_modules", ".xoder", "skills", "scripts"))]
    if backend_files:
        f = str(backend_files[0].relative_to(root))
        if f.endswith("pom.xml") or str(f).endswith("build.gradle"):
            lang = "Java (Spring Boot)"
        elif f.endswith("go.mod"):
            lang = "Go"
        elif f.endswith("requirements.txt") or str(f).endswith("pyproject.toml"):
            lang = "Python"
        elif f.endswith("Cargo.toml"):
            lang = "Rust"
        elif f.endswith("package.json"):
            # Check if Node backend or frontend
            try:
                pkg = json.load(open(root / f))
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "next" in deps or "express" in deps or "fastify" in deps or "nestjs" in str(deps).lower():
                    lang = "Node.js (Backend)"
                else:
                    lang = "Node.js"
            except:
                lang = "Node.js"
        else:
            lang = "Detected"
        sections.append({"id": "backend-arch", "title": "后端架构设计",
                        "detected": True, "evidence": f"Build file: {f}"})
        evidence["backend-arch"] = f"Build file {f} → {lang}"

    # === Detect frontend architecture ===
    try:
        # Look for frontend in package.json dependencies
        for pkg_file in root.rglob("package.json"):
            if any(x in str(pkg_file) for x in ("node_modules", ".xoder", "skills", "scripts", "dashboard")):
                continue
            try:
                pkg = json.load(open(pkg_file))
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                frontend_frameworks = []
                if "vue" in str(deps).lower():
                    frontend_frameworks.append("Vue.js")
                if "react" in str(deps).lower():
                    frontend_frameworks.append("React")
                if "flutter" in str(deps).lower():
                    frontend_frameworks.append("Flutter")
                if "angular" in str(deps).lower():
                    frontend_frameworks.append("Angular")
                if frontend_frameworks:
                    fw_str = "/".join(frontend_frameworks)
                    pkg_rel = str(pkg_file.relative_to(root))
                    sections.append({"id": "frontend-arch", "title": "前端架构设计",
                                    "detected": True, "evidence": f"package.json → {fw_str}"})
                    evidence["frontend-arch"] = f"{pkg_rel} → {fw_str}"
                    break
            except:
                pass
    except:
        pass

    # Also check for HTML/CSS/JS directories
    if not any(s["id"] == "frontend-arch" for s in sections):
        front_dirs = list(root.rglob("src/main/resources/static")) + \
                     list(root.rglob("public")) + list(root.rglob("webapp"))
        front_dirs = [d for d in front_dirs if not any(x in str(d) for x in ("node_modules", ".xoder"))]
        if front_dirs:
            sections.append({"id": "frontend-arch", "title": "前端架构设计",
                            "detected": True, "evidence": "Static resource dir detected"})
            evidence["frontend-arch"] = "Static resources directory found"

    # === Detect admin backend ===
    admin_detected = False
    try:
        eps_file = root / ".xoder-local" / "stage" / "entry_points.json"
        if eps_file.exists():
            eps_data = json.load(open(eps_file))
            for ep in eps_data.get("entry_points", []):
                path = (ep.get("path") or "").lower()
                file = (ep.get("file") or "").lower()
                if "/admin" in path or "admin" in file or "admincontroller" in file:
                    admin_detected = True
                    break
    except:
        pass
    # Also check for Thymeleaf templates or @PreAuthorize
    if not admin_detected:
        for p in root.rglob("*.html"):
            rp = str(p.relative_to(root)).lower()
            if "admin" in rp and "templates" in rp:
                admin_detected = True
                break
    if admin_detected:
        sections.append({"id": "admin-backend", "title": "管理后台系统",
                        "detected": True, "evidence": "Admin routes/templates detected"})
        evidence["admin-backend"] = "Admin controller or templates found"

    # === Detect API documentation need ===
    try:
        eps_file = root / ".xoder-local" / "stage" / "entry_points.json"
        if eps_file.exists():
            eps_data = json.load(open(eps_file))
            ep_count = eps_data.get("total", 0)
            if ep_count >= 3:
                sections.append({"id": "api-docs", "title": "API接口文档",
                                "detected": True, "evidence": f"{ep_count} entry points"})
                evidence["api-docs"] = f"{ep_count} entry points detected"
    except:
        pass

    # === Detect database section ===
    try:
        orm_file = root / ".xoder-local" / "stage" / "orm_data.json"
        if orm_file.exists():
            orm_data = json.load(open(orm_file))
            table_count = len(orm_data.get("tables", []))
            if table_count >= 1:
                sections.append({"id": "database", "title": "数据库设计",
                                "detected": True, "evidence": f"{table_count} tables"})
                evidence["database"] = f"{table_count} tables from ORM scan"
    except:
        pass

    # === Detect third-party integration ===
    try:
        im_file = root / ".xoder-local" / "stage" / "import_map.json"
        if im_file.exists():
            im_data = json.load(open(im_file))
            imap = im_data.get("importMap", {})
            external_patterns = ["alipay", "wechat", "taobao", "jd", "pdd", "douyin",
                               "weixin", "oss", "cos", "sms", "push", "map", "pay",
                               "oauth", "openid", "unionpay", "stripe", "paypal"]
            external_hits = set()
            for imports in imap.values():
                for imp in imports:
                    for pat in external_patterns:
                        if pat in str(imp).lower():
                            external_hits.add(pat)
            if external_hits:
                sections.append({"id": "third-party", "title": "第三方集成",
                                "detected": True, "evidence": str(external_hits)})
                evidence["third-party"] = f"External integrations: {', '.join(sorted(external_hits))}"
    except:
        pass

    # === Universal: deployment & ops (always useful) ===
    sections.append({"id": "deployment", "title": "部署与运维", "universal": True})
    evidence["deployment"] = "universal"

    # === Universal: extension development ===
    sections.append({"id": "extend-dev", "title": "扩展开发", "universal": True})
    evidence["extend-dev"] = "universal"

    # === Detect test strategy — check for actual test files, not just directory count ===
    test_frameworks = {"*Test.java", "*Tests.java", "*IT.java", "test_*.py", "*_test.py",
                       "*.test.ts", "*.test.tsx", "*.spec.ts", "*.spec.tsx",
                       "*_test.go", "*.test.js", "*.spec.js"}
    test_file_count = 0
    for d_name in ("test", "tests", "__tests__", "spec"):
        for td in root.rglob(d_name):
            rp = str(td.relative_to(root)).replace('\\', '/')
            if any(x in rp for x in ("node_modules", ".xoder", "skills", "scripts", "dashboard", ".venv")):
                continue
            for pattern in test_frameworks:
                for tf in td.rglob(pattern):
                    trp = str(tf.relative_to(td))
                    if not any(x in trp for x in ("__pycache__", ".pyc")):
                        test_file_count += 1
                        break
                if test_file_count >= 3:
                    break
            if test_file_count >= 3:
                break
            break
    if test_file_count >= 3:
        sections.append({"id": "testing", "title": "测试策略",
                        "detected": True, "evidence": f"{test_file_count} test files"})
        evidence["testing"] = f"{test_file_count} test files found (frameworks: JUnit/Vitest/pytest/Go test)"

    # === Universal sections ===
    sections.append({"id": "dev-guide", "title": "开发规范", "universal": True})
    evidence["dev-guide"] = "universal"
    sections.append({"id": "troubleshooting", "title": "故障排除", "universal": True})
    evidence["troubleshooting"] = "universal"

    # === Build reading guide ===
    reading_guide = {
        "新手入门": ["overview", "quickstart"],
        "进阶开发": [],
        "运维部署": []
    }
    # Add architecture docs to both 新手 and 进阶
    if any(s["id"] == "backend-arch" for s in sections):
        reading_guide["新手入门"].append("backend-arch")
        reading_guide["进阶开发"].append("backend-arch")
    if any(s["id"] == "frontend-arch" for s in sections):
        reading_guide["新手入门"].append("frontend-arch")
        reading_guide["进阶开发"].append("frontend-arch")

    # API and DB go to 进阶
    if any(s["id"] == "api-docs" for s in sections):
        reading_guide["进阶开发"].append("api-docs")
    if any(s["id"] == "database" for s in sections):
        reading_guide["进阶开发"].append("database")
    if any(s["id"] == "third-party" for s in sections):
        reading_guide["进阶开发"].append("third-party")
    if any(s["id"] == "dev-guide" for s in sections):
        reading_guide["进阶开发"].append("dev-guide")
    if any(s["id"] == "extend-dev" for s in sections):
        reading_guide["进阶开发"].append("extend-dev")
    if any(s["id"] == "admin-backend" for s in sections):
        reading_guide["进阶开发"].append("admin-backend")

    # Ops go to 运维部署
    if any(s["id"] == "deployment" for s in sections):
        reading_guide["运维部署"].append("deployment")
    if any(s["id"] == "testing" for s in sections):
        reading_guide["运维部署"].append("testing")
    reading_guide["运维部署"].append("troubleshooting")

    return {
        "title": "项目知识库",
        "sections": sections,
        "evidence": evidence,
        "reading_guide": reading_guide,
        "section_count": len(sections),
        "universal_count": sum(1 for s in sections if s.get("universal")),
        "detected_count": sum(1 for s in sections if s.get("detected"))
    }


def main():
    parser = argparse.ArgumentParser(description="Xoder Wiki Outline Generator")
    parser.add_argument("--workspace", "-w", default=".", help="Project root")
    parser.add_argument("--output", "-o", required=True, help="Output JSON file")
    args = parser.parse_args()

    outline = detect_sections(args.workspace)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(outline, f, ensure_ascii=False, indent=2)

    universal = outline["universal_count"]
    detected = outline["detected_count"]
    titles = [s["title"] for s in outline["sections"]]
    print(f"OK: {universal} universal + {detected} detected = {len(titles)} sections")
    for t in titles:
        tag = "[固定]" if any(s["title"] == t and s.get("universal") for s in outline["sections"]) else "[检测]"
        print(f"  {tag} {t}")
    print(f"Reading guide: {', '.join(outline['reading_guide'].keys())}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
