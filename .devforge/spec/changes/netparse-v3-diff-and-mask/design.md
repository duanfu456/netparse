# Design · netparse-v3-diff-and-mask

> 配套：`proposal.md` · `specs/secret-mask/spec.md` · `specs/incumbent-diff/spec.md` · `tasks.md`
> 作者：高见远（架构师）｜日期：2026-11-19

---

## Context

### 现状（代码事实，全部带出处）

**netparse 仓库** `C:\Users\duanf\WorkBuddy\2026-11-19-14-44-56\netparse\`

| 组件 | 位置 | 事实 |
|---|---|---|
| 引擎 | `tools/probe_stack.py`（约 780 行） | 唯一实现。`parse_template` / `compile_rule` / `run` / `lint` 四个主函数 |
| 写入路径 | `probe_stack.py:370-397` `apply_writes` | 唯一的字段落值入口；`convert(value, fld)` 在 `probe_stack.py:388/397` 被调用 |
| 捕获组取值 | `probe_stack.py:630-636` | `path = cr.group_paths.get(g, g)`；**裸命名组不做路径还原** |
| 模板 | `templates/huawei-vrp.cmdb`（234 行） | **唯一交付模板**。12 个集合、5 个子集合 |
| 密码规则 | `huawei-vrp.cmdb:167-168, 174` | `Password type(str)` + `-> BgpPeers{Peer,PasswordType="cipher",Password}` → **明文落盘** |
| secondary 地址 | `huawei-vrp.cmdb:96` | `^\s*ip address\s+${Address}\s+${Mask}(?:\s+sub)?\s*$` → **`sub` 被正则消耗但未捕获** |
| 静态路由 | `huawei-vrp.cmdb:117-124` | 只有 `Prefix/Mask/NextHop/Preference/Description/VpnInstance`；**无出接口字段** |
| 回归 | `tests/run_regression.py` | 比对 `tests/fixtures/sample-vrp.expected.json`；7 集合 / 11 记录；**lint 有"错误"即失败**（`run_regression.py:68`） |
| 探针 | `tools/probe_*.py`（13 个） | 全部 `import probe_stack` —— **改引擎文件名会打断全部探针** |

**incumbent 生产管线** `E:\配置文件\配置解析\华为\`（**只读**）

| 组件 | 位置 | 事实 |
|---|---|---|
| 命令层 | `parse_huawei_vrp.py`（41KB） | 3 层架构：Python 切块 → TextFSM 逐块 → Python 整形 |
| TextFSM 模板 | `huawei_vrp.fsm`（3.5KB，69 行） | `Value List IPV4 ((?P<ip>\S+) (?P<mask>\S+)(?P<sub>\s+sub)?)` —— **sub 是捕获的** |
| 资产层 | `build_excel.py`（21KB） | **纯函数、模块级 import 无副作用**（`if __name__ == "__main__"` 在第 599 行，`main()` 之前无写操作） |
| 资产层可复用函数 | `build_excel.py` | `mask_to_prefix:128` `cidr4:152` `join_cidr4:160` `join_cidr6:170` `to_m:185` `fmt_m:196` `get_speed:205` `get_vlan_fields:214` `build_qos_index:254` `extract_time:296` `pick_latest_per_device:318` `build_route_index:344` `build_rows:374` `COLUMNS:104` |
| schema 权威 | `doc\字段解析说明.md`（31KB） | 输出字段的权威定义 |
| 校验脚本 | `vlancheck.py` `routecheck.py` `crosscheck.py` `fullcheck.py` `verify_excel.py` | 5 个 |

**incumbent 解析产物** `E:\配置文件\BRAS&SR\解析后\`（**只读**）

- `_index.json`：`file_count 8440 / ok 8440 / empty 0 / fail 0 / total_interfaces 6,278,430 / elapsed_sec 490.8`
- 目录结构：`解析后/<月目录>/华为/*.json`（26 个月目录）
- 单文件 JSON 顶层键：`device_name` `device_ip` `interfaces` `route-static` `qos-profile`
- `route-static` 是 **dict**（`{"IPV4":[...], "IPV6":[...]}`），不是 list
- `route-static.IPV4[]` 元素：`{dest, mask, interface, nexthop, vpn, preference, description}`
  —— ⚠️ **`interface`（出接口）只在路由有显式出接口时存在**
- 接口级键（实测同一样本文件 1044 个接口）：`name` 1044 / `description` 868 / `IP` 301 / `multicast` 141 / `vpn` 232 / `VLAN` 809 / `bas` 496 / `traffic-policy` 88 / `QOS` 47 / `qos-profile` 31

**语料**（本轮复核）

- 全语料 11462 个文件 → **632 个唯一管理 IP**：华为 626 / 中兴 64 / 诺基亚 6；Cisco 0 / Juniper 0
- ⚠️ 团队给的"1013（华为 318/中兴 64/诺基亚 631）"**与实测不符**：诺基亚实际只有 **6** 个文件，且 631 不成立（疑为 984 的笔误）。华为唯一 IP 实测 **626**（不是 318）。**规格以实测为准，并列入 Open Questions Q1。**
- **8440 个 incumbent JSON ↔ 原始配置路径映射：100% 命中（8440/8440）**。反推规则：`解析后/<月>/华为/<stem>.json` → `原始配置/<月>/华为/<stem>` 或 `<stem>.txt`（两种候选，先无扩展名再 `.txt`）

### 本轮补充核实的 4 条事实（直接决定 D2 的口径设计）

> 由 team-lead 提供，我逐条独立复核；**其中事实 3 的机制描述有误，结论正确**，已在下方标注。

| # | 事实 | 复核结果 | 对设计的影响 |
|---|---|---|---|
| **1** | **接口数两边精确一致**：同一份配置（1558 接口）incumbent 1558 / netparse 1558 | ✅ **独立复现成立**（我另在 1044 接口的文件上也精确一致） | 接口**识别层**两边完全对齐 → **差距全在字段**。这是 diff 的第一项基准，也是"如果字段差距也小就能替代"的前提 |
| **2** | **接口级字段 4/10 对得上**；netparse **多出 10+ 个字段**（AdminStatus/Mtu/Speed/Duplex/Dcn/Isis…）incumbent 全没有 | ✅ 按最新统一口径复核：**netparse 15 / incumbent 10 / 共有 4 / netparse 独有 11 / incumbent 独有 6**；只统计接口记录的直接子键，**不下钻、不展开子集合** | ⇒ **diff 必须双向报（缺什么 + 多什么）**。只报缺口会系统性低估 netparse。**已并入 D2 的"段二 · 能力口径"**；此前“17 个”误计了子集合字段，废弃 |
| **3** | "`route-static` 里没有 interface 字段 → 按接口反查是资产层的事" | ⚠️ **结论对，机制错**：`interface` **不是** build_excel 反查算的，而是**解析时**由 `_split_iface_nexthop()` 拆出并存入的（实测有 `{"interface":"Eth-Trunk55.2500","nexthop":"..."}` 的条目）；`build_route_index` 只**读它并跳过没有它的路由** | ⇒ 边界不变（**join 归资产层**），但**netparse 的缺口是"该抓的字段没抓"**，不是"资产层算不出来" ⇒ 根因分类标 `字段丢失`，已改写 R6 |
| **4** | **限速是三层 join**：顶层命名空间定义（list，40/40 文件都有）+ 接口级引用（dict，1770 个接口）+ 接口级直给（dict，2304 个接口）；规则是"两者都存在取小" | ✅ 成立 | ⇒ **第 6/7 列在中间 JSON 层无定义** → **台账口径必须走 `build_rows`**。这是 D2 的决定性理由（原版只说"业务口径"，现补上硬理由） |

### 两个能力的依赖关系

```
                    ┌──────────────────────────────────────────┐
                    │  netparse 引擎 (tools/probe_stack.py)      │
                    │                                          │
   能力 A ─────────►│  apply_writes 落值前插入遮蔽              │
   (脱敏)           │  lint 增加 secret 相关检查                │
                    └───────────────┬──────────────────────────┘
                                    │ 产出 netparse JSON
                                    ▼
                    ┌──────────────────────────────────────────┐
   能力 B ─────────►│  适配器：netparse JSON → incumbent 形状    │
   (diff)           │  （纯结构映射，不合成数据）               │
                    └───────────────┬──────────────────────────┘
                                    │ 写临时 JSON
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │  import build_excel (只读)               │
                    │  build_rows(json_path) → 19 列行          │
                    └───────────────┬──────────────────────────┘
                                    │
                    ┌───────────────┴──────────────────────────┐
                    │  diff：netparse 19 列 vs incumbent 19 列   │
                    │  + 根因分类 + 工作量结论                    │
                    └──────────────────────────────────────────┘
```

**⇒ 能力 A 必须先于能力 B**：diff 报告要落到文件上，未脱敏的 diff 报告会**把密码明文写进报告**。

---

## Goals / Non-Goals

### Goals

| # | 目标 | 可验证 |
|---|---|---|
| G1 | netparse 产出的任何 JSON 中**不含可用密文明文** | 全量 JSON 文本 grep `%^%#` → 0 命中 |
| G2 | 「未配置」与「已配置已遮蔽」在输出中**可区分** | 键存在性 + 值断言 |
| G3 | 复用 incumbent 资产层，**零修改** incumbent 文件 | 只读自检：跑完后 mtime 不变 |
| G4 | 产出一份能支撑"投 / 不投"决策的 diff 报告 | 报告含 19 列填充率 + 根因 + 工作量 |
| G5 | diff 结论**可复现** | 同一份代码 + 同一份语料 → 同一份报告 |
| G6 | 本变更**不改变引擎的解析语义**（脱敏除外） | 现有回归基线除密码字段外逐字段不变 |

### Non-Goals

见 `proposal.md` 的 10 条 Non-Goals。补充两条设计层面的：

11. **不重构引擎**。本变更**不抽包、不改类结构、不动 `run()` 主循环**。引擎的任何结构改造（抽 `src/netparse/`）是独立变更（见 D10）。
12. **不追求 diff 工具的通用性**。diff 工具是**一次性决策工具**，不是产品组件。它的验收标准是"能回答那个问题"，不是"功能完备"。

---

## Decisions

> 每条含：决策内容 · 理由 · **为什么不用另一种** · 代价。

---

### D1 · 形状对齐方式 = **适配器**（netparse JSON → incumbent 形状）

**决策**：新写一个纯函数适配器 `netparse_json → incumbent_shape`，把 netparse 的输出翻译成 incumbent 的 JSON 形状，然后**两侧都喂给 incumbent 的 `build_rows`**。

**理由**：
- netparse 的模板风格（PascalCase 字段名 + "像配置"的声明式）是它相对 incumbent 的**唯一优势**（见方案评估 B 节）。为了比对而把模板改成 incumbent 的 snake_case，等于自毁优势。
- 适配器是**一次性的、单向的、可测的**纯映射层，出问题能一眼定位；改模板则会把"风格问题"混进"能力差距"里，让 diff 结论失真。
- 适配器是 diff 工具的**内部实现**，不进引擎，不影响任何其他消费者。

**为什么不用另一种**：
- ❌ **改 netparse 模板字段名去贴合 incumbent 形状**：要改 `Name→name`、`Description→description`、`IpAddress[].Address→IP.IPV4[].ip`、`SysName→device_name`、`RouteStatics`（list）→`route-static`（dict），且要把 5 个子集合拆开重排。**模板会变得不像配置**，直接抵消 netparse 的可读性优势；同时模板改动会**污染 diff 结论**（分不清"能力差距"和"字段改名差距"）。
- ❌ **改 `build_excel.py` 直接吃 netparse 形状**：**违反硬约束 1**（incumbent 生产脚本只读）。且这条路线要动的是唯一存活的资产层实现，风险与收益完全倒挂。

**代价**：多一层代码（约 150-250 行）；适配器的映射表需要人工维护，且**必须严格只做映射不做语义**（见 D9）。

---

### D2 · diff 口径 = **两段式**：台账口径（19 列）+ 能力口径（双向字段覆盖）；台账口径**复用** `build_excel.build_rows`

**决策**：diff 产出**两段互不替代**的结论：

| 段 | 单位 | 回答的问题 | 数据来源 |
|---|---|---|---|
| **段一 · 台账口径** | 19 列的一行（一行一接口） | "能不能替代 incumbent，产出**同样**的接口台账？" | 两侧都过 `import build_excel` 的 `build_rows(json_path)` |
| **段二 · 能力口径** | 字段（双向） | "netparse 的**整体**解析能力是更强还是更弱？多出来的那些字段值不值得用上？" | 两侧的**中间 JSON**（按字段名归一化后做集合比较） |

台账口径**不重新实现**掩码→CIDR、限速取小、qinq→PVLAN/CVLAN、快照去重等资产层规则。

**⚠️ 段一必须再分两个粒度（否则会漏掉最严重的一类差距）**：

| 粒度 | 指标 | 作用 |
|---|---|---|
| **逐文件** | 每文件每列的 `{filled, total, rate}` | 看**分布**：哪些设备/哪些列差 |
| **全局汇总** | 每列的**值条数**（不按文件切分） | 看**总量差距** |

**为什么必须有全局汇总（实测证据）**：第 10 列「IPv4静态路由明细」在**单文件粒度上没有分辨力** ——
- 实测 `SNANK-MC-CMNET-BAS03-SCEC-X16A`（1558 接口）：**incumbent 自己就是 0/1558 = 0.0%**（该文件 79 条路由全无显式出接口）—— 逐列填充率与 team-lead 给的 19 列基线**逐项吻合**
- **此前样本**：netparse 修前丢掉的 72 条「出接口 + 下一跳」路由**分散在 60 个文件里**（单文件仅 1-2 条）
- ⇒ 该样本的逐文件口径会显示"第 10 列：incumbent 0%、netparse 0% → 无差距"，**把 75 条中丢 72 条（96%）的形态 2 丢失掩盖掉**
- ⇒ 实测 120 文件样本的**全局**口径：incumbent 第 10 列值条数 **178**（分布在 42 个文件）vs netparse 修前 ≈ **6** 条 → **差 96.6%**

**⇒ 报告规则**：
1. 每列**同时**给「逐文件填充率」与「全局值条数」。
2. **判据**：某列的**全局值条数**两侧差 > **20%** → **显式标红**，即使逐文件填充率都接近 0。
3. 当某列两侧的逐文件填充率**都 < 1%** → 在该列注明「**本列逐文件差异不具分辨力，请以全局值条数为准**」。

**理由**：

**段一（台账口径）**——
- **业务口径**：CMDB 要的是那 19 列，不是中间 JSON 的键名。中间 JSON 比的是"字段命名风格"，噪声远大于信息。
- **口径一致性是 diff 可信的前提**：两侧用**同一个** `build_rows`，才能保证"列 8 端口IPv4 的差异"是解析能力的差异，而不是两边格式化规则的差异。
- **⭐ 限速列逼出这个口径（决定性理由）**：实测限速是**三层 join**，缺一不可——
  - **命名空间级定义**（顶层 list，40/40 抽样文件都有）：`"qos-profile": [{"name":"TV+_6M","in":{"cir":"6144"},"out":{"cir":"6144"}}]`
  - **接口级引用**（dict，值是**名字**）：`"qos-profile": {"in":"car5000m","out":"car5000m"}`（实测 1770 个接口）
  - **接口级直给**（dict，值是**数值**）：`"QOS": {"in":{"car":"10240"},"out":{"car":"10240"}}`（实测 2304 个接口）

  `build_excel.get_speed:205` 的规则是"**两者都存在取小**，/1024 转 M"。⇒ **第 6/7 列的值在中间 JSON 层根本不存在，只存在于 join 之后。** 换句话说：**如果 diff 比中间 JSON，第 6/7 列在 netparse 侧是"无定义"，报告里无法区分"netparse 不会算限速"和"netparse 没抓到 qos-profile"** —— 而这两件事的补法完全不同。**只有走 `build_rows` 才拿得到可比的答案。**
- **复用成本几乎为零**：`build_excel` 的模块级依赖只有 `argparse/gc/json/os/re/sys/time/openpyxl`，`openpyxl 3.1.5` 已装，`if __name__ == "__main__"` 在第 599 行 → **import 无副作用**。

**段二（能力口径）**——
- **netparse 在接口基础属性上其实更强**。按最新统一口径，两侧接口记录的**直接子键**为：incumbent 10 个（`name` `description` `IP` `multicast` `vpn` `VLAN` `bas` `traffic-policy` `QOS` `qos-profile`），netparse 15 个；共有 4 个（`name` `description` `IP` `vpn`），netparse 独有 11 个（`AdminStatus` `CarrierDownHoldTime` `Dcn` `Duplex` `EthTrunkMember` `Isis` `Mtu` `Speed` `Statistic` `Type` `UserVlan`），incumbent 独有 6 个。**此前“多 17 个”是把子集合字段也计入的错误口径，现统一改为“多 11 个”。**
- **字段口径必须显式写明**：`interface_field_coverage` 只统计接口记录的直接子键，**不含子集合及其内部字段**（`Statistic.*`、`Vrrp.*`、`IpAddress.*` 等不展开为独立接口级字段）；名称对应按业务语义判定，不能只按 PascalCase/snake_case 字面比较。**`VLAN ↔ UserVlan` 单列为「内容对应、结构不同」**：incumbent 的 `VLAN` 是含 `type/user-vlan/QinQ/dot1q` 的嵌套对象，netparse 的 `UserVlan` 是标量。集合计数仍保留 incumbent 独有 `VLAN` 与 netparse 独有 `UserVlan` 的原始键清单，但不得据此声称 netparse 完全没有用户 VLAN 内容；PVLAN/CVLAN/VLAN 封装类型等更细粒度的差异，必须按映射与 trace 证据分别归因，不能一律写成“模板未覆盖”。
- **⭐ netparse 覆盖的资产类别也多得多**：incumbent 的产物顶层键**恒为 5 个**（`device_name` / `device_ip` / `interfaces` / `route-static` / `qos-profile`，401 个产物抽样一致）⇒ 资产类别只有 **4 类**；netparse 有 **12 类**（`Device` `Interfaces` `IpAddress` `EthTrunkMember` `RouteStatics` `VpnInstances` `Bgp` `BgpPeers` `Acls` `AclRules` `Nqas` `NqaSessions`）。
- ⇒ **只报"缺什么"的报告会严重低估 netparse**。台账的 19 列只是 CMDB 当前要的那 19 个；**"多出来的字段"和"多出来的资产类别"恰恰是"netparse 值不值得继续投"的核心论据**（更丰富的 CI 模型：MTU/速率/双工/管理状态/聚合成员/ISIS 等）。
- ⇒ **段二必须双向**：既列 netparse 缺的，也列 netparse 多的。**只列缺口的报告，结论会系统性偏向"换回 incumbent"。**
- ⇒ **段二要比两件事**：① 同一类资产里**字段**谁多 ② **资产类别**谁多。**结论必须两面都报**：「台账列差 N」+「资产类别多 M 类、接口级字段多 K 个」。

**⚠️ 段一（台账口径）没有密码列**：incumbent 的 `huawei_vrp.fsm` **不抓 BGP peer** ⇒ 其产物里**既没有密码、也没有 peer 段**（实测：一个配置原文含 58 行 `password cipher` 的设备，产物里 `%^%#` / `password` / `peer` 命中均为 False）。
⇒ **映射表不得添加 peer / Password 相关行**（incumbent 侧无字段可映射，加了会写出**恒为空的映射** = 假缺口）。
⇒ 段二会把 `Bgp` / `BgpPeers` 列为 **netparse 独有的能力** —— 这是能力更强的证据，不是差异项。

**为什么不用另一种**：
- ❌ **只比中间 JSON 字段**：第 6/7 列在 netparse 侧无定义（见上）；且 PascalCase vs snake_case、list vs dict 的形状噪声淹没真差异。
- ❌ **只比 19 列**：会丢掉 netparse 的额外能力（段二），结论系统性偏负。
- ❌ **在 diff 工具里重写资产层规则**：两边规则可能不同步 → 结论不可信；而且等于把 21KB 已投产的业务规则重写一遍（`docs/FINAL-plan.md` 8.2 明确"资产层从零建"要砍掉）。
- ❌ **比 xlsx**：得跑 `build_excel` 的完整写出流程（write_only 模式、样式），慢且脆；JSON 层的行更干净。

**代价**：
- diff 工具依赖 `openpyxl`（虽然只为 import `build_excel`）→ 需在 `requirements` 里声明。
- **两段口径 ⇒ 两套比对逻辑**（台账按列、能力按字段），工作量比单口径多约 0.5 人日（已含在 2.4 的估算里）。
- `build_rows(json_path)` 只接受**文件路径**，所以 netparse 的输出必须先落临时文件（可接受，8440 个临时文件用完即删）。
- `build_rows` 的 `collect_time=None` 时从 JSON 文件名提取时间（`extract_time:296`）→ **netparse 侧的临时 JSON 文件名必须保留原时间戳**，否则"采集时间"列会不同。**这是一个容易翻车的点，见 tasks.md 翻车点 2。**

---

### D3 · secondary 地址（`type=sub`）= **模板加字段捕获**

**决策**：在 `IpAddress` 集合加字段 `AddressType type(str) reg(sub)`，规则改为
`^\s*ip address\s+${Address}\s+${Mask}(?:\s+(?P<AddressType>sub))?\s*$`。
主地址无该键（值缺失），副地址该键为 `"sub"` —— **与 incumbent 的键存在性语义完全一致**（incumbent 实测：主地址 `{"ip":...,"mask":...}` 无 `type` 键，副地址 `{"ip":...,"mask":...,"type":"sub"}`）。

**理由**：
- **信息在源头丢掉，适配器补不回来**。模板正则里的 `(?:\s+sub)?` 已经把 `sub` 消费掉了，引擎拿到的捕获组里没有它。
- 副地址占比 **2.40%**（120 文件抽样：主 25696 / sub 631）—— 不算大，但**一旦漏掉，第 8 列"端口IPv4"的语义就与 incumbent 不一致**，diff 会报出大量假差异，淹没真差异。
- 键存在性语义天然对齐：incumbent 主地址**没有** `type` 键，netparse 主地址**没有** `AddressType` 键 → 适配器直接映射，零转换。

**为什么不用另一种**：
- ❌ **适配器合成**：适配器拿不到 `sub` 信息（源头已丢），合成 = **编造数据**，违反 D9，且会让 diff 结论失真。
- ❌ **先不覆盖**：那第 8 列的 diff 就是"假红"，浪费 diff 实验的唯一目的。
- ❌ **用 `type(bool)` 布尔字段**：incumbent 的语义是三态（无键 / `"sub"`），布尔会把"无键"和"False"混起来，且 `convert()` 的 bool 分支（`probe_stack.py:335-343`）对未知值原样返回，行为不干净。**用 `type(str)` + 值为 `"sub"` 最贴近 incumbent**。

**代价**：模板改了 → 回归基线变（`sample-vrp.expected.json` 的 `IpAddress` 元素多一个键）。**注意**：`sample-vrp.txt` 里是否含 `sub` 地址需要确认；若不含，基线不变（见 tasks.md 2.2.2）。

**⚠️ 与 D2 的一致性声明**：D3 同样遵循"**不要在引擎里造 join**"的原则 —— `sub` 是**配置里原样出现的关键字**（不是任何派生/关联结果），所以在源头捕获它属于命令层；而"把路由按出接口归集到接口行"是资产层已有的 join，引擎不背这个责任。**判据：值是配置里直接写出来的 → 命令层抓；值需要跨集合关联/推导 → 资产层算。**

**⚠️ 关联风险**：`(?P<AddressType>sub)` 是**裸命名组**。`probe_stack.py:634` 对裸命名组不做路径还原 —— 但 `AddressType` 是顶层字段，路径还原不适用，无问题。**不过 lint 的"命名组名必须在字段表里"这条（上一轮 item 4）要求 `AddressType` 先声明再用**，顺序要对。

---

### D4 · 脱敏机制 = **模板显式 `type(secret)`（L1）+ 引擎密文形态兜底（L2）**，**不做关键字猜测**

**决策**：三层机制，实际实现 L1 + L2：

| 层 | 机制 | 定位 |
|---|---|---|
| **L1** | 字段声明 `type(secret)` → 引擎在写入时遮蔽 | **精确保证**。模板作者标注了就绝不泄露 |
| **L2** | 引擎内置**密文形态正则**（默认识别华为 `%^%#...%^%#` 包裹 + 高熵长 token），命中即遮蔽 | **兜底**。防模板漏标 |
| **L3** | 规则级强制遮蔽（`-> Secret`） | **本批不做** |

**理由**：
- **L1 是唯一能"保证"的机制**，因为 netparse 输出里出现的密码 **100% 来自模板显式捕获的字段** —— 模板没抓的命令根本不进 JSON。所以"模板标注齐全"⇒"零泄露"，这是个可审计的完备性论证。
- **L2 必须有**，因为实测模板**已经漏标**了（`Password type(str)`），而且模板作者未来还会漏。
- **不做关键字猜测的理由（关键）**：实测语料里含 `password`/`cipher` 字样的行形形色色 ——

  | 命令形态 | 出现次数（200 文件抽样） | 是密码吗 |
  |---|---|---|
  | `default-password-template iptv` / `ottx` | 9666 / 9651 | ❌ **模板名，不是密码** |
  | `ssh server cipher aes256_gcm aes128_gcm ...` | 200 | ❌ **加密套件列表** |
  | `ssh user X authentication-type password` | 198 | ❌ 无密码值 |
  | `user-password password-force-change disable` | 175 | ❌ |
  | `peer X password cipher %^%#...` | 162+91 | ✅ |
  | `radius-server shared-key-cipher %^%#...` | 828 | ✅ **但不含 "password" 字样** |
  | `isis authentication-mode md5 cipher %^%#...` | 372 | ✅ 不含 "password" |
  | `snmp-agent community read cipher %^%#...` | 389+308+195+84+77 | ✅ 不含 "password" |
  | `default-password cipher %^%#...` | 184 | ✅ |
  | `md5-password cipher <ip> %^%#...` | 64 | ✅ |
  | `access-type layer2-leased-line ... password cipher %^%#...` | 449 | ✅ |

  ⇒ **关键字匹配会同时产生海量误报（`default-password-template` 19317 次！）和漏报（`shared-key-cipher`/`community read cipher`/`authentication-mode md5 cipher` 全不含 "password"）**。误报会让脱敏规则不可用，漏报会让 L2 兜底失效。

- **L2 用"形态"而不是"关键字"**，正好绕开这个坑：形态识别看的是**值本身**（是否 `%^%#...%^%#` 包裹 / 是否高熵长串），与命令文本无关。实测华为密文**全部**是 `%^%#...%^%#` 形态 → L2 在本语料上召回率 100%。
- **L3 不做**：规则级强制遮蔽是第三套机制，收益（覆盖 L1+L2 之外的场景）在本批语料上为零，复杂度却高。

**为什么不用另一种**：
- ❌ **纯 L1（只靠模板标注）**：模板漏标就漏。实测今天就漏了。而且"模板作者记得标"不可审计、不可自动验证。
- ❌ **纯 L2（只靠形态）**：形态规则会随厂商变化，且对"短密码"（如 `password abc123`）失效。L1 是通用的、跨厂商的。
- ❌ **L1 + 关键字猜测（L2 换成关键字）**：见上表，误报 19317 次量级、漏报多个真实形态。
- ❌ **在资产层/输出层脱敏**：中间 JSON 已落盘明文，来不及。**这已被写进 `docs/FINAL-plan.md` 6.3 安全基线。**

**代价**：L2 的密文形态正则需要维护；新增厂商可能要补形态规则。**缓解**：L2 规则内置在引擎里、可通过 CLI 关闭（`--no-mask-heuristic`）、并在报告里统计"L2 命中了多少个 L1 未标的字段"（这个数字就是"模板标注完整度"的度量）。

---

### D5 · 掩码表示 = **固定掩码串**，且「未配置」与「已遮蔽」必须可区分

**决策**：
- 被遮蔽的值 → 固定掩码串，默认 `"******"`（6 个 `*`），可通过 CLI `--mask-value` 自定义。
- **未捕获到值的字段 → 键不存在**（与 incumbent 的键存在性语义一致）。
- **不保留长度、不保留前缀/后缀**（长度与片段本身都是信息）。
- `type(secret)` 的字段类型恒为 `str`（遮蔽串是字符串；若声明成 `int` 会在 `convert()` 里转换失败并回退原值 → 破坏遮蔽）。

**理由**：
- **"配了密码" vs "没配密码" 是有业务价值的信息**（CMDB 里能回答"这台设备哪些 peer 配了密码"）。如果两者都变成空串，这个信息就丢了。
- **固定串而非长度提示**：长度也是信息（泄露密码长度）。而且固定串让下游可以用"值 == 掩码串"做判定。
- **键存在性而非空串**：与 incumbent 语义一致（incumbent 主地址没有 `type` 键，sub 有），也避免"空串"和"缺键"在下游混淆。

**为什么不用另一种**：
- ❌ **空串 `""`**：丢掉了"是否配置"的信息，且与 incumbent 的"键缺失"语义不匹配（会造成 diff 假差异）。
- ❌ **保留首尾字符**（`%^%#...abcd`）：泄露信息，且密文是整体可用的，保留片段意义不大。
- ❌ **保留长度**（`****** (32)`）：长度也是信息；且会让下游的正则判定变复杂。
- ❌ **整条记录/字段删除**：删掉会让"这台设备配了密码"这件事彻底消失；且删除会让 JSON 结构不稳定。

**代价**：下游若要判断"是不是被遮蔽了"，得比较值等于掩码串 → 需要在文档里写清掩码串可配置。**缓解**：CLI 开关 + 报告里写明本次使用的掩码串。

---

### D6 · 脱敏时机 = **`_apply` 捕获到值的那一刻**，不是输出时、也不是"落值前"

**决策**：遮蔽发生在 **`_apply()` 捕获到值的那一刻**（捕获组取出后立刻遮蔽）：
```
捕获组 gd → 逐个 mask_value() → masked（全是掩码）
                        ↓
        writes / last 缓存 / obj（members 分支）/ out（assigns 分支）
                        ↓
                  apply_writes → convert → set_path
```
**⇒ 引擎里的全部写入路径都只见过掩码**：`rec` / `children` / `last` 缓存 / 异常堆栈的 repr，一处明文都没有。

**★ 实现期修订（2026-11-19，能力 A 交付后复核）**：本条原表述是「在 `apply_writes()` 落值之前」。实现时发现**两条分支会绕过 `apply_writes` 直接读原始捕获组 `gd`**：
- `-> 集合名{...}` 的 **members 分支**（构造 `obj` 时读 `gd.get(grp)`）
- `-> 路径.字段` 的 **assigns 分支**（构造 `out` 时读 `gd.get(grp)`）

⇒ 若只在 `apply_writes` 遮蔽，这两条路径的**中间值仍是明文**（最终落盘虽会被遮蔽，但中间态违反"进入记录前就没明文"）。
⇒ **正确做法：把遮蔽点上移到 `_apply` 的捕获处** —— 实测这样 `writes` / `last` / `obj` / `out` 全部天然是掩码，**不需要每个写入路径各自记得遮蔽**。**实现采用的就是这个（`_apply` 677-695 行）。**

**★ D6 的「所有写入路径」清单（共 4 处，实施与复核都按这个清单逐点查）**：

| # | 路径 | 要求 |
|---|---|---|
| 1 | **`_apply` 捕获处**（`gd` → `masked`） | **主遮蔽点**；从这里流出的 `writes`/`last`/`obj`/`out` 全部已是掩码 |
| 2 | `apply_writes` 的**两处**分支（list 分支 / 普通分支） | 兜底再遮一次（幂等 —— `mask_value` 对已等于掩码串的值原样返回且不重复计数） |
| 3 | **`apply_defaults`**（`default(...)` 填充） | **⚠️ 规格原版漏列，实现期由开发者自己发现并补上** —— 默认值也可能是凭据 |
| 4 | `convert()` 对 secret 字段短路 | 保证掩码串不被 int/float 转换搞坏（转换失败会**回退原值**） |

**理由**：
- 引擎里**有第二条值通道**：`last[(coll.name, path)]`，供 `inherit=` 使用。如果只在输出层遮蔽，`last` 里仍是明文，`inherit` 把它复制到别的字段时会绕过遮蔽。
- **中间态泄露**：异常堆栈、`--json` 之前的任何调试打印、内存快照，都可能带明文。
- **⭐「每个写入路径各自记得遮蔽」是易漏的约定** —— 实测就漏了 `apply_defaults` 一处（规格原版也没列它）。**上移到捕获处 = 结构性解决，不依赖约定。**
- 输出层遮蔽还会让"值是否被遮蔽"依赖调用方 → 多个出口（`--json` / 未来 CLI / Python API）容易漏。

**为什么不用另一种**：
- ❌ **输出层遮蔽**（`main()` 里遍历结果替换）：`last` 缓存漏网；多出口易漏；且 `inherit=` 复制出来的是明文，绕过遮蔽。
- ❌ **只在 `apply_writes` 遮蔽**（原表述）：members / assigns 两条分支的中间值仍是明文（见上面的实现期修订）。
- ❌ **在 `convert()` 里做**：语义不清（`convert` 管类型，不管安全），且 `convert` 对未知类型直接返回原值，容易漏。
- ❌ **在 `compile_rule` 阶段做**：那是编译期，值还不存在。

**代价**：`mask_value` 在热路径上（每条规则的每个捕获组都过）→ L2 的形态正则必须**带缓存**（`lru_cache`，实现已做）；判断本身是字符串比较 + 一次缓存查表，可忽略。

---

### D7 · `type(secret)` 字段 **禁止** 与 `option(Key)` 同时出现，且 **禁止** 被 `inherit=` 引用

**决策**：lint 报**错误**：
- `option(Key)` + `type(secret)` 同字段 → 错误。理由：遮蔽后所有记录该字段的值都是同一个掩码串 → 聚合键全部相同 → `merge_by_key`（`probe_stack.py:447-467`）会把本该是不同记录的项**合并成一条**，造成静默数据丢失。
- `inherit=<集合>.<字段>` 指向 secret 字段 → 错误。理由：`inherit` 会把 secret 值复制到**非 secret 字段**，绕过遮蔽。

**为什么不用另一种**：
- ❌ **只警告**：合并丢数据是静默的、不可逆的，必须是错误级别。
- ❌ **允许但自动把 secret 字段从 Key 里剔除**：那样 Key 就变了（少一个分量）→ 聚合语义被静默改变，更难发现。**要求作者显式选择**才对。

**代价**：模板作者要意识到 secret 与 Key 不兼容。**缓解**：错误信息直接给出修法（"把该字段的 Key 标记去掉，或改用非 secret 字段做键"）。

---

### D8 · diff 报告必须带**有证据的根因分类**、**全局值条数**与**工作量结论**，不只给数字

**决策**：报告对**每一个未填满的列**给出根因分类 ∈：

| 根因 | 自动判定方式与必备证据 | 例子 |
|---|---|---|
| `模板未覆盖该命令` | 真实输入行的命令族在引擎 trace 中 0 命中；附命令族、原始行号/脱敏样本、`total_lines`、`hit_lines`、trace 来源 | 第 13 列 域名名称 ← `bas` 块 514 行 0% 覆盖 |
| `字段丢失` | 同一真实行有规则命中且产生记录，但目标字段未声明/未捕获；附模板集合与字段声明、命中规则、记录键及 trace | 第 10 列 ← 静态路由**出接口**未捕获 |
| **`整条记录丢失`** | 从真实配置可定位 incumbent 有记录，而 netparse 对应记录缺失；附原始行号、候选规则匹配结果与记录计数，不能把“没有 trace 命中”误写成“命令已命中” | 第 10 列 ← 修前模板丢失显式出接口路由 |
| `join缺失` | **单独成类**：原始输入字段已被捕获且两侧原始值可对应，仅因资产层缺少跨集合关联而无法成列；附源集合、目标集合、关联键、关联前后条数。缺原始捕获字段时不得归为 join 缺失 | 第 6/7 列 ← 仅在原始 qos 字段齐全时才适用 |
| `语义不同` | 两侧都有值且原始字段可对应但值不同；附两侧值及对应原始行 | 待实测 |

**根因自动分类的证据门禁**：不得仅凭映射条目 `source=None`、`root_cause_if_empty` 等人工常量自动断言根因。映射常量只能作为**待验证假设**。报告每个自动根因 MUST 附可复核的 `evidence`：至少包含真实文件/行号或脱敏命令样本、模板声明（集合/字段/规则标识）及真实 `trace` 摘要；无法满足门禁时输出 `root_cause: "待人工判定"` 与 `evidence_status: "INSUFFICIENT"`，不得猜分类。`join缺失` 必须单列，不得与“模板未覆盖”合并成无法行动的多值标签；同一列可因不同样本存在多个根因，但每个根因都要有自己的证据集合。

**并且报告必须有一节 `workload_estimate`**：逐列列出「缺口 → 需要改哪个模板的哪条规则 → 预估人日」，最后给出**总人日区间**，与 `docs/FINAL-plan.md` 8.4 的 68-104 人日区间对照。只有证据充分的根因才进入自动工作量映射；证据不足的列单列为待诊断工作量。

**并且每列必须同时给「逐文件填充率」与「全局值条数」**（见 D2 的粒度拆分）：全局值条数差 > 20% → **显式标红**；逐文件填充率两侧都 < 1% → 注明「本列逐文件差异不具分辨力，请以全局值条数为准」。

**理由**：
- **只给数字的报告不可行动**。"第 13 列填充率 0%" 没人知道该干什么；有真实 trace 证明 `bas` 块 514 行完全未覆盖，才能提出可复核的模板补齐工作量。
- **`source=None` 只表示映射表没写来源，不证明模板没覆盖**：可能是漏写映射、结构对应或资产层 join 问题。把人工常量直接当根因，会制造确定但错误的结论。
- **join 与捕获是两笔不同的账**：缺原始字段要改模板，原始字段齐全但关联缺失才要改资产层；混在一起会重复或错配工作量。
- **工作量结论是这个 diff 存在的唯一目的**（`docs/FINAL-plan.md` 8.3 实验 1 的判据是"差距大 → 重新评估路线 2"）。没有工作量估算，报告回答不了那个问题。
- **⭐ 只有逐文件口径会掩盖最严重的差距**：第 10 列在单文件粒度上两侧都是 0（incumbent 自己也 0%，因为那些文件没有带出接口的路由），**逐文件口径显示"无差距"**，而全局口径显示 **96.6%** 的差距。**两种粒度必须并列，单给一种都是失真的。**

**为什么不用另一种**：
- ❌ **只报填充率**：不可行动。
- ❌ **凭 `source=None` 或人工根因常量自动出结论**：没有模板声明和真实 trace 佐证，会把映射遗漏误判为能力缺口。
- ❌ **把 join 缺失并入模板未覆盖**：无法区分“抓字段”和“做关联”的工作量。
- ❌ **只报差异样本**：样本是诊断材料，不是结论。
- ❌ **只报逐文件填充率**：会掩盖第 10 列这类"分散在大量文件里"的差距（实测差 96.6% 却显示 0%）。

**代价**：映射表需保留来源与待验证假设，报告还需汇总模板声明和 trace 证据；映射表本身仍要人工审。这是保证结论可信的必要成本，见 Open Questions Q3。

---

### D9 · 适配器是**纯结构映射**，**禁止合成数据**

**决策**：适配器只做「键改名 + 结构重排 + 值搬运」。**禁止**：
- 推导、计算、默认值填充；
- 把一个字段拆成多个（除 D3 已明确的 `AddressType` 一对一）；
- 把多个字段合并（除 incumbent 侧确实是复合结构的地方，如 `VLAN` 的三层嵌套 —— 但那依赖 netparse **先有**对应字段，无则留空）；
- **任何"netparse 没抓到 → 适配器补一个合理默认值"的行为**。

**理由**：
- **适配器补出来的值会直接变成 diff 的"填充率"**，掩盖真实的能力差距。**diff 的价值全在于诚实。**
- 合成 = 编造。CMDB 入库后无法区分"配置里真有"和"适配器猜的"。

**为什么不用另一种**：
- ❌ **允许适配器做简单推导**（如从接口名推断接口类型）：那是**资产层**的职责（`docs/FINAL-plan.md` 4.4），不在 diff 工具里做。放进来会让 diff 测的是"适配器有多聪明"，不是"netparse 模板有多全"。
- ❌ **允许默认值**：同上，且更隐蔽。

**代价**：netparse 侧缺的列，diff 里就是 0%。**这正是我们要的**。

---

### D10 · 引擎代码位置：~~本批不抽包~~ → **已抽包为 `src/netparse/`（后续变更反转）**

> **状态：已被反转。** 本决策在能力 B 收尾后被独立变更「代码规整为 Python 库」推翻，
> 引擎现位于 `src/netparse/engine.py`，`tools/probe_stack.py` 降级为兼容 shim。
> 反转理由与执行结果见本节末尾「反转记录」。下面是**当时的原始决策**，保留作归因依据。

**原始决策**：脱敏改动**直接改** `tools/probe_stack.py`；新代码（适配器、diff 工具）放在 `tools/` 或新建 `src/netparse/`，但**通过 `sys.path` import 现有的 `tools/probe_stack.py`**（与 `tests/run_regression.py:17` 现有做法一致：`sys.path.insert(0, os.path.join(ROOT, "tools"))`）。

**理由**：
- **13 个探针脚本全部 `import probe_stack`**（`probe_stack.py:761` 被 `probe_coverage2/3.py`、`probe_acl_rules.py`、`probe_flat2.py`、`probe_blind.py` 等引用）。抽包 = 改 import 路径 = **打断全部探针** + 打断回归脚本。
- 引擎正式化（抽 `src/netparse/` 包 + CLI + 打包）是 `docs/FINAL-plan.md` 8.4 里 8-12 人日的独立工作项，**不该混进一个 2-3 人日的决策实验里**。
- 本变更的价值是**产出结论**，不是产出架构。架构改造会污染"改了什么"的归因。

**为什么不用另一种**：
- ❌ **抽 `src/netparse/` 包**：破坏 13 个探针 + 回归脚本的 import；且把一个决策实验变成一个架构改造，验收变模糊。
- ❌ **把脱敏做成引擎外的包装层**：违背 D6（写入时遮蔽）。
- ❌ **复制一份引擎到 `src/`**：两份实现必然漂移，是最坏选项。

**代价**：引擎继续待在 `tools/`（观感不佳），且 `sys.path` hack 继续存在。**缓解**：在 `design.md` 与 `tasks.md` 里显式记录这条技术债，并在 `docs/FINAL-plan.md` 8.4 的"引擎正式化"项里注明"需处理 13 个探针的 import 迁移"。

**反转记录（代码规整为 Python 库）**

原决策的核心顾虑是「抽包 = 打断 13 个探针 + 回归脚本」。该顾虑**成立但可解**，做法是
**shim + 一次性迁移**而不是「二选一」：

| 顾虑 | 实际解法 | 结果 |
|---|---|---|
| 13 个探针 `import probe_stack` 会断 | 探针迁入 `tools/legacy/`，import 改成 `from netparse import engine as P` | 14 个探针全部可跑（`test_cli.py` 有断言） |
| 文档 51+ 处 `python tools/probe_stack.py` 会失效 | `tools/*.py` 保留薄 shim 转发到 `netparse` 子命令 | 旧命令逐字可用（`--lint` 参数也做了翻译） |
| 回归脚本靠 `sys.path` | `tests/*` 改 `sys.path.insert(0, ROOT/"src")` + `from netparse import ...` | 5 个测试全绿 |

**新增的实质收益**（原决策低估的部分）：
- `pyproject.toml` + `[project.scripts]` ⇒ `pip install -e .` 后 `netparse` 命令在**任意目录**可用；
- `paths.py` 用「向上找 `pyproject.toml`」定位仓库根，替代 `dirname(__file__)` 的脆弱算法；
- `MaskingConfig(CONFIG)` 单例替代两个模块级全局，堵住「`from ... import MASK_HEURISTIC` 拿到副本、赋值静默失效」这一类 bug；
- `diff` 对生产 `build_excel` 的依赖改为**惰性**，`netparse parse` 在没有生产盘的机器上不再被连带拖死；19 列口径冻结 + 运行期漂移校验。

**行为等价性验证**（抽包不能改变解析结果）：
- HEAD 原引擎 vs 新包引擎，60 台真实华为配置 + 4 个 fixture + 2 套模板，**逐字节一致**；
- `netparse diff --sample 20 --jobs 4`：接口 join `18114/0/0`、文件奇偶校验 `20/20`、
  路由修前丢 `39/39（100%）` 修后丢 `0`、L1=164 / L2=2 —— 与库化前同一批文件的硬指标一致。

---

### D11 · diff 的行对齐：**按接口名 join**，不按索引

**决策**：netparse 的接口行与 incumbent 的接口行**按接口名（大小写不敏感）匹配**，不按出现顺序匹配。报告分别统计「两侧都有」「仅 incumbent 有」「仅 netparse 有」三类。

**理由**：
- 顺序对齐是脆的：netparse 与 incumbent 对同一份配置的解析路径不同（netparse 用状态栈，incumbent 用"顶格切块"），**只要有一条规则的顺序不同，接口顺序就会错位** → 按索引比对会产生 100% 的假差异。
- 接口名是 CMDB 的主键（第 4 列"接口"），按主键 join 是业务口径。
- **实测依据**：同一个样本文件，netparse 与 incumbent 都给出 **1044** 个接口 —— 数量一致，但仍必须按名 join（顺序未必一致）。

**为什么不用另一种**：
- ❌ **按索引**：脆，且无法解释"哪些接口多/少了"。
- ❌ **按集合全等比较**：会丢掉"接口名相同但字段不同"的差异。

**代价**：需要处理重名接口（同一文件内两个同名接口）。**实测"接口名重复 0 个"**，但仍要在报告里检测到并报错，而不是静默覆盖。

---

### D12 · diff 的输入范围：**8440 个 incumbent JSON 对应的原始配置**，不重跑 incumbent 解析

**决策**：diff 的左侧 = 已落盘的 `解析后/<月>/华为/*.json`（**直接读**）；右侧 = 用 netparse 重新解析**同一份原始配置**得到的结果。**不重跑 incumbent 的 `parse_huawei_vrp.py`**。

**理由**：
- 硬约束 1 明确"不重跑 incumbent 解析"；而且重跑既慢又可能产生与已落盘产物不一致的结果（版本差异），**破坏 diff 的可比性**。
- 映射已验证 **8440/8440 命中**（本轮实测），路径反推规则确定。
- 已落盘产物是"incumbent 当时的输出"，正是我们要比的基线。

**为什么不用另一种**：
- ❌ **重跑 incumbent**：违反只读约束；且 `textfsm` 未安装（实测 `import textfsm` 失败）。
- ❌ **只抽样一部分**：8440 个文件全量跑的成本可接受（incumbent 全量解析 490 秒；netparse 单文件约 0.1-1 秒，8440 个文件估计 15-40 分钟，**可接受**）。抽样会丢失"某些设备格式不同"的信号。

**代价**：diff 需要能定位到原始配置路径 → 依赖"月目录名 + 文件名"的对应关系（已验证）。**若将来语料目录结构变了，diff 工具会失配** → 工具必须**显式报错**而不是静默跳过（见 spec R-B3）。

---

### D13 · 静态路由模板改动（2.2b）**纳入本变更**，19 列全部给「incumbent / 修前 / 修后」三栏

**决策**：2.2b（静态路由多形态，4 条规则 + `NextHopIface`）**在本变更内做**（≈0.5 人日）。同时：
1. **先存一份修前模板快照**：`.devforge/spec/changes/netparse-v3-diff-and-mask/baseline-template.cmdb`（= 改动前的 `templates/huawei-vrp.cmdb` 原样）
2. **diff 工具支持 `--template <path>`**，**跑两遍**：修前模板 + 修后模板
3. 报告的 **19 列全部列**、逐文件与全局汇总两种粒度，均给三栏：`incumbent` / `netparse_before_fix` / `netparse_after_fix`。**不得只对预计受影响的第 10 列给三栏**；未受影响的列也输出三栏，以实际数值证明无回归或暴露非预期变化。

**理由**：
- **保住"测量"的纯粹性**：报告同时包含"修前基线"与"修后结果"，两者分栏、明确标注；且给出「**补齐这 0.5 人日能买回多少**」的直接数字。
- **全列三栏是回归证据**：模板改动可能产生非预期影响，只在受影响列做对照会漏掉其他列的回归；全列输出也避免报告 schema 因列而异。
- **修前/修后对照本身就是最强的结论形式**：它把"能力差距"与"修复成本"同时量化。

**为什么不用另一种**：
- ❌ **只给受影响列三栏**：其他列无法证明修前/修后没有回归，且读者要处理两种列结构。
- ❌ **在 diff 工具里加"模拟补齐"分支**（不真改模板）：没有版本锚点，且模拟逻辑与真实模板可能漂移。
- ❌ **把 2.2b 推到后续批次**：本变更就失去"量化修复成本"的能力。

**代价**：19 列逐文件与全局报告都要保存三栏（实现上：跑两遍 + 三栏并列）；需要维护一份模板快照，并校验三栏键集合均为 `COLUMNS` 的完整 19 列。

### D14 · `--json` 时 lint 报告走 **stderr**（stdout 保持纯 JSON）

**决策**：命令行 `--json` 模式下，lint 报告写入 **stderr**；**非 `--json` 模式仍打 stdout**（原有用法不变）。

**理由**：
- **这不是行为破坏，是修一个本来就坏的地方**：改之前 `probe_stack.py <模板> <配置> --json > out.json` 产出的文件**根本无法 `json.load`** —— lint 文本（`=== lint ===` 开头）混在 stdout 的 JSON 前面。**能力 B 的 diff 工具正是要 `json.load` 它的输出**（能力 A 的验收命令 tasks 1.7.1 也是这么写的）⇒ 不修则能力 B 无法开工。
- **可观测性不能丢**：lint 报告是重要的诊断信息，走 stderr 只是换流，**内容一条不少**；`2>&1` 仍可合并，`2>/dev/null` 可静默。
- **对既有使用无影响**：非 `--json` 模式的输出与之前完全一致；`--lint` 模式不变。

**为什么不用另一种**：
- ❌ **`--json` 时干脆不输出 lint**：诊断信息丢失，解析失败时用户看不到原因 —— 而"为什么这条规则没命中"正是这套工具的价值所在。
- ❌ **给 stdout 加一个 JSON 包装层**（`{"result":..., "lint":...}`）：**破坏所有既有 `--json` 消费方的 schema**，且与能力 B 需要的"纯 JSON 结果"不符。
- ❌ **新增 `--json-strict` 之类的开关**：多一个模式就多一份要维护的语义；而 `--json` 的语义本来就应该是"stdout 是可解析的 JSON"。

**代价**：习惯 `> out.json` 且想同时看到 lint 的用户要改成 `> out.json 2> lint.txt`（或 `2>&1 | tee`）。**已在 tasks 1.7.1 的验收命令与 TEMPLATE-GUIDE 的命令表里同步说明。**

---

### D15 · `--sample N` = **按月轮转分层抽样**，月份覆盖期望与 N 挂钩

**决策**：先确定可选月份集合 `M`（存在至少 1 个可处理 incumbent/原始配置对的月目录，当前最多 26 个），按固定 `sample_seed` 打乱月份顺序；每轮从每个尚未耗尽文件的月份各随机抽 1 个文件（月内不放回），循环至抽满 N 个或候选耗尽。N 不超过候选文件总数时，期望不同月份数为 **`expected_distinct_months = min(N, len(M))`**；报告同时记录 `available_months`、`expected_distinct_months` 与实际 `months_covered`，并断言实际集合大小等于期望值。

**理由**：原要求“`--sample 20` 时 `months_covered >= 20`”与“覆盖全部 26 个月”互相矛盾：20 个文件不可能覆盖 26 个月。按月轮转保证小样本尽可能跨月，大样本再逐月加深；`min(N, 可选月数)` 在 20、26、30 等样本量下都可满足。

**为什么不用另一种**：
- ❌ **纯随机抽样后要求覆盖全部 26 月**：N<26 时逻辑不可能，N≥26 时纯随机也不保证每月至少 1 个。
- ❌ **只把验收改成 `>=20`**：仍未表达分层算法，样本可能集中在少数月份。

**代价**：抽样不是全语料上的简单均匀随机，而是月份分层后的样本；报告 MUST 标注算法与 `sample_seed`，不得把抽样填充率直接宣称为全量无偏估计。

---

## Interfaces

### 能力 A · 脱敏

**模板语法（新增）**

```text
<字段名> type(secret) reg(<正则>) desc(<说明>)
```
- `secret` 是新的字段类型，语义 = "该字段的值是凭据，落盘前必须遮蔽"
- 与其它类型互斥（一个字段只能有一个 type）
- 禁止与 `option(Key)` 同用（D7）
- 禁止被 `inherit=` 引用（D7）

**引擎 API（不破坏现有签名）**

```python
# tools/probe_stack.py
MASK_DEFAULT = "******"

class Field:
    def is_secret(self) -> bool:
        """type == 'secret'"""

# 新增模块级
def mask_value(value, fld, mask=MASK_DEFAULT, heuristic=True) -> str | None:
    """判断并遮蔽单个值。返回遮蔽后的值；非 secret 返回原值。"""

def should_mask_by_shape(text: str) -> bool:
    """L2 兜底：密文形态识别（内置华为 %^%#...%^%# + 高熵长串）。"""
```

**CLI（新增开关）**

```
--mask-value <str>       自定义掩码串（默认 "******"）
--no-mask-heuristic      关闭 L2 兜底（只保留 L1 显式标注）
```

**lint 输出（新增两级）**

```
[错误] BgpPeers: 字段 Password 同时声明了 option(Key) 和 type(secret)，遮蔽后聚合键会全部相同
[警告] BgpPeers: 规则 '^\s*peer\s+${Peer}\s+password\s+cipher\s+${Password}' 含凭据关键字
       (password/cipher/secret/shared-key/md5-password)，但字段 Password 未声明 type(secret)
```

### 静态路由多形态：如何绕开「同规则内重复 `${}` → 回溯引用」

> 实施期最容易踩的引擎语义坑。结论已**实测验证**，且最终方案已与 incumbent **逐字段比对一致**。

#### 坑 1 · 同规则内重复 `${字段}` → 回溯引用

`compile_rule` 的 `seen` 集合（`probe_stack.py:281`）对**同一条规则内**重复出现的 `${字段}`，第二次会编译成**回溯引用** `(?P=字段)`（`probe_stack.py:293-294`）。实测：

```
规则： ^ip\s+${Prefix}\s+${Mask}\s+(?:${NextHop}|(?P<Iface>\S+)\s+${NextHop})\s*$
编译： ^ip\s+(?P<Prefix>\S+)\s+(?P<Mask>\S+)\s+(?:(?P<NextHop>\S+)|(?P<Iface>\S+)\s+(?P=NextHop))\s*$
                                    ↑ 第一次是捕获组        ↑ 第二次变成回溯引用
⇒ 语义变成「接口名必须等于下一跳」→ 永远不匹配
```

**✅ 核心结论（实测 T-2 验证）**：`seen` 是 `compile_rule` 的**函数内局部变量**，**每次调用都新建** ⇒ **回溯引用只在「同一条规则内」重复引用才触发**。
⇒ **规避方法：拆成多条规则，每条规则内 `${NextHop}` 只出现一次 —— 不需要换字段名。**

```
规则1: ^ip\s+(?P<Prefix>\S+)\s+(?P<Mask>\S+)\s+(?P<NextHop>\S+)\s*$
规则2: ^ip\s+(?P<Prefix>\S+)\s+(?P<Mask>\S+)\s+(?P<Iface>\S+)\s+(?P<NextHop>\S+)\s*$
       → 两条规则各自编译出独立的 (?P<NextHop>...)，互不冲突 ✓
```

#### 坑 2 · 形态分流不能靠 alternation，要靠 `reg()`

华为 `ip route-static` 的出接口位与下一跳位**形态不同**，而 `reg()` 只能表达**肯定性**字符类。
**incumbent 的判定规则（`parse_huawei_vrp.py:519-537`）是：「首 token 不是 IP、也不是 NULL0 → 视为出接口」**。
⇒ 要精确复刻它，需要**两把判别式**：
- `NextHop` 的 reg = `IP|NULL0`（黑洞下一跳 `NULL0` 也算下一跳，**不算出接口**）
- `NextHopIface` 的 reg = `(?!NULL0)[A-Za-z][\w./-]*`（**以字母开头且不是 NULL0** = 接口名）

#### 坑 3 · `vpn-instance` 变体必须在 plain 变体**之前**

若 plain 规则排在前面，`ip route-static vpn-instance X P M N` 会被 plain 规则**错位捕获**（`Prefix="vpn-instance"`、`Mask="X"`）—— 这是**静默错值**，比丢数据更糟。
⇒ **规则顺序：vpn-instance 变体（2 条）→ plain 变体（2 条）。**
⇒ 与此同时，plain 变体的"纯下一跳"规则**尾部要放宽**（`(?:\s+.*)?`），使「首 token 是 IP 而后面还有 token」的形态与 incumbent 一致（incumbent 会把多余 token 丢弃）。

#### ✅ 最终方案（4 条规则，已逐字段验证）

```text
[value]
Prefix       type(str) reg(\S+)                                    desc(前缀)
Mask         type(str) reg(\S+)                                    desc(掩码)
VpnInstance  type(str) reg(\S+)                                    desc(所属VPN实例)
NextHop      type(str) reg(\d{1,3}(?:\.\d{1,3}){3}|NULL0)          desc(下一跳)
NextHopIface type(str) reg((?!NULL0)[A-Za-z][\w./-]*)              desc(出接口)
Preference   type(int) reg(\d+)                                   desc(优先级)
Description  type(str) reg(.+)                                    desc(描述)
[rule]
# ① vpn-instance 变体 · 纯下一跳
^ip\s+route-static\s+vpn-instance\s+${VpnInstance}\s+${Prefix}\s+${Mask}\s+${NextHop}(?:\s+preference\s+(?P<Preference>\d+))?(?:\s+description\s+(?P<Description>.+))?\s*$   -> RouteStatics{VpnInstance,Prefix,Mask,NextHop,Preference,Description}
# ② vpn-instance 变体 · 出接口 + 下一跳
^ip\s+route-static\s+vpn-instance\s+${VpnInstance}\s+${Prefix}\s+${Mask}\s+(?P<NextHopIface>(?!NULL0)[A-Za-z][\w./-]*)\s+${NextHop}(?:\s+preference\s+(?P<Preference>\d+))?(?:\s+description\s+(?P<Description>.+))?\s*$   -> RouteStatics{VpnInstance,Prefix,Mask,NextHopIface,NextHop,Preference,Description}
# ③ plain 变体 · 纯下一跳（尾部放宽，吞掉多余 token）
^ip\s+route-static\s+${Prefix}\s+${Mask}\s+${NextHop}(?:\s+preference\s+(?P<Preference>\d+))?(?:\s+description\s+(?P<Description>.+))?(?:\s+.*)?\s*$   -> RouteStatics{Prefix,Mask,NextHop,Preference,Description}
# ④ plain 变体 · 出接口（下一跳可选）
^ip\s+route-static\s+${Prefix}\s+${Mask}\s+(?P<NextHopIface>(?!NULL0)[A-Za-z][\w./-]*)(?:\s+${NextHop})?(?:\s+preference\s+(?P<Preference>\d+))?(?:\s+description\s+(?P<Description>.+))?\s*$   -> RouteStatics{Prefix,Mask,NextHopIface,NextHop,Preference,Description}
```

**⭐ 验证结果（真实文件逐字段比对）**：
文件 `SNBJI-MC-CMNET-BRAS01-MYL-X16A_218.200.1.138.20250824061211`（含出接口路由）
→ **netparse 212 条 / incumbent 212 条，(dest, mask, interface, nexthop, vpn) 集合完全一致，差异 0**。

**⭐ 边界形态（与 incumbent 判据逐条对齐）**：

| 输入 | netparse 输出 | incumbent 判据 | |
|---|---|---|---|
| `P M 10.1.1.1 description pure` | `NextHop=10.1.1.1` | `(iface='', nexthop='10.1.1.1')` | ✓ |
| `P M NULL0` | `NextHop=NULL0` | `('', 'NULL0')` | ✓ |
| `P M Eth-Trunk188 192.168.0.70 description both` | `NextHopIface=Eth-Trunk188`, `NextHop=192.168.0.70` | `('Eth-Trunk188','192.168.0.70')` | ✓ |
| `P M Eth-Trunk99`（只有出接口） | `NextHopIface=Eth-Trunk99`（**无 NextHop 键**） | `('Eth-Trunk99','')` | ✓ |
| `P M 1.2.3.4 5.6.7.8`（首 token 是 IP） | `NextHop=1.2.3.4`（**5.6.7.8 被丢弃**） | `('', '1.2.3.4')` | ✓ |
| `vpn-instance V1 P M Eth-Trunk77 172.16.0.1` | `VpnInstance=V1`, `NextHopIface=Eth-Trunk77`, `NextHop=172.16.0.1` | `('Eth-Trunk77','172.16.0.1')` | ✓ |
| `vpn-instance V2 P M 10.7.7.7` | `VpnInstance=V2`, `NextHop=10.7.7.7` | `('', '10.7.7.7')` | ✓ |

**两条规则为什么不会互相抢**（机制）：
- **引擎是「第一条命中即生效」**（`probe_stack.py:625-627`）：plain 的"纯下一跳"规则成功时，后面的"出接口"规则根本不会执行。
- plain 的"纯下一跳"规则**匹配不上**出接口形态，因为 `${NextHop}` 的 reg 是 `IP|NULL0`，`"Eth-Trunk188"` 不匹配 ⇒ 必然落到"出接口"规则。
- 两条规则**各带 `->` 目标且显式列出字段**（含 `NextHopIface`）—— ⚠️ **`->` 目标里必须显式带上 `NextHopIface`，否则该捕获组不进记录**（目标只写花括号里列出的字段，`probe_stack.py:656-668`）。

**为什么这个写法比 alternation 稳**：
- **形态分流靠 `reg()` 而不是靠 alternation 的分支顺序** —— alternation 里"分支顺序 + 回溯引用"是两个坑叠在一起；`reg` 收窄后，形态 2/3 在规则 A 上**必然失配**（不依赖尾部锚定的巧合）。
- **顺带修掉一个静默错误**：收窄后 `NextHop` **永远不可能是接口名** —— 形态 3 的路由不再把接口名误填进 `NextHop`（当前模板 1 条实测存在此问题）。
- **代价**：下一跳为**主机名**（非 IP）的路由匹配不到。实测语料 **0 条**，但这是**已知边界**，须在模板注释里写明。

**⚠️ 实现注意**：
1. `NextHopIface` 是**裸命名组** → 引擎不还原点号路径（顶层字段，无影响）；但 **lint 的「命名组名必须在字段表」要求它先声明** ⇒ **先加字段声明，再改规则**。
2. `Preference` 的 `type(int)` 保持不变；`convert()` 会把 `"220"` 转成 `220`。
3. **`vpn-instance` 前缀变体**：现有模板已有独立的一条规则处理 `ip route-static vpn-instance ...` ⇒ 上面的 A/B/C **各需一份 vpn-instance 版本**（共 6 条规则）。**注意 vpn-instance 版本里 dest/mask 的位置整体右移两格。**
4. **适配器侧**：`incumbent` 的 `nexthop` 在"只有出接口"形态下是**空串 `""`**（`_split_iface_nexthop` 返回 `("", head)` 后 `nexthop` 取 `""`），而 netparse 侧是**键缺失** ⇒ 适配器应把"缺失"映射成 `""`，与 incumbent 对齐（**属 D9 允许的"值搬运"，但必须在映射表里写明**）。

### 能力 B · diff

**适配器（新增）**

```python
# src/netparse/adapters/incumbent.py
def to_incumbent_shape(np_result: dict) -> dict:
    """netparse 顶层结果 → incumbent 顶层 JSON 形状。

    纯结构映射：不合成、不推导、不填默认值（D9）。
    映射表见 MAPPING_SPEC，每个条目带 source 描述与 root_cause 分类（D8）。
    """
```

**映射表结构（规格化，便于报告直接消费）**

```python
MAPPING_SPEC = [
    {
        "path": "device_name",
        "source": "Device[0].SysName",
        "root_cause_if_empty": None,          # 有映射源；若实际为空仍须用真实证据诊断
    },
    {
        "path": "interfaces[].IP.IPV4[].type",
        "source": "Interfaces[].IpAddress[].AddressType",
        "root_cause_if_empty": "待人工判定",   # 假设：字段丢失；须由模板声明 + 真实 trace 证实
    },
    {
        "path": "interfaces[].VLAN.user-vlan",
        "source": "Interfaces[].UserVlan",   # 内容对应、结构不同：标量 → 嵌套列表项
        "root_cause_if_empty": None,          # 不以 source=None 猜根因；空值按真实 trace 分类
    },
    {
        "path": "interfaces[].VLAN.type",
        "source": None,                        # 仅为待验证假设，不等于自动根因
        "root_cause_if_empty": "待人工判定",   # 须附模板声明 + 真实 trace 才能自动归类
    },
    ...
]
```

**diff 工具 CLI**

```
python tools/diff_incumbent.py --all
    [--sample N]              抽样 N 个文件（默认 200，用于快速冒烟）
    [--month YYYYMM]          只跑某个月目录
    [--out-dir PATH]          报告输出目录（默认 .devforge/spec/changes/netparse-v3-diff-and-mask/diff-report/）
    [--json PATH]             机器可读报告
    [--markdown PATH]         人可读报告
    [--dry-run]               只做映射检查，不跑 diff
```

**报告结构（JSON）**

```json
{
  "meta": {
    "generated_at": "...", "incumbent_index": "E:/配置文件/BRAS&SR/解析后/_index.json",
    "files_total": 8440, "files_processed": 8440, "files_failed": 0,
    "mask_value": "******",
    "l1_hits": 128, "l2_hits": 0,
    "_masking_note": "l2_hits = 模板标注完整度：L2（形态兜底）命中数远大于 L1 ⇒ 模板漏标 secret。实测 L2=0 ⇒ 当前模板对已抓到的凭据字段标注完整",
    "_masking_note2": "masking.STATS 是 per-run 的（run() 开头 reset）→ 工具必须自己累加每个文件跑完后的 as_dict()"
  },

  "part1_ledger": {
    "_desc": "段一 · 台账口径：三侧都过同一个 build_excel.build_rows → 19 列；每列、每种粒度均为三栏",
    "_columns": "<build_excel.COLUMNS 的完整 19 列；column_fill_rate 与 global_column_value_count 键集合必须与其完全一致>",
    "column_fill_rate": {
      "设备名称": {
        "incumbent": {"filled": 8440, "total": 8440, "rate": 1.0},
        "netparse_before_fix": {"filled": 8440, "total": 8440, "rate": 1.0},
        "netparse_after_fix": {"filled": 8440, "total": 8440, "rate": 1.0},
        "root_cause": null
      },
      "域名名称": {
        "incumbent": {"filled": 0, "total": 6278430, "rate": 0.0},
        "netparse_before_fix": {"filled": 0, "total": 6278430, "rate": 0.0},
        "netparse_after_fix": {"filled": 0, "total": 6278430, "rate": 0.0},
        "root_cause": "模板未覆盖该命令",
        "evidence_status": "VERIFIED",
        "evidence": {"command_family": "access-type layer2-subscriber",
                     "total_lines": 514, "hit_lines": 0,
                     "template_declaration": "<集合/字段/规则标识>",
                     "trace": "<真实文件/行号/脱敏样本/trace 摘要>"}
      }
    },
    "global_column_value_count": {
      "_desc": "全局汇总：不按文件切分；完整 19 列均有 incumbent / before / after 三栏值条数",
      "IPv4静态路由明细": {
        "incumbent": 18780,
        "netparse_before_fix": 3,
        "netparse_after_fix": 18780,
        "delta_pct_before": 100.0,
        "delta_pct_after": 0.0,
        "flag": "RED",
        "flag_reason": "修前全局值条数差 > 20%；修后单独计算",
        "per_file_resolvable": false,
        "note": "本列逐文件差异不具分辨力，请以全局值条数为准"
      }
    },
    "interface_alignment": {"both": 6278430, "only_incumbent": 0, "only_netparse": 0},
    "interface_count_parity": {"files_matched": 8440, "files_mismatched": 0,
                               "note": "接口识别层对齐；差距全在字段"},
    "diff_samples": { "域名名称": [{"device": "...", "interface": "...",
                                    "incumbent": "...", "netparse": null}] }
  },

  "part2_capability": {
    "_desc": "段二 · 能力口径：中间 JSON 的字段级双向覆盖",
    "interface_level_fields": {
      "_desc": "只统计接口记录的直接子键，不含子集合内部字段；按业务语义对应，不按大小写/命名风格机械比较",
      "matched":  ["name", "description", "IP", "vpn"],
      "only_incumbent": ["QOS", "VLAN", "bas", "multicast", "qos-profile", "traffic-policy"],
      "only_netparse": ["AdminStatus", "CarrierDownHoldTime", "Dcn", "Duplex",
                        "EthTrunkMember", "Isis", "Mtu", "Speed", "Statistic", "Type", "UserVlan"],
      "content_correspondence_diff_shape": [
        {"incumbent": "VLAN", "netparse": "UserVlan",
         "note": "内容对应、结构不同：incumbent 为 type/user-vlan/QinQ/dot1q 嵌套对象，netparse 为标量；更细的 PVLAN/CVLAN/封装类型差异分别按证据归因"}
      ],
      "coverage": {"incumbent": 10, "netparse": 15, "matched": 4,
                   "only_netparse": 11, "only_incumbent": 6}
    },
    "collection_level_fields": {
      "qos_profile_top": {"incumbent": "list[{name,in:{cir},out:{cir}}]", "netparse": null,
                          "note": "顶层命名空间级定义；netparse 模板无任何 qos-profile 规则"},
      "route_static": {"incumbent": "{IPV4:[{dest,mask,interface?,nexthop,vpn}], IPV6:[...]}",
                       "netparse": "list[{Prefix,Mask,NextHop,Preference,Description,VpnInstance}]",
                       "missing_in_netparse": ["interface(出接口)"]}
    },
    "asset_categories": {
      "_desc": "两侧各覆盖哪些资产类别 —— 比「同一类里谁字段多」更能说明能力差距。**必须三分类**（见 _taxonomy）",
      "_taxonomy": {
        "shared_same_shape": ["Device↔device", "Interfaces↔interfaces", "RouteStatics↔route-static"],
        "shared_diff_shape": ["IpAddress↔interfaces[].IP", "VpnInstances↔interfaces[].vpn"],
        "netparse_only": ["EthTrunkMember", "Bgp", "BgpPeers", "Acls", "AclRules", "Nqas", "NqaSessions"],
        "incumbent_only": ["qos-profile(顶层命名空间定义)"]
      },
      "incumbent": {"total_categories": 4, "shared_same_shape": 3, "shared_diff_shape": 0,
                    "only": 1, "list": ["device", "interfaces", "route-static", "qos-profile"]},
      "netparse": {"total_categories": 12, "shared_same_shape": 3, "shared_diff_shape": 2,
                   "only": 7,
                   "list": ["Device", "Interfaces", "IpAddress", "EthTrunkMember", "RouteStatics",
                            "VpnInstances", "Bgp", "BgpPeers", "Acls", "AclRules", "Nqas", "NqaSessions"]},
      "netparse_only_instances": [
        {"category": "EthTrunkMember", "instances": 86},
        {"category": "Bgp",            "instances": 1},
        {"category": "BgpPeers",       "instances": 6},
        {"category": "Acls",           "instances": 30},
        {"category": "AclRules",       "instances": 129},
        {"category": "Nqas",           "instances": 34},
        {"category": "NqaSessions",    "instances": 2}
      ],
      "_incumbent_note": "incumbent 产物顶层键恒为 5 个（401 个产物抽样）；其 interfaces[] 的子键实测只有 10 种：name/description/VLAN/bas/IP/vpn/multicast/traffic-policy/QOS/qos-profile ⇒ **无 ACL、无 NQA、无聚合成员、无 BGP**。**注意：IpAddress 与 VpnInstances 的业务内容 incumbent 是有的**（`interfaces[].IP` 120/120 个文件、`interfaces[].vpn` 117/120），**只是结构层级不同** ⇒ 不能算「netparse 独有」"
    },
    "extra_value_note": "netparse 多出的接口级字段不在 19 列台账内 → 它们是「更丰富的 CI 模型」的论据，报告须单列。**结论必须两面都报**：『台账列差 N』+『资产类别真正独有 7 类（另有 2 类是同一业务对象、不同结构层级）、接口级字段多 11 个』。VLAN/UserVlan 的内容对应关系另列，不把结构差异当成内容完全缺失。"
  },

  "repro": {
    "_desc": "复现锚点 —— 报告是唯一的决策依据，每列数字都要能回溯",
    "command": "python tools/diff_incumbent.py --all --template templates/huawei-vrp.cmdb --json <report.json> --markdown <report.md>",
    "command_baseline": "python tools/diff_incumbent.py --all --template .devforge/spec/changes/netparse-v3-diff-and-mask/baseline-template.cmdb --json <report-baseline.json>",
    "corpus_snapshots": [{"dir": "cfgfile_YYYYMMDD", "files": 0}],
    "corpus_total_files": 8440,
    "git_commit_tool": "<工具的 commit hash>",
    "git_commit_template_current": "<templates/huawei-vrp.cmdb 的 commit hash>",
    "git_commit_template_baseline": "<baseline-template.cmdb 对应的 commit hash>",
    "_note": "语料是**月度快照**，下个月就变 ⇒ 没有这些锚点就无法复现。模板要记**两个** commit（修前/修后），因为报告 19 列全部有「incumbent/修前/修后」三栏"
  },

  "workload_estimate": {
    "per_column": [
      {"column": "域名名称", "gap": "0/6278430",
       "change": "templates/huawei-vrp.cmdb: 新增 Bas 子集合 + 3 条规则", "effort_days": 0.5},
      {"column": "IPv4静态路由明细", "gap": "0/6278430",
       "change": "templates/huawei-vrp.cmdb: RouteStatics 增 出接口 字段 + 区分「接口名/下一跳」；并修复带出接口路由被整条丢弃",
       "effort_days": 0.5},
      {"column": "接口入流限速(M)", "gap": "0/6278430",
       "change": "templates/huawei-vrp.cmdb: 顶层 QosProfiles 集合 + 接口级 QosProfile 引用 + 接口级 Qos 直给（三者缺一不可）",
       "effort_days": 1.0}
    ],
    "total_effort_days": {"min": 8, "max": 15},
    "compare_with_plan": {"final_plan_range": "68-104", "delta": "..."}
  },
  "readonly_check": {"status": "OK", "checked_paths": 3}
}
```

---

## Diagrams

### 数据流（能力 B 全量 diff）

```
原始配置 (只读)                      incumbent 产物 (只读)
E:\配置文件\BRAS&SR\原始配置\<月>\华为\<stem>    E:\配置文件\BRAS&SR\解析后\<月>\华为\<stem>.json
        │                                      │
        │ netparse 引擎                          │ 直接读
        ▼                                      ▼
   netparse JSON                            incumbent JSON
   {Device[], Interfaces[], ...}              {device_name, device_ip, interfaces[], ...}
        │                                      │
        │  适配器 (纯结构映射)                   │
        ▼                                      │
   incumbent 形状 JSON ──────────┐              │
                                 │              │
                                 ▼              ▼
                        import build_excel (只读)
                        build_rows(json_path) → 19 列行
                                 │              │
                                 └──────┬───────┘
                                        ▼
                                 逐行按接口名 join
                                        ▼
                     ┌──────────────────┴──────────────────┐
                     │  19 列填充率 · 差异样本 · 根因分类     │
                     │  workload_estimate（对照 8.4 的 68-104）│
                     └─────────────────────────────────────┘
```

### 脱敏的写入路径（能力 A）

```
规则匹配 _apply()                       ← 捕获组 + 匹配行
      │  gd = m.groupdict()             ← 这里还是明文
      ▼
   writes = [(path, value), ...]         ← 这里还是明文
      │
      ▼
  ┌────────────────────────────────────────────┐
  │ apply_writes()  ★遮蔽插在这里★             │
  │   for path, value in items:                │
  │     fld = coll.all_fields.get(path)        │
  │     if fld.is_secret():      value = MASK  │  ← L1
  │     elif heuristic(value):   value = MASK  │  ← L2
  │     value = convert(value, fld)            │
  │     set_path(rec, segs, value)            │
  └────────────────────────────────────────────┘
      │
      ▼
   fr.rec / fr.children        ← 从此以后全是掩码
   last[(coll,path)] = value  ← inherit 缓存也是掩码
```

---

## Risks / Trade-offs

| # | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| R1 | **diff 报告里出现密码明文** | 高 | **P0 安全** | 能力 A 先于 B；diff 工具**复用引擎的脱敏**（不自己解析）；报告生成后再扫一遍明文特征，命中即中止并报错 |
| R2 | **适配器映射表写错**（映射了错的字段 / 漏映射） | 中 | diff 结论失真 | ① 映射表逐条评审（D8）；② 每个映射条目必须有**可自动验证的最小样例**（一个 netparse JSON 输入 → 期望的 incumbent JSON 输出），进回归；③ 报告里打印"本列两侧都有值但不一致"的样本数 —— 若映射错了，这个数会异常高 |
| R3 | **`build_excel.build_rows` 依赖 `openpyxl`，且模块级常量硬编码路径** | 低 | import 失败 | `openpyxl` 已装（实测 3.1.5）；import 只读常量不触发 IO；diff 工具在 import 失败时给出明确报错而不是栈回溯 |
| R4 | **8440 文件全量跑太慢** | 低 | 时间超预期 | 实测：netparse 单文件 0.1-1 秒 → 预计 15-40 分钟。工具必须**流式写报告**（不能全量攒内存）；提供 `--sample 200` 冒烟模式 |
| R5 | **临时 JSON 文件的命名丢掉时间戳** → "采集时间"列全线不同 | **中高** | 假差异淹没真差异 | `extract_time` 从文件名提取 → 临时文件名必须保留 `<stem>` 原名。tasks.md T4.3 单列这个检查；diff 报告**先校验"采集时间"列填充率**，若异常低则判定为路径问题而非能力差距 |
| R6 | **修前静态路由出接口缺失导致第 10 列基本全空** | **修前已证实；修后已在实测样本消除** | diff 必须保留修前/修后对照 | **机制澄清**：`route-static[].interface` 由命令层 `parse_huawei_vrp.py` 的 `_split_iface_nexthop()`（第 519 行）在解析时拆出；`build_route_index`（`build_excel.py:344-372`）只读 `r.get("interface")`，为空则跳过。显式出接口判定 MUST 排除 `NULL0`。<br>**最新实测（以 `tools/check_route_forms.py` 为准）**：**60/60 文件修后与 incumbent 一致**；60 文件中共 **150 条显式出接口路由（排除 NULL0）**，修前丢 **150 条（100%）**，修后丢 **0 条**。<br>**此前样本（保留作历史诊断，不与最新口径混算）**：60 台、20250824 快照、1651 条 `ip route-static`：

  | 形态 | 条数 | netparse 修前抓到 | 修前丢失 | 修前丢失率 |
  |---|---|---|---|---|
  | 1 · 纯下一跳（IP / NULL0） | 1575 | 1571 | 4 | 0.3% |
  | 2 · 出接口 + 下一跳 | 75 | 3 | 72 | 96.0% |
  | 3 · 只有出接口 | 1 | 1 | 0 | 0% |
  | 合计 | 1651 | 1575 | 76 | 4.6% |

  此前的 **75/72/96%** 是该样本的形态 2 条件丢失率；最新 **150/150/100%** 是 `check_route_forms.py` 对“显式出接口（排除 NULL0）”统一口径的复测结果。两组数字的样本筛选与统计口径不同，SHALL 并列标注，不得相减或拼接。<br>⇒ incumbent 第 10 列只收带 `interface` 的路由，修前缺口会显著压低该列全局值条数；修后是否完全对齐以全量三栏报告为准。<br>⇒ 根因需区分 `字段丢失` 与 `整条记录丢失`，并附模板声明和真实 trace（D8）。<br>**已投入工作量 ≈ 0.5-1.0 人日**：4 条规则 + `NextHopIface`；单文件实测 212/212 一致，最新 60 文件复测 60/60 一致。 |
| R7 | **L2 密文形态正则误伤正常值**（如长描述、哈希校验和） | 中 | 字段被无意义遮蔽 | L2 默认**只认 `%^%#...%^%#` 包裹**这一种华为密文形态 + 极严格的"高熵长串"启发；**提供 `--no-mask-heuristic` 开关**；报告统计 L2 命中数（若 L2 命中数远超 L1 命中数，说明形态规则过宽，需收窄） |
| R8 | **改引擎影响 13 个探针 + 回归** | 中 | 探针失效 | 脱敏是**加法**（不改 `run()` 签名、不改输出结构，只改写入的值）；回归基线同步更新并加断言；13 个探针在实现后全部跑一遍作为冒烟 |
| R9 | **改模板（`type(secret)` / `AddressType`）破坏回归基线** | 中 | 回归红 | 基线更新是本变更的一部分（tasks.md T4.1）；更新后**人工 diff 基线**，确认变化只在预期字段 |
| R10 | **diff 工具被误当成生产组件** | 低 | 过度工程 | 工具定位写进 docstring；不打包、不进 `netparse` 包；`docs/FINAL-plan.md` 不把它列入产品形态 |

---

## Migration Plan

本变更**不改变任何生产管线的行为**，无数据迁移。以下是**实施顺序**（不可乱序）：

```
阶段 1 · 能力 A（脱敏）
  A1 引擎加 mask 机制（apply_writes 写入前）
  A2 lint 加两条检查
  A3 模板标 type(secret)（BgpPeers.Password 等）
  A4 回归基线更新 + 脱敏断言
  A5 全量探针冒烟
  ⛔ 门禁：能力 A 未通过 → 能力 B 不启动（否则 diff 报告会带明文）

阶段 2 · 能力 B（diff）
  B1 适配器 + 映射表 + 最小样例回归
  B2 diff 工具骨架（--dry-run 只做映射检查）
  B3 冒烟：--sample 20，人工核对报告
  B4 全量：--all，产出正式报告
  B5 结论回写 docs/FINAL-plan.md 8.3 实验 1

阶段 3 · 文档
  TEMPLATE-GUIDE 补 type(secret) 语法
  FINAL-plan 回填 diff 结论
```

**回滚**：本变更全部改动在 netparse 仓库内，**回滚 = 撤销 git 提交**（但注意仓库目前**无 git**，见 Open Questions Q2）。能力 A 与 B 分两个提交，能力 B 可单独回滚而不损失安全修复。

---

## Open Questions

| # | 问题 | 为什么现在答不了 | 建议处理 |
|---|---|---|---|
| **Q1** | ~~**语料规模口径**~~ | **✅ 已关闭（team-lead 2026-11-19 确认）** | 口径 = **按管理 IP 去重 = 632 台**（华为 560 / 中兴 66 / 诺基亚 6）。team-lead 已自行修正 `docs/FINAL-plan.md` 5.1（原写的 1013 / "诺基亚 631" 均为误算）。**tasks 3.3 取消**（不重复改同一份文档）。 |
| **Q2** | **netparse 仓库没有 git**。本变更的"回滚 = 撤销提交"和"能力 A/B 分两个提交"都依赖版本控制。 | 建 git 仓库会改变项目结构，属于超出本变更范围的决策 | **建议在本变更任务 T0.1 里先 `git init` + 一次基线提交**（不改任何现有文件），使后续变更可回滚、可 diff。**需 team-lead 拍板是否越界。** |
| **Q3** | **适配器映射表的 19 列 × 约 30 个字段，谁来评审**？映射表是 diff 结论的唯一可信来源（R2）。 | 需要业务方（熟悉《字段解析说明》的人）确认"netparse 的这个字段等价于 incumbent 的那个字段" | **建议**：映射表交给《字段解析说明》的作者评审；同时每条映射配一个最小样例进回归，让机器也能兜一部分 |
| **Q4** | **L2 兜底是否要覆盖其它厂商的密文形态**（诺基亚/中兴）？本批只跑华为，但引擎是通用的。 | 中兴/诺基亚语料里没有可对比的密文样本（诺基亚 6 台） | **建议：本批只实现华为形态 + 一条保守的通用高熵启发**；其它厂商形态等有语料时再补，并在文档里标明"L2 目前只对华为密文形态完备" |
| **Q5** | **`type(secret)` 是否要支持"部分遮蔽"**（保留首 2 字符，便于人工识别是哪条密码）？ | 无人提出该需求；且泄露信息 | **建议本批不做**（D5 已定固定掩码）。若将来有人要，引擎加 `type(secret, keep=2)` 即可，不影响现有语义 |
| **Q6** | ~~**第 6/7 列（限速）的"join 缺失"根因是否允许多值**~~ | **✅ 已关闭（D8 证据门禁）** | 当前既无 `qos car` 规则也无顶层 `qos-profile` 集合，原始字段不齐 ⇒ 只能归模板覆盖/字段缺口，**不得归 `join缺失`**。只有真实 trace 证明源字段已捕获、仅关联未发生时，才单列 `join缺失`；不同样本可分别归类并分别附证据 |
| **Q7** | 是否需要把 diff 报告**提交进仓库**（`.devforge/.../diff-report/`）？报告可能很大（8440 文件 × 19 列）。 | 取决于仓库大小策略 | **建议**：JSON 报告**压缩后提交**（或只提交汇总 + 样本），全量报告写到 `.devforge/` 下但加 `.gitignore` 条目 |
| **Q8** | **netparse 多出来的 11 个接口级直接字段（AdminStatus/CarrierDownHoldTime/Dcn/Duplex/EthTrunkMember/Isis/Mtu/Speed/Statistic/Type/UserVlan）要不要变成新的台账列？** 它们不在 incumbent 的 19 列里，但 `AdminStatus`（管理状态）和 `Mtu`（MTU）是 CMDB 接口 CI 的基础属性。`VLAN ↔ UserVlan` 另有内容对应关系，不能因结构不同把 UserVlan 简单视为全新业务内容。 | **这是产品决策**（CMDB 要不要多几列），不是技术决策；且用户没提过"接口台账要加列" | **建议**：本变更**只报告、不决策** —— 在 `part2_capability.extra_value_note` 里列出这些字段 + 它们在真实配置里的填充率，作为"是否值得给 CMDB 增列"的依据。**是否增列由用户拍板。** |
| **Q9** | **限速三层（顶层定义 / 接口级引用 / 接口级直给）全部补齐要 3 处模板规则**，但 netparse 当前**一条 qos-profile 规则都没有**。这三层补齐属于本变更的"模板缺口"（不补 → 第 6/7 列必然 0%），还是属于后续批次 A？ | 本变更的定位是"测量差距"，不是"补差距" | **建议：不补**。diff 的价值就是把这个缺口**量化出来**（"补齐需 1.0 人日"），而不是在本变更里悄悄补掉。**但需 team-lead 确认**：若希望 diff 报告直接给出"补完之后第 6/7 列能到多少"，那就要在 diff 工具里加一个"模拟补齐"的分支 —— **不推荐**，那会让报告同时包含"现状"和"假设"，难以解释。 |

---

## 附：本设计引用的实测数据（全部可复现）

| 事实 | 数值 | 复现方式 |
|---|---|---|
| incumbent 产物规模 | 8440 文件 / 0 失败 / 6,278,430 接口 / 490.8s | `E:\配置文件\BRAS&SR\解析后\_index.json` |
| 原始配置 ↔ incumbent JSON 映射 | **8440/8440 命中** | 按 `<月>/华为/<stem>` 匹配，候选 `<stem>` 与 `<stem>.txt` |
| 同一样本文件两侧接口数 | netparse **1044** = incumbent **1044** | 两个引擎各跑一次 |
| `sub` 地址占比 | 2.40%（主 25696 / sub 631，120 文件抽样） | 读 incumbent JSON 统计 `IP.IPV4[].type` |
| 带显式出接口的 IPv4 静态路由（此前样本） | **8.4%**（282/3360，120 文件抽样）；与最新 `check_route_forms.py` 口径不同，不混算 | 解析 `ip route-static` 的第 3 个 token 是否为 IP |
| 静态路由修前/修后（最新统一口径） | **60/60 文件修后一致**；显式出接口（排除 NULL0）150 条，修前丢 150（100%），修后丢 0 | `python tools/check_route_forms.py` |
| 静态路由形态 2（此前样本） | 75 条中修前丢 72 条（96%）；仅作历史诊断并列保留 | 60 台、20250824 快照、1651 条路由的形态分层统计 |
| 模板里 `sub` 未被捕获 | `huawei-vrp.cmdb:96` 的 `(?:\s+sub)?` 消耗但不捕获 | 读模板 |
| incumbent 的 `.fsm` 捕获了 sub | `huawei_vrp.fsm:43` `Value List IPV4 ((?P<ip>\S+) (?P<mask>\S+)(?P<sub>\s+sub)?)` | 读 .fsm |
| 密码类命令形态 | `default-password-template` 19317 / `ssh server cipher` 200 / `ssh user ... authentication-type password` 198 / `user-password ... disable` 175（**均非密码**）；`peer ... password cipher` 253 / `radius-server shared-key-cipher` 828 / `isis authentication-mode md5 cipher` 372 / `snmp-agent community read cipher` 1053 / `default-password cipher` 184 / `md5-password cipher` 64（**均为密码**） | 200 文件抽样，剔除 `%^%#` 后取命令骨架 |
| 密文形态 | 华为密文**全部**为 `%^%#...%^%#` 包裹 | 抽样核对 |
| `openpyxl` | 3.1.5 已装 | `python -c "import openpyxl; print(openpyxl.__version__)"` |
| `textfsm` | **未装** | `python -c "import textfsm"` → ImportError |
| `build_excel` import 副作用 | 无（`if __name__ == "__main__"` 在第 599 行） | 读源码 |
