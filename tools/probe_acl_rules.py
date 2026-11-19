"""探针 v2：ACL 规则方案验证（类型 + 值分离）

修正上一版的问题：源/目的被拆成多组捕获组（Source / SourceGroup / SourceHost），
字段分裂，CMDB 表会很宽。改成「类型一列 + 值一列」。

用法: python tools/probe_acl_rules.py <配置文件>
"""

import os
import re
import sys
from collections import Counter

PROTO = "ip|ipv6|tcp|udp|icmp|igmp|gre|esp|ah|ipsec"

RULE = re.compile(
    r"^\s*rule\s+(?P<Sequence>\d+)\s+(?P<Action>permit|deny)"
    rf"(?:\s+(?P<Protocol>{PROTO}))?"
    r"(?:\s+vpn-instance\s+(?P<VpnInstance>\S+))?"
    r"(?:\s+source\s+(?:(?P<SourceType>user-group|host|ip-address|any)\s+)?"
    r"(?P<Source>\S+)(?:\s+(?P<SourceMask>\S+))?)?"
    r"(?:\s+destination\s+(?:(?P<DestinationType>ip-address|user-group|host|any)\s+)?"
    r"(?P<Destination>\S+)(?:\s+(?P<DestinationMask>\S+))?)?"
    r"(?:\s+destination-port\s+(?:eq|lt|gt|neq)\s+(?P<DestinationPort>\S+))?"
    r"(?:\s+source-port\s+(?:eq|lt|gt|neq)\s+(?P<SourcePort>\S+))?"
    r"\s*$"
)


def main():
    cfg = sys.argv[1]
    lines = open(cfg, encoding="utf-8", errors="replace").readlines()
    rules = [l.rstrip("\n") for l in lines if re.match(r"^\s*rule\s+\d+\s", l)]

    hit = 0
    misses = []
    field_stat = Counter()
    shape_stat = Counter()
    samples = []

    for l in rules:
        m = RULE.match(l)
        if not m:
            misses.append(l)
            continue
        hit += 1
        g = {k: v for k, v in m.groupdict().items() if v is not None}
        for k in g:
            field_stat[k] += 1
        shape = " ".join(
            k for k in ("Protocol", "VpnInstance", "Source", "SourceMask", "SourceType",
                        "Destination", "DestinationMask", "DestinationType",
                        "DestinationPort", "SourcePort") if k in g)
        shape_stat[shape] += 1
        if len(samples) < 8 and ("Source" in g and "Destination" in g):
            samples.append((l.strip(), g))

    print(f"=== 命中率 ===\n  命中 {hit} / {len(rules)} ({hit*100.0/len(rules):.1f}%)，未命中 {len(misses)}")
    for l in misses[:20]:
        print("   MISS:", l.strip()[:100])

    print("\n=== 各字段被抽到的条数 ===")
    for k, n in field_stat.most_common():
        print(f"  {n:>5}  {k}")

    print("\n=== 字段组合形态（这决定 CMDB 表的稀疏度）===")
    for k, n in shape_stat.most_common():
        print(f"  {n:>5}  {k}")

    print("\n=== 源+目的都有的样例 ===")
    for line, g in samples[:5]:
        print(f"  {line[:86]}")
        print(f"      → {g}")


if __name__ == "__main__":
    main()
