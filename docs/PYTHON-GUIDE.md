# Python 调用指南

把 netparse 当库用：嵌入你自己的采集/入库脚本，或写批量解析。零第三方依赖，Python ≥ 3.9。

## 安装与导入

```bash
pip install .        # 仓库根目录执行；之后 import netparse 即可
```

```python
from netparse import parse_template, run       # 核心两步
from netparse import run_str, run_file, run_files   # 显式输入形态（可选）
from netparse import masking                   # 脱敏控制（可选）
```

不安装也可以：把仓库的 `src/` 加进 `sys.path`，或直接 `python -m netparse ...`。

## 核心 API：两步模型 + 四个显式执行函数

```python
colls = parse_template(path)   # ① 编译：.cmdb/.np 文本 → 集合树/正则（一次）
result = run(colls, lines)     # ② 执行：编译产物 + 行列表 → dict（可无限复用）
```

- `parse_template(path)`：`path` 任意目录、相对/绝对均可。返回对象兼容 `list`
  （可迭代集合），并携带 `.registry`（transform 注册表）
- `run(colls, lines)`：`lines` 是**行列表**（严格形态）。
  返回 dict，顶层 key 是集合名。每次 `run` 会复位脱敏统计并重新收集 transform 警告
  （`colls.transform_warnings`）

### 输入形态：四个显式函数（推荐生产代码使用）

| 函数 | 输入 | 说明 |
|---|---|---|
| `run(colls, lines)` | 行列表 | 严格形态 |
| `run_str(colls, text)` | 多行字符串 | 内部自动 splitlines |
| `run_file(colls, path, encoding=)` | 单文件路径 | 路径不存在立刻报错 |
| `run_files(colls, *paths, ...)` | 多文件/目录/通配符 | 批量，见下节 |

`run_str / run_file / run_files` 是**显式声明**——调用处的意图写在函数名里，
不存在"字符串是路径还是内容"的歧义。

### run 的扩展参数

```python
# 字段白名单（解析全量、输出按需；Key 字段自动保留）
result = run(colls, lines, fields={"Interfaces": {"Name", "Type"}, "RouteStatics": None})

# 记录级过滤（解析后、返回前；不动引擎匹配逻辑）
# 条件值三形态：精确值 / "contains:子串" / callable(v)->bool
result = run(colls, lines, where={"Command": "contains:ospf"})          # 简写：应用到全部集合
result = run(colls, lines, where={"Commands": {"Command": "contains:ospf"}})  # 严格：集合→字段→条件
result = run(colls, lines, where={"Command": lambda v: v and v.startswith("display")})

# 统计摘要（返回 (结果, info) 二元组）
result, info = run(colls, lines, stats=True)
# info = {"lines": 行数, "records": {集合名: 条数}, "elapsed": 秒, "filtered": {集合名: 过滤掉条数}}

# 模板加载即自动执行 [__main__] 段（失败抛 TemplateFileError）
colls.selftest          # 段元数据（None = 模板无该段）
```

### run_files：多文件批量（目录/通配符/混传）

```python
from netparse import run_files

# 三种来源形态可任意混传：
b = run_files(colls, "a.log", "b.log")                    # ① 精确文件
b = run_files(colls, r"E:\logs\宝鸡PSR1")                # ② 目录（扫 pattern="*.log"）
b = run_files(colls, r"E:\logs", recursive=True)          # ③ 目录递归
b = run_files(colls, r"E:\logs\\**\\*.log")               # ④ 通配符串（** 递归）
b = run_files(colls, "a.log", r"E:\logs", r"E:\logs\\**\\*.log") # 混传（自动去重）

b = run_files(colls, r"E:\logs", recursive=True,
              encoding="utf-8",          # 逐文件读取编码
              on_error="skip",           # skip / raise / collect
              provenance="file",         # file=合并+每条注入 _file / group=按文件分组
              progress=lambda d, t, p: print(f"{d}/{t} {p}"),
              fields={"Commands": None})

b.stats        # {"files": 142, "files_ok": 140, "records": 230146, "elapsed": 37.2}
b.files_ok     # 成功文件列表
b.errors       # [(路径, 异常摘要)]（on_error="collect" 时非空）
b["Commands"]  # 合并后的记录（每条含 _file=来源文件名）
```

说明：
- 结果 `sorted` 保证确定性——同样输入永远同样解析顺序（CSV 行序可复现）
- 通配空匹配：警告 + 跳过（不炸批量任务）；目录既不存在也不通配 → `FileNotFoundError`
- 性能参考：142 文件 / 523 万行 / 230146 条记录 → 串行 37 秒（26 万行/s）。
  并发实测无收益（GIL），故不提供并发参数

### 模块化模板

`main.np` 内 `from 01-interfaces import *` 组合子模板（`.cmdb` 后缀可省略）；
`parse_template("templates/huawei/main.np")` 加载入口即可，
引用路径相对当前文件、循环引用/同名集合会在加载期报错。

**批量场景必须只编译一次**（编译开销远大于执行）：

```python
from pathlib import Path
from netparse import parse_template, run

colls = parse_template(r"D:\mytpl\huawei.cmdb")        # 编译一次
for cfg in Path(r"E:\配置文件\华为").rglob("*"):
    lines = cfg.read_text(encoding="utf-8", errors="replace").splitlines()
    result = run(colls, lines)                          # 循环内只执行
    print(cfg.name, len(result.get("Interfaces", [])), "个接口")
```

318 台真机实测：编译一次 + 循环执行，8440 文件全量约 490 秒（17 文件/秒），无性能压力。

> **不想写代码**？CLI 已内置文件夹批量：
> `netparse parse tpl.cmdb E:/配置/华为/ E:/out/` —— 每个配置文件生成一个
> 同名 `.json`（子目录结构保留），每个 JSON 就是该文件单独解析的结果。
> 语义与上面的循环一致（模板只编译一次），见 CLI-GUIDE。

## 读取结果

```python
result["Interfaces"][0]
# {'Name': 'GigabitEthernet1/0/0', 'Description': 'TO-...',
#  'Type': '以太光口',                       ← func= 派生字段（transform 生效时）
#  'IpAddress': [{'Address': '10.1.1.1', 'Mask': '255.255.255.252'}],
#  'AdminStatus': 'up'}

# 嵌套结构直接按 dict/list 访问
for itf in result["Interfaces"]:
    if "Bas" in itf:
        print(itf["Name"], itf["Bas"].get("AuthDomain"))

# 序列化入库
import json
json.dump(result, open("out.json", "w", encoding="utf-8"), ensure_ascii=False)
```

**字段缺省语义**：配置里没出现的字段**不会**出现在结果里（引擎不造数据）。
取值一律用 `.get()`，别假设键存在。

## 脱敏控制

默认双层开启（模板 `type(secret)` 精确遮蔽 + 密文形态兜底），落库/外发保持默认：

```python
from netparse import masking

masking.CONFIG.heuristic = False   # 关形态兜底，只留 type(secret) 精确遮蔽
masking.CONFIG.value = "###"       # 自定义掩码串（默认 ******）
# ... parse_template + run ...
masking.CONFIG.reset()             # 用完复位
```

统计：`masking.STATS.l1_hits / l2_hits` —— `l2_hits` 远大于 `l1_hits`
说明模板标注不完整（是模板完整度的度量）。

## transform（`func=`）相关

模板带 `[python_file]` / `[python]` 时：

```python
colls = parse_template("templates/huawei-vrp.np")   # 加载时已执行注册
print(sorted(colls.registry))                          # 可用函数名清单
result = run(colls, lines)
warnings = colls.transform_warnings                    # 本次 run 的 transform 警告
                                                       # （执行异常/派生失败，去重后）
```

警告需要关注：它意味着某字段的派生失败、值已回退为原值。写自己的函数时遵守：

```python
def f(value, record=None):
    # value —— 本字段捕获值（已过类型转换）；无捕获行时为 None
    # record —— 当前记录半成品 dict（跨字段派生从这里取，如 record.get("Name")）
    # 返回 None → 字段留空/保留原值；抛异常会被引擎捕获并记警告
    ...
```

## lint（模板体检）

```python
from netparse import engine

colls = engine.parse_template("xx.cmdb")
issues = engine.lint(colls)        # [(级别, 集合名, 消息), ...]
errors = [x for x in issues if x[0] == "错误"]
```

级别三种：`错误`（引擎会崩或产出损坏）/ `警告` / `提示`（Key 聚合丢弃提醒等，属正常设计）。
CI 门禁：`errors` 非空即失败。

## 模板内容的动态检查（进阶）

```python
colls = parse_template(p)
for c in colls:
    print(c.name, c.type, c.start, len(c.all_fields), len(c.rules))
    for f in c.all_fields.values():
        print("  ", f.path, f.type, "func=" + f.func if f.func else "")
```

可用于自研工具：字段清单导出、模板覆盖率报表、编辑器集成等。

## func 挂在 list/dict 字段（洋葱时序 + 类型契约）

transform 的执行时序由内向外（洋葱模型）：

1. **子字段 func**（如 `Ifs.IfName ... func=f`）：每次写入即触发，
   `f(value, record)`，value=捕获值
2. **dict 字段 func**（`Sub type(dict) func=f`）：commit 时触发一次，
   `f(当前完整dict, record)` → 返回 dict=整体替换 / None=字段清空
3. **list 字段 func**（`Ifs type(list) func=f`）：commit 时触发一次，
   `f(当前完整list[dict], record)` → 返回 list[dict]=整表替换（可过滤/排序/去重）
   / None=字段清空；`[]` 合法（清空）

```python
def filter_down(value, record=None):
    # 挂在 Ifs type(list) 上：value 是完整元素列表，元素字段已被内层 func 处理完
    return [x for x in value if x.get("Status") != "down"]
```

**类型契约**（违约 → 保留原值 + transform 警告，不炸解析）：

- list func 返回必须 `list[dict]`（`[]` 合法）；dict func 返回必须 `dict`
- None 永远合法 = 字段清空（list/dict）/ 留空（普通字段）

## 输入形态与函数速查

| 函数 | 输入 | 典型用途 |
|---|---|---|
| `run(colls, lines)` | 行列表 | 已有行数据 |
| `run_str(colls, text)` | 多行字符串 | SSH 回显粘贴 |
| `run_file(colls, path, encoding=)` | 单文件 | 常规解析 |
| `run_files(colls, *paths, pattern=, recursive=, on_error=, provenance=, progress=, fields=, where=)` | 文件/目录/通配混传 | 批量 → `BatchResult` |

## 错误处理速查

| 异常 | 场景 | 处理建议 |
|---|---|---|
| `TemplateFileError` | `[python_file]` 文件缺失 / `[python]` 段语法错误 | 修复模板或补齐 .py 文件 |
| `KeyError`（字段未声明） | 规则引用未声明字段 | lint 会提前报；按消息补字段声明 |
| `re.error` | 规则正则语法错 | lint 编译期暴露；改正则 |
| `FileNotFoundError` | 模板/配置路径不存在 | 检查路径（任意目录均可，无白名单限制） |
