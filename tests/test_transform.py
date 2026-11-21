# -*- coding: utf-8 -*-
"""[python]/[python_file]/func= 单元测试（T4）"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from netparse import engine

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("  ✗ %s %s" % (name, detail))


def build(tmp, files):
    for name, content in files.items():
        with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
            fh.write(content)
    return os.path.join(tmp, next(iter(files)))


def run_tpl(path, lines):
    colls = engine.parse_template(path)
    res = engine.run(colls, lines)
    return colls, res


with tempfile.TemporaryDirectory() as tmp:
    # ── 组 1：python_file 加载 + func 执行 ──
    p = build(tmp, {
        "t.cmdb": """[python_file]
tf1.py

X type(list) start(^a)
[value]
Name type(str) reg(\\S+) option(Key)
Kind type(str) func=kind_of
[rule]
^a\\s+${Name}(?:\\s+.*)?\\s*$
^ {1}kind\\s+${Kind}(?:\\s+.*)?\\s*$""",
        "tf1.py": "def kind_of(v, r=None):\n    return v or 'B'\n",
    })
    colls, res = run_tpl(p, ["a a1", " kind kindA", "a a2"])
    check("python_file func 执行", res["X"][0].get("Kind") == "kindA" and res["X"][1].get("Kind") == "B")

    # ── 组 2：[python] 内联 + 嵌套路径派生（derive_funcs）──
    p2 = build(tmp, {
        "t2.cmdb": """Y type(list) start(^b)
[python]
def kind_from_name(value, record=None):
    n = value or (record or {}).get("Name")
    return n and n.upper() or None

[value]
Name type(str) reg(\\S+) option(Key)
Kind type(str) func=kind_from_name
[rule]
^b\\s+${Name}(?:\\s+.*)?\\s*$""",
    })
    colls2, res2 = run_tpl(p2, ["b hello"])
    import json as _json
    print("  [debug] p2 exists:", os.path.isfile(p2), "| 内容头40:", open(p2, encoding='utf-8').read()[:40].replace("\n","|"))
    print("  [debug] res2 keys:", list(res2.keys()))
    check("内联 func + 派生补算", res2["Y"][0].get("Kind") == "HELLO")

    # ── 组 3：失败语义 ──
    p3 = build(tmp, {
        "t3.cmdb": """Z type(list) start(^c)
[python]
def boom(v, r=None):
    raise RuntimeError("boom")

def always_none(v, r=None):
    return None

[value]
Name type(str) reg(\\S+) option(Key)
F1 type(str) func=boom
F2 type(str) func=always_none
[rule]
^c\\s+${Name}(?:\\s+.*)?\\s*$
^ {1}f1\\s+${F1}(?:\\s+.*)?\\s*$""",
    })
    colls3, res3 = run_tpl(p3, ["c x", " f1 orig"])
    rec = res3["Z"][0]
    check("异常保留原值", rec.get("F1") == "orig")          # 捕获值路径：异常→保留
    check("None 返回则字段留空", "F2" not in rec)            # 派生路径：None→留空
    warns = getattr(colls3, "transform_warnings", [])
    check("异常产生警告", any("boom" in w for w in warns), str(warns))

    # ── 组 4：lint 检查 ──
    p4 = build(tmp, {
        "t4.cmdb": """W type(list) start(^d)
[value]
Name type(str) reg(\\S+) func=missing_fn
[rule]
^d\\s+${Name}(?:\\s+.*)?\\s*$""",
    })
    colls4 = engine.parse_template(p4)
    errs = [x for x in engine.lint(colls4) if x[0] == "错误"]
    check("lint：func 未定义报错", any("missing_fn" in x[2] for x in errs), str(errs))

    # ── 组 5：python_file 缺失 → 明确报错 ──
    p5 = os.path.join(tmp, "t5.cmdb")
    with open(p5, "w", encoding="utf-8") as fh:
        fh.write("[python_file]\nnope.py\n")
    try:
        engine.parse_template(p5)
        check("python_file 缺失报错", False)
    except engine.TemplateFileError:
        check("python_file 缺失报错", True)

    # ── 组 6：secret + func 禁止 ──
    p6 = build(tmp, {
        "t6.cmdb": """S type(list) start(^e)
[value]
Name type(str) reg(\\S+) option(Key)
Pwd type(secret) reg(.+) func=any_fn
[rule]
^e\\s+${Name}(?:\\s+.*)?\\s*$""",
        "any.py": "def any_fn(v, r=None): return v\n",
    })
    colls6 = engine.parse_template(p6)
    errs6 = [x for x in engine.lint(colls6) if x[0] == "错误"]
    check("lint：secret 禁止 func", any("secret" in x[2] for x in errs6))

    # ── 组 7：多 python_file + import 共存 ──
    p7 = build(tmp, {
        "t7.cmdb": """[python_file]
tf_a.py
tf_b.py

V type(list) start(^f)
[value]
Name type(str) reg(\\S+) option(Key)
Tag type(str) func=combined
[rule]
^f\\s+${Name}(?:\\s+.*)?\\s*$""",
        "tf_a.py": "def base(v, r=None):\n    return 'A'\n",
        "tf_b.py": "from tf_a import base\n\ndef combined(v, r=None):\n    return base(v) + '-B'\n",
    })
    colls7, res7 = run_tpl(p7, ["f n1"])
    check("多文件 + import", res7["V"][0].get("Tag") == "A-B")

print("\ntransform 单元测试：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
