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

本 skill 配套的执行程序是仓库里的 **`dist/netparse.exe`**（约 7MB，PyInstaller 单文件，
Windows 直接跑，**无需安装 Python**）。`<仓库根>` = 本 SKILL.md 向上两级
（即 `skills/netparse/` 的上一级）。

前置检查（在仓库根目录）：

```bash
dist/netparse.exe --version     # 需输出 netparse 0.3.0+
```

| 现象 | 处理 |
| --- | --- |
| `dist/netparse.exe` 不存在 | 找仓库维护者要 exe；或在本机（有 Python）重新打包：`pyinstaller --onefile --name netparse --paths src packaging/entry.py` |
| 中文乱码 | exe 已内置 UTF-8 强制输出，重定向到文件也是 UTF-8；若终端显示乱码是终端自身编码问题 |
| 杀软拦截 exe | 加入白名单；如企业杀软持续误报，找维护者要 embeddable 绿色包替代方案 |

**分发**：把整个仓库文件夹（含 `dist/netparse.exe` 与 `templates/`）给对方即可，
对方零安装。要装进 WorkBuddy 等 AI 工具时，把 `skills/netparse/` 文件夹拷到其
skills 目录（WorkBuddy 是 `%USERPROFILE%\.workbuddy\skills\`）。

## 0.5 AI 调用约定（输出读取）

exe 的输出对 AI 友好，按约定读取即可：

- `parse ... --json` → **stdout 是纯 JSON**（lint/统计走 stderr），`2>/dev/null` 后
  直接 `json.load`，顶层 key 就是集合名
- `parse <模板> <配置文件夹> <输出目录>` → 每个配置文件一个同名 `.json` 落到输出目录
  （子目录结构保留），逐文件 `json.load` 入库
- `lint --json` → JSON 问题列表（`level` ∈ 错误/警告/提示）
- 退出码：0 = 成功；非 0 = 有错误（lint 有错误 / 批量有失败文件）

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

**管道约定**：`--json` 时 stdout 是纯 JSON，lint/统计走 stderr → `2>/dev/null` 后可安全接 `jq`（有 jq 的话）或直接重定向落文件。

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

### 5.5 「给某个字段加派生/换算逻辑（transform）」

1. 确定逻辑归属：只此模板用且 <30 行 → 写进模板 `[python]` 段；多模板共用 → 写进
   `templates/transforms_*.py`，模板头部 `[python_file]` 引它
2. 函数签名必须 `def f(value, record=None)`：value 是捕获值（无捕获行时为 None），
   record 是当前记录 dict；返回 `None` = 字段留空；跨字段派生从 `record.get("字段")` 取
3. 字段声明加 `func=函数名`
4. `$EXE lint <模板> --strict` 必须过（func 未定义 / secret+func 都会被拦）
5. 小样验证：`$EXE parse <模板> <样例> --json`，核对派生值；
   改的是 `transforms_*.py` 时**不用重打包 exe**（外置热改）

排查顺序：
1. `lint` 有没有错误/警告
2. 目标命令在配置里的**真实形态** vs 模板正则（形态不符是第一大原因）
3. 字段是不是 `func=` 派生 → 检查函数与被依赖字段（如 iface_type 依赖 Name）
4. 缩进锚定：命令前导空格数与 `indent(n)` / `^ {n}` 是否一致

## 6. 交付包清单（给别人时发整个仓库文件夹）

```text
netparse/
├─ dist/netparse.exe        ← 程序本体（唯一必需）
├─ templates/                ← 模板 + transforms（必需；可热改不用重打包）
│  ├─ huawei-vrp.cmdb
│  ├─ transforms_huawei.py
│  └─ zte-m6000.cmdb
├─ docs/                     ← 深度文档（可选）
├─ skills/netparse/          ← 本 skill（给 AI 工具用）
└─ README.md
```

对方拿到后：把 `skills/netparse/` 拷进 AI 工具的 skills 目录（WorkBuddy 是
`%USERPROFILE%\.workbuddy\skills\`），其余留在原地——AI 通过 `<仓库根>\dist\netparse.exe`
调用。无 Python 依赖。
