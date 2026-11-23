# [__main__] 自测试段 — 完整说明

> 适用：netparse 0.4+（引擎 `[__main__]` 段）、VSCode 插件 0.4+（CodeLens 运行）。
> 配套范例：`main.np` 尾部就是实际使用的自测试段。

---

## 一、它是什么

`.cmdb` 文件尾部的 `[__main__]` 段是一段**完整的 Python 代码**。
**引擎加载模板时自动执行一遍**：

- 执行成功 → 模板可用（这就是冒烟测试）
- 任何异常（含 assert 失败）→ 显式报错 `[__main__] 自测试失败: ...`，解析不进行

设计目的：**改模板必看测试**——写错正则、改坏字段这类"静默不命中"的问题，
加载时就爆出来，不用等解析结果不对再回头排查。

---

## 二、怎么写

### 形态 0：expect= 声明式断言（★ 推荐，4 行起步覆盖 80% 场景）

```cmdb
[__main__]
cfgstr = """
sysname SMOKE-TEST-01
#
"""
expect = |
  Device[0].SysName = SMOKE-TEST-01
  Interfaces.count > 0
```

一行一条断言（`集合[索引].字段 = 期望值`），失败报 `期望 X, 实际 Y`。
支持 `[索引]` 多级 / `.count` / `> < >= <=` 比较。
复杂断言用 `def check(res):` 逃生门（expect 全过后执行）。

### 形态 1：def main(colls, run)（逻辑多时）

```text
[__main__]
def main(colls, run):
    # colls = 模板集合（import 进来的组也在里面）
    # run(x) = 跑解析：x 可以是配置文本 / 配置文件路径 / 行列表
    cfgstr = """
sysname SMOKE-TEST-01
#
interface Eth-Trunk1
 description smoke
 ip address 10.1.1.1 255.255.255.0
#
ip route-static 10.9.0.0 255.255.0.0 NULL0
#
"""
    res = run(cfgstr)                        # run 自适应：文本/路径/行列表
    assert res["Device"][0]["SysName"] == "SMOKE-TEST-01"
    itf = res["Interfaces"][0]
    assert itf["Name"] == "Eth-Trunk1"
    assert itf["Type"] == "链路聚合"           # func=iface_type 派生
    assert itf["IpAddress"][0]["Address"] == "10.1.1.1"
    rt = res["RouteStatics"][0]
    assert rt["Prefix"] == "10.9.0.0"
    assert rt["NextHopIface"] == "NULL0"      # NULL0 黑洞 → 出接口字段
    return "冒烟通过: 设备/接口/路由 全部命中"   # 返回文本 = PASS 输出


_result = main(colls, run)                     # 引擎约定：有 main() 就调用它
print("[__main__]", _result)
```

### 形态 2：平铺语句（简单模板直接写）

```text
[__main__]
cfgstr = """
sysname SMOKE-TEST-01
#
"""
res = run(cfgstr)
assert res["Device"][0]["SysName"] == "SMOKE-TEST-01"
print("[__main__] 冒烟通过")
```

### 形态 3：用外部配置文件

```text
[__main__]
cfgpath = "smoke-sample.cfg"                  ← 相对模板目录
with open(cfgpath, encoding="utf-8") as f:
    res = run(colls, f.readlines())
assert len(res["Interfaces"]) > 0
print("[__main__] 外部样例通过:", len(res["Interfaces"]), "接口")
```

---

## 三、段内可用的注入对象

| 注入名 | 类型 | 说明 |
|---|---|---|
| `colls` | 模板对象 | 解析好的集合列表；`run(colls, lines)` 需要它 |
| `run(x)` | 函数 | x = 配置文本 / 配置文件路径 / 行列表（自适应），返回结果 dict |
| `here` | str | 本模板文件所在目录（相对路径拼接用） |
| `engine` | module | netparse 引擎模块（高级用法：`engine.run` / `engine.parse_template`） |
| `print` | 函数 | **输出走 stderr**——stdout 属于 `--json` 管道，不会被污染 |

自己写的 `netparse = r"D:/..."`、`cfgstr`、`cfgpath` 等就是普通 Python 变量
（段内从上到下执行，先后顺序即依赖顺序）。

---

## 四、怎么运行

| 方式 | 命令 | 触发机制 |
|---|---|---|
| CLI | `netparse parse x.cmdb --selftest` | 只加载模板执行段，输出 `[PASS]/[FAIL]`，退出码 0/1 |
| CLI | `netparse parse x.cmdb <配置> --json` | 正常解析同时也会先执行段 |
| exe | 同上（`dist/netparse.exe`） | 一样 |
| VSCode 插件 | 点 `[__main__]` 行上方 **▶ 运行自测试** | 调用 `netparse parse x.cmdb --selftest --json`，结果进输出面板 |

插件设置里 `netparse.execPath` 指定可执行文件路径（exe 完整路径或 PATH 命令名）。

---

## 五、行为规则与坑

1. **段定界**：顶格集合声明行（`Xxx type(...)`）结束该段——段内代码不要顶格写
2. **空行安全**：段内空行不会中断段（引擎豁免 `__main__` 模式）
3. **print 走 stderr**：直接写 `print(...)` 已被引擎重定向，`--json` 输出不受污染
4. **异常即失败**：段内任何异常都会让模板加载失败（错误信息含异常类型与消息）
5. **子模板单独自测**：每组 `.cmdb` 都可以有自己的 `[__main__]`（只测本组）；
   若本组字段用了 `func=`，组文件头部要自己声明
   `[python_file] transforms_huawei.py`（单组调试时无人替你提供）
6. **模块化交互**：加载 `main.np` 只执行 main 的段；加载 `02-routes.np`
   只执行 routes 的段——谁的文件谁的段
7. **不需要写 netparse 路径**：`--selftest` 进程内直接执行段；
   exe/插件路径由调用方决定（PATH / VSCode `netparse.execPath` 设置）

---

## 六、常见错误对照

| 报错 | 原因 | 解法 |
|---|---|---|
| `[__main__] 自测试失败: KeyError: 'Interfaces'` | 测试文本没命中接口规则 | 检查 test 文本的命令拼写/缩进与模板规则是否一致 |
| `func=xxx 未定义` | 单组调试缺共享 transform | 组文件头部加 `[python_file] transforms_huawei.py` |
| `段的语法错误` | Python 代码写错 | 修语法（段内是纯 Python） |
| `缺少 main() 入口` 警告 | 段内定义了代码但无 `main()` | 补 `def main(colls, run):` 或改平铺 |
| `--selftest` 后 JSON 输出带冒烟文本 | print 写到 stdout 了 | 确认用引擎注入的 `print`（自动走 stderr） |
