[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "SCUM 服务端下载与更新"

$rootDir = $PSScriptRoot
$steamcmdPath = Join-Path $rootDir "steamcmd\steamcmd.exe"
$serverDir = Join-Path $rootDir "server"

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "             SCUM 专用服务端下载/更新程序" -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $steamcmdPath)) {
    Write-Host "[1/2] 正在下载 SteamCMD 官方工具..." -ForegroundColor Yellow
    $zipPath = Join-Path $rootDir "steamcmd.zip"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip" -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath (Join-Path $rootDir "steamcmd") -Force
    Remove-Item $zipPath -Force
}

Write-Host "[2/2] 正在连接 Steam 下载/更新 SCUM 服务端 (AppID: 3792580)..." -ForegroundColor Yellow
Write-Host "提示：首次下载大小约为 20~30 GB，下载时长取决于您的网络速度，请保持窗口开启..." -ForegroundColor Gray
Write-Host ""

& $steamcmdPath +force_install_dir $serverDir +login anonymous +app_update 3792580 validate +quit

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "========================================================" -ForegroundColor Green
    Write-Host "服务端下载/更新完成！" -ForegroundColor Green
    Write-Host "========================================================" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "========================================================" -ForegroundColor Red
    Write-Host "下载可能中断或未完全完成（代码: $LASTEXITCODE）。如果未完成，请重新运行此程序继续下载。" -ForegroundColor Yellow
    Write-Host "========================================================" -ForegroundColor Red
}
