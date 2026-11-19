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
- **AND** `Family == "ipv6"` 的条目进入 `IPV6` 列表，且键名为 `ip`、前缀键为 `mask`（与 incumbent 形状一致）
  （实测依据：`解析后/.../SNANK-MC-CMNET-BAS03-SCEC-X16A_*.json` 的 `interfaces[].IP.IPV6[0]` 为
  `{"ip": "2409:8070:9A:1::7009", "mask": "127"}`，键名是 `ip` 而非 `addr`）
- **AND** `Family` 字段本身不出现在输出中

#### Scenario: 未捕获的字段不被合成

- **WHEN** netparse 的某接口不含任何 VLAN 相关字段，且真实 trace 证明对应命令未被捕获
- **THEN** 适配器输出中该接口**不含** `VLAN` 键
- **AND** 不出现 `VLAN: {"type": "", "user-vlan": [], "QinQ": []}` 之类的空壳结构

#### Scenario: VLAN 内容对应但结构不同

- **WHEN** netparse 接口含标量 `UserVlan: 100`，incumbent 接口含嵌套 `VLAN.user-vlan` 中的同一用户 VLAN 内容
- **THEN** 适配器只把已捕获的 `UserVlan` 值搬到 incumbent 对应位置，不合成 `type/QinQ/dot1q` 等未捕获内容
- **AND** 报告把 `VLAN ↔ UserVlan` 标为「内容对应、结构不同」，不得据此声称 netparse 完全没有用户 VLAN 内容
- **AND** PVLAN/CVLAN/VLAN 封装类型等其余差异分别按模板声明和真实 trace 归因，不笼统标为“模板未覆盖”

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

---

### Requirement: 报告给出全局汇总口径（值条数），不只给逐文件填充率

系统 SHALL 在逐文件填充率之外，给出**全局汇总**的每列**值条数**（不按文件切分，把所有文件的记录汇总后统计每列的非空值个数）。

**理由（此前样本，保留作历史诊断）**：第 10 列「IPv4静态路由明细」在**单文件粒度上可能缺乏分辨力** —— 实测 `SNANK-MC-CMNET-BAS03-SCEC-X16A`（1558 接口）**incumbent 自己就是 0/1558 = 0.0%**（该文件 79 条路由全无显式出接口）；此前 60 台样本的形态 2「出接口 + 下一跳」为 75 条，修前丢 72 条（96%）。120 文件样本的全局口径为 incumbent 第 10 列 **178** 条（分布在 42 个文件）vs netparse 修前约 **6** 条，差 **96.6%**。
**最新统一复测见「报告给出根因分类」**：`check_route_forms.py` 的 60 文件、150 条显式出接口（排除 NULL0）结果与上述样本筛选/统计口径不同，必须并列标注、不得混算。

**报告规则**：
1. 每列**同时**给「逐文件填充率」与「全局值条数」；
2. **判据**：全局值条数两侧差 > **20%** → **显式标红**（`flag: "RED"`），即使逐文件填充率都接近 0；
3. 当某列两侧的逐文件填充率**都 < 1%** → 该列附注「**本列逐文件差异不具分辨力，请以全局值条数为准**」。

#### Scenario: 全局值条数存在

- **WHEN** 全量跑完
- **THEN** 报告的 `global_column_value_count` 含 19 个列名，每列均有 `incumbent` / `netparse_before_fix` / `netparse_after_fix` 三栏值条数
- **AND** 每栏值 = 把所有文件对应侧的该列非空值累加

#### Scenario: 超过 20% 差距被标红

- **WHEN** 某列的全局值条数两侧相差 > 20%
- **THEN** 该列的 `flag` 为 `"RED"`，且 `flag_reason` 说明原因
- **AND** **即使该列的逐文件填充率两侧都接近 0，也必须标红**

#### Scenario: 无分辨力的列被标注

- **WHEN** 某列两侧的逐文件填充率都 < 1%
- **THEN** 该列的 `per_file_resolvable` 为 `false`
- **AND** 该列有 `note` 说明"本列逐文件差异不具分辨力，请以全局值条数为准"

#### Scenario: 第 10 列能被看见

- **WHEN** 全量跑完
- **THEN** 「IPv4静态路由明细」列的全局值条数显示 incumbent 远大于 netparse（修前）
- **AND** 该列被标红
- **AND** **报告不会显示"该列无差距"**

---

### Requirement: 19 列全部给「incumbent / 修前 / 修后」三栏

系统 SHALL 支持 `--template <path>` 参数，并 SHALL 对 **19 列全部列**给出三栏：`incumbent` / `netparse_before_fix` / `netparse_after_fix`。逐文件填充率、全局填充率与全局值条数 SHALL 都遵守该三栏结构，不得只对预计受影响的列输出三栏。

修前模板 SHALL 快照为 `baseline-template.cmdb`（= 改动前 `templates/huawei-vrp.cmdb` 的原样副本），修后模板 = 当前 `templates/huawei-vrp.cmdb`。

**理由**：分栏能量化「补齐这 0.5 人日能买回多少」；全列对照还能发现模板修复对其他列的非预期影响，避免只验证第 10 列而漏掉回归。

#### Scenario: 19 列三栏完整

- **WHEN** 全量跑完
- **THEN** `column_fill_rate` 与 `global_column_value_count` 的列键集合均与 `build_excel.COLUMNS` 的 19 列完全一致
- **AND** 每列均有 `incumbent` / `netparse_before_fix` / `netparse_after_fix` 三栏，包括预计不受模板改动影响的列

#### Scenario: 静态路由修复效果可对照

- **WHEN** 检查「IPv4静态路由明细」列
- **THEN** 修前全局值条数差与修后全局值条数差分别计算、分别标注
- **AND** 最新 `check_route_forms.py` 复测结果与该三栏结论并列呈现，不使用“修前约 3 条”的此前样本数字替代最新 150 条口径

#### Scenario: 修前模板可复现

- **WHEN** 用 `--template .devforge/spec/changes/netparse-v3-diff-and-mask/baseline-template.cmdb` 跑
- **THEN** 产出与"修前"栏一致的数字
- **AND** 该快照文件的内容与改动前的 `templates/huawei-vrp.cmdb` 逐字节相同

#### Scenario: 两栏都标注来源

- **WHEN** 读报告的三栏
- **THEN** 每栏都标明它来自哪份模板/产物（不是无来源的数字）

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

**SHALL NOT** 只报缺口。接口级字段口径 MUST 统一为：**netparse 15 / incumbent 10 / 共有 4 / netparse 独有 11 / incumbent 独有 6**。只统计接口记录的**直接子键**，**不含子集合内部字段**（如 `Statistic.*`、`Vrrp.*`、`IpAddress.*` 不展开为独立接口级字段）；按业务语义对应，不按命名风格机械比较。此前“多 17 个”废弃，结论统一为“多 11 个”。

netparse 独有 11 项 MUST 为：`AdminStatus` `CarrierDownHoldTime` `Dcn` `Duplex` `EthTrunkMember` `Isis` `Mtu` `Speed` `Statistic` `Type` `UserVlan`。多出来的字段虽不在 19 列台账内，但 SHALL 单列其对 CI 建模的价值。

`VLAN ↔ UserVlan` SHALL 单列为「内容对应、结构不同」：incumbent 的 `VLAN` 是含 `type/user-vlan/QinQ/dot1q` 的嵌套对象，netparse 的 `UserVlan` 是标量。集合清单保留原始直接子键差异，但结论不得把该结构差异说成 netparse 完全没有用户 VLAN 内容；更细的 PVLAN/CVLAN/VLAN 封装类型差异按证据分别归因。

#### Scenario: 双向字段清单

- **WHEN** 全量跑完
- **THEN** 报告的 `part2_capability.interface_level_fields` 含 `matched` / `only_incumbent` / `only_netparse` 三个清单
- **AND** `coverage` 明确给出 `incumbent=10 / netparse=15 / matched=4 / only_netparse=11 / only_incumbent=6`
- **AND** 报告注明统计口径为“接口记录直接子键，不含子集合内部字段”

#### Scenario: 已知基线被复现

- **WHEN** 在同一份 1558 接口的配置上运行
- **THEN** `matched` 恰为 4 项（`name` / `description` / `IP` / `vpn`）
- **AND** `only_incumbent` 恰为 6 项（`QOS` / `VLAN` / `bas` / `multicast` / `qos-profile` / `traffic-policy`）
- **AND** `only_netparse` 恰为 11 项（`AdminStatus` / `CarrierDownHoldTime` / `Dcn` / `Duplex` / `EthTrunkMember` / `Isis` / `Mtu` / `Speed` / `Statistic` / `Type` / `UserVlan`）
- **AND** `content_correspondence_diff_shape` 含 `VLAN ↔ UserVlan`，并说明“内容对应、结构不同”

#### Scenario: 集合级结构差异被记录

- **WHEN** 全量跑完
- **THEN** 报告的 `part2_capability.collection_level_fields` 记录 `qos_profile_top`（incumbent 为顶层 list，netparse 为 null）与 `route-static`（incumbent 为 dict 含 `interface?`，netparse 为 list 且缺 `interface`）
- **AND** 顶层 `qos-profile` 的 netparse 侧被标注为 null，并附说明"模板无任何 qos-profile 规则"

#### Scenario: 多出来的字段被单列说明

- **WHEN** 读报告的 `part2_capability.extra_value_note`
- **THEN** 它明确指出 netparse 多出的接口级字段不在 19 列台账内，以及它们对 CI 建模的价值

---

### Requirement: 报告给出**资产类别覆盖对比**（谁覆盖的资产种类多）

系统 SHALL 在段二（能力口径）给出**两侧各覆盖了哪些资产类别**的对比。

**分类必须是三分类**（实测依据，见下）：

| 分类 | 数量 | 内容 |
|---|---|---|
| **共有 · 结构同** | 3 | `Device↔device`、`Interfaces↔interfaces`、`RouteStatics↔route-static` |
| **共有 · 结构不同（同一业务对象）** | 2 | `IpAddress↔interfaces[].IP`、`VpnInstances↔interfaces[].vpn` |
| **netparse 真正独有** | **7** | `EthTrunkMember` `Bgp` `BgpPeers` `Acls` `AclRules` `Nqas` `NqaSessions` |
| **incumbent 真正独有** | 1 | `qos-profile`（顶层命名空间定义） |

**⇒ netparse 12 类 = 3 + 2 + 7；incumbent 4 类 = 3 + 0 + 1。**

**⚠️ 为什么不能简单说"netparse 独有 9 类"**：实测 incumbent 的 `interfaces[]` 子键只有 **10 种** —— `name` / `description` / `VLAN` / `bas` / `IP` / `vpn` / `multicast` / `traffic-policy` / `QOS` / `qos-profile`（120 个产物抽样）⇒ **`IP` 出现在 120/120 个产物里（26831 次）、`vpn` 在 117/120（21486 次）**。
⇒ **`IpAddress` 与 `VpnInstances` 的业务内容 incumbent 是有的**，netparse 只是把它们提升成了**独立顶层集合**（结构差异，不是内容差异）。
⇒ **把它们算作"netparse 独有"会高估 2 类**；一旦有人打开 incumbent 的 JSON 就会发现 IP 数据明明在那儿 ⇒ **整份报告的可信度受损**。

**⇒ 报告 SHALL 给出 netparse 真正独有类别清单 + 每个类别在真实语料里的实例数。**

#### Scenario: 资产类别清单与三分类

- **WHEN** 全量跑完
- **THEN** 报告的 `part2_capability.asset_categories` 含两侧各自的类别清单与**三分类计数**
- **AND** 计数为：netparse `total=12 / shared_same_shape=3 / shared_diff_shape=2 / only=7`；incumbent `total=4 / shared_same_shape=3 / shared_diff_shape=0 / only=1`

#### Scenario: 真正独有类别的实例数

- **WHEN** 读 `netparse_only_instances`
- **THEN** 它恰含 **7** 项：`EthTrunkMember` `Bgp` `BgpPeers` `Acls` `AclRules` `Nqas` `NqaSessions`
- **AND** 每项含实例数（条数，不是行数比例）
- **AND** **不含** `IpAddress` 与 `VpnInstances`（它们属"共有·结构不同"）

#### Scenario: 结构不同的两类被正确归位

- **WHEN** 读 `asset_categories._taxonomy.shared_diff_shape`
- **THEN** 它恰含 `IpAddress↔interfaces[].IP` 与 `VpnInstances↔interfaces[].vpn`
- **AND** **不**出现在 `netparse_only` 里

#### Scenario: 结论两侧都报

- **WHEN** 读报告的结论段
- **THEN** 它同时给出**负面**（台账列差 N）与**正面**（资产类别真正独有 7 类、另有 2 类是同一业务对象不同层级、接口级字段多 11 个）两部分
- **AND** **不**只报其中一面

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
- **AND** 每条样本含 `device` / `interface` / `incumbent` / `netparse_before_fix` / `netparse_after_fix` 五个字段

#### Scenario: 无差异的列不产生样本

- **WHEN** 第 1 列（设备名称）两侧完全一致
- **THEN** `diff_samples` 中该列不存在或为空数组

#### Scenario: 样本可回溯

- **WHEN** 拿一条样本的 `device` + `interface` 去原始配置里查
- **THEN** 能定位到对应的接口配置行

---

### Requirement: 报告给出有真实证据的根因分类

系统 SHALL 为每一个**未填满**的列给出根因分类，取值 ∈：`模板未覆盖该命令`、`字段丢失`、**`整条记录丢失`**、`join缺失`、`语义不同`、`待人工判定`。

**自动根因的证据门禁**：不得只凭映射表的 `source=None`、`root_cause_if_empty` 或其他人工常量自动断言根因。这些常量只是待验证假设。每个自动分类 MUST 附 `evidence`，至少含：
1. **真实输入证据**：文件标识、原始行号及必要的脱敏命令样本；
2. **模板声明证据**：相关集合、目标字段声明、候选规则标识/文本位置；
3. **真实 trace 证据**：该行的命中/失配结果、命中集合与规则、产生记录情况，以及命令族 `total_lines/hit_lines`。
证据不足时 SHALL 输出 `root_cause: "待人工判定"` 与 `evidence_status: "INSUFFICIENT"`，不得猜测。

**分类判据**：
- `模板未覆盖该命令`：真实命令族在 trace 中 0 命中，且模板声明检查确认无对应捕获规则；
- `字段丢失`：同一真实行命中规则并产生记录，但模板未声明/未捕获目标字段；
- `整条记录丢失`：真实配置可定位 incumbent 有记录而 netparse 无对应记录，候选规则匹配/失配结果能解释缺失；不得把“trace 0 命中”同时描述成“命令被模板命中”；
- `join缺失`：**单列**。真实 trace 与输出记录证明所需原始字段已捕获且两侧原始值可对应，仅资产层缺少跨集合关联；附源/目标集合、关联键、join 前后条数。原始字段未捕获时不得归为 join 缺失；
- `语义不同`：两侧原始字段可对应且都有值，但值不同。

同一列的不同样本允许不同根因，但每个根因 SHALL 有自己的证据集合；不得用无证据的多值标签掩盖分类不确定性。

**静态路由实测必须并列保留两组口径**：
- **最新结果（`tools/check_route_forms.py`）**：**60/60 文件修后与 incumbent 一致**；60 文件中显式出接口（**排除 NULL0**）共 **150 条**，修前丢 **150 条（100%）**，修后丢 **0 条**。
- **此前样本**：60 台、20250824 快照、1651 条 `ip route-static` 中，形态 2「出接口 + 下一跳」75 条，修前丢 72 条（96%）；该数字只作历史诊断，不与最新 150 条结果混算。
- **更早 18.1% 估算废弃**：`interface NULL0` 污染了“已知接口名集合”，把 NULL0 下一跳误算成显式出接口。后续判定 MUST 排除 `NULL0`。

#### Scenario: 模板未覆盖的自动判定有证据

- **WHEN** 某列填充率为 0，真实命令族在引擎 trace 中命中 0 行，且模板声明检查无对应捕获规则
- **THEN** 报告该列的 `root_cause` 包含 `模板未覆盖该命令`
- **AND** `evidence` 含真实文件/行号或脱敏样本、模板检查结果、命令族总行数与命中行数、真实 trace 摘要

#### Scenario: source 为空不能单独触发根因

- **WHEN** 映射条目为 `source=None`，但没有模板声明和真实 trace 证据
- **THEN** 工具不得自动输出 `模板未覆盖该命令` 或 `字段丢失`
- **AND** 输出 `root_cause="待人工判定"`、`evidence_status="INSUFFICIENT"`

#### Scenario: 字段丢失的判定

- **WHEN** 某真实行在 trace 中命中规则并产生记录，但模板声明缺少该列所需字段
- **THEN** `root_cause` 包含 `字段丢失`
- **AND** `evidence` 指明缺失字段、相关集合/规则、真实行及产生的记录

#### Scenario: join 缺失单列且不与捕获缺失混淆

- **WHEN** 真实 trace 与 netparse 记录证明关联所需原始字段已捕获，仅资产层缺少跨集合 join
- **THEN** `root_cause` 单列 `join缺失`
- **AND** `evidence` 含源/目标集合、关联键及 join 前后条数
- **AND** 若原始字段未捕获，则不得同时标注 `join缺失`

#### Scenario: 根因与工作量对应

- **WHEN** 某列的根因经证据门禁判为 `字段丢失`
- **THEN** 该列的 `workload_estimate.per_column` 条目给出"要改哪个模板的哪条规则"
- **AND** 证据不足的列进入待诊断工作量，不套用自动人日常量

#### Scenario: 静态路由最新结果被记录

- **WHEN** 运行 `tools/check_route_forms.py` 并把结果写入报告
- **THEN** 报告记录 60/60 文件修后一致
- **AND** 记录显式出接口（排除 NULL0）150 条、修前丢 150（100%）、修后丢 0
- **AND** 此前 75/72/96% 明确标为“此前样本”，不与最新结果混算

#### Scenario: 已知静态路由出接口缺口被正确归类

- **WHEN** 处理修前模板生成的第 10 列缺口
- **THEN** 报告按真实行分别区分 `字段丢失` 与 `整条记录丢失`
- **AND** `evidence` 说明出接口由命令层解析时拆出，`build_excel.build_route_index` 只读取它并跳过没有它的路由
- **AND** 修后模板结果与最新 60 文件复测共同证明修复效果，不把修前根因原样套到修后结果

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

### Requirement: 支持按月轮转的分层抽样冒烟模式

系统 SHALL 支持 `--sample N` 模式，抽取 N 个文件跑完整流程，用于快速验证工具本身可用。

抽样 SHALL 采用**按月轮转分层**：先确定存在至少 1 个可处理文件对的月份集合 `M`，以固定 `sample_seed` 打乱月份顺序；每轮从每个尚未耗尽文件的月份随机抽 1 个文件（月内不放回），循环至抽满 N 个或候选耗尽。N 不超过候选文件总数时，期望不同月份数为 **`expected_distinct_months=min(N,len(M))`**，当前最多 26 个月。报告 SHALL 记录 `available_months`、`expected_distinct_months`、实际 `months_covered`、抽样算法与 `sample_seed`。

该期望取代“20 个样本必须覆盖全部 26 个月”的矛盾要求。抽样报告 SHALL 注明其为月份分层样本，不得把抽样填充率直接宣称为全量无偏估计。

#### Scenario: 抽样跑通

- **WHEN** 以 `--sample 20` 运行且可选月份不少于 20、候选文件不少于 20
- **THEN** 处理 20 个文件，产出与全量同结构的报告
- **AND** `expected_distinct_months=20` 且实际 `len(months_covered)=20`
- **AND** 运行时间显著短于全量

#### Scenario: 样本量小于可选月数

- **WHEN** `N < len(M)` 且候选文件充足
- **THEN** 实际不同月份数等于 N，而不是被要求等于 `len(M)`

#### Scenario: 样本量大于可选月数

- **WHEN** `N > len(M)` 且候选文件不少于 N
- **THEN** 实际不同月份数等于 `len(M)`，其余样本按月轮转继续抽取

#### Scenario: 可选月份或文件不足时显式报告

- **WHEN** 可选月份数或候选文件数不足以达到 N
- **THEN** 工具报告实际可选集合与候选总数，不伪造月份或重复文件凑数
- **AND** `expected_distinct_months` 按实际 `len(M)` 计算

---

### Requirement: 报告的 meta 段带脱敏统计（模板标注完整度）

系统 SHALL 在报告的 `meta` 段给出脱敏统计 `l1_hits` 与 `l2_hits`（来自 `masking.STATS.as_dict()`）。

**`l2_hits` 是「模板标注完整度」的度量**：L2（形态兜底）命中数远大于 L1（显式标注）⇒ **模板漏标了 secret 字段**。实测真实文件 **L1=3/4/4/9、L2=0** ⇒ 当前模板对已抓到的凭据字段标注完整、无漏标。

⚠️ **`STATS` 是 per-run 的**（`run()` 开头会 `STATS.reset()`）⇒ 工具 SHALL **自己累加**每个文件跑完后的统计，不能只在最后读一次。

#### Scenario: meta 含脱敏统计

- **WHEN** 全量跑完
- **THEN** 报告的 `meta` 含 `l1_hits` 与 `l2_hits`
- **AND** 它们的和 = 全量运行中被遮蔽的值总数

#### Scenario: l2_hits 反映模板标注完整度

- **WHEN** 某个模板漏标了 secret 字段（该字段靠 L2 形态兜底才被遮蔽）
- **THEN** `l2_hits > 0`，而对应字段的 L1 命中数不增加
- **AND** 报告能据此回答"模板还需不需要补 secret 标注"

#### Scenario: 统计跨文件累加

- **WHEN** 跑多个文件
- **THEN** `meta.l1_hits` / `meta.l2_hits` 是**各文件之和**，不是最后一个文件的值

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

#### Scenario: ⭐ 段一无密码列（incumbent 侧无 peer/BGP 段）

**实测事实（2026-11-19 复核）**：incumbent 产物的顶层键**恒为 5 个** —— `device_name` / `device_ip` / `interfaces` / `route-static` / `qos-profile`（401 个产物抽样，顶层键并集一致）。
**incumbent 的 `huawei_vrp.fsm` 不抓 BGP peer** ⇒ 其产物里**既没有密码、也没有 peer 段**（实测：一个配置原文含 58 行 `password cipher` 的设备，产物里 `%^%#` 命中 False、`password` 命中 False、`peer` 命中 False）。

**⇒ 段一（19 列台账）根本没有密码列**，不涉及两侧脱敏对齐；**段二（能力口径）会显示「netparse 独有 Bgp / BgpPeers 能力」—— 这是能力更强的证据，不是差异项**。

**⇒ 映射表 SHALL NOT 添加 peer / Password 相关行** —— incumbent 侧无字段可映射，加了会写出**恒为空的映射**（假缺口）。

- **WHEN** 构建映射表
- **THEN** 映射表中**不含** `Bgp` / `BgpPeers` / `Password` 等 incumbent 侧不存在的目标字段
- **AND** 段一报告里不出现任何与密码相关的列或结论
- **AND** 段二的 `asset_categories.netparse_only` 清单里**包含** `Bgp` / `BgpPeers`（作为 netparse 独有的资产类别；不与接口直接子键的 `only_netparse` 混用）
