# netparse

网络设备配置解析器。把华为 / 中兴 / Nokia 等设备的**配置文件**按模板 DSL 解析成结构化 JSON，
用于 CMDB 与接口台账。

零运行时第三方依赖，只用 Python 标准库。

## 安装

```bash
pip install -e .
```

不装也能用（把 `src` 加进 `PYTHONPATH` 即可）：

```bash
PYTHONPATH=src python -m netparse --help
```

## 快速开始

### 作为库

```python
from netparse import parse_template, run

colls = parse_template("templates/huawei-vrp.cmdb")
with open("device.cfg", encoding="utf-8") as f:
    result = run(colls, f.readlines())

for itf in result["Interfaces"]:
    print(itf["Name"], itf.get("Description", ""))
```

只要凭据脱敏、不要形态兜底：

```python
from netparse import masking, parse_template, run

masking.CONFIG.heuristic = False     # 只保留模板 type(secret) 的精确遮蔽
colls = parse_template("templates/huawei-vrp.cmdb")
result = run(colls, lines)
masking.CONFIG.reset()               # 复位
```

### 作为命令行

```bash
netparse parse templates/huawei-vrp.cmdb device.cfg            # 集合树 + 条数
netparse parse templates/huawei-vrp.cmdb device.cfg --json     # 完整 JSON
netparse lint  templates/huawei-vrp.cmdb                       # 模板体检
```

## 子命令

| 子命令 | 用途 |
|---|---|
| `netparse parse` | 按模板解析配置文件 |
| `netparse lint` | 模板体检（规则引用、字段声明、凭据标注等） |
| `netparse diff` | 与 incumbent 的 19 列台账三栏对比（需生产只读语料） |
| `netparse route-forms` | 静态路由多形态校验（需生产只读语料） |
| `netparse readonly` | 只读目录树基线采集 / 校验 |

前两个子命令不依赖任何外部资产，后三个需要挂载生产只读目录。

## 模板 DSL 一句话

集合用缩进表达层级，每层可声明 `start` / `end` / `indent` / `end_indent` / `[value]` / `[rule]`：

```text
Interfaces type(list) start(^interface\s+) end(^#$) end_indent(0) indent(1) desc(接口集合)
[value]
Name type(str) reg(\S+) desc(名称) option(Key)
Description type(str) reg(.+) desc(描述)
AdminStatus type(str) reg(up|down) desc(管理状态)
[rule]
^interface\s+${Name}\s*$
^ {1}description\s+${Description}\s*$
^ {1}undo shutdown\s*$          -> AdminStatus=up
^ {1}shutdown\s*$               -> AdminStatus=down
```

- 裸 `#` / `!` 是**块分隔符**（不是注释），参与 `end` 出栈；
  `end_indent(0)` 表示只有顶格的 `#` 才关本层
- `indent(1)` 表示本集合的规则只匹配「恰好 1 个前导空格」的行——
  华为现网缩进 100% 一致（318 台实测），用它能把同一条命令在
  不同缩进层出现时分进不同集合（如 `bas` 视图正文）
- 规则尾部统一放宽 `(?:\s+.*)?\s*$`，避免命令尾巴吃掉整条匹配

完整语法 + 15 个场景手册见 `docs/TEMPLATE-GUIDE.md`；
2026-11 模板审查的实测数据与设计原则见其第 8 节。

## 目录结构

```
src/netparse/
├─ engine.py           嵌套状态栈引擎（parse_template / run / lint / compile_rule）
├─ masking.py          脱敏策略（L1 type(secret) + L2 密文形态兜底）
├─ paths.py            仓库路径解析
├─ adapters/           形状适配器（netparse JSON → incumbent 形状）
└─ cli/                命令行子命令
    ├─ parse.py        parse / lint
    ├─ diff.py         19 列三栏 diff
    ├─ route_forms.py  静态路由校验
    └─ readonly.py     只读基线
tools/                 兼容 shim（旧命令仍可用）+ legacy/ 历史探针
                       + _probe_*.py 测量脚本（守恒 / 缺口 / 基线 / 形态统计）
templates/             模板（华为 VRP 15 集合 / 中兴 M6000 / Nokia SROS）
tests/                 单元测试与 fixture 回归
```

## 当前状态（2026-11-20）

| 指标 | 数值 |
|---|---|
| 华为模板 | 15 个集合（8 个子集合），接口块内覆盖率 **100%**（318 台 / 166 万行实测） |
| 中兴模板 | `zte-m6000.cmdb`（分区根命令模型），64 台实测 0 失败 / 0 密文泄漏 |
| ACL 规则 | 14,723 / 14,723 全解析 |
| 块数守恒 | 6 类块 × 20 台逐台核对全部一致 |
| 脱敏 | 引擎层 `type(secret)` + 密文形态兜底，全量 8440 文件零明文 |
| 台账 19 列 | 8/19 完整对齐，其余列的模板规则已补，**台账口径复测未跑** |

## 兼容性

`tools/` 下保留了薄 shim，旧命令仍然可用：

```bash
python tools/probe_stack.py <模板> <配置> --json
python tools/diff_incumbent.py --all --jobs 12
```

会有一行 `DeprecationWarning` 提示改用 `netparse` 子命令。

## 测试

```bash
python tests/test_engine_scope.py       # 引擎作用域（#/! 分隔符、end 匹配）
python tests/test_masking.py            # 脱敏单元
python tests/test_adapter.py            # 适配器映射样例
python tests/test_cli.py                # CLI 与包结构
python tests/test_diff_incumbent.py     # diff 工具纯逻辑
python tests/run_regression.py          # 端到端回归（--update 覆盖基线）
```

前四个不依赖外部资产；`test_diff_incumbent.py` 与 `netparse diff` 需要生产只读语料。

## 测量与验证工具（写模板必跑）

```bash
python tools/_probe_conserve.py     # 块数守恒：模板解析块数 vs 配置真实块数（逐台核对）
python tools/_probe_gap_split.py    # 接口块内缺口 A/B/C 分解（未建模 / 写法 / 命中）
python tools/_probe_baseline.py --compare   # 与基线对比覆盖率与字段填充率（--save 刷新）
python tools/_probe_acl_verify.py   # ACL 绝对条数核对
python tools/_probe_iface_forms.py  # 命令形态统计（写规则前先跑这个）
```

> 这些探针依赖生产语料目录（`tools/_cfginspect.py` 里的 `DEFAULT_ROOT`），
> 换环境时改那个常量。统计口径是**引擎内埋点的真实匹配结果**，
> 事后用正则重测会把作用域外的同名命令误判成已覆盖。
