"""把接口块内的「未命中」分解成两个独立原因：

  A. 命令族根本没建模 —— 属于层级建模缺失
  B. 命令族已建模但正则写法规格太死 —— 属于写法规格缺陷
"""
import os
import re
import sys
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

from netparse import parse_template, run
import _cfginspect as ci

TPL = os.path.join(HERE, "..", "templates", "huawei-vrp.np")
FILES = ci.devices(limit=20)

tpl = parse_template(TPL)

# 收集所有规则里出现过的命令词 + 所有 start 正则里的命令词，用于判「族已建模」。
# start 也要算：`bas` 这类视图入口行由 start 消费，不产生字段，
# 但它确实被模板接管了 —— 不算进去会把它误判成「未建模」。
fam_decl = set()
for c in tpl:
    pats = [r.pattern for r in c.rules]
    if c.start:
        pats.append(c.start)
    for pat in pats:
        for m in re.finditer(r"\\s\*([a-zA-Z][\w-]*)", pat):
            fam_decl.add(m.group(1))
        for m in re.finditer(r"\^ ?(?:\{\d\})?\\s*([a-zA-Z][\w-]*)", pat):
            fam_decl.add(m.group(1))

rows = collections.Counter()
hit = collections.Counter()

for name in FILES:
    p = os.path.join(ci.DEFAULT_ROOT, name)
    lines = ci.load(p)
    trace = []
    run(tpl, lines, trace=trace)
    eaten = {no for no, line, h, stack in trace if h is not None and line.strip()}
    for no, cmd, path, is_header in ci.walk(p):
        if not path[0].startswith("interface ") or is_header:
            continue
        k = ci.fam(cmd)
        rows[k] += 1
        if no in eaten:
            hit[k] += 1

A = B = C = 0
detailA, detailB = [], []
for k, n in rows.items():
    h = hit[k]
    if h >= n:
        C += n
        continue
    toks = k.split()
    if any(t in fam_decl for t in toks[:2]) or k in fam_decl:
        B += n - h
        detailB.append((k, n, h))
    else:
        A += n - h
        detailA.append((k, n, h))

total = sum(rows.values())
print("样本 %d 台 | 接口块内命令行 %d" % (len(FILES), total))
print()
print("A. 命令族完全未建模     %7d 行  %5.1f%%" % (A, A / total * 100))
print("B. 族已建模但规则不匹配   %7d 行  %5.1f%%" % (B, B / total * 100))
print("C. 已命中                %7d 行  %5.1f%%" % (C, C / total * 100))
print()

for label, detail, sub in (("A 类（未建模族）", detailA, A), ("B 类（写法规格）", detailB, B)):
    detail.sort(key=lambda x: -(x[1] - x[2]))
    print("=== %s TOP 20" % label)
    print("%-38s%9s%9s%9s" % ("命令族", "行数", "命中", "未命中"))
    print("-" * 66)
    for k, n, h in detail[:20]:
        print("%-38s%9d%9d%9d" % (k, n, h, n - h))
    print("合计 %d 行\n" % sub)
