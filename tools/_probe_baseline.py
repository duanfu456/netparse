"""固化改动前基线，供重写模板后对比。

用法：
    python3 tools/_probe_baseline.py            # 生成/刷新基线
    python3 tools/_probe_baseline.py --compare  # 与基线对比
"""
import os
import sys
import json
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from netparse import parse_template, run

D = "E:\\配置文件\\BRAS&SR\\原始配置\\cfgfile_20251224\\华为"
TPL = os.path.join(HERE, "..", "templates", "huawei-vrp.np")
OUT = os.path.join(HERE, "_probe_baseline.json")

# 固定样本：排序后取前 20 台，保证可复现
FILES = [f for f in sorted(os.listdir(D)) if os.path.isfile(os.path.join(D, f))][:20]


def walk(path):
    """纯缩进切块，返回 [(行号, 命令, 路径列表, 是否块首)]。"""
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    stack = []
    inkey = False
    out = []
    for no, ln in enumerate(lines, 1):
        st = ln.strip()
        if not st:
            continue
        if "public-key-code begin" in st:
            inkey = True
            continue
        if inkey:
            if "public-key-code end" in st:
                inkey = False
            continue
        ind = len(ln) - len(ln.lstrip())
        if st in ("#", "!"):
            continue
        while stack and stack[-1][0] > ind:
            stack.pop()
        if ind == 0:
            stack = [(0, st)]
            out.append((no, st, [st], True))
            continue
        if not stack:
            stack = [(ind, st)]
            out.append((no, st, [st], True))
            continue
        if ind > stack[-1][0]:
            stack.append((ind, st))
            out.append((no, st, [h for _, h in stack], True))
        else:
            out.append((no, st, [h for _, h in stack], False))
    return out


def measure():
    tpl = parse_template(TPL)
    m = {
        "samples": len(FILES),
        "iface_lines": 0,          # 接口块内命令行总数
        "iface_eaten": 0,          # 其中被模板命中的
        "block_counts": {},        # 集合 -> 原文块数
        "parsed_counts": {},       # 集合 -> 解析条数
        "field_fill": {},          # 字段 -> [命中记录数, 总记录数]
    }
    fill = collections.defaultdict(lambda: [0, 0])

    def collect(coll, recs, path=""):
        for fname in coll.fields:
            key = path + fname
            for r in recs:
                fill[key][1] += 1
                v = r.get(fname)
                if v is not None and v != "" and v != [] and v != {}:
                    fill[key][0] += 1
        for ch in coll.children:
            subs = []
            for r in recs:
                v = r.get(ch.name)
                if isinstance(v, list):
                    subs.extend(v)
                elif isinstance(v, dict):
                    subs.append(v)
            if subs:
                collect(ch, subs, path + ch.name + ".")

    for name in FILES:
        p = os.path.join(D, name)
        lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
        trace = []
        res = run(tpl, lines, trace=trace)
        eaten = {no for no, line, hit, stack in trace if hit is not None and line.strip()}
        for no, cmd, path, is_header in walk(p):
            if not path[0].startswith("interface ") or is_header:
                continue
            m["iface_lines"] += 1
            if no in eaten:
                m["iface_eaten"] += 1
        for key, prefix in (("Interfaces", "interface "), ("Acls", "acl number"),
                            ("Nqas", "nqa "), ("VpnInstances", "ip vpn-instance")):
            m["block_counts"][key] = m["block_counts"].get(key, 0) + sum(
                1 for l in lines if l.startswith(prefix))
        for k, v in res.items():
            m["parsed_counts"][k] = m["parsed_counts"].get(k, 0) + len(v)
        for c in tpl:
            recs = res.get(c.name) or []
            if recs:
                collect(c, recs, c.name + ".")

    m["field_fill"] = {k: v for k, v in fill.items()}
    m["iface_coverage"] = round(m["iface_eaten"] / m["iface_lines"] * 100, 2) if m["iface_lines"] else 0
    return m


def show(m):
    print("样本 %d 台" % m["samples"])
    print("接口块内覆盖率 : %d/%d = %.1f%%" % (m["iface_eaten"], m["iface_lines"], m["iface_coverage"]))
    print("块数守恒       : %s" % json.dumps(m["block_counts"], ensure_ascii=False))
    print("解析条数       : %s" % json.dumps(m["parsed_counts"], ensure_ascii=False))
    print("字段填充率（<60%% 单列）:")
    for k in sorted(m["field_fill"]):
        a, b = m["field_fill"][k]
        pct = a / b * 100 if b else 0
        mark = "  <-- 低" if pct < 60 else ""
        print("   %-40s %7d/%-7d %5.1f%%%s" % (k, a, b, pct, mark))


cur = measure()

if "--compare" in sys.argv and os.path.exists(OUT):
    base = json.load(open(OUT, encoding="utf-8"))
    print("=" * 66)
    print("基线 vs 当前")
    print("=" * 66)
    print("接口块内覆盖率 : %.1f%%  ->  %.1f%%   (%+.1f 个百分点)" % (
        base["iface_coverage"], cur["iface_coverage"],
        cur["iface_coverage"] - base["iface_coverage"]))
    print()
    print("字段填充率变化（只列变化 > 5 个百分点的）:")
    keys = set(base["field_fill"]) | set(cur["field_fill"])
    for k in sorted(keys):
        a0, b0 = base["field_fill"].get(k, [0, 0])
        a1, b1 = cur["field_fill"].get(k, [0, 0])
        p0 = a0 / b0 * 100 if b0 else 0
        p1 = a1 / b1 * 100 if b1 else 0
        if abs(p1 - p0) >= 5:
            print("   %-40s %5.1f%% -> %5.1f%%" % (k, p0, p1))
else:
    show(cur)
    if "--save" in sys.argv:
        json.dump(cur, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("\n基线已保存到 %s" % os.path.relpath(OUT, os.path.join(HERE, "..")))
