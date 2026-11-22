# CLI 使用指南

`netparse` 命令行的全部子命令与参数。安装方式见 README（`pip install .` 后有 `netparse` 命令；不安装可用 `python -m netparse` 等价执行）。

## 快速对照

```bash
netparse parse templates/huawei-vrp.cmdb device.cfg            # 解析：集合树 + 条数
netparse parse templates/huawei-vrp.cmdb device.cfg --json     # 完整 JSON（stdout 纯 JSON）
netparse lint  templates/huawei-vrp.cmdb                       # 模板体检
netparse lint  templates/huawei-vrp.cmdb --strict              # 有警告也非零退出（CI 用）
netparse diff --sample 200                                     # 与生产台账 19 列对比（需只读语料）
netparse readonly --verify                                     # 只读守卫：确认没改过生产文件
```

模板路径**任意目录均可**（相对/绝对都行）；模板若带 `[python_file]`，配套 `.py` 放在模板同目录即可随模板一起分发。

---

## `netparse parse` —— 解析配置文件 / 文件夹

```bash
netparse parse <模板路径> <配置文件 或 配置文件夹> [输出路径] [选项]
```

| 参数 | 说明 |
|---|---|
| `template`（位置参数） | `.cmdb` 模板路径，任意目录 |
| `config`（位置参数） | **配置文件或文件夹**——文件夹 = 递归解析里面全部文件 |
| `output`（位置参数，可选） | 单文件模式 = 输出 JSON 文件路径；文件夹模式 = 输出**目录**（必填） |
| `--json` | stdout 输出完整 JSON（与 output 二选一） |
| `--mask-value <串>` | 自定义掩码串（默认 `******`） |
| `--no-mask-heuristic` | 关闭密文形态兜底，只保留模板 `type(secret)` 的精确遮蔽 |

**四种用法**：

```bash
# ① 单文件，stdout 摘要（人看）
netparse parse tpl.cmdb device.cfg
# ② 单文件，stdout 完整 JSON（管道）
netparse parse tpl.cmdb device.cfg --json
# ③ 单文件，落盘
netparse parse tpl.cmdb device.cfg out.json
# ④ 整个文件夹批量（必须给输出目录）：每个配置文件一个同名 .json
netparse parse tpl.cmdb E:/配置/华为/ E:/out/
```

**批量模式**：输出路径是**目录**。每个配置文件生成一个同名 `.json`
（子目录结构原样保留，扩展名换成 `.json`）：

```text
E:/配置/华为/          →  E:/out/
├─ 设备A.cfg              ├─ 设备A.json
└─ 子目录/设备B.cfg       └─ 子目录/设备B.json
```

- 每个 JSON 的内容 = 该文件单独解析的结果（顶层 key 是集合名），可直接逐文件入库
- 单个文件解析异常不会中断批量：stderr 打 `[fail] 文件名: 原因`，退出码非 0
- 文件夹模式必须给输出目录（缺省报错退出码 1）

**默认输出**（人类可读）：脱敏统计 → lint 提示 → 集合树 → 各集合条数 + 前 2 条样例：

```text
=== lint ===
  [提示] Interfaces: option(Key) 字段 Name 全部为空时，该记录会被丢弃

集合树:
Interfaces             type=list   start='^interface\s+'  fields=92 rules=64 Key=Name
    IpAddress          type=list   start='^\s*ip(v6)? address' ...
RouteStatics           type=list   ... Key=Prefix,Mask,NextHop,VpnInstance

=== 结果 ===
  Interfaces      4 条
  ...
```

**`--json` 输出**：顶层 key 就是集合名，直接 `json.load` 或管道给 `jq`：

```bash
netparse parse templates/huawei-vrp.cmdb device.cfg --json 2>/dev/null | jq ".Interfaces[0]"
```

**批量上千台**：命令行文件夹模式即可（模板只编译一次）；需要更复杂逻辑（如按设备过滤、每台单独落文件）用库方式，见 PYTHON-GUIDE.md。

**脱敏说明**：默认双层开启——模板 `type(secret)` 精确遮蔽（L1）+ 密文形态兜底（L2，华为 `%^%#...%^%#`、中兴 `*NN*base64` / `$5$salt$hash`）。落库/外发场景保持默认即可。

---

## `netparse lint` —— 模板体检

```bash
netparse lint <模板路径> [--json] [--strict]
```

| 参数 | 说明 |
|---|---|
| `--json` | JSON 输出问题列表 |
| `--strict` | 有「警告」也返回非零退出码（默认只有「错误」才非零） |

## 字段白名单（--fields）

输出层裁剪——**解析始终全量**，只裁输出视图（SELECT 列语义）：

    netparse parse huawei.cmdb device.cfg --json --fields "Interfaces=Name,Type,IpAddress;RouteStatics=Prefix,Mask,NextHop"

规则：
- `集合=字段1,字段2` → 只输出点名集合的点名字段
- `集合` 单独出现（不带 =）→ 保留该集合全部字段
- 未列出的集合 → 整个不输出
- **Key 字段自动保留**（记录身份不能丢，如路由的 Prefix/Mask/NextHop）
- 未指定 --fields → 全量输出（默认行为不变）

## 模块化模板

入口模板用 `import` / `from ... import *|集合名` 组合子模板（语法见
TEMPLATE-GUIDE 场景 18）。CLI 用法不变——入口传 main 或任一子模板均可：

    netparse parse templates/huawei/main.cmdb device.cfg --json
    netparse parse templates/huawei/02-routes.cmdb device.cfg  # 单组调试

引用路径相对**当前被解析文件**（模板文件夹整体拷贝引用不失效）。

检查内容（节选）：`func=` 引用不存在的 transform 函数、`secret` 字段禁止 `func=`、字段引用了未声明的名字、集合/字段重名、`option(Key)` 缺失提示等。**编辑模板后必跑**；lint 不过的模板不要落库。

退出码：0 = 通过；1 = 有错误；`--strict` 时有警告也返回 1。

⚠️ **管道退出码坑**：`netparse lint xx.cmdb | tail -1; echo $?` 里 `$?` 取的是
`tail` 的退出码（永远是 0）。要拿 netparse 的退出码，不要接管道，
或用 `PIPESTATUS`（bash：`netparse lint xx.cmdb --strict | tee log; exit ${PIPESTATUS[0]}`）。

---

## `netparse diff` —— 与生产台账 19 列对比

验证 netparse 输出与现有生产管线（incumbent）的逐字段差异。**需要能只读访问生产配置目录**。

```bash
netparse diff --sample 200                    # 分层抽样 200 个文件
netparse diff --all                           # 全量 8440 个
netparse diff --month cfgfile_20251224        # 只跑某个月份
netparse diff --dry-run                       # 只打印范围不真跑
```

| 参数 | 说明 |
|---|---|
| `--all` / `--sample N` | 范围二选一 |
| `--month <名>` | 限定快照月份 |
| `--template <路径>` | 指定"修后"模板（默认当前交付模板） |
| `--out-dir <目录>` | 报告输出目录 |
| `--json` / `--markdown` | 同时落 JSON / MD 报告路径 |
| `--jobs N` | 并行数 |

产出 19 列三栏表（incumbent / 修前 / 修后），列级填充率与差异根因。

**本质**：回归验收工具——回答「netparse 与现有生产管线逐字段差多少」。
三侧对比：incumbent（生产基准）/ 修前（`baseline-template.cmdb` 冻结的旧模板）/
修后（当前模板）。

**四个范围参数的使用场景**：

| 参数 | 场景 | 耗时感 |
|---|---|---|
| `--dry-run` | 跑之前先看会扫到哪些文件/月份，确认范围 | 秒级 |
| `--sample N` | **日常迭代主力**：模板每次改动后的快速回归，分层抽样足以暴露绝大多数字段回归 | 几分钟 |
| `--month <名>` | 定位某批次配置变更的影响面，或某列差异只在特定月份出现时缩小范围 | 视范围 |
| `--all` | 模板大版本定稿/上线前的**终验**，出正式验收报告 | ~490 秒 |

可组合：`--month` 与 `--sample` 叠加 = 某月内抽样；`--jobs 4` 并行加速（结果与并行度无关）。

**典型工作流**：

```bash
netparse diff --dry-run              # 1. 确认范围
netparse diff --sample 200          # 2. 日常回归，发现问题
netparse diff --month cfgfile_20251224 --all   # 3. 定位问题月份
netparse diff --all --jobs 4        # 4. 定稿终验
```

前提：本机挂了生产配置目录（`readonly_guard --verify` 通过），且能只读加载生产
`build_excel.py`。没有生产环境的机器跑不了这个子命令（parse/lint 不受影响）。

---

## `netparse readonly` —— 只读守卫

开发期防止误改生产文件：

```bash
netparse readonly --capture        # 采集基线（对生产目录做一次快照）
netparse readonly --verify         # 与基线比对，报告任何改动
```

跑 `diff` 前建议先 `--verify` 一次。

---

## `netparse route-forms`

静态路由命令形态统计（写 `RouteStatics` 规则前的形态调查工具）。`netparse route-forms --help` 看参数。

---

## 退出码与管道约定

- `--json` 时 **stdout 是纯 JSON**，lint/统计走 stderr → `2>/dev/null` 后可安全接 `jq` / `python -m json.tool`
- `lint` 退出码可直接做 CI 门禁：`netparse lint xx.cmdb --strict || exit 1`
- `parse` 退出码：0 成功；非 0 = 模板解析失败或配置文件不可读

## 常见问题

**Q：模板和 .py 文件放哪都行吗？**
可以。引擎对路径零限制。若模板带 `[python_file]`，路径相对**模板文件自身**解析，把 `.py` 和模板放一起即可。

**Q：解析结果里的密码是明文吗？**
不是。`type(secret)` 与密文形态兜底默认开启，输出里是 `******`。确需关闭形态兜底用 `--no-mask-heuristic`。

**Q：批量上千台怎么跑最快？**
用 Python 库方式（`parse_template` 编译一次 + 循环 `run`），见 PYTHON-GUIDE.md。
