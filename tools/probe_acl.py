"""探针：华为 ACL rule 命令形态分析

回答：686 条 rule 有多少种形态？各多少条？模板要补多少条规则？

用法: python tools/probe_acl.py <配置文件>
"""

import os
import re
import sys
from collections import Counter


def normalize(line):
    """把 rule 行归一化成'形态'：保留命令词，参数换成占位符"""
    s = line.strip()
    s = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<ip>", s)
    s = re.sub(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b", "<mac>", s)
    s = re.sub(r"\b\d+\b", "<n>", s)
    # 用户组名 / 自定义名（不含空格的标识符，且不是命令词）
    return s


def classify(line):
    """按语义槽位分类，比纯字符串归一化更能反映'一条规则要抓什么'"""
    s = line.strip()
    toks = s.split()
    out = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if re.match(r"^\d+$", t):
            out.append("<seq>")
        elif t in ("permit", "deny"):
            out.append(t)
        elif t in ("ip", "ipv6"):
            out.append(t)
        elif t in ("source", "destination"):
            out.append(t)
            # 下一段是地址类型
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            if nxt == "any":
                out.append("any")
                i += 1
            elif nxt == "host":
                out.append("host")
                i += 2
            elif nxt == "user-group":
                out.append("user-group")
                i += 2
            elif nxt == "ip-address":
                out.append("ip-address")
                i += 2
            else:
                out.append("<addr>")
                i += 1
        elif t == "destination-port":
            out.append("destination-port")
            i += 2
        elif t == "source-port":
            out.append("source-port")
            i += 2
        else:
            out.append(t)
        i += 1
    return " ".join(out)


def main():
    cfg = sys.argv[1]
    lines = open(cfg, encoding="utf-8", errors="replace").readlines()
    rules = [l for l in lines if re.match(r"^\s*rule\s+\d+\s", l)]

    print(f"rule 行总数: {len(rules)}")

    shapes = Counter()
    for l in rules:
        shapes[classify(l)] += 1
    print(f"语义形态数: {len(shapes)}\n")
    for k, n in shapes.most_common():
        print(f"  {n:>5}  {k}")

    # 哪些 token 组合出现了（参数位置的取值）
    print("\n=== 源地址取值的形态 ===")
    srcs = Counter()
    for l in rules:
        m = re.search(r"\bsource\s+(.*)$", l.strip())
        if m:
            srcs[classify("source " + m.group(1))] += 1
    for k, n in srcs.most_common(12):
        print(f"  {n:>5}  {k}")

    print("\n=== 目的地址取值的形态 ===")
    dsts = Counter()
    for l in rules:
        m = re.search(r"\bdestination\s+(.*)$", l.strip())
        if m:
            dsts[classify("destination " + m.group(1))] += 1
    for k, n in dsts.most_common(12):
        print(f"  {n:>5}  {k}")

    # 前后缀
    print("\n=== 其它修饰词出现频次 ===")
    others = Counter()
    for l in rules:
        for t in l.split():
            if t in ("rule", "permit", "deny", "source", "destination", "ip", "ipv6",
                    "any", "host", "user-group", "ip-address", "destination-port",
                    "source-port", "eq", "gt", "lt", "fragments", "established"):
                continue
            if re.match(r"^[\d.]+$", t):
                continue
            others[t] += 1
    for k, n in others.most_common(25):
        print(f"  {n:>5}  {k}")


if __name__ == "__main__":
    main()
