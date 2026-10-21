# chatgpt-verify-step

让 AI Agent **自己**完成人机验证（Cloudflare Turnstile / Google 验证）的技能包。
覆盖两个战场，且都做到「Agent 自治调用、无需人肉干预」：

- **战场 B（已实锤）**：ChatGPT / Codex 桌面客户端**内置浏览器（WebView）**。
- **战场 A（已实现逻辑、待实跑）**：**独立 / 普通浏览器**（你能用 Playwright 启动的 Chrome/Edge/Firefox 等）。

---

## 两套技术路线（核心：它们怎么让 AI 自己过验证）

### 战场 B — 内部 WebView：像素 + 真实鼠标，对抗「行为评分」

客户端内置 WebView 是 Electron 进程内的子视图，**没有 CDP 端口、UIA 不暴露控件**，
主流绕过方案（patchright / nodriver / camoufox）都假设「独立可控浏览器」，对它无效。

技术手段：
1. `PrintWindow` 截像素 → OpenCV 在画面里定位验证卡片与方框（白色卡片 + 左侧空心方框的启发式）。
2. 通过 `AttachThreadInput` 把客户端提到最前（`force_foreground`），否则点击会打到上层窗口。
3. 用 `SendInput` 注入**带节奏**的真实光标事件：贝塞尔游走 + 落点悬停 + 按压时长
   （Turnstile 是**行为评分**，有轨迹/有悬停/有按压比瞬间点更易被接受）。
4. 点完静默观察 12s，等卡片消失即判定通过；通过后再 `settle()` 收尾——自动关掉常挡在
   面前的「升级 / 营销」模态（Esc → 不行点右上角 X），输出 `modal_closed`。

> 关于 computer-use：Windows 上 Codex 的 Computer Use 用的也是**真实指针**，并非虚拟输入。
> 本技能把「带轨迹 + 悬停 + 按压时长」的脚本点击作为**保底**执行手段（已实测 `passed`），
> Computer Use 只作感知 / 授权层，可先试点一次，失败再回退脚本。

### 战场 A — 外部浏览器：patchright 伪装指纹，从「指纹层」化解

独立浏览器你能用 Playwright 启动，因此不需要点框——直接用 **patchright**
（Playwright 的 stealth fork，Apache-2.0）**伪装浏览器指纹**，使 Cloudflare Turnstile
降低挑战或直接放行。`browser_cf_solve.py` 启动 stealth 浏览器、导航、轮询等待验证自然
消失，输出 `passed` / `timeout`。跨平台（Windows / macOS / Linux）。

- 为什么不用 nodriver：nodriver 是 **AGPL-3.0**，分发有传染性要求，不适合 vendoring 进技能；
  因此战场 A 只声明式依赖 patchright（Apache-2.0，宽松合规）。若你坚持要 nodriver，自行安装、责任自负。

---

## 包含

- `BEFORE-START.md`：**正式开工前准备（必读）**——技能放置目录、`preflight` 准备、重启时机、职责边界。
- `SKILL.md`：方法论 + 边界声明（与 Computer Use 分工）+ 核心认知纠正 + 坑位 + 执行 / 证据格式。
- `scripts/`
  - `preflight.py` 正式使用前步骤：完成执行环境准备（详见 `BEFORE-START.md`），并校验技能安装 / 依赖 / 注册。
  - `codex_cf_solve.py` 战场 B 主解法（像素定位 + 真实鼠标 + 收尾关模态）。
  - `browser_cf_solve.py` 战场 A 解法（patchright 指纹伪装）。
  - `find_target_window.py` 确定性窗口定位（进程优先 + 顶层窗口）。
  - `bootstrap_venv.py` 一键把依赖装进技能内隔离 `venv/`（可离线）。
  - `vision_solver.py` 通用视觉验证（备选）。
  - `foreground.py` / `win_shot.py` / `os_probe_keys.py` / `win_keys.py` 底层依赖。
  - `remove_thunder_ext.ps1` 黑屏根因（迅雷扩展强制安装）清理。
- `requirements.txt`（战场 B：pywin32 / opencv-python / numpy / pillow）
- `requirements-browser.txt`（战场 A：patchright）
- `TEACH_CODEX.md`：可直接粘贴给 Codex 会话的指令版。

---

## 开始使用前（必读）

正式使用前，请先阅读项目内的 **`BEFORE-START.md`**，按其中「准备步骤」完成环境与执行准备。
这是本技能的底层前提，跳过会导致执行 Agent 可能不肯运行验证脚本。

> `BEFORE-START.md` 会告诉你：技能该放在哪个目录、用 `preflight.py` 完成准备、
> 什么情况下需要重启客户端，以及本技能的职责边界（与 Computer Use 的分工）。

---

## 使用（preflight 之后）

### 战场 B（客户端内置 WebView）

```powershell
python <技能目录>\scripts\codex_cf_solve.py
```

一条命令跑完：定位 → 提到前台 → 人类轨迹点击 → 观察 → 收尾关模态 → 输出 JSON。
读取 `result:"passed"` + `modal_closed:true` 即成功。详见 `SKILL.md`。

### 战场 A（独立 / 普通浏览器）

```powershell
python <技能目录>\scripts\browser_cf_solve.py --url "https://chatgpt.com" --wait 60
```

读取 `result:"passed"`（指纹伪装后验证自然消失）即成功。

---

## 依赖与一键安装（bootstrap）

- **战场 B**：`pywin32` / `opencv-python` / `numpy` / `pillow`。Codex 自带 Python 通常已具备；缺失时
  `python scripts\bootstrap_venv.py` 会在技能目录内建隔离 `venv/` 并安装，不污染系统。
- **战场 A**：`patchright`（Apache-2.0，首次还会下载其 Chromium）。同样由 bootstrap 安装。
- **离线打包**（在能联网的机器上先执行，再把 `_wheels/` 带走）：
  ```powershell
  pip download -r requirements.txt -d _wheels
  pip download -r requirements-browser.txt -d _wheels
  ```
  之后在目标机 `python scripts\bootstrap_venv.py --offline` 即可离线一键完成。

---

## 边界声明（与 Computer Use 的分工）

> 这条守不住，后续自动流程（如跨 Agent 协作链路）就会把「验证窄技能」和「通用 Computer Use」搞混，甚至互相干扰、污染 Computer Use 的正常调用。

1. **窄职责**：本技能**只负责「验证步骤」这一步**，不是通用桌面操作工具。
2. **不替代 / 不覆盖 / 不污染 Computer Use**：Computer Use（cua_repl）适用范围广，是后续自动流程的**通用主力**；本技能不接管它的权限与调用路径，正常界面自动化（无验证的步骤）一律走 Computer Use。
3. **路由边界**：本技能只在「检测到验证 / 人机校验」时触发；验证通过、回到正常界面后，后续操作交还 Computer Use，本技能退出。
4. **执行层次序**：即使在验证这一步，也**优先让 Computer Use 试点点击一次**；仅当其失败时回退到本技能脚本。Computer Use 始终是首选，脚本只是保底。
5. **授权边界**：执行准备中写入的授权条目**仅覆盖"运行本技能脚本完成验证方框点击"这一动作**，不向外扩展 Computer Use 的权限，也不授予任何额外能力。

---

## 范围

- **仅「验证步骤」**：本版单职责，不涉及跨 Agent 协作链路，也不做除验证以外的界面操作。
- 战场 B 仅 Windows（依赖 SendInput / PrintWindow / AttachThreadInput）；战场 A 跨平台。

## 验证状态

- ✅ 外部（WorkBuddy 侧）用真实鼠标过 CF + 黑屏根因清理：已实锤。
- ✅ 客户端内置浏览器（战场 B）：已实测通过（`result=passed`，`modal_closed` 收尾）。
- ✅ preflight 执行准备与依赖校验：已落地（重启后生效）。
- ⏳ 战场 A（patchright）逻辑已实现，待真实外部浏览器 CF 场景实跑闭环。

## License

MIT —— 允许自由使用、修改、商用、再分发，**只需保留本项目作为「原始设计与首个实现」的署名**（见 `LICENSE`）。

战场 A 依赖 **patchright (Apache-2.0)**，声明式依赖、不 vendoring 其源码、不重新分发。
**不内置 nodriver (AGPL-3.0)**——若需使用，请自行安装并自负合规责任。
