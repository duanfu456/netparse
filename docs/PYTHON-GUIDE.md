# Python 调用指南

把 netparse 当库用：嵌入你自己的采集/入库脚本，或写批量解析。零第三方依赖，Python ≥ 3.9。

## 安装与导入

```bash
pip install .        # 仓库根目录执行；之后 import netparse 即可
```

```python
from netparse import parse_template, run       # 核心两步
from netparse import masking                   # 脱敏控制（可选）
```

不安装也可以：把仓库的 `src/` 加进 `sys.path`，或直接 `python -m netparse ...`。

## 核心 API：两步模型

```python
colls = parse_template(path)   # ① 编译：.cmdb 文本 → 集合树/正则（一次）
result = run(colls, lines)     # ② 执行：编译产物 + 配置行列表 → dict（可无限复用）
```

- `parse_template(path)`：`path` 任意目录、相对/绝对均可。返回对象兼容 `list`
  （可迭代集合），并携带 `.registry`（transform 注册表）
- `run(colls, lines)`：`lines` 是**行列表**（`str.splitlines()` 或 `f.readlines()` 均可）。
  返回 dict，顶层 key 是集合名。每次 `run` 会复位脱敏统计并重新收集 transform 警告
  （`colls.transform_warnings`）

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
> `netparse parse tpl.cmdb E://配置//华为// out.json` —— 输出 JSON 带
> `files/ok/failed/failures/results` 汇总结构（results 的 key 是相对路径）。
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
colls = parse_template("templates/huawei-vrp.cmdb")   # 加载时已执行注册
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

## 错误处理速查

| 异常 | 场景 | 处理建议 |
|---|---|---|
| `TemplateFileError` | `[python_file]` 文件缺失 / `[python]` 段语法错误 | 修复模板或补齐 .py 文件 |
| `KeyError`（字段未声明） | 规则引用未声明字段 | lint 会提前报；按消息补字段声明 |
| `re.error` | 规则正则语法错 | lint 编译期暴露；改正则 |
| `FileNotFoundError` | 模板/配置路径不存在 | 检查路径（任意目录均可，无白名单限制） |
