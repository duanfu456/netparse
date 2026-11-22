---
name: netparse
description: "用 netparse 模板引擎把网络设备配置文件（华为 VRP / 中兴 M6000）解析成 CMDB 入库 JSON。适合'解析这份配置''把配置转成 JSON''批量解析这个文件夹（每台一个 JSON）''检查模板写对了没''给设备建接口台账'这类任务。本 skill 自带 exe 与全部模板，目标机器无需 Python。"
official: false
version: 1.0.0
---

# netparse — 网络设备配置 → CMDB JSON

**自包含 skill**：`netparse.exe`（Windows 直接跑，无需 Python）+ 全部模板 + 速查文档都在本文件夹内。
把整个 `netparse/` 文件夹拷进 AI 工具的 skills 目录（WorkBuddy 是
`%USERPROFILE%\.workbuddy\skills\`）即可生效。

## 0. 路径与前置检查

- `<SKILL>` = 本文件夹（SKILL.md 所在目录）
- 程序：`<SKILL>\netparse.exe`
- 模板：`<SKILL>\templates\`（`huawei-vrp.cmdb` 华为 / `zte-m6000.cmdb` 中兴）

前置检查（首次执行前跑一次）：

```bash
<SKILL>\netparse.exe --version     # 需输出 netparse 0.3.0+
```

| 现象 | 处理 |
| --- | --- |
| exe 被杀软拦截 | 加入白名单；持续误报时找维护者要 embeddable 绿色包版本 |
| 中文乱码 | exe 已强制 UTF-8 输出，重定向到文件也是 UTF-8；终端显示乱码是终端编码问题 |
| `--version` 报错 | exe 损坏，重新拷贝本文件夹 |

## 1. AI 调用约定（输出读取）

- `parse ... --json` → **stdout 是纯 JSON**（lint/统计走 stderr），`2>/dev/null` 后直接 `json.load`，顶层 key 就是集合名
- `parse <模板> <配置文件夹> <输出目录>` → **每个配置文件一个同名 `.json`** 落到输出目录（子目录结构保留），逐文件 `json.load` 入库
- `lint --json` → JSON 问题列表（`level` ∈ 错误/警告/提示）
- 退出码：0 = 成功；非 0 = 有错误（lint 有错误 / 批量有失败文件）

## 2. 常用命令（全部实测）

```bash
EXE=<SKILL>\netparse.exe
TPL=<SKILL>\templates\huawei-vrp.cmdb

# ① 单文件，stdout 摘要（人看：lint 提示 + 集合树 + 条数 + 前 2 条样例）
%EXE% parse %TPL% device.cfg

# ② 单文件，stdout 完整 JSON（管道 / 程序读取）
%EXE% parse %TPL% device.cfg --json

# ③ 单文件，落盘
%EXE% parse %TPL% device.cfg out.json

# ④ 整个文件夹批量（必须给输出目录）：每个配置文件一个同名 .json，子目录结构保留
%EXE% parse %TPL% E:\配置\华为\ E:\out\

# 模板体检（写完/改完模板必跑；--strict 有警告也非零退出）
%EXE% lint %TPL%
```

**批量模式形态**：

```text
E:\配置\华为\          →  E:\out\
├─ 设备A.cfg              ├─ 设备A.json      ← 该文件单独解析结果
└─ 子目录\设备B.cfg       └─ 子目录\设备B.json
```

- 每个 JSON 顶层 key 就是集合名（Interfaces / RouteStatics / ...），可直接逐文件入库
- 单文件解析异常不中断批量：stderr 打 `[fail] 文件名: 原因`，退出码非 0
- 脱敏默认开启：密码输出为 `******`（`type(secret)` + 密文形态兜底双层）

## 3. 现有模板与字段（写解析任务前先查）

| 模板 | 覆盖 | 状态 |
|---|---|---|
| `templates/huawei-vrp.cmdb` | 华为 VRP：接口（92 字段）/静态路由/VPN/BGP/ACL/NQA/设备信息，15 集合 | ✅ 318 台真机验证，接口块内覆盖率 100% |
| `templates/transforms_huawei.py` | 华为 transform：`iface_type`（接口类型派生，21,427 接口实测 100%） | ✅ 随模板热改，无需重打包 exe |
| `templates/zte-m6000.cmdb` | 中兴 M6000：分区根命令模型 | ✅ 64 台验证 |
| `templates/nokia-sros.cmdb` | Nokia SR OS | ⚠️ 实验用，未完成，勿直接交付 |

**模板是外置的**：改 `.cmdb` 或 `transforms_*.py` **不需要重新打包 exe**——exe 只含引擎逻辑。

## 4. 模板说明（速览）

模板 = `.cmdb` 纯文本，三要素：

```text
[python_file]                       ← 可选：外置 transform 函数（相对本模板的路径）
transforms_huawei.py

[value]                             ← 字段声明：名字 type(...) reg(...) option(Key)
Name type(str) reg(\S+) option(Key)
Type type(str) func=iface_type desc(接口类型)

Interfaces type(list) start(^interface\s+) end(^#$) end_indent(0) indent(1)
    IpAddress type(list) start(^\s*ip(v6)? address)     ← 缩进表达层级
[rule]                              ← 规则：正则匹配命令行，${字段} 引用 reg 捕获
^interface\s+${Name}(?:\s+.*)?\s*$
^ {1}description\s+${Description}\s*$
```

**速查文档**：`docs/TEMPLATE-CHEATSHEET.md`（集合/字段/规则/transform 声明对照 + 7 条常见坑 + 六步工作流）。

**模板铁律**（违者必出 bug）：

1. 正文行前缀 `^ {1}` / `^ {2}` 精确锚定缩进，禁止 `^\s+`（会误收别的缩进层的同名命令）
2. 规则以 `$` 结尾即可——尾部 `(?:\s+.*)?\s*$` 引擎自动补，不要手写
3. `default(...)` 只用于设备行为兜底（如 AdminStatus up），禁止造没出现的值
4. `secret` 字段禁止 `func=`；`func=` 引用的函数必须已定义（lint 会查）
5. 中兴开关词是 `no`，华为是 `undo`

## 5. 写/改模板的工作流（严格按顺序）

1. **统计形态**：先读真实配置里目标命令的所有变体（凭印象写规则是模板 bug 的主要来源）
2. **写/改 `.cmdb`**：对照 `docs/TEMPLATE-CHEATSHEET.md`
3. **lint 必须过**：`%EXE% lint xx.cmdb --strict` → 0 错误 0 警告才算完
4. **小样验证**：拿 1 份配置 `parse --json`，核对输出字段值
5. **批量验证**：文件夹模式跑一批，抽查 JSON
6. **transform 函数**：签名必须 `def f(value, record=None)`；返回 `None` = 字段留空；派生用 `record.get("字段名")`

## 6. 高频任务剧本

**「解析这份配置，给我接口清单」**：跑命令 ②，读 stdout JSON 的 `Interfaces` 数组回答。

**「批量解析整个文件夹」**：跑命令 ④，报告成功/失败数；失败文件看 stderr `[fail]` 行。

**「模板要支持一条新命令」**：按第 5 节工作流，正则从真实配置抄，不自创。

**「解析结果不对 / 某字段是空的」**——排查顺序：
1. `lint` 有没有错误/警告
2. 目标命令在配置里的**真实形态** vs 模板正则（形态不符是第一大原因）
3. 字段是不是 `func=` 派生 → 检查函数与被依赖字段（如 iface_type 依赖 Name）
4. 缩进锚定：命令前导空格数与 `indent(n)` / `^ {n}` 是否一致
