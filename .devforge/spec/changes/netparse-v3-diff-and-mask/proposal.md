# Proposal · netparse-v3-diff-and-mask

> 变更名：`netparse-v3-diff-and-mask`
> 阶段：Phase 2 提案（规格制品包）
> 作者：高见远（架构师）
> 日期：2026-11-19
> 范围：**只做第一批，两个能力**（引擎层密码脱敏 + netparse vs incumbent 逐字段 diff）

---

## Why

### 业务动机一：安全 —— netparse 当前会把可用密文明文写进落盘 JSON

netparse 解析华为配置时，BGP peer 密码被完整捕获并原样输出：

```json
{"Peer": "218.200.1.10", "PasswordType": "cipher",
 "Password": "%^%#;}4v<YDuLCwX&[69w2WFv+ao8<R4O6_!Q*4ULlz:%^%#"}
```

对应配置原文 `peer 218.200.1.10 password cipher %^%#...%^%#`。

这条密文是**可用的**（VRP 的 `%^%#...%^%#` 是可解的 cipher 封装），不是不可逆哈希。它一旦落进 `解析后/*.json` 这类中间产物并进入备份、网盘、Git、日志，就是**凭据泄露**。而 netparse 的产物是要批量入库 CMDB 的，扩散面极大。

**资产层来不及救**：落盘的中间 JSON 本身就是明文，脱敏必须发生在**引擎写入的那一刻**。

### 业务动机二：决策 —— 这是决定 netparse 该不该继续投 68-104 人日的那个实验

方案评估（`docs/FINAL-plan.md` 8.3 实验 1）已经定调：netparse 不写新功能，**先做逐字段 diff**，用现有 8440 个真实文件回答一个具体问题：

> **netparse 距离能替代 incumbent，还差哪几列、差多少工作量？**

这个问题现在答不了，因为：
- netparse 输出形状（`Interfaces[].IpAddress[].Address`）与 incumbent 形状（`interfaces[].IP.IPV4[].ip`）**不兼容**，直接比 JSON 会产生大量"形状噪声"而非"能力差异"；
- **限速列的值在中间 JSON 层根本不存在** —— 它是「顶层命名空间定义 + 接口级引用 + 接口级直给」三层 join 的产物（实测顶层 list 40/40 文件都有、接口级引用 1770 个接口、接口级直给 2304 个接口，规则是"两者都存在取小"）。**只有走资产层的 `build_rows` 才拿得到可比的答案**；
- netparse 的 CMDB 19 列覆盖状态是**估算值 9/19**，没有实测。

**而有一个强信号必须写进结论**：**两侧的接口数精确一致**（1044 接口的文件两边都是 1044；1558 接口的文件两边都是 1558）⇒ **接口识别层已完全对齐，差距全在字段**。按只统计接口记录直接子键、**不含子集合内部字段**的最新口径：netparse 15 / incumbent 10 / 共有 4 / netparse 独有 11 / incumbent 独有 6。netparse 多出的 11 项为 `AdminStatus` `CarrierDownHoldTime` `Dcn` `Duplex` `EthTrunkMember` `Isis` `Mtu` `Speed` `Statistic` `Type` `UserVlan`。此前“多 17 个”误计了子集合字段，现废弃。**只报"缺什么"的报告会系统性低估 netparse**，因此 diff 必须**双向**。另须单列 `VLAN ↔ UserVlan` 为**内容对应、结构不同**：incumbent 是嵌套 VLAN 对象，netparse 是标量，不能把结构差异笼统当成内容完全未覆盖。

而 incumbent 侧的条件已经全部具备且**只读可用**：
- 解析产物是 JSON（不是 xlsx），8440 个文件、0 失败、6,278,430 个接口；
- 资产层 `build_excel.py` 的 `build_rows(json_path)` 是一个**纯函数**，import 即用，不修改生产脚本；
- `openpyxl 3.1.5` 已安装，`build_excel` 模块级 import 无副作用；
- 8440 个 incumbent JSON ↔ 原始配置的路径映射**已实测 100% 命中**。

**这两个能力都不依赖任何待定决策，做完就能支撑"投 / 不投"的决策。**

---

## What Changes

### 能力 A · 引擎层密码脱敏（`secret-mask`）

| 变更 | 说明 |
|---|---|
| A1 模板语法新增字段类型 `secret` | `Password type(secret) reg(\S+) desc(密码)` —— 字段声明为 secret，引擎在写入时遮蔽 |
| A2 引擎写入路径加遮蔽 | 遮蔽发生在 `apply_writes` 落值**之前**，不是输出时；中间态（`rec`、`last` 缓存）也不留明文 |
| A3 引擎内置密文形态兜底 | 即使模板漏标 `type(secret)`，命中内置密文形态的值也自动遮蔽；可用 CLI 关闭 |
| A4 掩码表示与可区分性 | 固定掩码串；**"未配置"（键不存在）与"已配置但已遮蔽"（键存在且为掩码）必须可区分** |
| A5 lint 新增两条检查 | ① `option(Key)` 与 `type(secret)` 同时出现 → 错误（遮蔽后所有值相同，聚合会误合并）；② 规则文本含 `password`/`cipher`/`secret`/`shared-key` 等关键字但捕获字段未声明 secret → 警告 |
| A6 存量模板补标 | `templates/huawei-vrp.cmdb` 的 `BgpPeers.Password` 标为 `type(secret)`；其余含密文的字段一并标注 |
| A7 回归基线更新 | `tests/fixtures/sample-vrp.expected.json` 相应更新；新增一条"密码不落明文"的断言 |

### 能力 B · netparse 与 incumbent 逐字段 diff（`incumbent-diff`）

| 变更 | 说明 |
|---|---|
| B1 形状适配器 | netparse JSON → incumbent 形状的**纯结构映射**（禁止合成数据） |
| B2 资产层复用 | `import build_excel` 调用其 `build_rows(json_path)` / `COLUMNS`，**不修改 incumbent 任何文件** |
| B3 全量 diff 工具 | 对 8440 个文件逐个跑两侧，产出**两段**结论：**段一 · 台账口径**（19 列，**两个粒度**：逐文件填充率 + **全局值条数**）+ **段二 · 能力口径**（字段级**双向**覆盖：缺什么 + **多什么**） |
| B4 有证据的根因分类 | 差异必须标注根因（模板未覆盖该命令 / 字段丢失 / **整条记录丢失** / **join缺失（单列）** / 语义不同）。自动分类必须附**模板声明 + 真实 trace + 真实文件/行号或脱敏样本**；不得只凭 `source=None` 等人工常量判根因，证据不足则标 `待人工判定` |
| B5 工作量结论 | 汇总必须明确回答"差哪几列、每列差多少、补齐需多少工作量" |
| B6 模板补 `AddressType` | 支撑 D3：secondary 地址（`type=sub`）在源头捕获，避免适配器补不回来 |
| B7 **接口数一致性基准** | 逐文件比对两侧接口数（实测两侧**精确一致**：1044↔1044、1558↔1558）→ 证明"接口识别层已对齐，差距全在字段" |
| B8 **双向能力报告** | 接口直接子键口径为 **netparse 15 / incumbent 10 / 共有 4 / netparse 独有 11 / incumbent 独有 6**，不含子集合内部字段；`VLAN ↔ UserVlan` 单列“内容对应、结构不同” → **只报缺口会系统性低估 netparse** |
| B9 **全局汇总口径（必须项）** | 每列给**全局值条数**（不按文件切分）+ 差 > 20% **标红**。**此前样本**：第 10 列逐文件口径会掩盖 72/75（96%）的形态 2 丢失，120 文件全局值条数差 96.6%；最新路由复测见 B11，两组口径不混算 |
| B10 **19 列全部三栏** | 存改前模板快照 + `--template` 参数跑两遍；**19 列全部列**在逐文件与全局粒度均给 `incumbent` / `netparse_before_fix` / `netparse_after_fix` 三栏，不只给受影响列 → 同时量化收益并检查其他列回归 |
| B11 **静态路由模板修复（2.2b）** | 4 条规则 + `NextHopIface`。最新 `check_route_forms.py`：**60/60 文件修后一致；显式出接口（排除 NULL0）150 条，修前丢 150（100%），修后丢 0**。此前样本 75 条丢 72（96%）并列保留但不混算 |
| B12 **按月轮转分层抽样** | `--sample N` 先按月轮转、月内随机不放回；期望 `distinct_months=min(N,可选月数)`。不再要求 20 个样本同时覆盖 26 个月 |

---

## Capabilities

| Capability | 目录 | 状态 |
|---|---|---|
| `secret-mask` | `specs/secret-mask/spec.md` | 新增 |
| `incumbent-diff` | `specs/incumbent-diff/spec.md` | 新增 |

**不新增** capability：本变更不引入"资产层"、"模板质量"、"扁平化输出"等能力，只为上述两个能力服务。

---

## Impact

### 新增文件（全部在 netparse 仓库内）

```
netparse/
├─ .devforge/spec/changes/netparse-v3-diff-and-mask/   ← 本规格包
├─ src/netparse/            （新建包，见 tasks.md 的目录决策）
│  ├─ __init__.py
│  ├─ engine.py             ← 从 tools/probe_stack.py 抽出的引擎（含脱敏）
│  ├─ masking.py            ← 脱敏策略
│  └─ adapters/incumbent.py ← netparse JSON → incumbent 形状
├─ tools/diff_incumbent.py  ← diff 工具 CLI
└─ tests/
   ├─ fixtures/secret-mask/ ← 脱敏专用样例
   └─ test_masking.py
```

### 修改文件（netparse 仓库内）

| 文件 | 改动 | 风险 |
|---|---|---|
| `templates/huawei-vrp.cmdb` | `Password` 标 `type(secret)`；`IpAddress` 增 `AddressType` 字段 + 规则捕获 | **改变输出**（脱敏是有意的；AddressType 是新增字段） |
| `tools/probe_stack.py` | 若引擎抽包则此处改为薄转发；否则直接改 | 探针脚本依赖 `import probe_stack`，**改名会打断 13 个探针** → 见 design.md D10 |
| `tests/run_regression.py` | 增加脱敏断言 | 基线值变化 |
| `tests/fixtures/sample-vrp.expected.json` | 更新为脱敏后的值 | 基线值变化 |
| `docs/TEMPLATE-GUIDE.md` | 补 `type(secret)` 语法 | 文档 |

### 依赖

| 依赖 | 状态 | 说明 |
|---|---|---|
| `openpyxl` | ✅ 已装 3.1.5 | `build_excel` 的模块级依赖，import 即用 |
| `textfsm` | ❌ 未装 | **本变更不需要**（只 import `build_excel`，不跑 incumbent 解析器） |
| 网络/外网 | 不需要 | 全部本地 |

### 只读资产（硬约束）

| 路径 | 允许的操作 |
|---|---|
| `E:\配置文件\配置解析\华为\` | **只读 import**（`build_excel`）。不修改任何文件、不重跑 `parse_huawei_vrp.py` |
| `E:\配置文件\BRAS&SR\解析后\` | **只读**（读 JSON 作 diff 左侧）。不写、不删、不重跑 |
| `E:\配置文件\BRAS&SR\原始配置\` | **只读**（读配置作 diff 输入）。⚠️ **不是** `E:\配置文件\原始配置\` —— 那个路径**不存在**（实现期修正） |
| `E:\配置文件\BRAS&SR\解析数据\` `端口状态\` | **不触碰**（本变更不产出 xlsx） |

### 数据库变更

无。

### 破坏性变更

| 变更 | 破坏性 | 缓解 |
|---|---|---|
| 密码字段输出变为掩码 | **是**（下游若读明文会受影响） | 当前下游无人消费 netparse 输出（netparse 尚无生产消费者）；掩码是安全目的的有意变更 |
| `IpAddress` 增加 `AddressType` 字段 | 是（JSON 多一个键） | 适配器与 diff 工具均容忍未知键；回归基线同步更新 |
| 引擎从 `tools/probe_stack.py` 抽到 `src/netparse/engine.py` | 是（import 路径变化） | 见 design.md D10：优先**保留 `tools/probe_stack.py` 作为唯一实现**，新代码 import 它；抽包作为独立后续变更 |

---

## Non-Goals

**这一节是本提案防越界的核心。以下明确不做：**

1. **不补模板的命令覆盖缺口**。qos car / traffic-policy / pim sm / bas / user-vlan qinq / vlan-type dot1q / ipv6 route-static 的规则**本批不写** —— 它们的缺失正是 diff 要**测量出来**的，写了就测不出差距了。
2. **不实现资产层**。不新建"中间字段 → CI 列"的映射层，不重写 `build_excel.py` 的任何逻辑。资产层只被**调用**，不被**改写**。
3. **不实现扁平化输出**。`--shape flat` / `flatten()` / `as=` / `parent()` / 外键声明 —— 全部延后（`docs/FINAL-plan.md` 6.4 / 8.2）。
4. **不改 incumbent 的任何文件**。包括不改 `build_excel.py`、不重跑 `parse_huawei_vrp.py`、不重新生成 `解析后/*.json`。
5. **不产出 xlsx**。diff 工具产出的是**报告**（JSON + Markdown），不是台账。台账仍由 incumbent 的 `build_excel.py` 产出。
6. **不做中兴 / 诺基亚模板**。diff 只跑华为（incumbent 只有华为的解析产物可比）。
7. **不做增量解析 / 并发优化**。8440 文件全量跑，接受分钟级耗时。
8. **不改引擎的解析语义**。唯一例外是脱敏（这是有意的输出变化，见硬约束 3）。
9. **不引入新的第三方依赖**。不用 pandas / pydantic / 任何 DSL 解析库。
10. **不承诺 netparse 能替代 incumbent**。diff 的目的是**测量差距并给出工作量估算**，不是宣布胜利。

---

## 验收标准（可自动断言）

### 能力 A

| 编号 | 断言 | 命令 |
|---|---|---|
| A-1 | 真实文件解析后，全量 JSON 文本中**密码明文 0 命中** | `python tools/probe_stack.py templates/huawei-vrp.cmdb <真实文件> --json \| grep -c "%\^%#"` → `0` |
| A-2 | 被脱敏字段**仍有值标记**（键存在、值为掩码、非空） | 解析结果中 `BgpPeers[].Password == "******"` 且键存在 |
| A-3 | 未配置密码的记录**键不存在**（与"已遮蔽"可区分） | 无密码的 peer 记录里 `Password` 键不存在 |
| A-4 | lint 拦住 `option(Key)` + `type(secret)` | 构造模板 → lint 报"错误" |
| A-5 | lint 警告"疑似漏标 secret" | 含 `password` 关键字但字段未标 secret 的规则 → lint 报"警告" |
| A-6 | 回归基线一致 | `python tests/run_regression.py` → 退出码 0 |
| A-7 | **回归断言非空** | 现有 `sample-vrp.txt` **不含任何 password/cipher** → 必须新增含凭据的 fixture；把模板的 `type(secret)` 改回 `type(str)` 后回归**必须失败** |

### 能力 B

| 编号 | 断言 | 命令 |
|---|---|---|
| B-1 | 8440 个文件全部找到对应原始配置（路径可反推） | 映射检查输出 `命中 8440 / 8440` |
| B-2 | 全量 diff 跑完，产出报告 | `python tools/diff_incumbent.py --all` → 退出码 0，报告文件存在 |
| B-3 | 报告含**每文件 19 列填充率**（段一 · 逐文件） | 报告 JSON 的 `part1_ledger.column_fill_rate` 里每个文件有 19 个列的填充率 |
| B-3b | 报告含**双向字段覆盖**（段二） | `interface_level_fields.coverage == {incumbent:10, netparse:15, matched:4, only_netparse:11, only_incumbent:6}`；清单恰为 4/11/6 项，口径注明“不含子集合”；`VLAN ↔ UserVlan` 在 `content_correspondence_diff_shape` 单列 |
| B-3c | ⭐ 报告含**全局值条数**（段一 · 全局） | `part1_ledger.global_column_value_count` 有 19 个键，每键均有 `incumbent/netparse_before_fix/netparse_after_fix`；「IPv4静态路由明细」修前被标红，修后差值单独计算 |
| B-3d | ⭐ 报告含**19 列全部三栏** | 逐文件与全局的每列均有 `incumbent` / `netparse_before_fix` / `netparse_after_fix`；列键集合与 `COLUMNS` 完全一致，不只检查第 10 列 |
| B-4 | 报告含**差异样本** | 每列至少 1 条差异样本（若该列有差异） |
| B-5 | 报告含**有证据的根因分类** | 每个自动根因附模板声明、真实 trace 与真实文件/行号或脱敏样本；`source=None` 不得单独触发分类；join 缺失单列且须证明原始字段已捕获；证据不足输出 `待人工判定` |
| B-6 | 报告**明确回答**"差哪几列、差多少工作量" | 报告有 `workload_estimate` 段，列出每列缺口与补齐人日 |
| B-6b | 报告**同时回答**"netparse 多了什么" | `part2_capability.extra_value_note` 说明多出的字段不在 19 列台账内、但对 CI 建模的价值 |
| B-7 | **只读约束** | diff 跑完后，`E:\配置文件\` 下所有文件的 mtime 与跑之前一致（脚本自检并输出 `READ-ONLY OK`） |
| B-8 | **接口数一致性基准** | `part1_ledger.interface_count_parity.files_mismatched == 0`（实测两侧精确一致） |
| B-9 | **修前模板快照可复现** | `--template baseline-template.cmdb` 跑出的数字与"修前"栏一致 |
| B-10 | **静态路由最新复测进入报告** | 报告记录 `check_route_forms.py`：60/60 文件修后一致；显式出接口（排除 NULL0）150 条，修前丢 150（100%）、修后丢 0；此前 75/72/96% 明确标为“此前样本” |
| B-11 | **抽样月份期望可满足** | `--sample 20` 时 `expected_distinct_months=min(20,available_months)` 且实际 `len(months_covered)` 等于该期望；报告记录按月轮转算法与 `sample_seed` |

---

## 相关文档

- `docs/FINAL-plan.md` —— 总方案（8.3 两个决定性实验）
- `docs/TEMPLATE-GUIDE.md` —— 模板手册（`type(secret)` 语法待补）
- 上一轮方案评估报告（19 项改动判定 + 方案层评估 A~E）
- `E:\配置文件\配置解析\华为\doc\字段解析说明.md` —— incumbent 的输出 schema 权威定义
