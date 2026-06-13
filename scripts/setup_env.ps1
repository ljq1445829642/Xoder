<#
.SYNOPSIS
    Xoder 离线环境依赖与 Mermaid-CLI 网关初始化脚本

.DESCRIPTION
    检查 Python >= 3.9, 安装/验证 Mermaid CLI (mmdc),
    创建 .xoder 目录结构, 打印设置完成状态。

.NOTES
    在项目根目录 (Xoder-Core-Project) 下运行此脚本。
#>

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path $ProjectRoot).Path

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Xoder Environment Setup"               -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# =============================================================================
# 1. Check Python >= 3.9
# =============================================================================
Write-Host "[1/4] Checking Python..." -ForegroundColor Yellow

$pythonVersion = $null
try {
    $pyOutput = & python --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        $pythonVersion = $pyOutput
    }
} catch {
    try {
        $pyOutput = & python3 --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $pythonVersion = $pyOutput
        }
    } catch {
        # fall through
    }
}

if (-not $pythonVersion) {
    Write-Host "  ERROR: Python not found. Please install Python >= 3.9" -ForegroundColor Red
    Write-Host "  Download from: https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}

Write-Host "  Found: $pythonVersion" -ForegroundColor Green

if ($pythonVersion -match "Python (\d+)\.(\d+)") {
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 9)) {
        Write-Host "  WARNING: Python $major.$minor detected. Python >= 3.9 is recommended." -ForegroundColor DarkYellow
    }
}

# =============================================================================
# 2. Check/install Mermaid CLI (mmdc)
# =============================================================================
Write-Host "[2/4] Checking Mermaid CLI (mmdc)..." -ForegroundColor Yellow

$mmdcFound = $false
try {
    $mmdcVersion = & mmdc --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        $mmdcFound = $true
        Write-Host "  Found: $mmdcVersion" -ForegroundColor Green
    }
} catch {
    # not found
}

if (-not $mmdcFound) {
    Write-Host "  mmdc not found. Checking npm..." -ForegroundColor DarkYellow

    $npmAvailable = $false
    try {
        $npmVersion = & npm --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $npmAvailable = $true
            Write-Host "  npm found: v$npmVersion" -ForegroundColor Green
        }
    } catch {
        Write-Host "  npm not found. Skipping mmdc installation." -ForegroundColor DarkYellow
    }

    if ($npmAvailable) {
        Write-Host "  Installing @mermaid-js/mermaid-cli globally..." -ForegroundColor Cyan
        try {
            & npm install -g @mermaid-js/mermaid-cli
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  mmdc installed successfully." -ForegroundColor Green
            } else {
                Write-Host "  WARNING: mmdc installation may have failed. Check npm output." -ForegroundColor DarkYellow
            }
        } catch {
            Write-Host "  WARNING: mmdc installation failed: $_" -ForegroundColor DarkYellow
            Write-Host "  You can install manually: npm install -g @mermaid-js/mermaid-cli" -ForegroundColor DarkYellow
        }
    } else {
        Write-Host "  WARNING: npm not available. Please install Node.js + npm first." -ForegroundColor DarkYellow
        Write-Host "  Download from: https://nodejs.org/" -ForegroundColor DarkYellow
        Write-Host "  Then run: npm install -g @mermaid-js/mermaid-cli" -ForegroundColor DarkYellow
    }
}

# =============================================================================
# 3. Create .xoder directory structure
# =============================================================================
Write-Host "[3/4] Creating .xoder directory structure..." -ForegroundColor Yellow

$dirs = @(
    ".xoder\repowiki\zh\content\modules",
    ".xoder\repowiki\zh\diagrams",
    ".xoder\repowiki\zh\meta",
    ".xoder\repowiki\en\content\modules",
    ".xoder\repowiki\en\diagrams",
    ".xoder\repowiki\en\meta",
    ".xoder-local\stage"
)

foreach ($dir in $dirs) {
    $fullPath = Join-Path $ProjectRoot $dir
    if (-not (Test-Path $fullPath)) {
        New-Item -ItemType Directory -Path $fullPath -Force | Out-Null
        Write-Host "  Created: $dir" -ForegroundColor Green
    } else {
        Write-Host "  Exists:  $dir" -ForegroundColor Gray
    }
}

# =============================================================================
# 4. Print completion status
# =============================================================================
Write-Host "[4/4] Verifying setup..." -ForegroundColor Yellow
Write-Host ""

$allOk = $true

Write-Host "  Python:" -NoNewline
if ($pythonVersion) {
    Write-Host " OK ($pythonVersion)" -ForegroundColor Green
} else {
    Write-Host " MISSING" -ForegroundColor Red
    $allOk = $false
}

Write-Host "  mmdc (Mermaid CLI):" -NoNewline
if ($mmdcFound) {
    Write-Host " OK" -ForegroundColor Green
} else {
    Write-Host " NOT FOUND (Mermaid diagrams will use degraded output)" -ForegroundColor DarkYellow
}

Write-Host "  .xoder/:" -NoNewline
if (Test-Path (Join-Path $ProjectRoot ".xoder")) {
    Write-Host " OK" -ForegroundColor Green
} else {
    Write-Host " MISSING" -ForegroundColor Red
    $allOk = $false
}

Write-Host "  .xoder-local/stage/:" -NoNewline
if (Test-Path (Join-Path $ProjectRoot ".xoder-local\stage")) {
    Write-Host " OK" -ForegroundColor Green
} else {
    Write-Host " MISSING" -ForegroundColor Red
    $allOk = $false
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
if ($allOk) {
    Write-Host "  Setup complete. Run: python scripts/xoder-cli.py run" -ForegroundColor Green
} else {
    Write-Host "  Setup finished with warnings. Review above." -ForegroundColor DarkYellow
}
Write-Host "========================================" -ForegroundColor Cyan
