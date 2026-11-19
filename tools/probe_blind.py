"""探针：引擎里"实现了但从未被真实模板验证过"的路径 + lint 盲区

重点测：
  1. end 机制（文档写了 4 段，两个模板一次没用过）
  2. inherit（文档说独立顶层集合要用，两个模板一次没用过）
  3. lint 盲区：集合名重复 / 字段名重复 / 正则语法错 / 命名组拼错
  4. 类型转换与 default 的边界
  5. Required 标在嵌套路径上

用法: python tools/probe_blind.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_stack as P  # noqa: E402


def run_case(title, tpl_text, cfg_text, show=True):
    import tempfile
    tf = tempfile.NamedTemporaryFile("w", suffix=".cmdb", delete=False, encoding="utf-8")
    tf.write(tpl_text)
    tf.close()
    cf = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    cf.write(cfg_text)
    cf.close()
    print(f"\n=== {title} ===")
    try:
        colls = P.parse_template(tf.name)
        issues = P.lint(colls)
        errs = [i for i in issues if i[0] == "错误"]
        if errs:
            print("  lint 错误:", errs)
        res = P.run(colls, open(cf.name, encoding="utf-8").readlines())
        if show:
            import json
            print("  输出:", json.dumps(res, ensure_ascii=False))
    except Exception as e:
        print(f"  ❌ 抛异常: {type(e).__name__}: {e}")
    finally:
        os.unlink(tf.name)
        os.unlink(cf.name)


def main():
    # ── 1. end 机制 ────────────────────────────────
    run_case("1. end 机制：子集合靠 end 退出", """\
Blk type(list) start(^block) desc(块)
[value]
Name type(str) reg(\\S+) desc(名)
[rule]
^block\\s+${Name}

  Sub type(list) start(^\\s*sub) end(^\\s*exit) desc(子项)
  [value]
  Val type(str) reg(\\S+) desc(值)
  [rule]
  ^\\s*sub\\s+${Val}\\s*$
""", """\
block A
 sub x
 exit
 sub y
 exit
block B
 sub z
 exit
""")

    # ── 2. inherit ────────────────────────────────
    run_case("2. inherit：独立顶层集合回填父键", """\
Ifaces type(list) start(^interface\\s+) desc(接口)
[value]
Name type(str) reg(\\S+) desc(接口名)
[rule]
^interface\\s+${Name}

Ports type(list) desc(物理口)
[value]
Interface type(str) reg(\\S+) inherit=Ifaces.Name desc(所属接口)
Port      type(str) reg(\\S+) desc(物理口)
[rule]
^\\s*port\\s+(?P<Port>\\S+)\\s*$
""", """\
interface Eth-Trunk1
 port 1/1/1
interface Eth-Trunk2
 port 1/1/2
""")

    # ── 3a. 集合名重复 ──────────────────────────────
    run_case("3a. 集合名重复", """\
A type(list) start(^a) desc(A)
[value]
X type(str) reg(\\S+) desc(x)
[rule]
^a\\s+${X}

A type(list) start(^b) desc(A又来一次)
[value]
Y type(str) reg(\\S+) desc(y)
[rule]
^b\\s+${Y}
""", "a 1\nb 2\n")

    # ── 3b. 字段名重复 ──────────────────────────────
    run_case("3b. 同一集合里字段名重复", """\
A type(list) start(^a) desc(A)
[value]
X    type(str) reg(\\S+) desc(第一个)
X    type(int) reg(\\d+) desc(第二个)
[rule]
^a\\s+${X}
""", "a 1\n")

    # ── 3c. 正则语法错误 ───────────────────────────
    run_case("3c. 规则正则语法错误", """\
A type(list) start(^a) desc(A)
[value]
X type(str) reg(\\S+) desc(x)
[rule]
^a\\s+(?P<X>[unclosed
""", "a 1\n")

    # ── 3d. 命名组拼错 ─────────────────────────────
    run_case("3d. 命名组名拼错（字段表里没有）", """\
A type(list) start(^a) desc(A)
[value]
Name type(str) reg(\\S+) desc(名字)
[rule]
^a\\s+(?P<Nam>\\S+)
""", "a hello\n")

    # ── 4. 类型转换与 default 边界 ─────────────────
    run_case("4. 类型转换：int 字段拿到非数字", """\
A type(list) start(^a) desc(A)
[value]
N  type(int)  reg(\\S+) desc(数字)
B  type(bool) reg(\\S+) desc(开关)
D  type(str)  default(up) desc(默认)
[rule]
^a\\s+${N}
^b\\s+${B}
""", "a notanumber\nb yes\n")

    # ── 5. Required 标在嵌套路径 ───────────────────
    run_case("5. Required 标在嵌套字段上", """\
A type(list) start(^a) desc(A)
[value]
Sub type(dict) desc(子对象)
  Req type(str) reg(\\S+) desc(必填子字段)
[rule]
^a\\s+$
^b\\s+${Sub.Req}\\s*$
""", "a\nb v1\n")

    # ── 6. 命名组 + ${} 混用同一字段 ───────────────
    run_case("6. 命名组与 ${} 指向同一字段", """\
A type(list) start(^a) desc(A)
[value]
X type(str) reg(\\S+) desc(x)
[rule]
^a\\s+${X}\\s+(?P<X>\\S+)
""", "a 1 1\n")


if __name__ == "__main__":
    main()
