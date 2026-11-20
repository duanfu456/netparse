"""探针 v3：lint 报告能不能区分「接口资产级缺口」和「业务配置级缺口」

trace 里带了栈上下文 → 知道每行命令处在哪个集合的上下文里。
接口块内的命令 = 接口资产（CMDB 要）；顶层的命令 = 设备/业务级（多数是业务配置）。

用法: python tools/probe_coverage3.py <模板> <配置文件> [接口集合名]
"""

import os
import re
import sys
from collections import Counter, OrderedDict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))
from netparse import engine as P  # noqa: E402


def skeleton(line):
    s = line.strip()
    s = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<ip>", s)
    s = re.sub(r"\b\d+\b", "<n>", s)
    return s


def family(line):
    head = []
    for t in skeleton(line).split():
        if t.startswith("<"):
            break
        head.append(t)
        if len(head) == 2:
            break
    return " ".join(head) if head else "(空)"


def main():
    tpl, cfg = sys.argv[1], sys.argv[2]
    scope = sys.argv[3] if len(sys.argv) > 3 else "Interfaces"
    colls = P.parse_template(tpl)
    lines = open(cfg, encoding="utf-8", errors="replace").readlines()

    trace = []
    P.run(colls, lines, trace=trace)

    # 按「是否在 scope 集合上下文内」分桶
    in_scope_total, in_scope_hit = Counter(), Counter()
    out_scope_total, out_scope_hit = Counter(), Counter()
    in_miss = OrderedDict()

    for no, line, hit, stack in trace:
        f = family(line)
        inside = scope in stack
        if inside:
            in_scope_total[f] += 1
            if hit:
                in_scope_hit[f] += 1
            else:
                in_miss.setdefault(f, []).append(line.strip())
        else:
            out_scope_total[f] += 1
            if hit:
                out_scope_hit[f] += 1

    it, ih = sum(in_scope_total.values()), sum(in_scope_hit.values())
    ot, oh = sum(out_scope_total.values()), sum(out_scope_hit.values())
    print(f"=== 覆盖率分桶（作用域集合 = {scope}）===")
    print(f"  {scope} 块内 : {ih}/{it} ({ih*100.0/max(it,1):.1f}%)  ← 缺口 = 接口资产信息缺失")
    print(f"  块外(顶层等): {oh}/{ot} ({oh*100.0/max(ot,1):.1f}%)  ← 缺口 = 设备/业务级配置")

    print(f"\n=== {scope} 块内的缺口（按命令族，资产级，优先级高）===")
    rows = sorted(in_scope_total.items(), key=lambda kv: -(kv[1] - in_scope_hit[kv[0]]))
    n = 0
    for f, tot in rows:
        h = in_scope_hit[f]
        if h == tot:
            continue
        n += 1
        if n > 28:
            break
        print(f"  ✗ {f:<30}{tot:>6}{h:>6}{h*100.0/tot:>6.0f}%   {in_miss[f][0][:50]}")

    print(f"\n=== 块外零覆盖的命令族（多半是业务配置，可折叠）===")
    zero = [(f, t) for f, t in out_scope_total.items() if out_scope_hit[f] == 0]
    zero.sort(key=lambda kv: -kv[1])
    print(f"  共 {len(zero)} 个族，合计 {sum(t for _, t in zero)} 行")
    for f, t in zero[:15]:
        print(f"    {t:>5}  {f}")


if __name__ == "__main__":
    main()
