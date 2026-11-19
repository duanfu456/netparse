"""模板语法回归测试

跑 tests/fixtures/sample-vrp.txt，与 sample-vrp.expected.json 逐字段比对。
模板语法一改，这个脚本就该重跑并更新基线。

用法：
    python tests/run_regression.py            # 比对
    python tests/run_regression.py --update   # 用当前输出覆盖基线
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import probe_stack as P  # noqa: E402

TEMPLATE = os.path.join(ROOT, "templates", "huawei-vrp.cmdb")
SAMPLE = os.path.join(HERE, "fixtures", "sample-vrp.txt")
BASELINE = os.path.join(HERE, "fixtures", "sample-vrp.expected.json")


def parse():
    colls = P.parse_template(TEMPLATE)
    with open(SAMPLE, encoding="utf-8") as f:
        lines = f.readlines()
    return P.run(colls, lines)


def diff(path, got, want, out):
    if isinstance(want, dict) and isinstance(got, dict):
        for k in sorted(set(want) | set(got)):
            if k.startswith("_"):
                continue
            diff(f"{path}.{k}", got.get(k), want.get(k), out)
    elif isinstance(want, list) and isinstance(got, list):
        if len(want) != len(got):
            out.append(f"{path}: 长度 {len(got)} != 基线 {len(want)}")
            return
        for i, (g, w) in enumerate(zip(got, want)):
            diff(f"{path}[{i}]", g, w, out)
    elif got != want:
        out.append(f"{path}: 实际 {got!r} != 基线 {want!r}")


def main():
    res = parse()

    if "--update" in sys.argv:
        out = {"_comment": "sample-vrp.txt 用 templates/huawei-vrp.cmdb 解析的期望输出"
                             "（嵌套形状：子集合嵌在父元素里）。模板语法的回归基线。"}
        out.update(res)
        with open(BASELINE, "w", encoding="utf-8") as f:
            f.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
        print(f"基线已更新: {BASELINE}")
        return 0

    with open(BASELINE, encoding="utf-8") as f:
        want = json.load(f)

    problems = []
    diff("", res, want, problems)

    # lint 必须没有错误
    issues = [i for i in P.lint(P.parse_template(TEMPLATE)) if i[0] == "错误"]
    for lvl, coll, msg in issues:
        problems.append(f"lint[{coll}]: {msg}")

    if problems:
        print(f"回归失败，共 {len(problems)} 处差异：")
        for p in problems[:40]:
            print("  -", p)
        return 1

    total = sum(len(v) for v in res.values())
    print(f"回归通过：{len(res)} 个集合 / {total} 条记录，与基线一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
