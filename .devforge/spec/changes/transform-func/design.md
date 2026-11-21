# transform 设计稿：`[python]` / `[python_file]` 双段 + `func=` 字段级函数

> 状态：待用户确认 → 确认后实施
> 关联：FINAL-plan.md 6.2「transform / 派生字段」（高优先级遗留项）

## 1. 目标

模板作者可以为任意字段声明一个 Python 函数，在引擎类型转换之后对该字段值做派生/规范化：

```text
[python_file]
iface_transforms.py

[python]
def kbps_to_m(value, record=None):
    return round(value / 1024, 1) if value else None

[value]
Name type(str) reg(\S+) option(Key)
Type type(str) func=iface_type desc(接口类型)
Mtu  type(int) reg(\d+) func=mtu_norm desc(MTU)
[rule]
...
```

解决的实际痛点（FINAL-plan 6.2）：
- 接口类型推断（现网 21,427 接口 Type 全空）
- `eth-trunk 42` → `Eth-Trunk42`
- 掩码 → 前缀长（255.255.255.252 → /30）
- kbps → Mbps
- NQA 主键统一（跨字段，`record` 参数支持）

## 2. 语法

### 2.1 `[python_file]` 段

- 每行一个路径，**相对本模板文件所在目录**解析（自包含分发）
- 0~N 条，按声明顺序加载
- 加载 = 以独立模块名（`_np_tf_<序号>_<文件名主干>`）`importlib` 加载，
  导出的公开符号（不以 `_` 开头）进入注册表
- **先于所有 `[python]` 段处理**（与段在文件中的物理位置无关——解析器先扫一遍收集）

### 2.2 `[python]` 段

- 段内所有行原样拼接成代码单元，`exec` 到模块级 namespace
- 此时可 `import` 前面 `[python_file]` 已加载的模块（引擎已把对应目录注入 `sys.path`，作用域内恢复）
- 可定义函数/常量，公开符号进入注册表
- 多个 `[python]` 段按顺序共享同一 namespace（后段可用前段定义的函数）

### 2.3 `func=` 字段属性

- `func=名字`：注册表查找，找不到 = **lint 错误**（编译期）
- 执行时机：`convert()` 类型转换**之后**（拿到的是 int/float/bool 已转换值）
- 签名：`def f(value, record=None)`；`record` 是当前记录的半成品 dict
  （依赖同记录其他字段时用，如 NQA 主键统一）
- 失败语义：返回 `None` → **保持原值**；抛异常 → 引擎捕获、保留原值、
  **每字段每次运行记一条警告**（去重：同一字段同一异常信息只记一次）
- `default` 与 `func` 的顺序：`default` 填充在前，`func` 对填充值同样生效
- `secret` 字段禁止 `func=`（lint 错误——防止凭据经函数外泄到普通字段）

## 3. 注册表与查找顺序

1. 内联（`[python]` 段）符号 —— 后定义覆盖先定义
2. 文件符号（`[python_file]` 各模块公开符号）—— 按声明顺序，后者覆盖前者
3. 查找优先级：**内联 > 文件**（就近覆盖原则，与 Python 作用域直觉一致）

重名处理：不同来源定义同名公开函数 → lint **警告**（提示覆盖关系）。

## 4. 失败与缓存

- `python_file` 缺失/语法错 → `parse_template` 抛明确异常（`TemplateError`，
  含绝对路径与原因）——模板跑不起来比静默跑错好
- 函数运行时异常 → 不中断解析（见 2.3 失败语义），lint 汇总警告
- **mtime 缓存失效**（编辑器 900ms 静默 lint 场景）：注册表按
  `(模板路径, 各 python_file 的 mtime)` 为 key 缓存；任一 py 文件 mtime 变化 → 重新加载
  （`importlib.reload` 对文件模块、重新 `exec` 对内联块）

## 5. lint 检查清单（新增）

| 检查 | 级别 |
|---|---|
| `func=` 引用的名字不在注册表 | 错误 |
| `python_file` 路径不存在 / 语法错误 | 错误（parse 期抛出） |
| `secret` 字段声明 `func=` | 错误 |
| 跨来源函数重名（覆盖） | 警告 |
| 函数运行时异常（运行期汇总） | 警告 |
| `python_file` 解析后的绝对路径 | 提示（可见性即约束） |

安全模型说明：不引入沙箱。信任假设 = "能改模板/py 的人 = 能改仓库的人"
（与 `.gitconfig`/`Makefile` 同级）。`python_file` 的解析路径在 lint 输出中强制可见。

## 6. 编辑器联动

- lint tree 每字段带 `func` 名 → `${` 补全提示里显示
- `[python]` / `[python_file]` 段头进入折叠与高亮段类型
- （后续可选）"试运行函数"按钮：输入样例值 → 显示输出

## 7. 不做的事

- 不做沙箱（见 §5 安全模型）
- 不做 `func=lambda ...` 内联表达式
- 不做多注册表命名空间（单注册表 + lint 重名警告足够）
- 本期不打包 SEA 适配（`python_file` 在 exe 中的路径语义记入待办）

## 8. 实施拆分

| 步骤 | 内容 | 验证 |
|---|---|---|
| T1 | 引擎：段识别 + python_file 加载 + 注册表 | 单测：双段模板编译出注册表 |
| T2 | 引擎：convert 后执行 func + 失败语义 | 单测：正常/None/异常三路径 |
| T3 | lint：5 条新检查 | 单测：每条检查一个用例 |
| T4 | 回归 + 守恒 + 真机探针（华为 20 台 iface_type 抽查） | 全绿 + Type 填充率报告 |
| T5 | 编辑器联动 + 文档（TEMPLATE-GUIDE 场景 16） | 手验 |
