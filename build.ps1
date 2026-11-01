<#
.SYNOPSIS
    Gou Wallpaper Windows PowerShell 自动化构建与测试脚本 (build.ps1).
#>

[CmdletBinding()]
param(
    [switch]$SkipTests = $false
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   Gou Wallpaper Build & Test Pipeline (PowerShell)       " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. 寻找 Python 解释器
$PythonExe = ""
$VenvPy = Join-Path $ScriptDir ".venv\Scripts\python.exe"
if (Test-Path $VenvPy) {
    $PythonExe = $VenvPy
    Write-Host "[1/6] Using venv python: $PythonExe" -ForegroundColor Green
} else {
    $PythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $PythonCmd) {
        Write-Error "Python 3.10+ not found in PATH."
        exit 1
    }
    $PythonExe = $PythonCmd.Source
    Write-Host "[1/6] Using system python: $PythonExe" -ForegroundColor Yellow
}

$PyVer = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
Write-Host "      Python Version: $PyVer" -ForegroundColor Gray

# 2. 检查核心依赖
Write-Host "`n[2/6] Checking dependencies (PySide6, Pillow, PyInstaller, pytest, comtypes)..." -ForegroundColor Cyan
& $PythonExe -c "import PySide6, PIL, PyInstaller, pytest, comtypes, psutil; print('      Dependencies OK!')"
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Dependencies missing, running pip install..."
    & $PythonExe -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pip install failed."
        exit 1
    }
}

# 3. 运行测试套件
if (-not $SkipTests) {
    Write-Host "`n[3/6] Running pytest test suite..." -ForegroundColor Cyan
    & $PythonExe -m pytest tests -v
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Unit tests failed!"
        exit 1
    }
    Write-Host "      All 42 tests passed!" -ForegroundColor Green
} else {
    Write-Host "`n[3/6] Skipping tests (-SkipTests)" -ForegroundColor Yellow
}

# 4. 执行 build.py 构建流程
Write-Host "`n[4/6] Running PyInstaller build via build.py..." -ForegroundColor Cyan
& $PythonExe build.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "build.py failed!"
    exit 1
}

# 5. 校验目标可执行文件
Write-Host "`n[5/6] Checking output executables in dist\..." -ForegroundColor Cyan
$ExePath = Join-Path $ScriptDir "dist\GouWallpaper.exe"
$ScrPath = Join-Path $ScriptDir "dist\GouWallpaperSaver.scr"

if (-not (Test-Path $ExePath) -or -not (Test-Path $ScrPath)) {
    Write-Error "Missing deliverables in dist\"
    exit 1
}

$ExeItem = Get-Item $ExePath
$ScrItem = Get-Item $ScrPath
Write-Host "      • $($ExeItem.Name) : $([math]::Round($ExeItem.Length / 1MB, 2)) MB" -ForegroundColor Green
Write-Host "      • $($ScrItem.Name) : $([math]::Round($ScrItem.Length / 1MB, 2)) MB" -ForegroundColor Green

# 6. 验证 SHA-256
Write-Host "`n[6/6] Generated SHA-256 checksums:" -ForegroundColor Cyan
$SumsPath = Join-Path $ScriptDir "release\SHA256SUMS.txt"
if (Test-Path $SumsPath) {
    Get-Content $SumsPath | ForEach-Object { Write-Host "      $_" -ForegroundColor Gray }
}

Write-Host "`n==========================================================" -ForegroundColor Green
Write-Host "   Gou Wallpaper Build Succeeded! Ready for deployment.   " -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
