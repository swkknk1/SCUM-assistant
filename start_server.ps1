[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "SCUM 专用服务器启动程序"

$rootDir = $PSScriptRoot
$serverExe = Join-Path $rootDir "server\SCUM\Binaries\Win64\SCUMServer.exe"

if (-not (Test-Path $serverExe)) {
    Write-Host "[错误] 未检测到 SCUMServer.exe！" -ForegroundColor Red
    Write-Host "请先运行 '1-下载或更新服务端.bat' 完成服务端下载后，再启动服务器。" -ForegroundColor Yellow
    Write-Host ""
    Read-Host "按回车键退出..."
    exit 1
}

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "             正在启动 SCUM 专用服务器..." -ForegroundColor Green
Write-Host ""
Write-Host "端口配置说明:" -ForegroundColor Yellow
Write-Host "  - 游戏主端口 (Game Port): 7777 (UDP)" -ForegroundColor White
Write-Host "  - 原始数据端口 (Raw Port): 7778 (UDP)" -ForegroundColor White
Write-Host "  - 查询/直连端口 (Query Port): 7779 (UDP)" -ForegroundColor White
Write-Host ""
Write-Host "联机重要提醒:" -ForegroundColor Yellow
Write-Host "  - 朋友在游戏内【直接连接】时，必须输入: 你的IP:7779 (注意端口是 7779)" -ForegroundColor Cyan
Write-Host "  - 首次运行会自动生成配置文件，之后可在 server\SCUM\Saved\Config\WindowsServer\ServerSettings.ini 中修改" -ForegroundColor Gray
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

$exeDir = Split-Path $serverExe
Start-Process -FilePath $serverExe -ArgumentList "-log -port=7777 -QueryPort=7779 -MaxPlayers=32" -WorkingDirectory $exeDir
