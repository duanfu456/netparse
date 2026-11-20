"""引擎作用域与块分隔符语义验收；不读取生产配置。

重点锁住一条曾经出过真实 bug 的语义：
裸 `#` / `!` 是**块分隔符**，不是注释。华为全量配置 100% 用 `#`
（222 万行抽样：`#` 249282 次、`!` 0 次），若把 `#` 当注释跳过，
模板里声明的 `end(^#$)`` 永远不会生效，层级会一路挂着不收，
后续块里的同名字段（如 aaa 的 description）就被归到上一个接口上
——实测 103 个文件的 NULL0 接口因此凭空多出 ACL 的 description。
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "src"))

import probe_stack as engine  # noqa: E402

FAILURES = []
PASSED = 0


def check(name, condition, extra=""):
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILURES.append(name + ((" — " + extra) if extra else ""))


ACL_TEMPLATE = """\
Acls type(list) start(^acl number\\s+) end(^#$)
[value]
Number      type(int) reg(\\d+) desc(号)
Description type(str) reg(.+) desc(描述)
[rule]
^acl number\\s+${Number}
^\\s*description\\s+${Description}\\s*$

  AclRules type(list) start(^\\s*rule) end(^#$)
  [value]
  Sequence type(int) reg(\\d+) desc(序号)
  Source   type(str) reg(\\S+) desc(源)
  [rule]
  ^\\s*rule\\s+${Sequence}\\s+permit\\s+source\\s+${Source}\\s+\\S+\\s*$

Interfaces type(list) start(^interface\\s+)
[value]
Name        type(str) reg(\\S+) desc(名) option(Key)
Description type(str) reg(.+) desc(描述)
[rule]
^interface\\s+${Name}
^\\s*description\\s+${Description}\\s*$
"""

NO_END_TEMPLATE = """\
Acls type(list) start(^acl number\\s+)
[value]
Number      type(int) reg(\\d+) desc(号)
Description type(str) reg(.+) desc(描述)
[rule]
^acl number\\s+${Number}
^\\s*description\\s+${Description}\\s*$

Interfaces type(list) start(^interface\\s+)
[value]
Name        type(str) reg(\\S+) desc(名) option(Key)
Description type(str) reg(.+) desc(描述)
[rule]
^interface\\s+${Name}
^\\s*description\\s+${Description}\\s*$
"""

# 真实故障形态：ACL 块用 # 结束，紧随其后的 interface 块没有 description。
LEAK_CONFIG = """\
acl number 2000
 description SSH-Acl-Control
 rule 15 permit source 211.137.133.232 0.0.0.7
#
interface NULL0
#
"""


def run(template_text, config_text):
    handle, path = tempfile.mkstemp(suffix=".cmdb")
    os.close(handle)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(template_text)
        colls = engine.parse_template(path)
    finally:
        os.unlink(path)
    trace = []
    result = engine.run(colls, [line + "\n" for line in config_text.splitlines()], trace=trace)
    return result, trace


def main():
    result, trace = run(ACL_TEMPLATE, LEAK_CONFIG)
    interfaces = result.get("Interfaces", [])
    null0 = [it for it in interfaces if it.get("Name", "").lower() == "null0"]
    check("声明 end(^#$)$ 后裸 # 会出栈", bool(null0), "NULL0 接口未产出")
    check("裸 # 不被当注释跳过（ACL 描述不回灌接口）",
          bool(null0) and "Description" not in null0[0],
          "实际：" + repr(null0[0] if null0 else None))
    check("ACL 自己的描述仍正确归位",
          (result.get("Acls") or [{}])[0].get("Description") == "SSH-Acl-Control")
    check("ACL 规则挂在 ACL 下而非接口下",
          bool((result.get("Acls") or [{}])[0].get("AclRules")))
    # 分隔符那一行的栈必须已经收干净：这是「出栈」而非「碰巧没匹配到」的直接证据。
    # 只检查第一处 `#`（ACL 块结束）；第二处 `#` 在 interface 之后，
    # 而本用例里 Interfaces 没有 end 声明，那一帧本就应该留着（见下方向后兼容用例）。
    hash_frames = [item[3] for item in trace if item[1].strip() == "#"]
    check("分隔符行执行时 ACL 层已收干净",
          bool(hash_frames) and hash_frames[0] == ["__root__"],
          "实际：" + repr(hash_frames))

    # 向后兼容：模板没声明 end 时，`#` 不得擅自关层级（仍靠下一个 start 归档）。
    # 判据是「ACL 层在分隔符后仍然活着」——用栈而非接口字段判断，
    # 因为这份配置的 interface 本来就没有 description，用字段判断测不出泄漏。
    legacy, legacy_trace = run(NO_END_TEMPLATE, LEAK_CONFIG)
    legacy_after_hash = [item[3] for item in legacy_trace if item[1].strip() == "#"]
    check("未声明 end 的层不被裸 # 关闭（保持旧行为）",
          bool(legacy_after_hash) and "Acls" in legacy_after_hash[0],
          "实际：" + repr(legacy_after_hash))

    # 非裸 # 长行仍按注释处理，不能误伤模板里的注释内容。
    comment_cfg = "# 这是一行注释\ninterface Eth-Trunk1\n#\n"
    commented, _ = run(ACL_TEMPLATE, comment_cfg)
    check("非裸 # 长行按注释跳过",
          [it.get("Name") for it in commented.get("Interfaces", [])] == ["Eth-Trunk1"])

    # `!` 也是常见分隔符（华为以外厂商），同一套逻辑要认。
    bang_template = ACL_TEMPLATE.replace("end(^#$)", "end(^!$)")
    bang, _ = run(bang_template, LEAK_CONFIG.replace("#", "!"))
    bang_null0 = [it for it in bang.get("Interfaces", [])
                  if it.get("Name", "").lower() == "null0"]
    check("! 同样作为块分隔符生效",
          bool(bang_null0) and "Description" not in bang_null0[0],
          "实际：" + repr(bang_null0))

    # 多层同时结束：Acls → AclRules 要一并收起，不能留残留帧。
    deep = """\
acl number 2000
 rule 15 permit source 1.1.1.1 0.0.0.0
#
interface Eth-Trunk1
 description uplink
#
"""
    deep_result, deep_trace = run(ACL_TEMPLATE, deep)
    deep_if = deep_result.get("Interfaces", [])
    check("嵌套子层随分隔符一并收起",
          len(deep_if) == 1 and deep_if[0].get("Description") == "uplink",
          "实际：" + repr(deep_if))

    # 普通 end 命令（非分隔符）：从栈顶向下找「第一个 end 能匹配」的层收起，
    # 而不是只看栈顶。Nokia 的 interface 块内常有 ipv6/egress 子块，
    # 栈顶是子集合时，父集合的 end 会匹配不到（曾导致 exit 永远关不掉接口块）。
    NESTED_END_TEMPLATE = """\
Outer type(list) start(^outer\\s+) end(^done\\s*$)
[value]
Name type(str) reg(\\S+) desc(名) option(Key)
[rule]
^outer\\s+${Name}

  Item type(list) start(^\\s*item\\s+)
  [value]
  Id type(int) reg(\\d+) desc(编号)
  [rule]
  ^\\s*item\\s+${Id}\\s*$
"""
    # 场景：outer 块内先有 item 子集合压栈，随后 done 到来 ——
    # 栈顶是 Item（end 为 None），旧逻辑只查栈顶会漏掉 done → Outer 永不关闭。
    nested_cfg = "outer o1\n item 1\n item 2\ndone\n"
    nested, nested_trace = run(NESTED_END_TEMPLATE, nested_cfg)
    check("普通 end 从栈顶向下找匹配层（子集合挡顶时不失效）",
          [it["Name"] for it in nested.get("Outer", [])] == ["o1"]
          and [it["Id"] for it in nested["Outer"][0].get("Item", [])] == [1, 2],
          "实际：" + repr(nested.get("Outer")))
    check("end 命令执行后栈已收起",
          nested_trace[-1][3] == ["__root__"],
          "实际：" + repr(nested_trace[-1][3]))

    print("引擎作用域验收：%d 项通过 / %d 项失败" % (PASSED, len(FAILURES)))
    for failure in FAILURES:
        print("  -", failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
