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
| **2** | **接口级字段 4/10 对得上**；netparse **多出 10+ 个字段**（AdminStatus/Mtu/Speed/Duplex/Dcn/Isis/Vrrp/Track…）incumbent 全没有 | ✅ 成立（incumbent 侧实测 10 个接口级键；netparse 侧 17 个） | ⇒ **diff 必须双向报（缺什么 + 多什么）**。只报缺口会系统性低估 netparse。**已并入 D2 的"段二 · 能力口径"** |
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
- **netparse 在接口基础属性上其实更强**。实测两侧接口级字段：incumbent 10 个（`name` `description` `IP` `multicast` `vpn` `VLAN` `bas` `traffic-policy` `QOS` `qos-profile`），netparse 17 个（多出 `AdminStatus` `Mtu` `Speed` `Duplex` `Dcn` `Type` `VlanType` `UserVlan` `AccessVlan` `PortLinkType` `TrunkAllowPass` `CarrierDownHoldTime` `Statistic` `Isis` `Vrrp` `Track` `EthTrunkMember`）。**这些多出来的字段 incumbent 一个都没有。**
- ⇒ **只报"缺什么"的报告会严重低估 netparse**。台账的 19 列只是 CMDB 当前要的那 19 个；**"多出来的字段"恰恰是"netparse 值不值得继续投"的核心论据**（更丰富的 CI 模型：MTU/速率/双工/管理状态/VLAN 类型/VRRP/track…）。
- ⇒ **段二必须双向**：既列 netparse 缺的，也列 netparse 多的。**只列缺口的报告，结论会系统性偏向"换回 incumbent"。**

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

### D6 · 脱敏时机 = **写入时**（`apply_writes` 落值之前），不是输出时

**决策**：遮蔽发生在 `apply_writes()` 内、`convert()` 之前：
```
原值 → [是否 secret？] → 是则替换为掩码串 → convert() → set_path()
```
即：**值一旦进入引擎的记录对象就已经是掩码**，`rec`、`children`、`last` 缓存、异常堆栈里的 repr，全都不含明文。

**理由**：
- 引擎里**有第二条值通道**：`last[(coll.name, path)]`（`probe_stack.py:636`），供 `inherit=` 使用。如果只在输出层遮蔽，`last` 里仍是明文，`inherit` 把它复制到别的字段时会绕过遮蔽。
- **中间态泄露**：异常堆栈、`--json` 之前的任何调试打印、内存快照，都可能带明文。
- 输出层遮蔽还会让"值是否被遮蔽"依赖调用方 → 多个出口（`--json` / 未来 CLI / Python API）容易漏。

**为什么不用另一种**：
- ❌ **输出层遮蔽**（`main()` 里遍历结果替换）：`last` 缓存漏网；多出口易漏；且 `inherit=` 复制出来的是明文，绕过遮蔽。
- ❌ **在 `convert()` 里做**：语义不清（`convert` 管类型，不管安全），且 `convert` 对未知类型直接返回原值，容易漏。
- ❌ **在 `compile_rule` 阶段做**：那是编译期，值还不存在。

**代价**：`apply_writes` 是热路径（每条规则的每个捕获组都过），多一次判断 → 可忽略（判断是 `fld.type == "secret"` 的字符串比较 + L2 的一次正则匹配）。**L2 的正则匹配在热路径上要做一次缓存**（按值长度/前缀缓存结果），否则 8440 文件 × 百万捕获组会明显变慢。

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

### D8 · diff 报告必须带**根因分类**与**工作量结论**，不只给数字

**决策**：报告对**每一个未填满的列**给出根因分类 ∈：

| 根因 | 判定方式 | 例子 |
|---|---|---|
| `模板未覆盖该命令` | 该列对应的命令族在引擎内 trace 里 0 命中 | 第 13 列 域名名称 ← `bas` 块 514 行 0% 覆盖 |
| `字段丢失` | 命令被模板命中，但模板没把需要的字段捕获出来 | 第 10 列 ← 静态路由**出接口**未捕获；`interface` 缺失导致 `build_route_index` 跳过全部 |
| `join缺失` | 字段都齐，但资产层需要跨集合按名 join | 第 6/7 列 ← 限速需 join 顶层 `qos-profile` |
| `语义不同` | 两边都填了但值不同 | 待实测 |

**并且报告必须有一节 `workload_estimate`**：逐列列出「缺口 → 需要改哪个模板的哪条规则 → 预估人日」，最后给出**总人日区间**，与 `docs/FINAL-plan.md` 8.4 的 68-104 人日区间对照。

**理由**：
- **只给数字的报告不可行动**。"第 13 列填充率 0%" 没人知道该干什么；"第 13 列 0%，因为 `bas` 块 514 行完全未覆盖，需要在 `Interfaces` 下加一个 `Bas` 子集合 + 3 条规则，约 0.5 人日" 才是决策依据。
- **根因分类是自动判定的**：引擎的 `trace`（`probe_stack.py:594/610`）已经记录了每行命中哪个集合 → 命令族覆盖率可自动算；字段级缺口可由适配器的映射表**逐字段标注**得出。**⇒ 适配器的映射表里，每个目标字段都要带一个 `source` 描述 + `coverage` 分类**，报告直接读它。
- **工作量结论是这个 diff 存在的唯一目的**（`docs/FINAL-plan.md` 8.3 实验 1 的判据是"差距大 → 重新评估路线 2"）。没有工作量估算，报告回答不了那个问题。

**为什么不用另一种**：
- ❌ **只报填充率**：不可行动。
- ❌ **人工在报告里写工作量**：不可复现、不可自动化、每次都要重写。
- ❌ **只报差异样本**：样本是诊断材料，不是结论。

**代价**：适配器的映射表要多写两个字段（`source` / `root_cause`），且映射表本身要人工审。**这是本变更最需要评审的部分，见 Open Questions Q3。**

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

### D10 · 引擎代码位置：**本批不抽包**，保留 `tools/probe_stack.py` 为唯一实现

**决策**：脱敏改动**直接改** `tools/probe_stack.py`；新代码（适配器、diff 工具）放在 `tools/` 或新建 `src/netparse/`，但**通过 `sys.path` import 现有的 `tools/probe_stack.py`**（与 `tests/run_regression.py:17` 现有做法一致：`sys.path.insert(0, os.path.join(ROOT, "tools"))`）。

**理由**：
- **13 个探针脚本全部 `import probe_stack`**（`probe_stack.py:761` 被 `probe_coverage2/3.py`、`probe_acl_rules.py`、`probe_flat2.py`、`probe_blind.py` 等引用）。抽包 = 改 import 路径 = **打断全部探针** + 打断回归脚本。
- 引擎正式化（抽 `src/netparse/` 包 + CLI + 打包）是 `docs/FINAL-plan.md` 8.4 里 8-12 人日的独立工作项，**不该混进一个 2-3 人日的决策实验里**。
- 本变更的价值是**产出结论**，不是产出架构。架构改造会污染"改了什么"的归因。

**为什么不用另一种**：
- ❌ **抽 `src/netparse/` 包**：破坏 13 个探针 + 回归脚本的 import；且把一个决策实验变成一个架构改造，验收变模糊。
- ❌ **把脱敏做成引擎外的包装层**：违背 D6（写入时遮蔽）。
- ❌ **复制一份引擎到 `src/`**：两份实现必然漂移，是最坏选项。

**代价**：引擎继续待在 `tools/`（观感不佳），且 `sys.path` hack 继续存在。**缓解**：在 `design.md` 与 `tasks.md` 里显式记录这条技术债，并在 `docs/FINAL-plan.md` 8.4 的"引擎正式化"项里注明"需处理 13 个探针的 import 迁移"。

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
        "root_cause_if_empty": None,          # 有源 → 不可能空
    },
    {
        "path": "interfaces[].IP.IPV4[].type",
        "source": "Interfaces[].IpAddress[].AddressType",
        "root_cause_if_empty": "字段丢失",     # D3 的产出
    },
    {
        "path": "interfaces[].VLAN.type",
        "source": None,                        # 模板未覆盖该命令
        "root_cause_if_empty": "模板未覆盖该命令",
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
    "mask_value": "******"
  },

  "part1_ledger": {
    "_desc": "段一 · 台账口径：两侧都过 build_excel.build_rows → 19 列",
    "column_fill_rate": {
      "设备名称": {"filled": 8440, "total": 8440, "rate": 1.0, "root_cause": null},
      "域名名称": {"filled": 0, "total": 6278430, "rate": 0.0,
                   "root_cause": "模板未覆盖该命令",
                   "evidence": {"command_family": "access-type layer2-subscriber",
                                "total_lines": 514, "hit_lines": 0, "source": "引擎内 trace"}}
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
      "matched":  ["name", "description", "IP", "vpn"],
      "only_incumbent": ["QOS", "VLAN", "bas", "multicast", "qos-profile", "traffic-policy"],
      "only_netparse": ["AdminStatus", "Mtu", "Speed", "Duplex", "Dcn", "Type", "VlanType",
                        "UserVlan", "AccessVlan", "PortLinkType", "TrunkAllowPass",
                        "CarrierDownHoldTime", "Statistic", "Isis", "Vrrp", "Track",
                        "EthTrunkMember"],
      "coverage": {"incumbent": 10, "netparse": 17, "matched": 4}
    },
    "collection_level_fields": {
      "qos_profile_top": {"incumbent": "list[{name,in:{cir},out:{cir}}]", "netparse": null,
                          "note": "顶层命名空间级定义；netparse 模板无任何 qos-profile 规则"},
      "route_static": {"incumbent": "{IPV4:[{dest,mask,interface?,nexthop,vpn}], IPV6:[...]}",
                       "netparse": "list[{Prefix,Mask,NextHop,Preference,Description,VpnInstance}]",
                       "missing_in_netparse": ["interface(出接口)"]}
    },
    "extra_value_note": "netparse 多出的接口级字段不在 19 列台账内 → 它们是「更丰富的 CI 模型」的论据，报告须单列"
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
E:\...\原始配置\<月>\华为\<stem>      E:\...\解析后\<月>\华为\<stem>.json
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
| R6 | **静态路由出接口缺失导致第 10 列恒 0%** | **确定会发生** | diff 结论正确但需解释 | **机制澄清（重要，与初稿表述不同）**：`route-static[].interface` **不是** `build_excel.py` 用 dest+mask 反查算出来的 —— 它由命令层 `parse_huawei_vrp.py` 的 `_split_iface_nexthop()`（第 519 行）在**解析时**拆出"出接口 / 下一跳"并存入。实测：1558 接口那个文件的 79 条 IPv4 路由**全部无 `interface`**（都是默认路由/下一跳直连）；而另一个文件里有 `{"dest":"2409:8770:...","interface":"Eth-Trunk55.2500","nexthop":"2409:8070:..."}`。<br>**`build_route_index`（`build_excel.py:344-372`）只做两件事：读 `r.get("interface")`，为空则 `continue`；否则按接口名小写归集。** ⇒ **"把路由挂到接口行"是资产层的 join（已存在），"解析出出接口"是命令层的事（netparse 缺）。**<br>⇒ netparse 侧的缺口是**两条**：① 模板不捕获出接口 → 适配器无法产出 `interface` → 第 10 列 0%；② **带显式出接口的路由（实测 8.4%，282/3360）当前被 netparse 整条丢弃**（`NextHop` 的 reg `\S+` 捕获到接口名，随后的下一跳 IP 使规则末尾 `\s*$` 失配）。<br>**根因分类标 `字段丢失`，工作量 ≈ 0.5 人日**（模板加出接口字段 + 一个"是接口名还是下一跳"的判定）。**这一条印证并锁定了边界：join 归资产层，引擎不背；但引擎该把 join 所需的原始字段抓全。** |
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
| **Q1** | **语料规模口径**：本轮实测唯一管理 IP = **632**（华为 626 / 中兴 64 / 诺基亚 6），任务给的是 1013（华为 318 / 中兴 64 / 诺基亚 631）。**诺基亚 631 与实测的 6 差 100 倍**，华为 318 与实测 626 也不符。 | 需要确认口径：按管理 IP 去重 vs 按"设备名+型号"去重 vs 按月快照折算 | **规格以实测为准（632 / 华为 626）**。请 team-lead 复核 `解析/cfgfile_20260924` 目录 —— 该目录 658 个文件里只有 6 个是诺基亚（7950XRS/7750SR），其余是华为（CE12808/NE5000E/HWNE40E/HW-ZTE5960 等）。**若最终口径是 1013，请给出可复现的去重脚本。** |
| **Q2** | **netparse 仓库没有 git**。本变更的"回滚 = 撤销提交"和"能力 A/B 分两个提交"都依赖版本控制。 | 建 git 仓库会改变项目结构，属于超出本变更范围的决策 | **建议在本变更任务 T0.1 里先 `git init` + 一次基线提交**（不改任何现有文件），使后续变更可回滚、可 diff。**需 team-lead 拍板是否越界。** |
| **Q3** | **适配器映射表的 19 列 × 约 30 个字段，谁来评审**？映射表是 diff 结论的唯一可信来源（R2）。 | 需要业务方（熟悉《字段解析说明》的人）确认"netparse 的这个字段等价于 incumbent 的那个字段" | **建议**：映射表交给《字段解析说明》的作者评审；同时每条映射配一个最小样例进回归，让机器也能兜一部分 |
| **Q4** | **L2 兜底是否要覆盖其它厂商的密文形态**（诺基亚/中兴）？本批只跑华为，但引擎是通用的。 | 中兴/诺基亚语料里没有可对比的密文样本（诺基亚 6 台） | **建议：本批只实现华为形态 + 一条保守的通用高熵启发**；其它厂商形态等有语料时再补，并在文档里标明"L2 目前只对华为密文形态完备" |
| **Q5** | **`type(secret)` 是否要支持"部分遮蔽"**（保留首 2 字符，便于人工识别是哪条密码）？ | 无人提出该需求；且泄露信息 | **建议本批不做**（D5 已定固定掩码）。若将来有人要，引擎加 `type(secret, keep=2)` 即可，不影响现有语义 |
| **Q6** | **第 6/7 列（限速）的"join 缺失"根因**：netparse 模板当前既没有 `qos car` 规则，也没有顶层 `qos-profile` 集合 → 属于"模板未覆盖"还是"join 缺失"？ | 两边都对：命令没覆盖 **且** 资产层需要 join | **建议根因分类允许多值**（`["模板未覆盖该命令", "join缺失"]`），并在工作量里分别列"模板补规则"与"资产层支持 join"两笔账 |
| **Q7** | 是否需要把 diff 报告**提交进仓库**（`.devforge/.../diff-report/`）？报告可能很大（8440 文件 × 19 列）。 | 取决于仓库大小策略 | **建议**：JSON 报告**压缩后提交**（或只提交汇总 + 样本），全量报告写到 `.devforge/` 下但加 `.gitignore` 条目 |
| **Q8** | **netparse 多出来的 17 个接口级字段（AdminStatus/Mtu/Speed/Duplex/Dcn/Type/VlanType/UserVlan/AccessVlan/PortLinkType/TrunkAllowPass/CarrierDownHoldTime/Statistic/Isis/Vrrp/Track/EthTrunkMember）要不要变成新的台账列？** 它们不在 incumbent 的 19 列里，但 `AdminStatus`（管理状态）和 `Mtu`（MTU）是 CMDB 接口 CI 的**基础属性**，`Vrrp`/`Track` 是高价值关系。 | **这是产品决策**（CMDB 要不要多几列），不是技术决策；且用户没提过"接口台账要加列" | **建议**：本变更**只报告、不决策** —— 在 `part2_capability.extra_value_note` 里列出这些字段 + 它们在真实配置里的填充率，作为"是否值得给 CMDB 增列"的依据。**是否增列由用户拍板。** |
| **Q9** | **限速三层（顶层定义 / 接口级引用 / 接口级直给）全部补齐要 3 处模板规则**，但 netparse 当前**一条 qos-profile 规则都没有**。这三层补齐属于本变更的"模板缺口"（不补 → 第 6/7 列必然 0%），还是属于后续批次 A？ | 本变更的定位是"测量差距"，不是"补差距" | **建议：不补**。diff 的价值就是把这个缺口**量化出来**（"补齐需 1.0 人日"），而不是在本变更里悄悄补掉。**但需 team-lead 确认**：若希望 diff 报告直接给出"补完之后第 6/7 列能到多少"，那就要在 diff 工具里加一个"模拟补齐"的分支 —— **不推荐**，那会让报告同时包含"现状"和"假设"，难以解释。 |

---

## 附：本设计引用的实测数据（全部可复现）

| 事实 | 数值 | 复现方式 |
|---|---|---|
| incumbent 产物规模 | 8440 文件 / 0 失败 / 6,278,430 接口 / 490.8s | `E:\配置文件\BRAS&SR\解析后\_index.json` |
| 原始配置 ↔ incumbent JSON 映射 | **8440/8440 命中** | 按 `<月>/华为/<stem>` 匹配，候选 `<stem>` 与 `<stem>.txt` |
| 同一样本文件两侧接口数 | netparse **1044** = incumbent **1044** | 两个引擎各跑一次 |
| `sub` 地址占比 | 2.40%（主 25696 / sub 631，120 文件抽样） | 读 incumbent JSON 统计 `IP.IPV4[].type` |
| 带显式出接口的 IPv4 静态路由 | **8.4%**（282/3360，120 文件抽样） | 解析 `ip route-static` 的第 3 个 token 是否为 IP |
| 模板里 `sub` 未被捕获 | `huawei-vrp.cmdb:96` 的 `(?:\s+sub)?` 消耗但不捕获 | 读模板 |
| incumbent 的 `.fsm` 捕获了 sub | `huawei_vrp.fsm:43` `Value List IPV4 ((?P<ip>\S+) (?P<mask>\S+)(?P<sub>\s+sub)?)` | 读 .fsm |
| 密码类命令形态 | `default-password-template` 19317 / `ssh server cipher` 200 / `ssh user ... authentication-type password` 198 / `user-password ... disable` 175（**均非密码**）；`peer ... password cipher` 253 / `radius-server shared-key-cipher` 828 / `isis authentication-mode md5 cipher` 372 / `snmp-agent community read cipher` 1053 / `default-password cipher` 184 / `md5-password cipher` 64（**均为密码**） | 200 文件抽样，剔除 `%^%#` 后取命令骨架 |
| 密文形态 | 华为密文**全部**为 `%^%#...%^%#` 包裹 | 抽样核对 |
| `openpyxl` | 3.1.5 已装 | `python -c "import openpyxl; print(openpyxl.__version__)"` |
| `textfsm` | **未装** | `python -c "import textfsm"` → ImportError |
| `build_excel` import 副作用 | 无（`if __name__ == "__main__"` 在第 599 行） | 读源码 |
