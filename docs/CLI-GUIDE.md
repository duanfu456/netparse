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

## `netparse parse` —— 解析配置文件

```bash
netparse parse <模板路径> <配置文件> [选项]
```

| 参数 | 说明 |
|---|---|
| `template`（位置参数） | `.cmdb` 模板路径，任意目录 |
| `config`（位置参数） | 配置文件路径 |
| `--json` | stdout 输出完整解析 JSON；lint/统计信息走 stderr，方便管道 |
| `--mask-value <串>` | 自定义掩码串（默认 `******`） |
| `--no-mask-heuristic` | 关闭密文形态兜底，只保留模板 `type(secret)` 的精确遮蔽 |

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

**批量解析一个目录**（PowerShell / bash 循环，或用库方式——见 PYTHON-GUIDE.md，批量场景库方式更快：模板只编译一次）。

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

检查内容（节选）：`func=` 引用不存在的 transform 函数、`secret` 字段禁止 `func=`、字段引用了未声明的名字、集合/字段重名、`option(Key)` 缺失提示等。**编辑模板后必跑**；lint 不过的模板不要落库。

退出码：0 = 通过；1 = 有错误（`--strict` 时有警告也返回 1）。

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
