#============================================================
#  平台化企业智能问数工作台 - 一键配置 (setup.bat 调用的主逻辑)
#  版本: v2.6.7 - 产品模式: 门户一键(在线装配插件 + 后台启动 + 自动开浏览器)
#                幂等: 双库已初始化时用只读账号探活即跳过建库(root 密码不再反复询问)
#                v2.5: MySQL/Node 下载走多镜像(官方CDN+华为云+清华)并校验 ZIP 魔数,
#                      网关把下载换成 HTML 拦截页时自动换镜像重试, 不再解压报错中断
#                v2.6: 修复 Windows PowerShell 5.1 下 mysqld --initialize 的 stderr 日志
#                      经 2>&1 合并后被 EAP=Stop 当成致命错误逐行中断的问题(stderr 改落盘)
#                v2.6.1: mysqld 启动失败时把 .runtime\mysqld.err.log 尾部直接打到屏幕,
#                      并自动清理上次中断残留、占用 3306 的其它便携 mysqld 实例
#                v2.6.2: 修复 root 空密码时 init_db.py/db_init.py 报 "expected one argument"
#                      (PS5.1 丢弃空字符串参数) -> 改传 "--password=<值>" 单 token; 简化便携实例密码询问
#                v2.6.3: 缺 deepseek-harness 源码/依赖时, 3/6 与菜单 [F]/[D] 自动联网获取
#                      (git 浅克隆或官方 zip) 并 pnpm install, 不再报错要求手动装;
#                      ROOT 定位兼容两种启动方式(-File 与 scriptblock), 避免 Path 为空崩溃;
#                      git clone 的 stderr 进度不再用 2>&1 合并(PS5.1 + EAP=Stop 会误判为
#                      NativeCommandError 逐行中断), 改为降 EAP + 看 $LASTEXITCODE
#                v2.6.4: pnpm install 增加窗口顶部进度条 —— 百分比按 store 下载字节/预估 1.5GB,
#                      解析阶段自动转圈; 后台 node 直跑 pnpm + 日志轮询, 找不到真实入口时自动降级前台直跑
#                v2.6.5: pnpm 进度改为"真进度" —— 优先 --reporter=ndjson 结构化事件,
#                      按包计数显示 解析 N / 已下载 X / 已链接 Y; 老 pnpm 不认该参数时
#                      自动回退 v2.6.4 的体积估算, 两者都不影响安装本身
#                v2.6.6: 适配 pnpm 11/12 真实 ndjson 事件(level 为字符串、imported 事件不带
#                      packageId 需从 to 路径反推、pnpm:stage 给出真实阶段), 进度按"解析/
#                      下载/链接"显示; 回显 stderr(corepack 下载/网络错误不再隐形);
#                      日志长时间无输出时提示"可能在等网络"; 检测到直连 registry.npmjs.org
#                      时仅对本次安装注入国内镜像(DSH_SETUP_NO_MIRROR=1 可关闭)
#                v2.6.7: 关闭 corepack 下载 pnpm 版本时的交互询问(COREPACK_ENABLE_DOWNLOAD_PROMPT=0) ——
#                      后台重定向下"等回车"会永久卡住, 表现为进度条一直转但解析 0 个包一小时;
#                      启用镜像时同步设置 COREPACK_NPM_REGISTRY(corepack 不读 npm_config_registry);
#                      解析/等待阶段改用不确定进度(-1), 不再用"百分比在动而计数为 0"的假进度;
#                      30 秒无任何 pnpm 事件时明确提示"可能在下载 pnpm 版本/等待网络";
#                      子进程 stdin 改指空文件(corepack 仅在 stdin 为 TTY 时询问), 双重保险
#
#  环境识别(不以 PATH 命令为准, 避免装了服务但无命令行工具被误判):
#    Python    : 依次找 PATH python / py 启动器 / 常见安装目录
#    MySQL     : 探测 127.0.0.1:3306 TCP 是否可达(服务在跑即算有)
#    Node/pnpm : 仅"dsh 图形门户"需要; 3/6 自动定位/下载便携 Node(zip)
#                并用 npm 装 pnpm; harness 与 ~/.dsh/profiles/web 缺依赖时自动
#                pnpm install(需联网, harness≈1.5GB, 插件层数百 MB)
#    winget    : 仅作官网下载失败的兜底通道(非必需)
#
#  自动下载开关(均需联网; 版本/URL 于 2026-09 实测可达):
#    -AutoInstall     缺 python 时下载官方 python.org 安装器静默安装;
#                     缺 node 时下载官方 nodejs.org 便携 zip 解压到 .runtime\nodejs
#    -AutoMySQLZip    3306 不通时, 下载 CDN 直链 MySQL 8.0 ZIP 便携版到 .runtime,
#                     本地初始化(root 空密码)并启动, 供本机自举演示
#    -SkipDB          跳过建库造数
#    -SkipPortal      跳过 dsh 门户装配(配置/插件层)与依赖自举
#    -Verify          执行 47 条评测回归(默认关闭, 避免覆盖手写报告)
#    -ResetProfile    强制重建 ~/.dsh/profiles/web(清旧冲突依赖后在线重装插件)
#    -Product         无人值守产品模式 = AutoInstall+AutoMySQLZip, 装配完成后
#                     直接后台启动门户并自动打开浏览器(见第 5 步 [F])
#
#  产品用法: 全新机器一键 = setup.bat -Product      (全部自动, 完成后浏览器打开门户)
#  示例(带评测): setup.bat -Product -Verify
# ============================================================
[CmdletBinding()]
param(
    [string]$MySQLRootPassword = "",
    [switch]$AutoInstall,
    [switch]$AutoMySQLZip,
    [switch]$SkipDB,
    [switch]$SkipPortal,
    [switch]$Verify,
    [switch]$ResetProfile,
    [switch]$Product
)
$ErrorActionPreference = "Stop"
# 本脚本大量调用原生命令并以 $LASTEXITCODE 判断成败:
# 关闭 PS7.3+ "原生 stderr → 终止错误" 的默认行为, 避免告警类 stderr 误伤主流程
$PSNativeCommandUseErrorActionPreference = $false
# corepack 首次下载 pnpm/其它包管理器版本时会交互询问 "Do you want to continue? [Y/n]";
# 本脚本的安装/启动进程都是后台重定向运行的 —— 提示进了日志、也永远等不到回车,
# 表现为"进度条一直转但解析 0 个包"(像卡死)。这里统一关闭交互询问(自动继续)。
$env:COREPACK_ENABLE_DOWNLOAD_PROMPT = "0"
# 包根目录: 兼容两种启动方式
#   a) powershell -File setup.ps1  (setup.bat v2.6.3+): $MyInvocation 有脚本路径
#   b) scriptblock 动态加载运行: 路径为 null, 兜底取当前目录(setup.bat 启动前已 cd /d 到包根)
$ROOT = $null
if ($MyInvocation.MyCommand.Path) { $ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $ROOT -and $PSScriptRoot) { $ROOT = $PSScriptRoot }
if (-not $ROOT) { $ROOT = (Get-Location).Path }
# -Product = 无人值守产品模式: 自动补环境/MySQL, 装配完整后直接启动门户并开浏览器
if ($Product) { $AutoInstall = $true; $AutoMySQLZip = $true; $SkipDB = $false; $SkipPortal = $false }
$VENV = Join-Path $ROOT "bi_workbench\.venv"
$PY   = Join-Path $VENV "Scripts\python.exe"

# ---- 自动下载版本/URL (2026-09-09 已实测可达) ----
# Python: 官方 python.org 安装器(用户级静默安装, 装到 %LOCALAPPDATA%\Programs\Python\Python313)
$PY_VERSION   = "3.13.15"
$PY_EXE_NAME  = "python-$PY_VERSION-amd64.exe"
$PY_EXE_URL   = "https://www.python.org/ftp/python/$PY_VERSION/$PY_EXE_NAME"
# Node: 官方 nodejs.org LTS 便携 zip(解压即用, 无需管理员)
$NODE_VERSION = "v24.21.0"
$NODE_ZIP_NAME = "node-$NODE_VERSION-win-x64.zip"
$NODE_ZIP_URL  = "https://nodejs.org/dist/$NODE_VERSION/$NODE_ZIP_NAME"
# MySQL: 官方 8.0 ZIP 便携版 (注意: dev.mysql.com/get 网关常 403, 优先 CDN 直链)
$MYSQL_ZIP_NAME = "mysql-8.0.45-winx64.zip"
$MYSQL_ZIP_URLS = @(
    "https://cdn.mysql.com/Downloads/MySQL-8.0/$MYSQL_ZIP_NAME",                                          # 官方 CDN
    "https://mirrors.huaweicloud.com/mysql/Downloads/MySQL-8.0/$MYSQL_ZIP_NAME",                          # 华为云镜像
    "https://mirrors.tuna.tsinghua.edu.cn/mysql/downloads/MySQL-8.0/$MYSQL_ZIP_NAME",                     # 清华镜像
    "https://dev.mysql.com/get/Downloads/MySQL-8.0/$MYSQL_ZIP_NAME"                                       # 官方网关(常 403, 仅兜底)
)
$RUNTIME = Join-Path $ROOT ".runtime"

function Step([string]$t) { Write-Host "`n===== $t =====" -ForegroundColor Cyan }
function Ok([string]$t)   { Write-Host "  [OK] $t" -ForegroundColor Green }
function Warn([string]$t) { Write-Host "  [!] $t" -ForegroundColor Yellow }
function Err([string]$t)  { Write-Host "  [ERR] $t" -ForegroundColor Red }

# 等一个 TCP 端口就绪(最多 n 秒)
function Wait-Port([int]$port, [int]$timeoutSec = 30) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $timeoutSec) {
        $c = New-Object Net.Sockets.TcpClient
        try { $c.Connect("127.0.0.1", $port); $c.Close(); return $true }
        catch { $c.Dispose(); Start-Sleep -Milliseconds 500 }
    }
    return $false
}

# 探测"双库已初始化": 用只读账号(bi_ro/hr_ro)连库并查表, 全通返回 $true。
# 用于产品模式幂等: 已就绪则无需 root 密码重复建库造数, 一键重跑不再打断。
# 注意: 内嵌 python 仅用单引号, 规避 pwsh 向原生命令传参时剥掉双引号的问题。
function Test-BiDbReady {
    if (-not (Test-Path $PY)) { return $false }
    $code = @'
import pymysql, sys
def t(u, pw, db, q):
    try:
        c = pymysql.connect(host='127.0.0.1', port=3306, user=u, password=pw,
                            database=db, connect_timeout=2)
        cur = c.cursor(); cur.execute(q); cur.fetchone(); c.close(); return True
    except Exception:
        return False
ok = t('bi_ro', 'bi_ro_pass_2026', 'bi_workbench', 'SELECT COUNT(*) FROM audit_log') and t('hr_ro', 'hr_ro_pass_2026', 'hr_bi', 'SELECT COUNT(*) FROM hr_audit')
sys.exit(0 if ok else 1)
'@
    $old = $PSNativeCommandUseErrorActionPreference
    $PSNativeCommandUseErrorActionPreference = $false
    try { & $PY -c $code 2>$null } finally { $PSNativeCommandUseErrorActionPreference = $old }
    return ($LASTEXITCODE -eq 0)
}

# ------------------------------------------------------------ 下载/运行时工具
# 校验 ZIP 魔数(PK\x03\x04 / 空包 PK\x05\x06), 识别被网关换成 HTML 错误页的假文件
function Test-ZipFile([string]$Path) {
    try {
        $fs = [IO.File]::OpenRead($Path)
        try {
            $b = New-Object byte[] 4
            $null = $fs.Read($b, 0, 4)
            return (($b[0] -eq 0x50) -and ($b[1] -eq 0x4B) -and
                    (($b[2] -eq 0x03) -or ($b[2] -eq 0x05) -or ($b[2] -eq 0x07)))
        } finally { $fs.Close() }
    } catch { return $false }
}

# 下载文件(已存在则跳过; 失败自动清理残件并返回 $false)
#  -Zip: 下载后校验 ZIP 头, 假文件(HTML 拦截页)一律判失败以便换镜像重试
function Download-File([string]$Url, [string]$Out, [string]$Label, [switch]$Zip) {
    if (Test-Path $Out) { Ok "$Label 已存在, 跳过下载: $Out"; return $true }
    try {
        Warn "下载 $Label (需联网, 稍候) ..."
        Invoke-WebRequest -Uri $Url -OutFile $Out -UseBasicParsing -TimeoutSec 900 -UserAgent "Mozilla/5.0"
        $len = (Get-Item $Out).Length
        if ($len -lt 1KB) { throw "下载文件过小(疑似被网关拦截)" }
        if ($Zip -and -not (Test-ZipFile $Out)) { throw "不是有效 ZIP(疑似下载到 HTML 拦截页), 换下一镜像重试" }
        Ok "$Label 下载完成 ($([math]::Round($len/1MB,1)) MB)"
        return $true
    } catch {
        Err "下载 $Label 失败: $($_.Exception.Message)"
        if (Test-Path $Out) { Remove-Item $Out -Force -ErrorAction SilentlyContinue }
        return $false
    }
}

# 探测 .runtime\nodejs 下已解压的便携 node, 命中则把其目录加入当前进程 PATH
function Find-PortableNode {
    $root = Join-Path $RUNTIME "nodejs"
    $d = Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue |
         Where-Object { Test-Path (Join-Path $_.FullName "node.exe") } | Select-Object -First 1
    if ($d) { $env:PATH = "$($d.FullName);$env:PATH"; return (Join-Path $d.FullName "node.exe") }
    return $null
}

# 下载官方 Node 便携 zip 并解压到 .runtime\nodejs, 加入 PATH; 返回 node.exe 或 $null
function Install-PortableNode {
    $root = Join-Path $RUNTIME "nodejs"
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $zip = Join-Path $root $NODE_ZIP_NAME
    if (-not (Download-File -Url $NODE_ZIP_URL -Out $zip -Label "Node.js $NODE_VERSION 便携包" -Zip)) { return $null }
    try { Expand-Archive -Path $zip -DestinationPath $root -Force }
    catch { Err "解压 node 便携包失败: $($_.Exception.Message)"; return $null }
    $nodeExe = Join-Path (Join-Path $root ($NODE_ZIP_NAME -replace "\.zip$", "")) "node.exe"
    if (-not (Test-Path $nodeExe)) { Err "便携 node 解压异常, 未找到 node.exe"; return $null }
    $env:PATH = "$(Split-Path $nodeExe);$env:PATH"
    Ok "便携 node 就绪: $nodeExe"
    return $nodeExe
}

# 用已就绪 node 的 npm 全局安装 pnpm@12(幂等); 返回 pnpm 命令或 $null
function Ensure-Pnpm([string]$NodeExe) {
    $pnpm = (Get-Command pnpm -ErrorAction SilentlyContinue).Source
    if ($pnpm) { return $pnpm }
    $npmCmd = Join-Path (Split-Path $NodeExe) "npm.cmd"
    if (-not (Test-Path $npmCmd)) { return $null }
    Warn "未检测到 pnpm, 用 npm 安装 pnpm@12 ..."
    & $npmCmd install -g pnpm@12 --silent | Out-Null
    $pnpm = (Get-Command pnpm -ErrorAction SilentlyContinue).Source
    if (-not $pnpm) {
        $prefix = (& $npmCmd prefix -g 2>$null | Select-Object -First 1)
        $pc = Join-Path $prefix "pnpm.cmd"
        if (Test-Path $pc) { $pnpm = $pc }
    }
    return $pnpm
}

# 由 dsh-cordis.patch.yml 模板生成"当前解压根"的 MCP 补丁({{ROOT}} 占位 → 实际路径)
function New-DshPatch {
    $tpl = Join-Path $ROOT "dsh-cordis.patch.yml"
    $out = Join-Path $RUNTIME "dsh-cordis.generated.yml"
    if (-not (Test-Path $tpl)) { return $null }
    New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
    $rootF = $ROOT.Replace("\", "/")
    $txt  = (Get-Content -LiteralPath $tpl -Raw).Replace("{{ROOT}}", $rootF)
    [IO.File]::WriteAllText($out, $txt, (New-Object System.Text.UTF8Encoding($true)))
    Ok "已生成运行时 MCP 补丁: $out"
    return $out
}

# 定位 node: PATH → 用户级安装 → .runtime 便携; AutoInstall 时自动下载便携版
function Ensure-NodeRuntime {
    $ne = (Get-Command node -ErrorAction SilentlyContinue).Source
    if (-not $ne) { $ne = Find-PortableNode }
    if (-not $ne -and $AutoInstall) {
        Warn "未检测到 node, 自动下载官方 Node $NODE_VERSION 便携包到 .runtime (约 36MB)..."
        $ne = Install-PortableNode
    }
    return $ne
}

# 解析 dsh 门户本机地址(广告/第三方域名绝不参与匹配):
#   1) 优先 stdout 中 "dsh web:"/"Local:" 等显式标记后的 URL;
#   2) 其次在 out+err 里找 localhost/127.0.0.1 等回环地址;
#   返回 $null 表示尚未就绪。注: stderr 常先出现 gofastmcp/fastmcp.cloud 横幅, 只作诊断不用于开浏览器。
function Select-PortalUrl([string]$stdout, [string]$stderr) {
    if ($stdout) {
        $m = [regex]::Match($stdout, '(?:dsh web|Local|listening at|http server)[:：]*\s*(https?://[^\s"<>|]+)', [Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($m.Success) { return $m.Groups[1].Value.TrimEnd(')',']',',','.','|',';','；','，') }
    }
    $all = ($stdout + "`n" + $stderr)
    foreach ($m in [regex]::Matches($all, 'https?://[^\s"<>|]+')) {
        if ($m.Value -match 'https?://(localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)') {
            return $m.Value.TrimEnd(')',']',',','.','|',';','；','，')
        }
    }
    return $null
}

# 后台启动 dsh web(隐藏窗口 + 日志), 轮询日志解析地址并自动打开浏览器
function Start-DshPortal {
    param([string]$Harness)
    # 幂等: 上次启动的门户若仍在运行(端口可连), 直接复用并打开, 不重复起实例
    $plog = Join-Path $RUNTIME "logs\dsh-web.out.log"
    if (Test-Path $plog) {
        $pm = [regex]::Match((Get-Content -LiteralPath $plog -Raw -ErrorAction SilentlyContinue), 'dsh web:\s*(https?://[^\s"<>|]+)')
        if ($pm.Success) {
            $pu = $pm.Groups[1].Value.TrimEnd(')',']',',','.','|',';','；','，')
            $hm = [regex]::Match($pu, 'https?://(localhost|127\.0\.0\.1|\[::1\]):(\d+)')
            if ($hm.Success -and (Wait-Port ([int]$hm.Groups[2].Value) 2)) {
                Ok "门户实例已在运行, 直接复用: $pu"
                try { Start-Process $pu | Out-Null; Ok "已自动打开浏览器" }
                catch { Warn "自动打开浏览器失败, 请手动复制上面的地址访问" }
                return
            }
        }
    }
    if (-not (Test-Path (Join-Path $Harness "node_modules"))) {
        Err "deepseek-harness 依赖未安装, 无法启动门户。请先完整执行本脚本 3/6 或手动 cd '$Harness' 后 pnpm install"
        return
    }
    $nodeExe = Ensure-NodeRuntime
    if (-not $nodeExe) { Err "node 不可用, 无法启动门户"; return }
    $pnpm = Ensure-Pnpm $nodeExe
    if (-not $pnpm) { Err "pnpm 不可用, 无法启动门户"; return }
    # 门户插件依赖(~/.dsh/profiles/web)缺 node_modules 时在线安装
    $web = Join-Path $env:USERPROFILE ".dsh\profiles\web"
    if (Test-Path (Join-Path $web "package.json")) {
        if (-not (Test-Path (Join-Path $web "node_modules"))) {
            Warn "~/.dsh/profiles/web 插件未安装, 在线 pnpm install(需联网, 数百 MB, 可能数分钟)..."
            Push-Location $web
            try { & $pnpm install --no-frozen-lockfile; if ($LASTEXITCODE -ne 0) { Err "插件安装失败, 请检查网络后重试"; return } }
            finally { Pop-Location }
        }
    }
    $gen = New-DshPatch
    if (-not $gen) { Err "未找到 dsh-cordis.patch.yml 模板, 无法启动门户"; return }
    $logDir = Join-Path $RUNTIME "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $o = Join-Path $logDir "dsh-web.out.log"; $e = Join-Path $logDir "dsh-web.err.log"
    Remove-Item $o, $e -Force -ErrorAction SilentlyContinue
    # 通过临时 launcher 脚本在 harness 目录以 node 直跑 dsh(绕开 pnpm/cmd 引号问题)
    $launcher = Join-Path $logDir "dsh-web-launch.cmd"
    $cmdline = '@echo off' + "`r`n" + 'cd /d "' + $Harness + '" && "' + $nodeExe + '" --import tsx/esm apps/cli/src/bin.ts web --patch "' + $gen + '" > "' + $o + '" 2> "' + $e + '"'
    [IO.File]::WriteAllText($launcher, $cmdline, (New-Object System.Text.UTF8Encoding($false)))
    Ok "后台启动 dsh web(隐藏窗口)..."
    $proc = Start-Process -FilePath $launcher -WindowStyle Hidden -PassThru

    # 轮询日志解析门户地址(最多 120s)并自动开浏览器
    $url = $null; $outTxt = $null; $errTxt = $null
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt 120) {
        Start-Sleep -Seconds 3
        if (Test-Path $o) { $outTxt = Get-Content -LiteralPath $o -Raw -ErrorAction SilentlyContinue }
        if (Test-Path $e) { $errTxt = Get-Content -LiteralPath $e -Raw -ErrorAction SilentlyContinue }
        $url = Select-PortalUrl $outTxt $errTxt
        if ($url) { break }
        if ($proc.HasExited) { break }
    }
    if ($url) {
        Ok "门户已就绪: $url"
        try { Start-Process $url | Out-Null; Ok "已自动打开浏览器" }
        catch { Warn "自动打开浏览器失败, 请手动复制上面的地址访问" }
        Warn "门户在后台运行(隐藏窗口/日志: $o); 业务后端(MCP)由 dsh 按补丁自动拉起; 停止方式: 关闭隐藏窗口或重启后重跑 setup.bat -Product"
    } else {
        if ($proc.HasExited) {
            Err "dsh web 进程已退出(启动失败)。错误日志尾部:"
        } else { Warn "120s 内未解析到门户地址, 请查看日志: $o / $e" }
        if (Test-Path $e) { Get-Content -LiteralPath $e -Tail 15 | ForEach-Object { Write-Host "      $_" } }
    }
}

# 带进度条执行 pnpm install —— 优先"真进度":
#   加 --reporter=ndjson 让 pnpm 输出结构化事件, 解析每个包的三阶段状态
#   (resolved 已解析 / fetched|found_in_store 已下载 / imported 已链接),
#   进度百分比 = 已下载(或已链接)包数 / 已解析包数 —— 真实计数, 不再是估算;
#   解析阶段(pnpm 可能静默数分钟)显示转圈 + 已解析包数, 避免误认为卡死。
#   个别老 pnpm 不认 --reporter=ndjson(立即报错退出)时, 自动去掉该参数重跑一次,
#   并回退 v2.6.4 的 store 体积估算进度(百分比 = 增量字节 / 预估 1.5GB)。
#   后台用 node 直跑 pnpm 的 cjs/js 入口(绕过 .cmd 包装以便重定向 + 轮询);
#   找不到真实入口时降级为前台直跑(保留 pnpm 自带 UI)。
# 返回 $true=成功 / $false=失败(失败时已打印错误日志尾部)。
function Invoke-PnpmInstallProgress {
    param([string]$WorkDir, [string]$PnpmCmd, [string]$NodeExe, [string]$Label,
          [string]$ErrHint = "可重跑本脚本, 或手动: cd '$WorkDir' ; pnpm install")
    $logDir = Join-Path $RUNTIME "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $o = Join-Path $logDir "pnpm-install.out.log"
    $e = Join-Path $logDir "pnpm-install.err.log"
    Remove-Item $o, $e -Force -ErrorAction SilentlyContinue
    # stdin 指向空文件(而非控制台): corepack 只在 stdin 是 TTY 时才弹交互询问,
    # 这样即使 corepack 版本不认 COREPACK_ENABLE_DOWNLOAD_PROMPT 也不会挂住等回车
    $in0 = Join-Path $logDir "pnpm-install.stdin"
    [IO.File]::WriteAllText($in0, "")
    $js = $null
    if ($PnpmCmd) {
        $wrapDir = Split-Path $PnpmCmd
        # 候选1: npm 全局安装布局  <prefix>\node_modules\pnpm\bin\pnpm.cjs
        $c1 = Join-Path $wrapDir "node_modules\pnpm\bin\pnpm.cjs"
        if (Test-Path $c1) { $js = $c1 }
        if (-not $js) {
            # 候选2: corepack shim 布局  <nodejs>\node_modules\corepack\dist\pnpm.js
            # (pnpm.ps1/.cmd 与 corepack 同目录; node 直跑该文件 = 执行 corepack pnpm,
            #  会按工程 packageManager 字段自动选用正确版本, 与命令行 pnpm 完全等价)
            $c2 = Join-Path $wrapDir "node_modules\corepack\dist\pnpm.js"
            if (Test-Path $c2) { $js = $c2 }
        }
    }
    if (-not $js -or -not $NodeExe) {
        # 兜底: 前台直跑, pnpm 自带的进度仍直接显示
        Push-Location $WorkDir
        try { & $PnpmCmd install --no-frozen-lockfile; return ($LASTEXITCODE -eq 0) }
        finally { Pop-Location }
    }
    Warn "$Label (进度条在窗口顶部, 请保持联网; 详细日志: $o)"
    # 国内直连 registry.npmjs.org 常见极慢(解析/下载一卡几十分钟, 看着像卡死):
    # 未检测到自定义源时, 仅对本次安装进程注入国内镜像(不改用户全局 .npmrc);
    # 需要临时关闭: set DSH_SETUP_NO_MIRROR=1
    if (-not $env:DSH_SETUP_NO_MIRROR) {
        $regCfg = $env:npm_config_registry
        if (-not $regCfg) {
            foreach ($rc in @((Join-Path $env:USERPROFILE '.npmrc'), (Join-Path $WorkDir '.npmrc'))) {
                if (Test-Path $rc) {
                    $m = [regex]::Match((Get-Content -LiteralPath $rc -Raw -ErrorAction SilentlyContinue), '(?m)^\s*registry\s*=\s*(\S+)')
                    if ($m.Success) { $regCfg = $m.Groups[1].Value }
                }
            }
        }
        if (-not $regCfg -or $regCfg -match 'registry\.npmjs\.org') {
            $env:npm_config_registry = 'https://registry.npmmirror.com'
            # corepack 下 pnpm 版本走它自己的变量(不读 npm_config_registry), 一起指到镜像
            if (-not $env:COREPACK_NPM_REGISTRY) { $env:COREPACK_NPM_REGISTRY = 'https://registry.npmmirror.com' }
            Warn "未检测到自定义 npm 源, 已为本次安装启用国内镜像 registry.npmmirror.com(设置 DSH_SETUP_NO_MIRROR=1 可关闭)"
        }
    }
    $useNdjson = $true     # 真进度开关; 老 pnpm 不认该参数时置 $false 重跑
    $summarySeen = $false  # ndjson 模式下 "安装完成汇总" 是否出现(成功兜底判据)
    $attempt = 0
    $code = -1
    while ($true) {
        $attempt++
        Remove-Item $o, $e -Force -ErrorAction SilentlyContinue
        $pnpmArgs = @($js, 'install', '--no-frozen-lockfile')
        if ($useNdjson) { $pnpmArgs += '--reporter=ndjson' }
        # 体积估算兜底用: store 常见落点(自定义 store-dir 的场景尽量都覆盖) + node_modules\.pnpm
        $measDirs = @(
            (Join-Path $env:LOCALAPPDATA "pnpm\store"),
            (Join-Path $WorkDir "node_modules\.pnpm"),
            (Join-Path $WorkDir ".pnpm-store"),
            (Join-Path (Split-Path $WorkDir) ".pnpm-store")
        )
        $size0 = 0
        if (-not $useNdjson) {
            foreach ($m in $measDirs) { if (Test-Path $m) { $size0 += @(Get-ChildItem -LiteralPath $m -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum } }
        }
        # 真进度计数集合(按包名去重)
        $resolved = New-Object 'System.Collections.Generic.HashSet[string]'
        $obtained = New-Object 'System.Collections.Generic.HashSet[string]'
        $imported = New-Object 'System.Collections.Generic.HashSet[string]'
        $lastOut = 0; $lastErr = 0; $spin = 0; $tick = 0; $echoN = 0
        $stage = ''; $lastGrowAt = 0.0; $gotAnyEvent = $false   # 真实阶段 / 日志最后增长时刻 / 是否收到过任何 pnpm 事件
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $proc = Start-Process -FilePath $NodeExe -ArgumentList $pnpmArgs -WorkingDirectory $WorkDir -RedirectStandardOutput $o -RedirectStandardError $e -RedirectStandardInput $in0 -NoNewWindow -PassThru
        while (-not $proc.HasExited) {
            Start-Sleep -Milliseconds 900
            $spin++; $tick++
            # 先回显 stderr 增量(corepack 下载提示、网络错误都在这里, 默认看不见会像卡死)
            if (Test-Path $e) {
                $allE = Get-Content -LiteralPath $e -Raw -ErrorAction SilentlyContinue
                if ($allE -and $allE.Length -gt $lastErr) {
                    ($allE.Substring($lastErr)) -split "\r?\n" | ForEach-Object {
                        if ($_ -and $_ -notmatch '^\s*$') {
                            $echoN++
                            if ($echoN -le 60) { Write-Host ("    [stderr] " + $_) -ForegroundColor DarkYellow }
                        }
                    }
                    $lastErr = $allE.Length
                }
            }
            # 增量读 stdout: ndjson 模式解析进度事件; 文本模式回显 pnpm 生命期/警告行
            if (Test-Path $o) {
                $all = Get-Content -LiteralPath $o -Raw -ErrorAction SilentlyContinue
                if ($all -and $all.Length -gt $lastOut) {
                    ($all.Substring($lastOut)) -split "\r?\n" | ForEach-Object {
                        if (-not $_ -or $_ -match '^\s*$') { return }
                        if ($useNdjson) {
                            if ($_ -match '"name":"pnpm:') { $gotAnyEvent = $true }
                            if ($_ -match '"name":"pnpm:progress"') {
                                $st = ''; $id = ''
                                if ($_ -match '"status":"([a-z_]+)"') { $st = $Matches[1] }
                                if ($_ -match '"packageId":"([^"]+)"') { $id = $Matches[1] }
                                if (-not $id -and $_ -match '"to":"((?:[^"\\]|\\.)*)"') {
                                    # imported 事件不带 packageId: 从 to 路径 ...\.pnpm\<pkg>\node_modules\<name> 反推
                                    $toPath = $Matches[1] -replace '\\\\', '\'
                                    if ($toPath -match '\.pnpm\\([^\\]+)\\node_modules') { $id = $Matches[1] }
                                }
                                if ($id) {
                                    switch ($st) {
                                        'resolved'       { [void]$resolved.Add($id) }
                                        'fetched'        { [void]$obtained.Add($id) }
                                        'found_in_store' { [void]$obtained.Add($id) }
                                        'imported'       { [void]$imported.Add($id) }
                                    }
                                }
                            } elseif ($_ -match '"name":"pnpm:stage"' -and $_ -match '"stage":"([a-z_]+)"') {
                                # 真实阶段: resolution_started/done -> importing_started/done
                                $stage = $Matches[1]
                            } elseif ($_ -match '"name":"pnpm:summary"') {
                                $summarySeen = $true
                            } elseif ($_ -match '"level":(?:60|"error")' -and $_ -match '"message":"((?:[^"\\]|\\.)*)"') {
                                $echoN++
                                if ($echoN -le 60) { Write-Host ("    " + ($Matches[1] -replace '\\n', ' ')) -ForegroundColor DarkYellow }
                            } elseif ($_ -match '"level":(?:40|50|"warn")' -and $_ -match '"message":"((?:[^"\\]|\\.)*)"') {
                                $echoN++
                                if ($echoN -le 60) { Write-Host ("    " + ($Matches[1] -replace '\\n', ' ')) -ForegroundColor DarkGray }
                            }
                        } elseif ($_ -notmatch 'Progress:' -and $_ -match 'WARN|ERR|Packages:|added|Done in|Already up|Unsupported|deprecat|vulnerab|Ignored build') {
                            $echoN++
                            if ($echoN -le 60) { Write-Host ("    " + $_) -ForegroundColor DarkGray }
                        }
                    }
                    $lastOut = $all.Length
                    $lastGrowAt = $sw.Elapsed.TotalSeconds
                }
            }
            $secs = [int]$sw.Elapsed.TotalSeconds
            # "日志长时间无新增" 提示: 用来区分"在慢慢跑"与"真的卡死"
            $idleTip = ''
            if ($lastGrowAt -gt 0 -and ($sw.Elapsed.TotalSeconds - $lastGrowAt) -gt 240) {
                $idleTip = ("（日志已 {0} 分钟无输出, 可能在等网络/源较慢）" -f [int](($sw.Elapsed.TotalSeconds - $lastGrowAt) / 60))
            }
            $stageTxt = switch ($stage) {
                'resolution_started' { '解析依赖中: ' }
                'resolution_done'    { '下载依赖中: ' }
                'importing_started'  { '链接到 node_modules 中: ' }
                'importing_done'     { '收尾中: ' }
                default              { '解析依赖/连接源中: ' }
            }
            if ($useNdjson) {
                $tot = $resolved.Count
                $got = $obtained.Count
                $imp = $imported.Count
                $done = [math]::Max($got, $imp)
                if ($tot -ge 3 -and $done -gt 0) {
                    $pct = [int][math]::Min(99.0, $done * 100.0 / $tot)
                    Write-Progress -Activity $Label -Status ("{0}依赖包 {1}/{2}（已下载 {3} · 已链接 {4}）{5}| 已用时 {6}m{7}s" -f $stageTxt, $done, $tot, $got, $imp, $idleTip, [int]($secs/60), ($secs%60)) -PercentComplete $pct
                } elseif (-not $gotAnyEvent -and $secs -ge 30) {
                    # 30s 没有任何 pnpm 事件: 多半 corepack 正在下 pnpm 版本 / 网络不通, 而不是在解析
                    Write-Progress -Activity $Label -Status ("pnpm 尚未开始输出（可能在下载 pnpm 版本或等待网络, 见下方 [stderr] 与日志）| 已用时 {0}m{1}s" -f [int]($secs/60), ($secs%60)) -PercentComplete -1
                } else {
                    # 解析阶段总量未知: 用不确定进度(-1), 避免"百分比在动但计数为 0"的误导
                    Write-Progress -Activity $Label -Status ("{0}已解析 {1} 个包（pnpm 解析大工程较久, 静默属正常）{2}| 已用时 {3}m{4}s" -f $stageTxt, $tot, $idleTip, [int]($secs/60), ($secs%60)) -PercentComplete -1
                }
            } else {
                # 兜底: 体积估算(每 3 tick 量一次, 递归枚举大目录较费 IO)
                $mb = 0
                if ($tick % 3 -eq 0) {
                    $now = 0
                    foreach ($m in $measDirs) { if (Test-Path $m) { $now += @(Get-ChildItem -LiteralPath $m -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum } }
                    $mb = [math]::Round(($now - $size0) / 1MB, 0)
                }
                $real = [int][math]::Min(99.0, $mb / (1.5 * 1024) * 100.0)   # 全新安装约 1.5GB
                if ($real -ge 1) {
                    Write-Progress -Activity $Label -Status ("已下载约 {0} MB / 预估 1.5 GB | 已用时 {1}m{2}s" -f $mb, [int]($secs/60), ($secs%60)) -PercentComplete $real
                } else {
                    Write-Progress -Activity $Label -Status ("解析依赖/连接源中(此阶段下载量常为 0, 属正常){0}| 已用时 {1}m{2}s" -f $idleTip, [int]($secs/60), ($secs%60)) -PercentComplete -1
                }
            }
        }
        $proc.WaitForExit()   # 确保输出管道/句柄全部关闭后再判定
        # 退出码获取: 多数宿主 WaitForExit 后能同步到子进程退出码; 个别宿主
        # (如部分 PS5.1 环境)即使 HasExited/WaitForExit 后该属性仍为 $null ——
        # 不能据此判失败, 交由下方"产物+日志"兜底复核
        try { if ($proc.ExitCode -is [int]) { $code = $proc.ExitCode } } catch { }
        Write-Progress -Activity $Label -Completed
        # 参数兼容: 老 pnpm 不认 --reporter=ndjson 时会立即报错退出 -> 去掉参数重跑一次(转体积估算)
        if ($useNdjson -and $attempt -eq 1 -and $code -ne 0 -and $sw.Elapsed.TotalSeconds -lt 30) {
            $txt = ""
            foreach ($f in @($o, $e)) { if (Test-Path $f) { $txt += (Get-Content -LiteralPath $f -Raw -ErrorAction SilentlyContinue) } }
            if ($txt -match '(?i)(unknown reporter|reporter.|--reporter|unknown option|unrecognized)') {
                Warn "当前 pnpm 不支持 --reporter=ndjson(真进度), 回退为体积估算进度后重试…"
                $useNdjson = $false
                continue
            }
        }
        break
    }
    if ($code -ne 0) {
        # 兜底复核(仅当退出码拿不到即 -1 时启用): 一次成功的 pnpm install 必然
        # 生成 node_modules 产物、日志出现 "Done in ..."(文本模式)或 summary 汇总(ndjson),
        # 且不含致命错误; 失败的安装通常无成熟产物或日志带致命错误标记 -> 据此避免误报失败
        if ($code -eq -1) {
            $mod = Join-Path $WorkDir "node_modules"
            $modOk = (Test-Path $mod) -and (@(Get-ChildItem -LiteralPath $mod -Force -ErrorAction SilentlyContinue).Count -gt 0)
            $allTxt = ""
            foreach ($f in @($o, $e)) { if (Test-Path $f) { $allTxt += (Get-Content -LiteralPath $f -Raw -ErrorAction SilentlyContinue) } }
            $doneOK = ($allTxt -match "Done in \d") -or ($useNdjson -and $summarySeen)
            $fatal  = $allTxt -match "(?i)(ELIFECYCLE|ERR_PNPM_|Command failed|EINTEGRITY|ETIMEDOUT|EAI_AGAIN|ENOSPC|EPERM|EACCES|ENOTEMPTY|FetchError|npm error)" -or ($useNdjson -and $allTxt -match '"level":(?:60|"error")')
            if ($modOk -and $doneOK -and -not $fatal) { $code = 0 }
        }
    }
    if ($code -ne 0) {
        Err "pnpm install 失败(退出码 $code)。错误日志尾部:"
        $tail = @()
        if (Test-Path $e) { $tail = @(Get-Content -LiteralPath $e -Tail 25) }
        if (-not $tail -and (Test-Path $o)) {
            if ($useNdjson) {
                # ndjson 日志是 JSON 行: 只挑错误行并还原 message, 便于阅读
                $tail = @(Get-Content -LiteralPath $o -Tail 200 | Where-Object { $_ -match '"level":(50|60)' } | ForEach-Object {
                    if ($_ -match '"message":"((?:[^"\\]|\\.)*)"') { $Matches[1] -replace '\\n', ' ' }
                } | Select-Object -Last 25)
            } else { $tail = @(Get-Content -LiteralPath $o -Tail 25) }
        }
        $tail | ForEach-Object { Write-Host ("    " + $_) -ForegroundColor DarkYellow }
        Err $ErrHint
        return $false
    }
    return $true
}

# 确保 dsh 门户运行时(deepseek-harness)就绪: 缺源码时按需联网获取(git 浅克隆或官方 zip),
# 缺 node_modules 时自动 pnpm install。该引擎是第三方上游工程(源码数百 MB + 依赖≈1.5GB),
# 为控制仓库体积未内置; 等价宿主/评测不需要它。
# -FetchIfMissing: 交互(默认 setup.bat 菜单)不主动拉取; -Product 无人值守与菜单选 F/D 时自动/确认后拉取。
function Invoke-HarnessBootstrap {
    param([switch]$FetchIfMissing)
    $h = Join-Path $ROOT "deepseek-harness"
    $hasSrc = Test-Path (Join-Path $h "package.json")
    $hasMod = Test-Path (Join-Path $h "node_modules")
    if ($hasSrc -and $hasMod) { Ok "deepseek-harness 依赖已就绪(node_modules 存在), 跳过安装"; return $true }
    if ($hasSrc -and -not $hasMod) {
        Warn "deepseek-harness 缺少 node_modules, 开始自动安装门户依赖(需联网, 下载约 1.5GB, 可能 10-20 分钟) ..."
    } else {
        # 源码缺失 -> 询问/自动获取
        if (-not $FetchIfMissing) {
            Warn "未找到 deepseek-harness 源码(等价宿主/评测无需)。选 [F]/[D] 启动门户时会自动联网获取; -Product 模式全程自动"
            return $false
        }
        if (-not $Product) {
            if ([Console]::IsInputRedirected) { Warn "非交互终端且未启用 -Product, 跳过自动获取门户运行时"; return $false }
            $ans = (Read-Host "  将联网获取 dsh 门户运行时 deepseek-harness(官方上游, 源码+依赖约 1-2GB, 需 10-20 分钟)。继续? [Y/n]").Trim()
            if ($ans -ne "" -and $ans -ne "Y" -and $ans -ne "y") { Warn "已跳过, 等价宿主/评测仍可用"; return $false }
        }
        $repoBase = if ($env:HARNESS_REPO) { $env:HARNESS_REPO.TrimEnd('.git','/') } else { "https://github.com/deepseek-ai/deepseek-harness" }
        $branch   = if ($env:HARNESS_BRANCH) { $env:HARNESS_BRANCH } else { "master" }
        $git = (Get-Command git -ErrorAction SilentlyContinue).Source
        if ($git) {
            # 上次中断(如 clone 途中被 NativeCommandError 打断)会留下残缺目录, 先清掉再克隆
            if (Test-Path $h) {
                Warn "检测到残留的 deepseek-harness 目录(无 package.json), 清理后重新获取 ..."
                try { Remove-Item $h -Recurse -Force -ErrorAction Stop }
                catch { Err "残留目录无法删除($h), 请手动删除后重试"; return $false }
            }
            Warn "自动克隆 deepseek-harness($branch, 浅克隆, 联网下载源码; 进度显示在本窗口) ..."
            # PS5.1 陷阱(同 mysqld 初始化): git 的进度信息走 stderr, 若用 2>&1 合并进管道,
            # 在 EAP=Stop 下每条 stderr 都会被当成 NativeCommandError 致命错误逐行中断。
            # 解决: 不合并 stderr(直接上屏), 调用期临时把 EAP 降为 Continue, 成败只看退出码。
            $eapSave = $ErrorActionPreference; $ErrorActionPreference = "Continue"
            & $git clone --depth 1 --branch $branch "$repoBase.git" $h
            $cloneCode = $LASTEXITCODE
            $ErrorActionPreference = $eapSave
            if ($cloneCode -ne 0) { Err "git clone 失败(退出码 $cloneCode), 请检查网络/代理后重试"; return $false }
        } else {
            Warn "未检测到 git, 改为直接下载官方 zip 并解压..."
            $zip = Join-Path $RUNTIME "deepseek-harness.zip"
            $tmp = Join-Path $RUNTIME "harness-unzip"
            try {
                Invoke-WebRequest -Uri "$repoBase/archive/refs/heads/$branch.zip" -OutFile $zip -UseBasicParsing
                if ((Get-Item $zip).Length -lt 100KB) { throw "下载被拦截或文件异常(大小过小)" }
                if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
                Expand-Archive -Path $zip -DestinationPath $tmp
                $dir = Get-ChildItem -Path $tmp -Directory -Filter "deepseek-harness*" | Select-Object -First 1
                if (-not $dir) { throw "zip 解压后未找到 deepseek-harness 目录" }
                if (Test-Path $h) { Remove-Item $h -Recurse -Force }
                Move-Item $dir.FullName $h
            } catch { Err "自动获取 deepseek-harness 失败: $($_.Exception.Message)"; return $false }
        }
        if (-not (Test-Path (Join-Path $h "package.json"))) { Err "获取 deepseek-harness 失败, 请检查网络后重试(或手动放置该目录)"; return $false }
        Ok "deepseek-harness 源码已就绪($h)"
    }
    # 依赖安装
    $nodeExe = Ensure-NodeRuntime
    if (-not $nodeExe) { Warn "未检测到 node, 自动下载便携版(约 36MB)..."; $nodeExe = Install-PortableNode }
    if (-not $nodeExe) { Err "node 获取失败, 无法安装门户依赖"; return $false }
    $pnpm = Ensure-Pnpm $nodeExe
    if (-not $pnpm) { Err "pnpm 安装失败, 请手动执行 npm install -g pnpm 后重跑"; return $false }
    Ok "node $(& $nodeExe --version 2>$null) / pnpm $(& $pnpm --version 2>$null)"
    if (-not (Invoke-PnpmInstallProgress -WorkDir $h -PnpmCmd $pnpm -NodeExe $nodeExe -Label "deepseek-harness 依赖安装 (pnpm install)")) {
        Err "deepseek-harness 依赖安装失败, 门户暂不可启动(等价宿主/评测不受影响)。可重跑本脚本或手动: cd '$h' ; pnpm install"
        return $false
    }
    Ok "deepseek-harness 依赖安装完成。选 [F] 即可一键启动门户并自动打开浏览器"
    return $true
}

# ------------------------------------------------------------ 0. 体检
Write-Host "平台化企业智能问数工作台 - 一键配置" -ForegroundColor Magenta
Write-Host "仓库根: $ROOT"
Step "0/6 环境体检"

# --- Python ---
$pythonExe = $null
$g = Get-Command python -ErrorAction SilentlyContinue
if ($g) { $pythonExe = $g.Source }
if (-not $pythonExe) {
    $gp = Get-Command py -ErrorAction SilentlyContinue
    if ($gp) {
        try { $pythonExe = (& py -3 -c "import sys;print(sys.executable)" 2>$null | Select-Object -First 1) }
        catch { $pythonExe = $null }
    }
}
if (-not $pythonExe) {
    # 常见安装目录(用户级 winget / 官方安装器)
    $cands = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe")
    )
    $pythonExe = $cands | Where-Object { Test-Path $_ } | Select-Object -First 1
}
$pyOk = $false
if ($pythonExe) {
    & $pythonExe -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $pyOk = $true }
}
if ($pyOk) {
    $ver = & $pythonExe -c "import sys;print('.'.join(map(str,sys.version_info[:3])))"
    Ok "Python $ver ($pythonExe)"
} elseif (-not $pythonExe) {
    if ($AutoInstall) {
        Warn "未检测到 Python, 自动下载官方 Python $PY_VERSION 安装器并静默安装(约 28MB, 需联网)..."
        New-Item -ItemType Directory -Force -Path (Join-Path $RUNTIME "installers") | Out-Null
        $inst = Join-Path $RUNTIME "installers\$PY_EXE_NAME"
        $installed = $false
        if (Download-File $PY_EXE_URL $inst "Python $PY_VERSION") {
            Warn "正在静默安装(用户级, 不弹窗, 约 1-2 分钟), 请勿关闭本窗口 ..."
            $pr = Start-Process -FilePath $inst -ArgumentList "/quiet","InstallAllUsers=0","PrependPath=1","Include_test=0" -Wait -PassThru
            if ($pr.ExitCode -in @(0, 3010)) { $installed = $true; Ok "Python 安装器已结束(退出码 $($pr.ExitCode))" }
            else { Warn "Python 安装器退出码 $($pr.ExitCode), 尝试 winget 兜底" }
        }
        $p = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
        if ((-not $installed -or -not (Test-Path $p)) -and (Get-Command winget -ErrorAction SilentlyContinue)) {
            Warn "改用 winget 安装 Python 3.13 ..."
            winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null
        }
        if (Test-Path $p) {
            $pythonExe = $p
            Ok "Python 已自动安装: $p"
        } else { Err "自动安装后未找到解释器, 请手动安装 Python 3.11+ 后重试: https://www.python.org/downloads/"; exit 1 }
    } else {
        Err "未检测到 Python 3.11+。重跑加 -AutoInstall 可自动下载安装, 或手动安装: https://www.python.org/downloads/"
        exit 1
    }
} else {
    Err "Python 版本过低(<3.11)。请升级 Python 后重试"; exit 1
}

# --- MySQL (端口探测, 不依赖 PATH 里的 mysql.exe) ---
$mysqlUp = Wait-Port 3306 3
if ($mysqlUp) { Ok "MySQL 服务可达 127.0.0.1:3306" }
else {
    if ($AutoMySQLZip) {
        Warn "3306 未通, 尝试下载官方 MySQL 便携版($MYSQL_ZIP_NAME)到 .runtime ..."
        New-Item -ItemType Directory -Force -Path $RUNTIME | Out-Null
        $zip = Join-Path $RUNTIME $MYSQL_ZIP_NAME
        if (-not (Test-Path $zip)) {
            $dlOk = $false
            foreach ($u in $MYSQL_ZIP_URLS) {
                if (Download-File -Url $u -Out $zip -Label "MySQL $MYSQL_ZIP_NAME" -Zip) { $dlOk = $true; break }
            }
            if (-not $dlOk) { Err "MySQL 下载失败(官方 CDN 与国内镜像均不可达或被网关拦截)。可手动下载 $MYSQL_ZIP_NAME 放入 $RUNTIME 后重跑"; exit 1 }
        }
        $mysqlDir = Join-Path $RUNTIME $MYSQL_ZIP_NAME.Replace(".zip","")
        if (-not (Test-Path $mysqlDir)) {
            try { Expand-Archive -Path $zip -DestinationPath $RUNTIME -Force }
            catch { Err "解压 MySQL 便携包失败: $($_.Exception.Message)"; exit 1 }
        }
        $mysqld = Join-Path $mysqlDir "bin\mysqld.exe"
        if (-not (Test-Path $mysqld)) { Err "解压后未找到 mysqld.exe"; exit 1 }
        $dataDir = Join-Path $mysqlDir "data"
        if (-not (Test-Path $dataDir)) {
            Warn "初始化数据目录(root 空密码, 需 10~60 秒, 日志见 .runtime) ..."
            # PS5.1 陷阱: 原生命令的 stderr 若用 2>&1 合并, 在 EAP=Stop 下每行日志都会变成
            # NativeCommandError 致命错误而中断(用户实测 mysqld 初始化即被误杀)。
            # 解决: stderr 直接落盘到 mysql-init.err.log(不用 2>&1), 并在调用期临时把 EAP 降为
            # Continue(pwsh 7.3+ 即便落盘也可能按 EAP 抛错), 成功静默, 失败时提示日志路径排查。
            $initLog = Join-Path $RUNTIME "mysql-init.err.log"
            $eapSave = $ErrorActionPreference; $ErrorActionPreference = "Continue"
            & $mysqld --initialize-insecure --basedir=$mysqlDir --datadir=$dataDir --console 2>$initLog
            $initCode = $LASTEXITCODE
            $ErrorActionPreference = $eapSave
            if ($initCode -ne 0) { Err "mysqld 初始化失败, 日志: $initLog (通常需先安装 VC++ 运行库: vc_redist.x64)"; exit 1 }
        }
        $proc = Get-Process mysqld -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$mysqlDir*" } | Select-Object -First 1
        if (-not $proc) {
            # 防残留: 上次中断可能留下其它解压目录的便携 mysqld 占住 3306, 先探测再处理
            $stale = Get-NetTCPConnection -LocalPort 3306 -State Listen -ErrorAction SilentlyContinue
            if ($stale) {
                $owner = Get-Process -Id $stale[0].OwningProcess -ErrorAction SilentlyContinue
                if ($owner -and $owner.ProcessName -eq 'mysqld') {
                    Warn ("检测到残留 mysqld(PID {0}, {1}) 占住 3306, 先结束再启动本实例" -f $owner.Id, $owner.Path)
                    Stop-Process -Id $owner.Id -Force -ErrorAction SilentlyContinue
                    Start-Sleep -Seconds 2
                } else {
                    Err "端口 3306 已被其它程序(pid $($owner.Id))占用, 请先释放端口后重试"; exit 1
                }
            }
            Warn "后台启动 mysqld(仅本次会话, root 空密码) ..."
            $log = Join-Path $RUNTIME "mysqld.err.log"
            Start-Process -FilePath $mysqld -ArgumentList "--basedir=$mysqlDir","--datadir=$dataDir","--port=3306" -WindowStyle Hidden -RedirectStandardError $log
        }
        if (Wait-Port 3306 60) {
            $mysqlUp = $true
            if (-not $MySQLRootPassword) { $MySQLRootPassword = "" }  # 便携实例 root 为空
            Ok "便携 MySQL 已启动(数据目录 .runtime, root 密码为空)"
        } else {
            # 失败当场把日志尾部打到屏幕, 便于定位(杀软拦截/端口占用/VC++ 缺失等)
            if (Test-Path $log) {
                $lines = @(Get-Content $log)
                Err "便携 MySQL 启动失败, 错误日志尾部(共 $($lines.Count) 行):"
                $lines | Select-Object -Last 25 | ForEach-Object { Write-Host ("    " + $_) -ForegroundColor DarkYellow }
                if ($lines.Count -eq 0) { Write-Host "    (日志为空: 常见原因是杀毒软件拦截便携版 mysqld, 请将 .runtime 目录加入白名单后重试)" -ForegroundColor DarkYellow }
            }
            Err "请手动安装 MySQL 8 后重试, 或把上方日志发给我们排查"; exit 1
        }
    } else {
        Warn "3306 端口未通(未检测到 MySQL 服务)。"
        Warn "  可选: 加 -AutoMySQLZip 自动下载官方 MySQL 8.0 便携版自举(需联网, ~500MB 解压空间)"
        Warn "  或手动安装 MySQL 8 后重跑本脚本。加 -SkipDB 可跳过数据库步骤先行体验。"
    }
}

# --- Node / pnpm (仅 dsh 图形门户需要; 等价宿主/评测不需要) ---
$nodeOk = [bool](Get-Command node -ErrorAction SilentlyContinue)
$pnpmOk = [bool](Get-Command pnpm -ErrorAction SilentlyContinue)
if ($nodeOk) { Ok "node $(node --version 2>$null)" } else { Warn "未检测到 node(仅 dsh 图形门户需要)" }
if ($pnpmOk) { Ok "pnpm $(pnpm --version 2>$null)" } else { Warn "未检测到 pnpm(仅 dsh 图形门户需要)" }
$wingetOk = [bool](Get-Command winget -ErrorAction SilentlyContinue)
if ($wingetOk) { Ok "winget 可用(自动安装通道就绪)" } else { Warn "未检测到 winget, 缺失组件将无法自动安装" }

# 概要 (PS 5.1 无三元运算符 ?:, 用 if 表达式)
$pyS   = if ($pyOk)     { '有' } else { '缺' }
$myS   = if ($mysqlUp)  { '有' } else { '缺' }
$nodeS = if ($nodeOk)   { '有' } else { '缺' }
$pnpmS = if ($pnpmOk)   { '有' } else { '缺' }
$wingS = if ($wingetOk) { '有' } else { '缺' }
Write-Host ("`n  体检结论: Python={0} MySQL(3306)={1} node={2} pnpm={3} winget={4}" -f $pyS,$myS,$nodeS,$pnpmS,$wingS)
if (-not $mysqlUp -and -not $SkipDB) {
    if (-not $AutoMySQLZip) { Err "MySQL 不可用且未启用 -AutoMySQLZip, 后续建库步骤将失败。建议重跑: setup.bat -AutoMySQLZip -AutoInstall"; exit 1 }
}

# ------------------------------------------------------------ 1. venv + 依赖
Step "1/6 Python 虚拟环境与依赖"
if (-not (Test-Path (Join-Path $VENV "Scripts\activate.ps1"))) {
    & $pythonExe -m venv $VENV
    if ($LASTEXITCODE -ne 0) { Err "venv 创建失败"; exit 1 }
    Ok "新建 venv: $VENV"
} else { Ok "venv 已存在, 复用" }
if (Test-Path (Join-Path $ROOT "requirements.txt")) {
    & $PY -m pip install --disable-pip-version-check -q -r (Join-Path $ROOT "requirements.txt")
    if ($LASTEXITCODE -ne 0) { Err "pip 安装依赖失败, 请检查网络"; exit 1 }
    Ok "依赖安装完成 (requirements.txt)"
}

# ------------------------------------------------------------ 2. 建库造数
Step "2/6 MySQL 建库与造数 (bi_workbench + hr_bi)"
if ($SkipDB) { Warn "已跳过 (-SkipDB), 若未初始化请勿运行业务" }
elseif (-not $mysqlUp) { Err "MySQL 不可用, 建库步骤跳过。请先解决 MySQL 再重跑" }
elseif (Test-BiDbReady) {
    Ok "双库已初始化并可连通 (bi_ro/hr_ro 只读账号探活通过), 跳过重复建库造数"
    Warn "如需重置演示数据: 手动执行 init_db.py / db_init.py (需 root 密码), 或删库后重跑本脚本"
}
else {
    # ---- 确定 root 密码(可为空): 便携 MySQL 默认空; 系统实例/显式参数直接采用 ----
    if (-not $PSBoundParameters.ContainsKey('MySQLRootPassword') -and $AutoMySQLZip -and $mysqlUp) {
        $ans = Read-Host "  检测到便携 MySQL(root 空密码)。直接回车=root密码为空; 输入'#'=改用其他密码"
        if ($ans -eq "#") {
            $MySQLRootPassword = Read-Host "  请输入 MySQL root 密码"
        }
        elseif ($ans -ne "") {
            $MySQLRootPassword = $ans
        }
        # 直接回车 => 保持空(root 无密码), 不再二次询问
    }
    elseif (-not $MySQLRootPassword) {
        $MySQLRootPassword = Read-Host "  请输入 MySQL root 密码(便携实例直接回车)"
    }
    if (-not $MySQLRootPassword) { Ok "root 密码为空(便携实例), 直接以空密码初始化" }

    # 注意: 密码可能为空, 必须用 --password=<值> 单 token 传参:
    # PowerShell 5.1 向原生命令传裸空字符串参数时会被丢弃,
    # 导致 argparse 收到无值的 --password 而报 "expected one argument"
    & $PY (Join-Path $ROOT "bi_workbench\scripts\init_db.py") "--password=$MySQLRootPassword"
    if ($LASTEXITCODE -ne 0) { Err "bi_workbench 初始化失败(MySQL 是否已启动? 密码是否正确?)"; exit 1 }
    Ok "bi_workbench 库已建 + 造数完成"
    & $PY (Join-Path $ROOT "hr_backend\db_init.py") "--password=$MySQLRootPassword"
    if ($LASTEXITCODE -ne 0) { Err "hr_bi 初始化失败"; exit 1 }
    Ok "hr_bi 库已建 + 造数完成"
}

# ------------------------------------------------------------ 3. dsh 门户装配 + 依赖自举
Step "3/6 dsh 门户装配与依赖自举"
if ($SkipPortal) { Warn "已跳过 (-SkipPortal); 等价宿主/评测不需要此步" }
else {
    $dshHome = Join-Path $env:USERPROFILE ".dsh"
    # 先确保 ~/.dsh 存在, 否则首次 Copy-Item 会把源作为 .dsh 本身拷贝
    New-Item -ItemType Directory -Force -Path $dshHome | Out-Null
    $srcProf = Join-Path $ROOT "deployment\dsh-home"
    $marker  = Join-Path $dshHome ".setup-assembled"
    if (Test-Path $srcProf) {
        # 幂等: 已装配过(有标记)且未强制重建 -> 跳过覆盖, 避免打扰正在运行/只读的门户文件
        if ((Test-Path $marker) -and -not $ResetProfile) {
            Ok "dsh-home 已装配过(标记 .setup-assembled), 跳过模板覆盖; 如需刷新删该标记或加 -ResetProfile"
        } else {
            # 遍历 dsh-home 顶层条目逐一装配: 目标 .dsh 已存在, 拷贝为 .dsh\<leaf>,
            # 保证 profiles\web 落到 .dsh\profiles\web(leaf 与 .dsh 布局一致)
            foreach ($item in (Get-ChildItem -Path $srcProf -Force)) {
                if (-not $item.PSIsContainer) { continue }
                try {
                    Copy-Item -Path $item.FullName -Destination $dshHome -Recurse -Force
                    Ok "已装配: $($item.Name) -> $dshHome\$($item.Name)"
                } catch {
                    # 单条目被占用/只读(如门户运行中)不应中断整条产品流程
                    Warn "装配 $($item.Name) 未完全成功(可能被占用或只读): $($_.Exception.Message)"
                }
            }
            try { Set-Content -LiteralPath $marker -Value (Get-Date -Format "yyyy-MM-dd HH:mm") -Encoding UTF8 | Out-Null } catch {}
        }
    } else { Warn "deployment\dsh-home 不存在, 跳过" }

    # ---- 门户插件依赖(~/.dsh/profiles/web): 缺失/旧冲突/ResetProfile 时在线安装 ----
    $dshWeb = Join-Path $dshHome "profiles\web"
    if (Test-Path (Join-Path $dshWeb "package.json")) {
        $needInst = -not (Test-Path (Join-Path $dshWeb "node_modules"))
        $stale    = Test-Path (Join-Path $dshWeb "node_modules\@linxin666\dsh-web-ui-all")
        if ($ResetProfile) {
            Warn "(-ResetProfile) 删除旧 ~/.dsh/profiles/web 并从模板重建 ..."
            Remove-Item $dshWeb -Recurse -Force -ErrorAction SilentlyContinue
            $srcWeb = Join-Path $srcProf "profiles\web"
            if (Test-Path $srcWeb) { Copy-Item -LiteralPath $srcWeb -Destination $dshWeb -Recurse -Force }
            $needInst = $true; $stale = $false
        }
        if ($needInst -or $stale) {
            if ($stale) { Warn "检测到旧冲突插件依赖(web-ui-all 残留), 执行 pnpm install 清理并同步到新依赖树 ..." }
            $nodeExe = Ensure-NodeRuntime
            $pnpm = $null
            if ($nodeExe) { $pnpm = Ensure-Pnpm $nodeExe }
            if (-not $pnpm) {
                Warn "node/pnpm 不可用, 本次跳过插件在线安装(重跑加 -AutoInstall, 或选 [F] 时会自动尝试)"
            } else {
                Push-Location $dshWeb
                try {
                    Warn "在线安装门户插件全家桶 (pnpm install, 数百 MB, 需联网数分钟) ..."
                    & $pnpm install --no-frozen-lockfile
                    if ($LASTEXITCODE -eq 0) { Ok "门户插件依赖已就绪 (~/.dsh/profiles/web)" }
                    else { Warn "pnpm install 失败(见上方输出), 可重跑本脚本或稍后选 [F] 重试" }
                } finally { Pop-Location }
            }
        } else { Ok "门户插件依赖已就绪 (~/.dsh/profiles/web)" }
    }

    # ---- deepseek-harness 运行时自举(缺源码按需获取; 缺 node_modules 自动安装) ----
    # -Product 无人值守自动获取; 普通交互仅提示, 菜单选 [F]/[D] 时再确认获取
    if (-not (Invoke-HarnessBootstrap -FetchIfMissing:$Product)) {
        Warn "deepseek-harness 未就绪: 当前可用等价宿主/评测(无需门户); 启动门户请稍后选 [F] 或重跑 setup.bat -Product"
    }
}

# ------------------------------------------------------------ 4. 评测回归
Step "4/6 评测回归"
if ($Verify) {
    & $PY (Join-Path $ROOT "eval\runner.py")
    if ($LASTEXITCODE -ne 0) { Warn "评测存在未通过用例, 请看上方输出" } else { Ok "评测回归通过" }
} else { Warn "已跳过 (加 -Verify 执行 47 条评测回归)" }

# ------------------------------------------------------------ 5. 启动指引 (菜单式)
Step "5/6 启动方式选择"
Write-Host ""
Write-Host "  请选择要启动的方式 (输入字母后回车):" -ForegroundColor Yellow
Write-Host "    [F] 一键产品: 启动 dsh 图形门户 + 自动打开浏览器  ← 推荐(等价'双击即用')"
Write-Host "    [D] 前台启动 dsh 门户(输出留在本窗口, Ctrl+C 退出)"
Write-Host "    [A] 等价宿主演示  (无需 dsh/Node, 命令行验证闭环)"
Write-Host "    [B] 冒烟自检       (hr_backend 快速自检)"
Write-Host "    [C] 完整测试集评测 (47 条自动用例, 写 eval/result.json)"
Write-Host "    [E] 不启动, 结束配置"
Write-Host ""
$choice = ""
if ([Console]::IsInputRedirected) {
    if ($Product) { $choice = "F" } else { $choice = "E" }
} else {
    $choice = (Read-Host "  请输入 F/D/A/B/C/E").Trim().ToUpper()
}
switch ($choice) {
    "A" {
        Ok "启动等价宿主演示(双后端, Ctrl+C 退出)..."
        & $PY (Join-Path $ROOT "bi_workbench\scripts\dual_backends.py")
    }
    "B" {
        Ok "执行冒烟自检..."
        & $PY (Join-Path $ROOT "hr_backend\smoke_hr.py")
    }
    "C" {
        Ok "运行 47 条评测回归..."
        & $PY (Join-Path $ROOT "eval\runner.py")
    }
    "D" {
        if (-not (Invoke-HarnessBootstrap -FetchIfMissing)) { Err "门户运行时未就绪, 未能启动"; break }
        $nodeExe = Ensure-NodeRuntime
        if (-not $nodeExe) { Err "node 不可用, 无法启动门户"; break }
        $pnpm = Ensure-Pnpm $nodeExe
        if (-not $pnpm) { Err "pnpm 不可用, 无法启动门户"; break }
        $gen = New-DshPatch
        if (-not $gen) { Err "未找到 dsh-cordis.patch.yml 模板"; break }
        Ok "前台启动 dsh 图形门户(首次较慢, Ctrl+C 退出)..."
        Push-Location (Join-Path $ROOT "deepseek-harness")
        try { & $pnpm dsh web --patch $gen }
        finally { Pop-Location }
    }
    "F" {
        # 缺源码/依赖时先自动联网获取(官方上游)并 pnpm install, 再启动
        if (-not (Invoke-HarnessBootstrap -FetchIfMissing)) { Err "门户运行时未就绪, 未能启动"; break }
        Start-DshPortal (Join-Path $ROOT "deepseek-harness")
    }
    default { Ok "本次不启动任何服务, 配置完成" }
}

# ------------------------------------------------------------ 6. 尾注
Step "6/6 备注"
if ($mysqlUp -and (Get-Process mysqld -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$RUNTIME*" })) {
    Warn "当前运行的是 .runtime 便携 MySQL(root 空密码)。关机/重启后需重新执行 setup.bat -AutoMySQLZip -SkipDB -SkipPortal 启动它。"
}
Write-Host "完成。以上命令请用 PowerShell 执行。" -ForegroundColor Green
