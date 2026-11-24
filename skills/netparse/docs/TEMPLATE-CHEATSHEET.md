# 模板书写指南（速查版）

> 完整语法 + 16 个场景手册见 `TEMPLATE-GUIDE.md`；本文是**日常书写速查**——
> 写模板时打开对着抄的那种。所有写法均来自真实交付模板（华为 318 台 / 中兴 64 台验证）。

## 模板骨架（最小可用）

```text
[python_file]                      ← 可选：外部 transform 函数（相对本模板的路径）
transforms_iface.py

[python]                           ← 可选：内联 Python（小函数就地写）

[value]
SomeField type(str) reg(\S+) desc(说明)

Xxx type(list) start(^interface\s+) end(^#$) end_indent(0) indent(1) desc(接口)
[value]
Name type(str) reg(\S+) option(Key)
Description type(str) reg(.+)
[rule]
^interface\s+${Name}(?:\s+.*)?\s*$
^ {1}description\s+${Description}\s*$
```

## 集合声明（速查表）

```text
Xxx type(list) start(^interface\s+) end(^#$) end_indent(0) indent(1) desc(说明)
    │       │        │             │          │           │
    │       │        │             │          │           └ 人类可读说明
    │       │        │             │          └ 规则只匹配「恰好 1 空格缩进」的行
    │       │        │             └ 只有顶格的 # 才关本层（分隔符自带缩进时必加）
    │       │        └ 进入条件（正则）
    │       └ list = 一条条记录；dict = 折叠成对象；secret 字段用 type(secret)
    └ 集合名（大驼峰）
```

| 声明 | 什么时候用 |
|---|---|
| `type(list)` | 多条记录（接口、路由、ACL） |
| `type(dict)` | 每接口一条（Bas、QosCar） |
| `start(...)` | 进入条件；**不写** = 栈底常驻层（规则始终兜底） |
| `end(...)` + `end_indent(n)` | 块结束分隔符；`end_indent(0)`=顶格 `#` 关本层 |
| `indent(n)` | 规则只吃缩进 n 的行（同命令多层出现时分流用）——"进门要几空格" |
| `end_indent(n)` | 本层只被「恰好 n 个前导空格」的 `#` 关闭——"出门的 # 要几空格"。不声明 = 任意 `#` 都能关（旧行为，嵌套视图会跨层误关） |

## 字段声明（速查表）

```text
Name        type(str)  reg(\S+)              option(Key)  desc(接口名)
Type        type(str)  func=iface_type       desc(接口类型)
Mtu         type(int)  reg(\d+)              desc(MTU)
AdminStatus type(str)  default(up) reg(up|down) desc(管理状态)
Pwd         type(secret) reg(.+)             desc(密码，输出为 ******)
```

| 属性 | 说明 |
|---|---|
| `type(...)` | `str`/`int`/`float`/`bool`/`list`/`dict`/`secret` |
| `reg(...)` | 捕获正则（`${X}` 引用时自动复用） |
| `option(Key)` | 复合键：同键记录合并，不再新增 |
| `option(Required)` | 缺此字段的整条记录丢弃 |
| `func=名字` | transform 函数（`[python]`/`[python_file]` 里定义），类型转换后执行 |
| `desc(...)` | 说明（导出文档用） |
| `default(...)` | 没抓到值时填充——**只用于"设备行为确实如此"的字段**（如 AdminStatus up） |

**铁律**：

1. 没抓到的字段**不会出现在输出里**（引擎不造数据，取值用 `.get()`）
2. 末尾不要手写 `(?:\s+.*)?\s*$` —— 引擎**自动补**（规则以 `$` 结尾即触发）
3. 多 target 用 `;` 分隔：`-> In{Cir,Pir};Out{Cir,Pir}`（一条规则同时写两个
   集合/字段组）。不要用空格或逗号——逗号已被 members/assigns 占用；
   target 成员支持引用赋值 `Out{Cir=In.Cir}`（键=捕获组路径）
4. 模块化：文件顶部 `import x` / `from x import *|集合名`（`.np` 后缀可省略，`.cmdb` 兼容）；
   输出裁剪用 `--fields 集合=字段1,字段2`（解析全量、输出按需；Key 字段自动保留）；
   路径相对当前文件；跨文件集合同名=错误；子集合不能脱离父单独 import
5. `secret` 字段不能加 `func=`；`func=` 引用必须已定义（lint 会查）
6. 模板尾部可加 `[__main__]` 自测试段（完整 Python，加载时自动执行）：
   `run(colls, cfg.splitlines())` + assert；print 走 stderr 不污染 JSON；
   失败显式报错——改模板必看测试
7. 精确缩进锚定：正文行前缀写 `^ {1}` / `^ {2}`（华为 1 空格、中兴 2 空格），
   **不要**用 `^\s+`（会把别的缩进层的同名命令误收进来）

## 规则（速查表）

```text
^interface\s+${Name}(?:\s+.*)?\s*$              ← start 行本身也要写一条规则抓 Name
^ {1}description\s+${Description}\s*$
^ {1}no shutdown\s*$          -> AdminStatus=up        ← 常量赋值
^ {1}qos car cir\s+${QosCar.Cir}(?:\s+pir\s+${QosCar.Pir})?...
                                              ← 可选参数段 (?:...)?，顺序固定
^ {1}ip address\s+${IpAddress.Address}\s+${IpAddress.Mask}...
                                              ← 嵌套路径 ${父.子} 自动落位
```

- `${X.Y}`：引用字段声明里的 `reg()`；点路径自动写进嵌套结构
- `-> 字段=常量` / `-> 集合{字段...}`：显式赋值/组装（省略时全部捕获组自动落位）
- 规则匹配顺序 = 声明顺序；从栈顶集合向下找父层

## transform 函数（速查）

```text
[python_file]
transforms_huawei.py            ← 相对本模板的路径；多模板共用/大逻辑放这里

[python]
def kbps_to_m(value, record=None):
    return round(value / 1024, 1) if value else None

[value]
Cir type(int) func=kbps_to_m desc(Mbps)
```

签名**必须** `def f(value, record=None)`：

- `value`：本字段捕获值（已过 type 转换）；字段无捕获行时为 `None`
- `record`：当前记录半成品 dict；跨字段派生从这里取
- 返回 `None` = 字段留空/保留原值；抛异常 = 引擎捕获 + lint 警告

**派生函数标准开头**（字段无捕获行、从别的字段算出来时）：

```python
def iface_type(value, record=None):
    name = value or (record or {}).get("Name")   # 优先捕获值，其次同记录字段
    if not name:
        return None
    ...
```

## 写新模板的工作流

1. **统计形态**：先跑形态统计看命令长什么样（凭印象写规则是模板 bug 的主要来源）
2. **定集合**：接口/路由/ACL 各一个集合；厂商分区结构（中兴 `!<x>`）用分区根命令当 start
3. **写 [value]**：字段名与目标输出对齐；Key 字段想清楚（合并语义）
4. **写 [rule]**：从配置行抄正则；每条规则跑一次 lint
5. **验证三件套**：
   ```bash
   netparse lint xx.np --strict                       # 体检
   netparse parse xx.np 样例配置 --json | jq           # 看输出
   netparse parse xx.np 配置文件夹 out.json            # 整个文件夹批量
   python tools/_probe_conserve.py                      # 块数守恒（真机）
   ```
6. 有 `func=` 的字段：抽样核对派生结果（编辑器右栏 JSON 直接看）

## 常见坑（每条都真实踩过）

| 坑 | 症状 | 正确写法 |
|---|---|---|
| `^\s+` 宽松前缀 | 其他缩进层的同名命令被误收 | `^ {n}` 精确锚定 |
| 忘了尾部放宽 | 命令带尾巴参数时整行失配 | 引擎已自动补（规则以 `$` 结尾即可） |
| `default` 造数据 | 没配置的字段凭空有值 | `default` 只用于设备行为兜底（如 AdminStatus up） |
| 顶层集合写 `option(Key)` 期待合并 | 顶层不合并（语义=外键） | 合并只发生在子集合 |
| `secret` 加 `func=` | lint 错误（防凭据外泄） | 不加；脱敏引擎已兜底 |
| 中兴模板抄华为的 `undo` | `no shutdown` 命中不了 | 开关词按厂商写（中兴是 `no`） |
| 同名函数跨 `python_file` | 后加载覆盖前者 | lint 有警告；改名或合并文件 |

## [__test__.NN] 独立测试段

```text
[__test__.01]
cfgstr = """..."""
expect = |
  集合[0].字段 = 期望值
```

    netparse X.np -t          # 跑全部测试段
    netparse X.np -t 01,03    # 只跑指定编号

`[__main__]` 只放正式逻辑；测试段不带 `-t` 完全不执行（无残留副作用）。
