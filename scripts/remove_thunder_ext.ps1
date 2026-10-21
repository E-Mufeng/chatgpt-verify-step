#Requires -Version 5.1
<#
  彻底移除「迅雷下载支持」扩展  (ID: ncennffkjdiamlpmcbajkmaiiiddgioo)
  根因：HKLM 外部扩展强制安装项 + 已注入 Codex 各分区的扩展目录 + CRX 缓存

  用法（必须管理员）：
    1) 先完全退出 Codex（窗口关闭 + 托盘退出）
    2) 右键「开始」→ 终端(管理员)
    3) powershell -ExecutionPolicy Bypass -File ".\remove_thunder_ext.ps1"   # 用本脚本的实际路径

  若 Codex 仍在运行且你确认要强制关：
    powershell -ExecutionPolicy Bypass -File "...\remove_thunder_ext.ps1" -Force
#>
param([switch]$Force)

$ErrorActionPreference = 'SilentlyContinue'
$ID = 'ncennffkjdiamlpmcbajkmaiiiddgioo'

function Say($msg, $color = 'Gray') { Write-Host $msg -ForegroundColor $color }

# ---------- 0. 管理员检查 ----------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Say "[X] 不是管理员。请用「终端(管理员)」重新运行本脚本，否则无法删除 HKLM 根因项。" 'Red'
    exit 1
}
Say "[OK] 管理员权限已确认" 'Green'

# ---------- 1. Codex 运行检查 ----------
$procs = Get-Process | Where-Object { $_.Name -match '^(ChatGPT|Codex|codex|codex-code-mode-host|codex-computer-use-swift)$' }
if ($procs) {
    if ($Force) {
        Say "[..] 发现 Codex 进程，-Force 强制结束：$($procs.Id -join ',')" 'Yellow'
        $procs | Stop-Process -Force
        Start-Sleep -Seconds 3
    } else {
        Say "[X] Codex 正在运行（PID: $($procs.Id -join ',')）。" 'Red'
        Say "    请先完全退出 Codex（含系统托盘），再重跑本脚本；或加 -Force 强制关闭。" 'Red'
        exit 2
    }
}

# ---------- 2. 删除 HKLM 强制安装根因项 ----------
$keys = @(
    "HKLM:\Software\WOW6432Node\Google\Chrome\Extensions\$ID",
    "HKLM:\Software\Google\Chrome\Extensions\$ID",
    "HKLM:\Software\WOW6432Node\Chromium\Extensions\$ID",
    "HKLM:\Software\Chromium\Extensions\$ID"
)
foreach ($k in $keys) {
    if (Test-Path $k) {
        try { Remove-Item $k -Recurse -Force -ErrorAction Stop; Say "[OK] 已删除注册表项: $k" 'Green' }
        catch { Say "[X] 删除失败 $k : $($_.Exception.Message)" 'Red' }
    } else { Say "[--] 不存在: $k" }
}

# ---------- 3. 删除所有 Codex 分区 / 默认 profile 里的扩展目录 ----------
$profiles = @()
Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Packages') -Directory |
    Where-Object { $_.Name -like 'OpenAI.Codex*' } | ForEach-Object {
        $web = Join-Path $_.FullName 'LocalCache\Roaming\Codex\web\Codex'
        if (Test-Path $web) { $profiles += $web }
    }
if ($profiles.Count -eq 0) {
    $known = Join-Path $env:LOCALAPPDATA 'Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Roaming\Codex\web\Codex'
    if (Test-Path $known) { $profiles += $known }
}

foreach ($web in $profiles) {
    Say "== profile: $web"
    $targets = @( (Join-Path $web "Default\Extensions\$ID") )
    Get-ChildItem (Join-Path $web 'Default\Partitions') -Directory | ForEach-Object {
        $targets += (Join-Path $_.FullName "Extensions\$ID")
    }
    foreach ($t in $targets) {
        if (Test-Path $t) {
            try { Remove-Item $t -Recurse -Force -ErrorAction Stop; Say "[OK] 已删除扩展目录: $t" 'Green' }
            catch { Say "[X] 删除失败 $t : $($_.Exception.Message)" 'Red' }
        }
    }
    # 兜底：扫每个 Extensions 目录里名字含 ID 的残留
    $extDirs = @( (Join-Path $web 'Default\Extensions') )
    Get-ChildItem (Join-Path $web 'Default\Partitions') -Directory | ForEach-Object {
        $extDirs += (Join-Path $_.FullName 'Extensions')
    }
    foreach ($e in $extDirs) {
        if (Test-Path $e) {
            Get-ChildItem $e -Directory | Where-Object { $_.Name -like "*$ID*" } | ForEach-Object {
                try { Remove-Item $_.FullName -Recurse -Force -ErrorAction Stop; Say "[OK] 已删除残留目录: $($_.FullName)" 'Green' }
                catch { Say "[X] 残留删除失败: $($_.FullName)" 'Red' }
            }
        }
    }
}

# ---------- 4. 删除 CRX 缓存 ----------
$cache = Join-Path $env:LOCALAPPDATA 'ChromeExtensionCache'
Get-ChildItem $cache | Where-Object { $_.Name -like 'xl_ext_chrome*' } | ForEach-Object {
    try { Remove-Item $_.FullName -Force -ErrorAction Stop; Say "[OK] 已删除 CRX: $($_.Name)" 'Green' }
    catch { Say "[X] CRX 删除失败: $($_.Name)" 'Red' }
}
if (-not (Get-ChildItem $cache | Where-Object { $_.Name -like 'xl_ext_chrome*' })) { Say "[--] CRX 缓存已无相关文件" }

# ---------- 5. 清理迅雷客户端残留（可选，迅雷已卸载时安全） ----------
foreach ($t in @( (Join-Path $env:LOCALAPPDATA 'Thunder Network'), (Join-Path $env:APPDATA 'Thunder Network') )) {
    if (Test-Path $t) {
        try { Remove-Item $t -Recurse -Force -ErrorAction Stop; Say "[OK] 已删除迅雷残留目录: $t" 'Green' }
        catch { Say "[X] 迅雷目录删除失败（可能被占用）: $t" 'Red' }
    }
}
if (Test-Path 'HKCU:\Software\Thunder Network') {
    try { Remove-Item 'HKCU:\Software\Thunder Network' -Recurse -Force -ErrorAction Stop; Say "[OK] 已删除 HKCU\Software\Thunder Network" 'Green' }
    catch { Say "[X] 迅雷注册表删除失败" 'Red' }
}

# ---------- 6. 验证 ----------
Say "`n===== 验证 =====" 'Cyan'
$bad = $false
foreach ($k in $keys) { if (Test-Path $k) { Say "[X] 注册表项仍存在: $k" 'Red'; $bad = $true } }
foreach ($web in $profiles) {
    $extDirs = @( (Join-Path $web 'Default\Extensions') )
    Get-ChildItem (Join-Path $web 'Default\Partitions') -Directory | ForEach-Object {
        $extDirs += (Join-Path $_.FullName 'Extensions')
    }
    foreach ($e in $extDirs) {
        if (Test-Path $e) {
            $left = Get-ChildItem $e -Directory | Where-Object { $_.Name -like "*$ID*" }
            if ($left) { foreach ($l in $left) { Say "[X] 扩展目录仍在: $($l.FullName)" 'Red'; $bad = $true } }
        }
    }
}
if (-not $bad) {
    Say "[PASS] 注册表 + 全部分区扩展目录 + CRX 缓存 均已清理干净。" 'Green'
    Say "       注：profile 的 Preferences/Secure Preferences 里可能还留有旧条目，" 'Gray'
    Say "       Codex 下次启动发现扩展目录已不存在且无强制安装源，会自动清除，无需手改（避免破坏 MAC）。" 'Gray'
}
Say "`n完成。现在重新启动 Codex，打开浏览器面板访问 chatgpt.com 验证是否还会黑屏。" 'Cyan'
