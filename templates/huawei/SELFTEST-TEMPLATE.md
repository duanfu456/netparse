# [__main__] 自测试段写法模板（复制到你的 .np 尾部，按需改断言）

> 引擎加载模板时自动执行本段；无异常 = 冒烟通过，任何异常（含 assert
> 失败）= 显式报错。注入对象：`colls`（模板集合，含 import 的组）、
> `run(lines)`（跑解析返回结果 dict）、`here`（模板所在目录）、
> `print`（输出走 stderr，不污染 --json 管道）。

## 推荐写法：def main(colls, run) + 段尾调用

```cmdb
# ── 自测试段（引擎加载本模板时自动执行；失败 = 模板或样例写错）──
[__main__]


def main(colls, run):
    """冒烟测试：按你的模板实际字段改断言。"""
    cfgstr = """
sysname SMOKE-TEST-01
#
interface Eth-Trunk1
 description smoke-test
 ip address 10.1.1.1 255.255.255.0
#
ip route-static 10.9.0.0 255.255.0.0 NULL0
#
"""
    res = run(cfgstr)                       ← run 自适应：文本/路径/行列表

    assert res["Device"][0]["SysName"] == "SMOKE-TEST-01"
    itf = res["Interfaces"][0]
    assert itf["Name"] == "Eth-Trunk1"
    assert itf["Type"] == "链路聚合"
    assert itf["IpAddress"][0]["Address"] == "10.1.1.1"
    rt = res["RouteStatics"][0]
    assert rt["Prefix"] == "10.9.0.0"
    assert rt["NextHopIface"] == "NULL0"
    return "冒烟通过: 设备/接口/路由 全部命中"


# 引擎约定：段内有 main() 就调用它；返回文本即 PASS 输出
_result = main(colls, run)
print("[__main__]", _result)
```

## 简化写法：直接平铺语句（不用 main 函数也可以）

```cmdb
[__main__]
cfgstr = """
sysname SMOKE-TEST-01
#
"""
res = run(cfgstr)
assert res["Device"][0]["SysName"] == "SMOKE-TEST-01"
print("[__main__] 冒烟通过")
```

## 用外部配置文件（不想内嵌文本时）

```cmdb
[__main__]
cfgpath = "smoke-sample.cfg"                ← 相对模板目录（here 可拼绝对路径）
res = run(cfgpath)                          ← 直接传路径，引擎自动读取
assert len(res["Interfaces"]) > 0
print("[__main__] 外部样例通过:", len(res["Interfaces"]), "接口")
```

## 写法要点

1. **配置文本是设备真实命令**，与模板规则逐条对应——断言的字段
   必须有规则来源（引擎不造数据）
2. **断言里用 Key 字段定位**：多条记录的集合先 `res["集合名"]` 拿列表
   再下标；拿不准就先跑一遍把 JSON 打出来对照
3. **Windows 路径用 `r"D:/..."` 原始串**或正斜杠（`\t` 会转义成制表符）
4. `print` 走 stderr——不影响 `--json` 的 stdout 管道
5. `run()` 输入自适应：传配置文本、传文件路径、传行列表都行
6. 改完模板保存即自动执行（exe/CLI/VSCode 插件全部触发）；
   失败信息里带异常类型与消息，直接定位写错的规则或断言
