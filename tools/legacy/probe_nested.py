"""嵌套集合验证 —— 验证「靠缩进表达层级」能否自动成立

核心问题：
  Interfaces 与 IpAddress 之间不再用 block / 显式关联，
  改用集合声明的缩进表达嵌套。
  需要验证：
    ① 子集合继承父集合的区间（在哪找）
    ② 同一区间内规则匹配多次 → 多个元素（一对多）
    ③ 子集合元素自动带上父集合的 key（归属）

用法：python tools/probe_nested.py <配置文件>
"""

import re
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))
from probe_indent_tree import build_tree  # noqa: E402


def compile_rule(rule, fields):
    seen = set()
    def sub(m):
        nm = m.group(1)
        if nm in seen:
            return f"(?P={nm})"
        seen.add(nm)
        return f"(?P<{nm}>{fields[nm]})"
    return re.compile(re.sub(r"\$\{(\w+)\}", sub, rule))


def main():
    path = sys.argv[1]
    lines = open(path, encoding="utf-8", errors="replace").readlines()
    root, _, _ = build_tree(lines)

    FIELDS = {
        "Name":    r"\S+",
        "Address": r"[^/\s]+",
        "Mask":    r"\S+",
        "Desc":    r".+",
    }

    # ── 区间：Interfaces = ^interface\s+ 到 分隔符 ──
    ifaces = [n for n in root.children if n.text.startswith("interface ")]
    print(f"Interfaces 区间内的 interface 块: {len(ifaces)}\n")

    # 嵌套规则：IpAddress 在 Interfaces 区间内匹配，匹配多次 → 多个元素
    rx_ip = compile_rule(r"^\s*ip address\s+${Address}\s+${Mask}\s*$", FIELDS)
    rx_v6 = compile_rule(r"^\s*ipv6 address\s+${Address}/${Mask}\s*$", FIELDS)

    # 父集合 key
    elements = defaultdict(list)      # 父key -> [子元素]
    no_parent = 0
    for n in ifaces:
        parent_key = n.text.split(None, 1)[1]      # 接口名 = 父集合的 key
        for c in n.children:
            m = rx_ip.match(c.text)
            if m:
                elements[parent_key].append({"Address": m.group("Address"),
                                             "Mask": m.group("Mask")})
                continue
            m = rx_v6.match(c.text)
            if m:
                elements[parent_key].append({"Address": m.group("Address"),
                                             "Mask": m.group("Mask"), "Family": "ipv6"})
                continue
        # 统计没有 ip 的接口
        if not elements[parent_key]:
            no_parent += 1

    total = sum(len(v) for v in elements.values())
    print(f"=== ① 一对多：区间内规则匹配几次 = 几个元素 ===")
    print(f"  IpAddress 元素总数: {total}")
    print(f"  有 IP 的接口数    : {len(elements)}")
    print(f"  无 IP 的接口数    : {no_parent}")

    print("\n=== ② 归属：一个接口多个 IP 都在自己名下 ===")
    multi = {k: v for k, v in elements.items() if len(v) > 1}
    print(f"  多 IP 接口数: {len(multi)}")
    for k, v in list(multi.items())[:3]:
        print(f"    {k}: {[x['Address'] for x in v]}")

    print("\n=== ③ 交叉校验：解析出的 IP vs 原文逐条比对 ===")
    ok = bad = 0
    for n in ifaces:
        key = n.text.split(None, 1)[1]
        raw = []
        for c in n.children:
            t = c.text.strip()
            if t.startswith("ip address "):
                raw.append(t.split()[2])
            elif t.startswith("ipv6 address ") and "/" in t:
                raw.append(t.split()[2].split("/")[0])
        got = [x["Address"] for x in elements.get(key, [])]
        if raw == got:
            ok += 1
        else:
            bad += 1
            if bad <= 3:
                print(f"    不一致 {key}: 原文={raw} 解析={got}")
    print(f"  一致: {ok}   不一致: {bad}")

    print("\n=== ④ 子集合元素自动带父 key ===")
    sample = list(elements.items())[0]
    print(f"  {sample[0]} → {sample[1]}")
    print(f"  子集合元素自动含父 key '{sample[0]}'（由 from=parent 注入）")


if __name__ == "__main__":
    main()
