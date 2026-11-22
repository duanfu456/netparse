---
name: netparse
description: "This skill should be used when the user asks to parse network device configuration files (Huawei VRP, ZTE M6000) into structured CMDB JSON, batch-parse a folder of configs into per-device JSON files, validate an existing .cmdb template, or extend a template with new command rules or Python transform functions. Includes netparse.exe (no Python required) and ready-made validated templates."
official: false
version: 2.0.0
agent_created: true
---

# netparse — 网络设备配置 → CMDB JSON

把设备配置文件按模板解析成结构化 JSON，用于 CMDB 入库与接口台账。

**本文件夹自包含**：`netparse.exe`（无需 Python）+ 已验证模板 + 速查文档。
`<SKILL>` = 本文件夹（SKILL.md 所在目录）。所有命令都在 `<SKILL>` 内完成，不依赖仓库其他位置。

## 1. 前置检查

```bash
<SKILL>\netparse.exe --version    # 需输出 netparse 0.3.0+
```

| 现象 | 处理 |
|---|---|
| exe 被杀软拦截 | 加入白名单 |
| 中文乱码 | exe 已强制 UTF-8 输出；显示乱码是终端编码问题，不影响落盘文件 |
| `--version` 失败 | exe 损坏，重新拷贝本文件夹 |

## 2. 常用命令（全部实测）

模板用 `<SKILL>\templates\` 下的现成文件；`<配置>` 可以是单个文件，也可以是文件夹（文件夹 = 批量模式）。

```bash
EXE=<SKILL>\netparse.exe
TPL=<SKILL>\templates\huawei-vrp.cmdb

# ① 单文件，stdout 摘要（人看：lint 提示 + 集合树 + 条数 + 前 2 条样例）
%EXE% parse %TPL% device.cfg

# ② 单文件，stdout 完整 JSON（程序读取）
%EXE% parse %TPL% device.cfg --json

# ③ 单文件，落盘
%EXE% parse %TPL% device.cfg out.json

# ④ 文件夹批量：每个配置文件一个同名 .json 落到输出目录（第三个参数必填）
%EXE% parse %TPL% E:\配置\华为\ E:\out\

# 模板体检（写完/改完模板必跑）
%EXE% lint %TPL%
%EXE% lint %TPL% --strict    # 有警告也非零退出
```

**输出约定**（AI 读结果靠这个）：

- `--json` 时 **stdout 是纯 JSON**，顶层 key 就是集合名（`Interfaces` / `RouteStatics` / ...），`2>/dev/null` 后直接 `json.load`
- 批量模式：逐个读输出目录里的 `.json`；单个文件失败不中断，stderr 打 `[fail] 文件名: 原因`，退出码非 0
- 退出码：0 = 成功；非 0 = 有错误
- 密码输出为 `******`（脱敏默认开启，属正常）

| 现象 | 处理 |
|---|---|
| `错误：config 是文件夹时必须指定输出目录` | 批量模式第三个参数必填 |
| 批量退出码非 0 | stderr 有 `[fail] 文件名: 原因`，报告失败文件 |

## 3. 现成模板

模板在 `<SKILL>\templates\`：

| 文件 | 覆盖 | 状态 |
|---|---|---|
| `huawei-vrp.cmdb` | 华为 VRP 单文件版：接口（92 字段）/静态路由/VPN/BGP/ACL/NQA/设备信息/静态用户/QoS Profile，18 集合 | 318 台真机验证，接口块覆盖率 100% |
| `huawei/main.cmdb` | 华为模块化版（入口）：9 个功能组文件（device/interfaces/routes/vpn/bgp/acl/nqa/static-user/qos），`from import *` 组合（`.cmdb` 后缀可省略）；与单文件版输出逐字节一致 | 可单独跑某组调试（`parse 02-routes.cmdb <配置>`） |
| `transforms_huawei.py` | 华为 transform 函数：`iface_type`（接口类型派生，21,427 接口实测 100%） | 随模板热改，无需重打包 |
| `zte-m6000.cmdb` | 中兴 M6000：分区根命令模型 | 64 台验证 |

接到任务后先确认模板覆盖范围：`%EXE% lint %TPL%` 看集合树与字段数。

## 4. 高频任务剧本

### 4.1 解析一份配置，给出接口清单

```bash
%EXE% parse %TPL% <配置文件> --json 2>/dev/null
```

读 stdout JSON 的 `Interfaces` 数组（Name/Description/Type/IpAddress/...），直接回答。

### 4.2 批量解析整个文件夹，每台一个 JSON

```bash
%EXE% parse %TPL% <配置文件夹> <输出目录>
```

完成后报告成功/失败数与失败原因（stderr `[fail]` 行）。

### 4.3 模板要支持一条新命令

1. **统计形态**：读 1~3 份真实配置，看目标命令的全部变体（凭印象写规则是模板 bug 的主要来源）
2. **写规则**：在模板 `[value]` 加字段、`[rule]` 加正则行；正则从真实配置抄，不自创
3. **lint 必须过**：`%EXE% lint xx.cmdb --strict` → 0 错误 0 警告
4. **小样验证**：`%EXE% parse xx.cmdb 样例.cfg --json`，核对字段值
5. **批量验证**：文件夹模式跑一批，抽查 JSON

语法速查见 `<SKILL>\docs\TEMPLATE-CHEATSHEET.md`。

### 4.4 给字段加派生/换算逻辑（transform 函数）

1. 写函数：签名必须 `def f(value, record=None)`。`value` = 捕获值（字段无捕获行时为 `None`）；
   `record` = 当前记录 dict（跨字段派生用 `record.get("Name")`）；返回 `None` = 字段留空
2. 放置：<30 行且只此模板用 → 写进模板 `[python]` 段；多模板共用 → 写进
   `templates/transforms_huawei.py`（模板头部已有 `[python_file]` 引用）
3. 字段声明加 `func=函数名`
4. lint 必须过（`func=` 未定义、`secret` 字段挂 `func=` 都会被拦）
5. 小样验证派生值。改 `transforms_*.py` **不需要重打包**（外置热改）

## 5. 排查：解析结果不对 / 字段是空的

按顺序查：

1. `lint` 有没有错误/警告
2. 目标命令在配置里的**真实形态** vs 模板正则——形态不符是第一大原因（先数命令前导空格数）
3. 字段是不是 `func=` 派生 → 检查函数逻辑与被依赖字段（如 `iface_type` 依赖 `Name`）
4. 缩进锚定：命令前导空格数与规则前缀 `^ {n}` / 集合 `indent(n)` 是否一致

## 6. 模板语法速览与铁律

三要素（完整对照见 `<SKILL>\docs\TEMPLATE-CHEATSHEET.md`）：

```text
[python_file]                       ← 可选：外置 transform 函数（相对本模板的路径）
transforms_huawei.py

Interfaces type(list) start(^interface\s+) end(^#$) end_indent(0) indent(1)
[value]                             ← 字段声明（缩进 = 子集合层级）
Name type(str) reg(\S+) option(Key)
Type type(str) func=iface_type desc(接口类型)
[rule]                              ← 规则：${字段} 引用 reg 捕获
^interface\s+${Name}(?:\s+.*)?\s*$
^ {1}description\s+${Description}\s*$
```

**铁律**（违者必出 bug）：

1. 正文行前缀 `^ {n}` 精确锚定缩进（n = 真实配置的前导空格数），禁止 `^\s+`
2. 规则以 `$` 结尾即可——尾部 `(?:\s+.*)?\s*$` 引擎自动补，不要手写；
   一条规则写多个 target 用 `;` 分隔：`-> In{...};Out{...}`
3. `indent(n)` 管"进门要几空格"，`end_indent(n)` 管"出门的 `#` 要几空格"；
   同一命令出现在多个缩进层（如接口级 vs bas 视图）时必须靠它们分流
4. `default(...)` 只用于设备行为兜底，禁止造没出现的值
5. `secret` 字段禁止 `func=`；`func=` 引用的函数必须已定义（lint 会查）
6. 中兴开关词是 `no`，华为是 `undo`
