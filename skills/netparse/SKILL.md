---
name: netparse
description: "用 netparse 模板引擎把网络设备配置文件（华为 VRP / 中兴 M6000 / Nokia SR OS）解析成 CMDB 入库 JSON。适合'解析这份配置''把配置转成 JSON''批量解析这个文件夹''给设备建台账''检查模板写对了没'这类任务。支持自定义模板 DSL（缩进表达层级）与字段级 Python transform 函数。写模板前先读 docs/TEMPLATE-CHEATSHEET.md。"
official: false
version: 1.0.0
---

# netparse — 网络设备配置 → CMDB JSON

把设备配置文件按**模板 DSL** 解析成结构化 JSON，用于 CMDB 入库与接口台账。
华为 VRP 模板已交付（318 台真机验证，接口块内覆盖率 100%）；中兴 M6000 模板已交付（64 台验证）。

## 0. 前置检查（首次执行前跑一次）

本 skill 就在 **netparse 仓库内**（`<仓库根>/skills/netparse/`；`<仓库根>` = 本 SKILL.md
向上两级，即克隆/拷贝仓库后的根目录）。三种执行方式按可用性选择：

| 方式 | 适用 | 执行 |
|---|---|---|
| **exe**（首选，无需 Python） | 任何 Windows 机器 | `<仓库根>\dist\netparse.exe <子命令> ...` |
| **源码**（有 Python ≥3.9） | 开发/调试 | `cd <仓库根> && python -m netparse <子命令> ...` |
| **np.py 封装**（AI 用，输出全 JSON） | 本机有 Python ≥3.9 | `python skills/netparse/scripts/np.py ...` |

前置检查（在仓库根目录）：

```bash
dist/netparse.exe --version     # 需输出 netparse 0.3.0+
```

**分享 / 安装**：把整个仓库文件夹给对方即可。要装进 WorkBuddy 等 AI 工具时，
把 `skills/netparse/` 文件夹拷到其 skills 目录（WorkBuddy 是
`%USERPROFILE%\.workbuddy\skills\`）；SKILL.md 里的 `<仓库根>` 按对方机器的
实际仓库位置理解。

| 现象 | 处理 |
| --- | --- |
| exe 不存在 | 用源码方式；或重新打包：`pyinstaller --onefile --name netparse --paths src packaging/entry.py` |
| `No module named netparse` | cd 到仓库目录用源码方式 |
| 中文乱码 | exe 已内置 UTF-8 强制；源码方式设 `PYTHONIOENCODING=utf-8` |
| GitHub 拉取后 templates/ 为空 | 模板在仓库 `templates/` 目录，确认拉全 |

---

## 0.5 首选入口：`scripts/np.py`（AI 友好封装，输出全是 JSON）

**给 AI 用时优先用这个**——单入口、全 JSON 输出、错误也结构化（不用解析人类文本）：

```bash
# 脚本自动向上找仓库根（src/），无需配置；示例用仓库相对路径
python skills/netparse/scripts/np.py parse  templates/huawei-vrp.cmdb <配置文件|文件夹> [输出]  # 解析
python skills/netparse/scripts/np.py lint   templates/huawei-vrp.cmdb                          # 体检 + 集合树
python skills/netparse/scripts/np.py fields templates/huawei-vrp.cmdb                          # 全部字段清单
python skills/netparse/scripts/np.py info   templates/huawei-vrp.cmdb <配置文件>                # 摘要

- 输出：`{"ok": true/false, ...}`，parse 带 `result`（单文件）或 `results`（批量）+ `lint`
- 批量 + 输出目录时自动**每文件一个 JSON** 落盘，`written` 数组列出文件名
- 路径注意：给脚本传**真实 Windows 路径**（`C://...`），不要传 git-bash 的 `/tmp/...`（Windows Python 不认）
- 内部直接调引擎（`src/`），不依赖 exe；仓库不存在 exe 也能跑

## 0.6 执行方式详解：exe 与源码 CLI

**exe**（`<仓库根>\dist\netparse.exe`，约 7MB，PyInstaller 单文件）：

- 目标机器**无需 Python**，拷到任何目录直接跑
- stdout/stderr 已强制 UTF-8（中文 JSON 重定向到文件不会乱码）
- 模板是**外置的**：`.cmdb` 与 `transforms_*.py` 放在 exe 旁的 `templates\` 文件夹，
  **改模板不用重新打包**；只有引擎逻辑改动才需要重新打包（见 0 节前置检查的重打包命令）
- 分发：把 `netparse.exe` + `templates\` 整个文件夹给对方即可

**源码 CLI**（`cd <仓库根> && python -m netparse ...`）：

- 需要 Python ≥3.9；netparse 零第三方依赖，装不装包都行（装了有 `netparse` 命令）
- 功能与 exe 完全一致（同一份代码），开发调试用这种方式改完即跑
- 引擎可直接当 Python 库 import（见 docs/PYTHON-GUIDE.md）

| 场景 | 用哪个 |
|---|---|
| 给别人/别的机器跑 | exe |
| 本机开发调试、改模板、改引擎 | 源码 CLI |
| AI 脚本自动化 | `scripts/np.py`（内部走源码引擎，见 0.5 节） |

## 1. 核心概念（30 秒）

- **模板** = `.cmdb` 文本文件：`[value]` 段声明字段（名字/类型/正则），`[rule]` 段写每类命令的正则；
  集合用缩进表达层级（`Interfaces` 下缩进写 `IpAddress` 子集合）
- **两步执行**：`parse_template(模板)` 编译一次 → `run(编译产物, 配置行列表)` 出 dict
- **transform**：字段可挂 `func=函数名`，函数写在校内的 `[python]` 段或外置 `transforms_*.py`
  （`[python_file]` 相对模板路径）——接口类型推断就是这么做的
- **脱敏**：`type(secret)` 字段 + 密文形态兜底，输出永远是掩码

## 1.5 模板说明（速览）

模板 = `.cmdb` 纯文本，三要素：

```text
[python_file]                       ← 可选：外置 transform 函数（相对本模板的路径）
transforms_huawei.py

[value]                             ← 字段声明：名字 type(...) reg(...) option(Key)
Name type(str) reg(\S+) option(Key)
Type type(str) func=iface_type desc(接口类型)

Interfaces type(list) start(^interface\s+) end(^#$) end_indent(0) indent(1)
[value]                             ← 集合的缩进 = 层级（子集合声明在父集合规则内）
    Name type(str) reg(\S+) option(Key)
[rule]                              ← 规则：正则匹配命令行，${字段} 引用 reg 捕获
^interface\s+${Name}(?:\s+.*)?\s*$
^ {1}description\s+${Description}\s*$
```

**完整文档在仓库**（AI 写模板前必读速查版）：

| 文档 | 内容 |
|---|---|
| `<仓库根>\docs\TEMPLATE-CHEATSHEET.md` | **速查表**：集合/字段/规则/transform 声明对照 + 7 条常见坑 + 六步工作流 |
| `<仓库根>\docs\TEMPLATE-GUIDE.md` | 完整语法 + 16 个场景手册（含 transform 场景 16） |
| `<仓库根>\docs\CLI-GUIDE.md` | CLI 全参数与管道约定 |
| `<仓库根>\docs\PYTHON-GUIDE.md` | Python 库调用（两步模型/批量/脱敏控制） |

**模板铁律**（违者必出 bug）：

1. 正文行前缀 `^ {n}` 精确锚定缩进，禁止 `^\s+`
2. 规则以 `$` 结尾即可——尾部 `(?:\s+.*)?\s*$` 引擎自动补，不要手写
3. `default(...)` 只用于设备行为兜底，禁止造没出现的值
4. `secret` 字段禁止 `func=`；`func=` 引用的函数必须已定义（lint 会查）
5. 中兴开关词是 `no`，华为是 `undo`

## 2. 常用命令（全部实测）

```bash
EXE=<仓库根>\dist\netparse.exe

# 解析单文件 → stdout 摘要（人看：lint 提示 + 集合树 + 条数 + 前 2 条样例）
$EXE parse templates/huawei-vrp.cmdb device.cfg

# 解析单文件 → stdout 完整 JSON（管道；lint 走 stderr）
$EXE parse templates/huawei-vrp.cmdb device.cfg --json

# 解析单文件 → 落盘
$EXE parse templates/huawei-vrp.cmdb device.cfg out.json

# 整个文件夹批量（必须给输出目录）→ 每个配置文件一个同名 .json，子目录结构保留
$EXE parse templates/huawei-vrp.cmdb E:\配置\华为\ E:\out\

# 模板体检（错误/警告/提示；写完模板必跑）
$EXE lint templates/huawei-vrp.cmdb
$EXE lint templates/huawei-vrp.cmdb --strict      # 有警告也非零退出（CI 门禁）
```

| 现象 | 处理 |
| --- | --- |
| `错误：config 是文件夹时必须指定输出目录` | 批量模式第三个位置参数必填 |
| 批量退出码非 0 | 有文件解析失败，stderr 有 `[fail] 文件名: 原因`，看 failures |
| 输出里密码是 `******` | 正常，脱敏生效；确需关形态兜底加 `--no-mask-heuristic` |
| 模板路径不存在 | 模板放任意目录都可以，检查路径拼写 |

**管道约定**：`--json` 时 stdout 是纯 JSON，lint/统计走 stderr → `2>/dev/null` 后可安全接 `jq`/`python -m json.tool`。

## 3. 现有模板与字段（写解析任务前先查）

模板在 `<仓库根>\templates\`：

| 文件 | 覆盖 |
|---|---|
| `huawei-vrp.cmdb` | 华为 VRP：接口（92 字段）/静态路由/VPN/BGP/ACL/NQA/设备信息，15 集合 |
| `transforms_huawei.py` | 华为 transform：`iface_type`（接口类型派生，21,427 接口实测 100%）等 |
| `zte-m6000.cmdb` | 中兴 M6000：分区根命令模型（64 台验证） |
| `nokia-sros.cmdb` | Nokia SR OS：实验用，**未完成**，勿直接交付 |

任务前先看模板已覆盖什么：

```bash
$EXE lint templates/huawei-vrp.cmdb        # 集合树 + 字段数
grep -n "type(" templates/huawei-vrp.cmdb  # 字段清单
```

## 4. 写/改模板的工作流（严格按顺序）

1. **统计形态**：先看目标命令在真实配置里长什么样（凭印象写规则是模板 bug 的主要来源）。
   读 1~3 份真实配置文件里目标命令的所有变体
2. **写/改 `.cmdb`**：速查语法见 `<仓库根>\docs\TEMPLATE-CHEATSHEET.md`；
   深入场景（16 个）见 `docs/TEMPLATE-GUIDE.md`
3. **lint 必须过**：`$EXE lint xx.cmdb --strict` → 0 错误 0 警告才算完
4. **小样验证**：拿 1 份配置 `$EXE parse xx.cmdb 样例.cfg --json`，核对输出字段值
5. **批量验证**：`$EXE parse xx.cmdb 配置文件夹 out_dir\`，抽查几个 JSON
6. **transform 函数**：签名必须 `def f(value, record=None)`；返回 `None` = 字段留空；
   派生（从同记录其他字段算）用 `record.get("Name")` 这类取值；
   模板 `[value]` 里加 `func=函数名`

**模板禁忌**（每条都真实踩过，详见 CHEATSHEET 末尾）：

- 正文行前缀用 `^ {1}` 精确锚定，**不要** `^\s+`（会误收别的缩进层的同名命令）
- 规则以 `$` 结尾即可，**尾部 `(?:\s+.*)?\s*$` 引擎自动补**（不要手写）
- `default(...)` 只用于设备行为兜底（如 AdminStatus up），**禁止用来造没出现的值**
- 中兴设备的开关词是 `no`，不是华为的 `undo`

## 5. 高频任务剧本

### 5.1 「解析这份配置，给我接口清单」

```bash
$EXE parse templates/huawei-vrp.cmdb <配置文件> --json 2>/dev/null
```

输出 `Interfaces` 数组：Name/Description/Type/IpAddress/…；直接读 JSON 回答。

### 5.2 「批量解析整个文件夹，每台一个 JSON」

```bash
$EXE parse templates/huawei-vrp.cmdb <配置文件夹> <输出目录>
```

完成后报告：成功/失败数；失败文件与原因（stderr `[fail]` 行）。

### 5.3 「模板要支持一条新命令」

按第 4 节工作流：统计形态 → `[value]` 加字段（或 `[rule]` 加规则行）→ lint → 小样验证。
字段取值正则尽量从真实配置抄，不要自创。

### 5.4 「解析结果不对 / 某字段是空的」

排查顺序：
1. `lint` 有没有错误/警告
2. 目标命令在配置里的**真实形态** vs 模板正则（形态不符是第一大原因）
3. 字段是不是 `func=` 派生 → 检查函数与被依赖字段（如 iface_type 依赖 Name）
4. 缩进锚定：命令前导空格数与 `indent(n)` / `^ {n}` 是否一致

## 6. 用户没有 Python 环境时

exe 是单文件（~7MB），把 `<仓库根>\dist\netparse.exe` 和 `templates\` 文件夹
一起拷给对方即可；对方机器无需 Python。transform 的 `.py` 文件也是外置的，可热改。
