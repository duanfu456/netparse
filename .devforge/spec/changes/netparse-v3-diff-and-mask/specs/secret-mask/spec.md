# 能力规格 · secret-mask（引擎层密码脱敏）

> 所属变更：`netparse-v3-diff-and-mask`
> 设计依据：`../design.md`（D4 / D5 / D6 / D7）
> 目标：netparse 产出的任何 JSON 中**不含可用密文明文**，且「未配置」与「已配置已遮蔽」可区分。

---

## Purpose

netparse 把网络设备配置解析成 JSON 入 CMDB。配置里含有**可用的凭据**（华为 VRP 的 `password cipher %^%#...%^%#` 是可解的密文封装，不是不可逆哈希）。当前模板用 `Password type(str)` 捕获它，值被原样写进落盘 JSON，**一旦进入备份、网盘、日志、Git 就是凭据泄露**。

资产层来不及救 —— 落盘的中间 JSON 本身就是明文。因此脱敏必须发生在**引擎写入的那一刻**：值进入记录对象之前就已经是掩码。

本能力提供三层保障：
1. **L1 显式**：模板把字段声明为 `type(secret)` → 精确遮蔽；
2. **L2 兜底**：引擎按**值的密文形态**识别 → 即使模板漏标也遮蔽；
3. **L3 静态检查**：lint 拦住"secret 字段当聚合键"和"secret 被 inherit 复制"，并警告疑似漏标。

---

## Requirements

### Requirement: 模板可声明 secret 字段

系统 SHALL 支持在 `[value]` 段用 `type(secret)` 声明凭据字段。

字段声明中 `type(secret)` 与其它类型互斥；一个字段只能有一种 `type`。`secret` 字段仍可带 `reg(...)` / `desc(...)`，但 `reg(...)` 只用于**匹配**，不用于类型转换。

#### Scenario: 声明 secret 字段并被规则捕获

- **WHEN** 模板声明 `Password type(secret) reg(\S+) desc(密码)`，且某条规则把捕获值写入该字段
- **THEN** 解析结果的该字段值为固定掩码串 `"******"`，而**不是**捕获到的原始值
- **AND** 该字段的原始值不出现在解析结果的任何位置（整个结果 JSON 文本中不含原值子串）

#### Scenario: secret 与其它类型互斥

- **WHEN** 字段声明为 `Password type(secret) type(int)`
- **THEN** lint 报「错误」级问题，说明一个字段只能声明一种 type
- **AND** 引擎不产出该字段（该记录按缺字段处理），而不是抛未捕获异常

#### Scenario: secret 字段的 reg 仍参与匹配

- **WHEN** 字段声明 `Password type(secret) reg(\S+)`，配置行为 `peer X password cipher abc`
- **THEN** 规则按 `\S+` 匹配成功（reg 生效），捕获值被遮蔽为 `"******"`
- **AND** 该记录不因"值是密文形态"而被丢弃

---

### Requirement: 引擎在写入时遮蔽 secret 字段

系统 SHALL 在值写入记录对象**之前**完成遮蔽，即遮蔽发生在 `apply_writes()` 内、`convert()` 之前。

被遮蔽的值替换为固定掩码串，默认 `"******"`，可通过命令行开关 `--mask-value <str>` 自定义。**不保留长度、不保留首尾字符**。

#### Scenario: 遮蔽在 convert 之前发生

- **WHEN** 字段声明为 `type(secret)`，捕获值为 `"12345"`
- **THEN** 写入记录的值是 `"******"`（字符串），而**不是** `12345`（数字）
- **AND** 遮蔽先于类型转换，因此 secret 字段的声明类型被强制视为 `str`

#### Scenario: 自定义掩码串

- **WHEN** 以 `--mask-value "REDACTED"` 运行
- **THEN** 被遮蔽字段的值为 `"REDACTED"`
- **AND** 未被遮蔽的字段值不受影响

#### Scenario: 记录对象中不残留明文

- **WHEN** 引擎解析含密码的配置并产出结果
- **THEN** 引擎内部的记录对象（`Frame.rec` / `Frame.children`）中该字段的值已是掩码串
- **AND** 引擎的 `last[(集合名, 路径)]` 缓存（供 `inherit=` 使用）中该路径的值也是掩码串

---

### Requirement: 引擎按密文形态兜底遮蔽

系统 SHALL 在值未声明 `type(secret)` 时，按**值的形态**识别凭据并遮蔽（兜底层）。该层默认可用，可通过 `--no-mask-heuristic` 关闭。

形态识别 SHALL 基于值本身的特征（华为 `%^%#...%^%#` 包裹形态、严格的高熵长字符串），**SHALL NOT** 基于命令文本中是否出现 `password` / `cipher` 等关键字。

#### Scenario: 模板漏标时兜底生效

- **WHEN** 模板把密码字段声明为 `type(str)`（漏标），配置行为 `peer X password cipher %^%#;}4v<YDuLCwX%^%#`
- **THEN** 解析结果中该字段的值为掩码串 `"******"`
- **AND** 该字段被计入"兜底命中"计数

#### Scenario: 关键字误报不会发生

- **WHEN** 配置含 `default-password-template iptv`，且模板用 `type(str)` 捕获 "iptv"
- **THEN** 该字段的值为 `"iptv"`（**未被遮蔽**）
- **AND** 该字段不计入"兜底命中"计数

#### Scenario: 形态误报不会发生

- **WHEN** 配置含 `ssh server cipher aes256_gcm aes128_gcm`，且模板捕获 `"aes256_gcm"`
- **THEN** 该字段的值为 `"aes256_gcm"`（**未被遮蔽**）

#### Scenario: 关闭兜底层

- **WHEN** 以 `--no-mask-heuristic` 运行，且模板漏标了 secret
- **THEN** 漏标字段的值**不被遮蔽**（原样输出）
- **AND** 该行为被显式记录在运行统计中（兜底命中数为 0）

#### Scenario: 兜底层不误伤长描述

- **WHEN** 配置含 `description TO-[SNANK-PC-CMNET-RT01-NE5000E]-Eth-Trunk6=6*10GEUPLINKCR01`，且模板捕获该描述
- **THEN** 该描述字段的值**未被遮蔽**

---

### Requirement: 未配置与已遮蔽可区分

系统 SHALL 让「该字段未捕获到值」与「该字段捕获到了值但被遮蔽」在输出中**可区分**。

- 未捕获到值 → 该字段的键**不存在**于记录中
- 捕获到值且被遮蔽 → 该字段的键**存在**，值为掩码串（非空）

#### Scenario: 有密码的记录

- **WHEN** 某条记录的配置行含密码并被捕获
- **THEN** 该记录中密码字段的键存在，值等于掩码串（长度 > 0）

#### Scenario: 无密码的记录

- **WHEN** 某条记录的配置行不含密码
- **THEN** 该记录中密码字段的键**不存在**（不是空串，不是掩码串）

#### Scenario: 统计可回答"配了多少条凭据"

- **WHEN** 解析一个文件并统计被遮蔽的字段数
- **THEN** 该计数等于"实际捕获到凭据的记录数"，可用于回答"有多少条 peer 配了密码"

---

### Requirement: 脱敏不影响 Required 校验

系统 SHALL 在遮蔽后仍按原值语义执行 `option(Required)` 校验 —— 被遮蔽的值视为**已填写**（非空），不得因为遮蔽而使记录失效。

#### Scenario: 必填的 secret 字段

- **WHEN** 字段声明 `option(Required)` 且 `type(secret)`，捕获到值并被遮蔽
- **THEN** 该记录**通过** Required 校验（不被丢弃）

#### Scenario: 未捕获到的必填 secret 字段

- **WHEN** 字段声明 `option(Required)` 且 `type(secret)`，规则未匹配到该字段
- **THEN** 该记录**未通过** Required 校验（按原有语义被丢弃）

---

### Requirement: secret 字段不得作为聚合键

系统 SHALL 在 lint 中把「同一字段同时声明 `option(Key)` 与 `type(secret)`」报为**错误**。

理由：遮蔽后该字段在所有记录上取值相同 → 聚合键全部相同 → 不同记录会被合并成一条，造成静默数据丢失。

#### Scenario: lint 拦截

- **WHEN** 模板声明 `Password type(secret) reg(\S+) option(Key)`
- **THEN** lint 输出包含级别为「错误」的问题，指明该集合与该字段名
- **AND** 问题信息给出修法（去掉 Key 标记，或改用非 secret 字段做键）
- **AND** `python tests/run_regression.py` 因存在 lint 错误而失败（退出码非 0）

#### Scenario: 修正后 lint 通过

- **WHEN** 去掉该字段的 `option(Key)` 标记
- **THEN** lint 不再报该错误
- **AND** 回归脚本的 lint 检查通过

---

### Requirement: secret 字段不得被 inherit 引用

系统 SHALL 在 lint 中把「`inherit=` 指向一个 secret 字段」报为**错误**。

理由：`inherit=` 会把该值复制到另一个（非 secret）字段，绕过遮蔽，造成泄露。

#### Scenario: lint 拦截

- **WHEN** 字段 `Copied type(str) inherit=Other.Secret` 且 `Other.Secret` 被声明为 `type(secret)`
- **THEN** lint 输出包含级别为「错误」的问题，指明该集合与该字段名
- **AND** 问题信息说明"inherit 会把凭据复制到未标记 secret 的字段"

#### Scenario: 指向非 secret 字段不受影响

- **WHEN** 字段 `Copied type(str) inherit=Other.Plain` 且 `Other.Plain` 不是 secret
- **THEN** lint 不报该错误

---

### Requirement: lint 警告疑似漏标的凭据字段

系统 SHALL 在 lint 中检查：**规则文本含凭据关键字**（`password` / `cipher` / `secret` / `shared-key` / `md5-password` / `irreversible-cipher` / `key-string` / `authentication-key`）**且该规则捕获的字段未被声明为 `type(secret)`** → 报「警告」。

该检查 SHALL 只看模板文本（静态），SHALL NOT 需要读配置文件。

#### Scenario: 漏标被警告

- **WHEN** 规则为 `^\s*peer\s+${Peer}\s+password\s+cipher\s+${Password}\s*$`，而 `Password` 声明为 `type(str)`
- **THEN** lint 输出包含级别为「警告」的问题，指明该集合、该字段名与该规则文本

#### Scenario: 已标注不再警告

- **WHEN** 同一规则下 `Password` 改为 `type(secret)`
- **THEN** lint 不再报该警告

#### Scenario: 关键字出现在非凭据语境时不误报为错误

- **WHEN** 规则为 `^\s*ip\s+binding\s+vpn-instance\s+${VpnInstance}\s*$`（不含凭据关键字）
- **THEN** lint 不报任何与凭据相关的问题

#### Scenario: 警告不阻塞回归

- **WHEN** 存在「疑似漏标」类警告
- **THEN** `python tests/run_regression.py` **仍然通过**（只有「错误」级才阻塞）

---

### Requirement: 脱敏在所有输出出口生效

系统 SHALL 在**每一个**产生输出的出口应用脱敏，包括：命令行 `--json` 输出、Python API 直接返回的结果对象、以及未来新增的输出方式。系统 SHALL NOT 提供任何绕过脱敏的出口。

#### Scenario: 命令行输出

- **WHEN** 以 `--json` 运行并把输出重定向到文件
- **THEN** 文件内容中不含任何密文形态串（`%^%#` 等）
- **AND** 文件内容中含掩码串

#### Scenario: Python API

- **WHEN** 以 Python 代码 `import` 引擎并直接取 `run()` 的返回值
- **THEN** 返回值中密码字段已是掩码串（调用方不需要、也无法再做脱敏）

#### Scenario: 中间态无明文

- **WHEN** 解析过程中发生异常并打印异常上下文
- **THEN** 异常信息与栈中不出现密码明文

---

### Requirement: 存量模板的凭据字段被标注

系统 SHALL 把 `templates/huawei-vrp.cmdb` 中所有捕获凭据的字段标注为 `type(secret)`。

已识别：`BgpPeers.Password`（模板第 167-168 行声明、第 174 行规则捕获）。

#### Scenario: 存量模板解析后无明文

- **WHEN** 用 `templates/huawei-vrp.cmdb` 解析一份真实华为配置
- **THEN** 输出 JSON 全文中 `%^%#` 出现次数为 **0**
- **AND** `BgpPeers` 中有密码的 peer 记录里，`Password` 键存在且值为掩码串
- **AND** 没有密码的 peer 记录里，`Password` 键不存在

---

### Requirement: 回归基线覆盖脱敏行为

系统 SHALL 在 `tests/run_regression.py` 中增加可自动断言的脱敏检查，且更新 `tests/fixtures/sample-vrp.expected.json` 使回归通过。

系统 SHALL 提供一个**含凭据的回归 fixture**：现有 `tests/fixtures/sample-vrp.txt` 实测**不含任何 password / cipher 行**（`grep -i "password\|cipher"` → 0 命中），因此明文扫描断言在它上面是**空断言**（永远通过，等于没测）。新 fixture SHALL 至少覆盖：带密码的记录、不带密码的记录（键不存在）、长非凭据描述（不误报）、形似凭据但非凭据的命令（不误报）。

#### Scenario: 回归通过

- **WHEN** 运行 `python tests/run_regression.py`
- **THEN** 退出码为 0，输出"回归通过"
- **AND** 输出显示**至少跑了 2 个 fixture**（原 fixture + 含凭据的新 fixture）

#### Scenario: 回归包含明文扫描断言

- **WHEN** 回归脚本运行
- **THEN** 它对解析结果做全文本扫描，断言不含密文形态串
- **AND** 断言失败时退出码非 0，并打印命中的字段路径

#### Scenario: 明文扫描断言非空（防空断言）

- **WHEN** 把模板中 `BgpPeers.Password` 的 `type(secret)` 临时改回 `type(str)`
- **THEN** 回归**失败**，并指出命中位置
- **AND** 改回后回归恢复通过

#### Scenario: 不误报的断言生效

- **WHEN** 含凭据 fixture 中包含一条长描述（如 `TO-[SNANK-PC-CMNET-RT01-NE5000E]-Eth-Trunk6=6*10GEUPLINKCR01`）与一条 `default-password-template iptv`
- **THEN** 回归基线中这两个值**保持原样**，未被遮蔽

#### Scenario: 人工确认基线变化范围

- **WHEN** 因脱敏与 `AddressType`（见 `incumbent-diff` 能力）导致基线变化
- **THEN** 基线 diff 中**只有**密码字段与 `AddressType` 相关键发生变化
- **AND** 其余字段逐字段不变
