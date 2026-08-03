# Add to Chat - VS Code extension installer
# 支持 VS Code / Trae / Cursor / Windsurf

param(
    [string]$Target = "vscode"
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Add to Chat - 扩展安装器" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$targets = @{
    "vscode"   = "$env:USERPROFILE\.vscode\extensions\add-to-chat"
    "trae"     = "$env:USERPROFILE\.trae\extensions\add-to-chat"
    "cursor"   = "$env:USERPROFILE\.cursor\extensions\add-to-chat"
    "windsurf" = "$env:USERPROFILE\.windsurf\extensions\add-to-chat"
    "all"      = $null  # special: install to all
}

if (-not $targets.ContainsKey($Target)) {
    Write-Host "Usage: .\install.ps1 [-Target vscode|trae|cursor|windsurf|all]" -ForegroundColor Yellow
    Write-Host "Default: vscode" -ForegroundColor Yellow
    exit 1
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$files = @("package.json", "extension.js", "README.md")

function Install-To($destDir) {
    Write-Host "[→] 安装到: $destDir" -ForegroundColor Green
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    foreach ($f in $files) {
        $src = Join-Path $scriptDir $f
        $dst = Join-Path $destDir $f
        Copy-Item -Path $src -Destination $dst -Force
        Write-Host "    ✓ $f" -ForegroundColor Gray
    }
}

if ($Target -eq "all") {
    foreach ($key in $targets.Keys) {
        if ($key -ne "all" -and (Test-Path (Split-Path $targets[$key]))) {
            Install-To $targets[$key]
        }
    }
} else {
    Install-To $targets[$Target]
}

Write-Host ""
Write-Host "安装完成! 请重启 IDE 以加载扩展。" -ForegroundColor Green
Write-Host ""
Write-Host "使用方式:" -ForegroundColor Cyan
Write-Host "  Ctrl+Shift+A  →  添加选中文本到对话上下文" -ForegroundColor White
Write-Host "  右键菜单      →  📎 添加到对话上下文" -ForegroundColor White
Write-Host "  状态栏        →  点击查看/管理上下文面板" -ForegroundColor White