# 打包单文件 exe（PyInstaller）
#
#   .\build.ps1                       # 普通版（不含第三方 LiteMonitor）
#   .\build.ps1 -LiteSource <目录>     # 把 LiteMonitor 一起打进去（需自行确认授权）
#
param(
    [string]$LiteSource = "",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path "app.ico")) {
    & $Python make_icon.py
}
# 注意：--specpath 会改变相对路径的解析基准，图标必须用绝对路径
$icon = (Resolve-Path "app.ico").Path

$pyArgs = @(
    "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onefile", "--windowed",
    "--icon", $icon,
    "--name", "浏览器清理工具",
    "--paths", "src",
    "--distpath", "dist",
    "--workpath", "build",
    "--specpath", "build"
)

if ($LiteSource) {
    if (-not (Test-Path (Join-Path $LiteSource "LiteMonitor.exe"))) {
        throw "LiteMonitor.exe 不在 $LiteSource"
    }
    $pyArgs += @("--add-data", "$LiteSource;litemonitor")
    Write-Host "将把 $LiteSource 打包进 exe（请确认拥有再分发授权）"
}

$pyArgs += "src/main.py"
& $Python @pyArgs

Write-Host ""
Write-Host "输出：dist\浏览器清理工具.exe"
