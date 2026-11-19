"""探针 v2：lint「命令覆盖率」统计 —— 用引擎内埋点验证

覆盖判定必须来自引擎的真实匹配过程：子集合规则只在父层激活时生效，
事后用正则重测会把 aaa/bgp 块里的同名 description 误判成 100% 覆盖。

用法: python tools/probe_coverage2.py <模板> <配置文件>
"""

import os
import re
import sys
from collections import Counter, OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_stack as P  # noqa: E402


def skeleton(line):
    s = line.strip()
    s = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<ip>", s)
    s = re.sub(r"\b\d+\b", "<n>", s)
    return s


def family(line):
    """命令族 = 归一化骨架的前 1~2 个词（参数一律截断）"""
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
    colls = P.parse_template(tpl)
    lines = open(cfg, encoding="utf-8", errors="replace").readlines()

    trace = []
    P.run(colls, lines, trace=trace)

    fam_total = Counter()
    fam_hit = Counter()
    fam_miss = OrderedDict()
    for no, line, hit, _stack in trace:
        f = family(line)
        fam_total[f] += 1
        if hit:
            fam_hit[f] += 1
        else:
            fam_miss.setdefault(f, []).append((no, line, hit))

    total = len(trace)
    covered = sum(fam_hit.values())
    print(f"总行数 {total} · 被规则命中 {covered} ({covered*100.0/total:.1f}%)")

    print(f"\n=== 命令族覆盖率（{len(fam_total)} 族，按缺口排序）===")
    print(f"  {'族':<32}{'总':>6}{'覆盖':>6}{'覆盖率':>8}   未覆盖样例")
    rows = sorted(fam_total.items(), key=lambda kv: -(kv[1] - fam_hit[kv[0]]))
    for f, n in rows[:30]:
        h = fam_hit[f]
        rate = h * 100.0 / n
        ex = fam_miss[f][0][1].strip()[:52] if f in fam_miss else ""
        flag = "✓" if h == n else ("·" if rate >= 60 else "✗")
        print(f"  {flag} {f:<30}{n:>6}{h:>6}{rate:>7.0f}%   {ex}")

    # 零覆盖的族 = 模板完全没碰的命令
    zero = [(f, n) for f, n in fam_total.items() if fam_hit[f] == 0]
    zero.sort(key=lambda kv: -kv[1])
    print(f"\n=== 零覆盖的命令族（模板完全没碰）{len(zero)} 个 ===")
    for f, n in zero[:25]:
        print(f"  {n:>5}  {f:<30} 例: {fam_miss[f][0][1].strip()[:56]}")

    # 同名命令在不同作用域下的真实覆盖（验证"事后重测会误判"）
    print("\n=== 同名命令的真实覆盖（作用域隔离效果）===")
    for cmd in ("description", "password", "shutdown", "name"):
        tot = sum(n for f, n in fam_total.items() if f.split()[0] == cmd)
        hit = sum(h for f, h in fam_hit.items() if f.split()[0] == cmd)
        print(f"  {cmd:<12} 总 {tot:>5} · 引擎实抓 {hit:>5} "
              f"({hit*100.0/max(tot,1):.0f}%)  ← 事后正则重测会显示 100%")


if __name__ == "__main__":
    main()
