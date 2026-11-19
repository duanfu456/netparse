"""探针：命令覆盖分析

回答三个问题：
  1. 配置里有哪些命令形态？各多少条？
  2. 哪些行没被模板的任何 start/rule 命中？（= 覆盖缺口）
  3. 缺口的"命令骨架"分布如何？

用法: python tools/probe_coverage.py <模板> <配置文件> [--acl]
"""

import os
import re
import sys
from collections import Counter, OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_stack as P  # noqa: E402


# ── 骨架归一化：把参数换成占位符，留下命令结构 ──────────────
def skeleton(line):
    s = line.strip()
    s = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<ip>", s)
    s = re.sub(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b", "<mac>", s)
    s = re.sub(r"\b\d+\b", "<n>", s)
    return s


def cmd_head(s):
    """取命令头：前 1~3 个非参数词"""
    toks = s.split()
    head = []
    for t in toks:
        if re.match(r"^<", t) or t in ("ip", "ipv6"):
            head.append(t)
            continue
        head.append(t)
        if len(head) >= 3:
            break
    return " ".join(head)


def main():
    tpl, cfg = sys.argv[1], sys.argv[2]
    colls = P.parse_template(tpl)

    # 收集所有能识别一行的正则：规则 + start + end
    matchers = []
    for c in colls:
        for r in c.rules:
            try:
                cr = P.compile_rule(r, c)
            except KeyError as e:
                print("!! 规则编译失败:", e)
                continue
            matchers.append((f"{c.name}.rule", cr.rx))
        if c.start_re:
            matchers.append((f"{c.name}.start", c.start_re))
        if c.end_re:
            matchers.append((f"{c.name}.end", c.end_re))

    lines = open(cfg, encoding="utf-8", errors="replace").readlines()

    uncovered = []
    for no, raw in enumerate(lines, 1):
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        hit = None
        for name, rx in matchers:
            if rx.search(line):
                hit = name
                break
        if hit is None:
            uncovered.append((no, line))

    total = sum(1 for l in lines if l.strip())
    print(f"总行数 {total} · 未覆盖 {len(uncovered)} "
          f"({len(uncovered) * 100.0 / max(total, 1):.1f}%)")

    # ── 按命令骨架聚合 ─────────────────────────────
    groups = OrderedDict()
    for no, line in uncovered:
        key = cmd_head(skeleton(line))
        groups.setdefault(key, []).append((no, line))

    print(f"\n=== 未覆盖行的命令骨架（{len(groups)} 类）===")
    for key, items in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:40]:
        print(f"  {len(items):>5}  {key}")
        print(f"         例: {items[0][1].strip()[:100]}")

    # ── 全量命令形态分布（含已覆盖的）────────────────
    heads = Counter()
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        heads[cmd_head(skeleton(s))] += 1
    print(f"\n=== 全量命令形态 top 30（共 {len(heads)} 种）===")
    for key, n in heads.most_common(30):
        print(f"  {n:>6}  {key}")


if __name__ == "__main__":
    main()
