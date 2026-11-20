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

集合用缩进表达层级，每层可声明 `start` / `end` / `[value]` / `[rule]`：

```text
Interfaces type(list) start(^interface\s+) end(^#$) desc(接口集合)
[value]
Name type(str) reg(\S+) desc(名称) option(Key)
Description type(str) reg(.+) desc(描述)
AdminStatus type(str) reg(\S+) desc(管理状态)
[rule]
^interface\s+${Name}\s*$
^\s*description\s+${Description}\s*$
^\s*(undo\s+)?shutdown\s*-> AdminStatus=up
```

裸 `#` / `!` 是**块分隔符**（不是注释），参与 `end` 出栈。

完整语法见 `docs/TEMPLATE-GUIDE.md`。

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
templates/             模板（华为 VRP / Nokia SROS）
tests/                 单元测试与 fixture 回归
```

## 兼容性

`tools/` 下保留了薄 shim，旧命令仍然可用：

```bash
python tools/probe_stack.py <模板> <配置> --json
python tools/diff_incumbent.py --all --jobs 12
```

会有一行 `DeprecationWarning` 提示改用 `netparse` 子命令。

## 测试

```bash
python tests/test_engine_scope.py
python tests/test_masking.py
python tests/test_adapter.py
python tests/run_regression.py
python tests/test_incumbent_diff.py     # 需要生产语料
python tests/test_cli.py
```
