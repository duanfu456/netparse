# 能力规格 · incumbent-diff（netparse 与 incumbent 逐字段 diff）

> 所属变更：`netparse-v3-diff-and-mask`
> 设计依据：`../design.md`（D1 / D2 / D3 / D8 / D9 / D11 / D12）
> 目标：用现有 8440 个真实文件，**测量** netparse 距离能替代 incumbent 还差哪几列、差多少工作量。

---

## Purpose

netparse 是**第三版**配置解析方案（前两版：TextFSM + Python 切块、直接写 Python，都已在生产跑了 8440 个文件 0 失败）。现在必须回答一个决策问题：

> **netparse 距离能替代 incumbent，还差哪几列、差多少工作量？**

答不了的原因有两个：
1. 两侧 JSON 形状不兼容（PascalCase vs snake_case、list vs dict、集合划分不同），直接比 JSON 产生的是"形状噪声"而非"能力差距"；
2. netparse 的 CMDB 19 列覆盖状态目前只是**估算值 9/19**，没有实测。

本能力通过「**形状适配器 → 复用 incumbent 的资产层 → 比 19 列**」三条链解决这两点，并额外要求：
- 差异必须带**根因分类**（否则报告不可行动）；
- 报告必须给出**工作量估算**（否则回答不了决策问题）；
- 两侧必须用**同一个** `build_rows`（否则两边格式化规则可能不同步，结论不可信）。

---

## Requirements

### Requirement: 形状适配器做纯结构映射

系统 SHALL 提供一个适配器，把 netparse 的顶层结果转换为 incumbent 的 JSON 形状。

适配器 SHALL 只做：键改名、结构重排、值搬运。SHALL NOT 推导、计算、填默认值、拆分或合并字段。**netparse 未捕获的字段，适配器一律留空**（不合成）。

适配器的映射表 SHALL 为每个目标字段记录：netparse 来源路径、来源为空时的根因分类。

#### Scenario: 设备级字段映射

- **WHEN** netparse 结果为 `{"Device": [{"SysName": "X", "RouterId": "1.2.3.4"}], "Interfaces": [...]}`
- **THEN** 适配器输出为 `{"device_name": "X", "device_ip": "1.2.3.4", "interfaces": [...]}`
- **AND** 输出中不含 `Device` / `Interfaces` 这些 netparse 专有的顶层键

#### Scenario: 接口 IP 映射

- **WHEN** netparse 的某接口含 `IpAddress: [{"Address": "1.1.1.1", "Mask": "255.255.255.0", "Family": "ipv4"}]`
- **THEN** 适配器输出该接口含 `IP: {"IPV4": [{"ip": "1.1.1.1", "mask": "255.255.255.0"}], "IPV6": []}`
- **AND** `Family == "ipv6"` 的条目进入 `IPV6` 列表，且键名为 `addr`、前缀键为 `mask`（与 incumbent 形状一致）
- **AND** `Family` 字段本身不出现在输出中

#### Scenario: 未捕获的字段不被合成

- **WHEN** netparse 的某接口不含任何 VLAN 相关字段（当前模板 0% 覆盖 `vlan-type dot1q`）
- **THEN** 适配器输出中该接口**不含** `VLAN` 键
- **AND** 不出现 `VLAN: {"type": "", "user-vlan": [], "QinQ": []}` 之类的空壳结构

#### Scenario: 适配器是纯函数

- **WHEN** 对同一份 netparse 结果调用适配器两次
- **THEN** 两次输出**逐字节相等**
- **AND** 两次调用都不修改输入对象

---

### Requirement: 资产层以只读方式复用 incumbent

系统 SHALL 通过 `import` 方式调用 `E:\配置文件\配置解析\华为\build_excel.py` 的 `build_rows(json_path)` 与 `COLUMNS` 来取得 19 列行，SHALL NOT 修改、复制或重写该文件中的任何逻辑。

掩码→CIDR、限速取小、qinq→PVLAN/CVLAN、快照去重、路由按出接口反查等资产层规则，SHALL 由 `build_rows` 单侧实施。

#### Scenario: 只读复用

- **WHEN** diff 工具运行
- **THEN** `build_excel.py` 的 `build_rows` 被调用来生成两侧的行
- **AND** 该文件的内容与 mtime 在运行前后**完全一致**

#### Scenario: import 无副作用

- **WHEN** diff 工具 import `build_excel`
- **THEN** 不产生任何文件写入、不产生 xlsx 输出
- **AND** 若 `openpyxl` 缺失，工具给出明确的依赖缺失提示（而非 Python 栈回溯）

#### Scenario: 两侧用同一个格式化口径

- **WHEN** 对同一个接口，incumbent 侧与 netparse 侧的行都由 `build_rows` 产生
- **THEN** 两行的第 8 列（端口IPv4）都由同一个 `join_cidr4` 格式化（掩码→CIDR 规则一致）
- **AND** 第 6/7 列（限速）都由同一个 `get_speed` 取最小值

---

### Requirement: diff 的输入范围是 8440 个既有 incumbent 产物对应的原始配置

系统 SHALL 以 `E:\配置文件\BRAS&SR\解析后\<月目录>\华为\*.json` 为左側基准（直接读，不重跑 incumbent 解析），并按路径反推规则找到同名原始配置作为 netparse 的输入。

路径反推规则：`解析后/<月>/华为/<stem>.json` → `原始配置/<月>/华为/<stem>`，若不存在则尝试 `<stem>.txt`。

#### Scenario: 路径映射完整

- **WHEN** 工具扫描全部 incumbent JSON
- **THEN** 输出"找到对应原始配置 N / 总数 M"
- **AND** 数值应为 **8440 / 8440**
- **AND** 若有任何未命中，工具**显式报错并列出缺失清单**，不得静默跳过

#### Scenario: 不重跑 incumbent

- **WHEN** diff 全量运行
- **THEN** incumbent 侧的行来自**已落盘的 JSON**，不调用 `parse_huawei_vrp.py`
- **AND** `E:\配置文件\BRAS&SR\解析后\` 下无任何文件被修改或新增

#### Scenario: 全量处理

- **WHEN** 以 `--all` 运行
- **THEN** 处理的文件数为 8440，失败数为 0
- **AND** 报告的 `meta.files_total` / `meta.files_processed` / `meta.files_failed` 三者自洽

---

### Requirement: 报告给出每文件 19 列填充率

系统 SHALL 对每个处理过的文件，给出接口台账 19 列**每一列**在该文件中的填充情况（填充行数 / 总行数），并在汇总层给出全局填充率。

**限速列（第 6/7 列）的取值 SHALL 只能来自资产层的 join**（顶层 `qos-profile` 命名空间定义 + 接口级引用 + 接口级直给，三者取小），SHALL NOT 由 diff 工具自行计算。理由：该值在中间 JSON 层**不存在**，只存在于 join 之后；diff 工具自行计算会让两侧口径不同步。

#### Scenario: 单文件填充率

- **WHEN** 处理一个含 1044 个接口的文件
- **THEN** 报告该文件的每一列都有 `{filled, total, rate}`
- **AND** `total` 等于该文件的接口行数

#### Scenario: 全局汇总

- **WHEN** 全量跑完
- **THEN** 报告的 `column_fill_rate` 含 19 个列名，每列有全局 `{filled, total, rate}`
- **AND** `total` 等于两侧接口行数之和

#### Scenario: 列名与 incumbent 一致

- **WHEN** 检查报告的列名
- **THEN** 它们与 `build_excel.COLUMNS` 的第一列**逐字相同**（含括号与单位，如 `接口入流限速(M)`）
- **AND** 列顺序与 `COLUMNS` 一致

#### Scenario: 限速列的取值口径

- **WHEN** 检查报告中"接口入流限速(M)"列的填充率
- **THEN** 该列的值由 `build_excel.build_rows` 产生（两侧同源）
- **AND** diff 工具**不含**任何自制的限速计算逻辑

---

### Requirement: 报告给出接口数一致性（识别层基准）

系统 SHALL 在报告的 `interface_count_parity` 中给出两侧接口数的比对结果，作为 diff 的**第一项基准**。

已知基线：同一样本文件两侧接口数**精确一致**（1044 接口的文件两侧都是 1044；1558 接口的文件两侧都是 1558）—— 说明**接口识别层两边完全对齐，差距全在字段**。

#### Scenario: 逐文件接口数比对

- **WHEN** 全量跑完
- **THEN** 报告的 `interface_count_parity` 含 `files_matched` 与 `files_mismatched`
- **AND** 每列的 `total` 与该结论一致（不一致的文件要能定位到具体是哪些接口多了/少了）

#### Scenario: 接口数不一致时列基线不适用

- **WHEN** 某文件两侧接口数不同
- **THEN** 报告记录该文件为 mismatched，并列出仅 incumbent 有 / 仅 netparse 有的接口名
- **AND** 该文件的列填充率仍照常输出，但**不参与"差距全在字段"的结论**

---

### Requirement: 报告给出双向的字段级能力覆盖

系统 SHALL 在报告的 `part2_capability` 中，给出中间 JSON 层**两个方向**的字段覆盖：
- **netparse 缺什么**（incumbent 有、netparse 无）
- **netparse 多什么**（netparse 有、incumbent 无）

**SHALL NOT** 只报缺口。理由：实测 netparse 在接口基础属性上**更强**（多出 `AdminStatus` `Mtu` `Speed` `Duplex` `Dcn` `Type` `VlanType` `UserVlan` `AccessVlan` `PortLinkType` `TrunkAllowPass` `CarrierDownHoldTime` `Statistic` `Isis` `Vrrp` `Track` `EthTrunkMember`），而 incumbent 一个都没有 —— **只报缺口的报告会系统性低估 netparse**。

多出来的字段虽不在 19 列台账内，但它们是"更丰富的 CI 模型"的论据，SHALL 单列。

#### Scenario: 双向字段清单

- **WHEN** 全量跑完
- **THEN** 报告的 `part2_capability.interface_level_fields` 含 `matched` / `only_incumbent` / `only_netparse` 三个清单
- **AND** `coverage` 给出三方的数量

#### Scenario: 已知基线被复现

- **WHEN** 在同一份 1558 接口的配置上运行
- **THEN** `matched` 恰为 4 项（`name` / `description` / `IP` / `vpn`）
- **AND** `only_incumbent` 恰为 6 项（`QOS` / `VLAN` / `bas` / `multicast` / `qos-profile` / `traffic-policy`）
- **AND** `only_netparse` 非空且不少于 10 项

#### Scenario: 集合级结构差异被记录

- **WHEN** 全量跑完
- **THEN** 报告的 `part2_capability.collection_level_fields` 记录 `qos_profile_top`（incumbent 为顶层 list，netparse 为 null）与 `route-static`（incumbent 为 dict 含 `interface?`，netparse 为 list 且缺 `interface`）
- **AND** 顶层 `qos-profile` 的 netparse 侧被标注为 null，并附说明"模板无任何 qos-profile 规则"

#### Scenario: 多出来的字段被单列说明

- **WHEN** 读报告的 `part2_capability.extra_value_note`
- **THEN** 它明确指出 netparse 多出的接口级字段不在 19 列台账内，以及它们对 CI 建模的价值

---

### Requirement: 行按接口名对齐，不按索引

系统 SHALL 用接口名（大小写不敏感）匹配两侧的行，并在报告中分别统计「两侧都有」「仅 incumbent 有」「仅 netparse 有」三类。

#### Scenario: 名称匹配

- **WHEN** 两侧都有名为 `Eth-Trunk1` 的接口
- **THEN** 这两个接口的行被配成一对，逐列比较
- **AND** 不依赖它们在各自列表中的下标相同

#### Scenario: 数量不一致

- **WHEN** 某文件 incumbent 有 1044 个接口、netparse 有 1042 个
- **THEN** 报告的 `interface_alignment` 记录 `only_incumbent: 2`，并列出这 2 个接口名
- **AND** 这 2 个接口的缺失**不**被算成"19 列全空"

#### Scenario: 重名接口显式报错

- **WHEN** 某一侧出现重名接口
- **THEN** 工具**报错并跳过该文件**（不得静默合并），在报告的 `files_failed` 中计入

---

### Requirement: 报告给出差异样本

系统 SHALL 为每一列（若该列存在差异）给出至少 1 条差异样本，含设备名、接口名、incumbent 值、netparse 值。

样本 SHALL 从该列的实际差异中选取，SHALL NOT 人工编造。

#### Scenario: 样本存在

- **WHEN** 第 13 列（域名名称）两侧填充率不同
- **THEN** 报告的 `diff_samples["域名名称"]` 非空
- **AND** 每条样本含 `device` / `interface` / `incumbent` / `netparse` 四个字段

#### Scenario: 无差异的列不产生样本

- **WHEN** 第 1 列（设备名称）两侧完全一致
- **THEN** `diff_samples` 中该列不存在或为空数组

#### Scenario: 样本可回溯

- **WHEN** 拿一条样本的 `device` + `interface` 去原始配置里查
- **THEN** 能定位到对应的接口配置行

---

### Requirement: 报告给出根因分类

系统 SHALL 为每一个**未填满**的列给出根因分类，取值 ∈：`模板未覆盖该命令`、`字段丢失`、`join缺失`、`语义不同`（可多值）。

根因 SHALL 由**自动判定**得出，不得人工填写：

- `模板未覆盖该命令`：该列对应的命令族在**引擎内 trace**（`probe_stack.py` 的 `run(colls, lines, trace=...)`）中命中数为 0；
- `字段丢失`：命令被模板命中，但模板未捕获该列所需的字段；
- `join缺失`：字段齐备，但资产层需要跨集合按名 join（如限速需 join 顶层 `qos-profile`）；
- `语义不同`：两侧都有值但值不等。

#### Scenario: 根因自动判定

- **WHEN** 某列填充率为 0，且其对应命令族在引擎 trace 中命中 0 行
- **THEN** 报告该列的 `root_cause` 包含 `模板未覆盖该命令`
- **AND** `evidence` 含该命令族的总行数与命中行数

#### Scenario: 字段丢失的判定

- **WHEN** 某列填充率为 0，其命令族在 trace 中**有命中**，但模板未声明该列所需字段
- **THEN** `root_cause` 包含 `字段丢失`
- **AND** `evidence.source` 指明缺哪个字段

#### Scenario: 根因与工作量对应

- **WHEN** 某列的根因是 `字段丢失`
- **THEN** 该列的 `workload_estimate.per_column` 条目给出"要改哪个模板的哪条规则"

#### Scenario: 已知静态路由出接口缺口被正确归类

- **WHEN** 处理一个含 IPv4 静态路由的设备
- **THEN** 第 10 列（IPv4静态路由明细）的 `root_cause` 包含 `字段丢失`
- **AND** `evidence` 说明缺的是路由的**出接口**字段 —— 该字段由命令层在解析时拆出并存入（不是资产层反查算出来的）；`build_excel.build_route_index` 读取它并**跳过没有它的路由**。即：**"把路由挂到接口行"是资产层的 join（已存在），"解析出出接口"是命令层的事（netparse 缺）**
- **AND** 报告同时记录"带显式出接口的路由当前被 netparse 整条丢弃"这一事实

---

### Requirement: 报告明确回答工作量问题

系统 SHALL 在报告的 `workload_estimate` 段给出：逐列缺口 → 需要改什么 → 预估人日，并给出总人日区间，与 `docs/FINAL-plan.md` 8.4 的区间对照。

#### Scenario: 逐列工作量

- **WHEN** 全量跑完
- **THEN** `workload_estimate.per_column` 列出**每一个**未填满的列，含 `column` / `gap` / `change`（具体到文件与规则）/ `effort_days`

#### Scenario: 总人日与方案对照

- **WHEN** 全量跑完
- **THEN** `workload_estimate.total_effort_days` 给出 `{min, max}` 区间
- **AND** `workload_estimate.compare_with_plan` 引用 `docs/FINAL-plan.md` 8.4 的 `68-104` 人日区间，并说明本 diff 测出的"模板补齐"部分占其中多少

#### Scenario: 结论可回答决策问题

- **WHEN** 读报告的 `workload_estimate` 段
- **THEN** 能直接回答"netparse 距离替代 incumbent 还差哪几列、每列差多少、补齐共需多少人日"
- **AND** 能据此判断 `docs/FINAL-plan.md` 8.3 实验 1 的判据（差距大 → 重新评估路线 2）

---

### Requirement: 报告不含凭据明文

系统 SHALL 保证 diff 工具产出的任何文件（JSON / Markdown / 临时文件 / 日志）不含密文明文。

工具 SHALL 复用引擎的脱敏能力（不自行实现第二套脱敏），并在报告写盘后**再扫描一次**明文特征，命中即中止并报错。

#### Scenario: 报告无明文

- **WHEN** 全量跑完并检查报告文件
- **THEN** 报告全文中密文形态串（`%^%#` 等）出现次数为 0

#### Scenario: 写盘后自检

- **WHEN** 报告写盘
- **THEN** 工具对该文件做一次明文扫描
- **AND** 命中时工具以非 0 退出码终止，并打印命中位置，且**删除已写出的报告文件**

#### Scenario: 临时文件清理

- **WHEN** 工具为调用 `build_rows` 写过临时 JSON
- **THEN** 这些临时文件在流程结束时被删除
- **AND** 临时文件名保留原始 `<stem>`（含时间戳），以保证"采集时间"列一致

#### Scenario: 只读自检

- **WHEN** 全量跑完
- **THEN** 工具输出 `READ-ONLY OK`，并列出被检查的 3 个路径（原始配置 / 解析后 / 配置解析）
- **AND** 任一路径下有文件被修改时，工具以非 0 退出码终止并列出被改动的文件

---

### Requirement: 支持抽样冒烟模式

系统 SHALL 支持 `--sample N` 模式，随机抽取 N 个文件跑完整流程，用于快速验证工具本身可用。

抽样 SHALL 覆盖全部 26 个月目录（避免只命中某一类格式）。

#### Scenario: 抽样跑通

- **WHEN** 以 `--sample 20` 运行
- **THEN** 处理 20 个文件，产出与全量同结构的报告
- **AND** 运行时间显著短于全量

#### Scenario: 抽样覆盖各月

- **WHEN** 抽样结束
- **THEN** 报告的 `meta.months_covered` 列出的月份目录数 ≥ 20（当样本量允许）

---

### Requirement: 映射表有最小样例回归

系统 SHALL 为适配器映射表中的**每一条**映射提供一个最小样例：给一份 netparse 形状的输入 JSON，断言适配器输出等于期望的 incumbent 形状 JSON。

#### Scenario: 映射表全覆盖

- **WHEN** 运行适配器回归
- **THEN** 映射表里的每一条都有对应样例且通过
- **AND** 存在映射条目缺样例时，回归**失败**（而不是跳过）

#### Scenario: 映射写错会被抓到

- **WHEN** 某条映射被改成错误的源路径
- **THEN** 对应样例的断言失败，回归失败

#### Scenario: 与能力 A 的交互

- **WHEN** 适配器处理含密码的 netparse 结果
- **THEN** 适配器输出中的密码字段是掩码串（脱敏在引擎已完成，适配器不做二次处理也不还原明文）
