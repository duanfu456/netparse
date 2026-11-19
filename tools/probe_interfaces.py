"""多接口归属验证

用真实配置验证：每个接口的子命令是否正确归属到自己的接口对象，
而不是泄漏到上一个接口。输出前若干个接口的解析结果。

用法：python tools/probe_interfaces.py <配置文件路径> [显示数量]
"""

import sys
from collections import Counter

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from probe_indent_tree import build_tree  # noqa: E402


def camel(text):
    parts = [p for p in text.replace("/", " ").replace("-", " ").split() if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


def parse_interface(node):
    """把一个 interface 块的子命令解析成 dict（简化版规则引擎）"""
    out = {"Name": node.text.split(None, 1)[1] if " " in node.text else ""}
    ip_list = []
    for c in node.children:
        t = c.text
        if t == "shutdown":
            out["AdminStatus"] = "down"
        elif t == "undo shutdown":
            out["AdminStatus"] = "up"
        elif t.startswith("description "):
            out["Description"] = t[len("description "):]
        elif t.startswith("mtu "):
            out["Mtu"] = int(t[4:])
        elif t.startswith("ip address "):
            parts = t.split()
            ip_list.append({"Address": parts[2], "Mask": parts[3] if len(parts) > 3 else ""})
        elif t.startswith("ipv6 address "):
            out.setdefault("Ipv6Addresses", []).append({"Address": t[len("ipv6 address "):]})
    if ip_list:
        out["IpAddresses"] = ip_list
    return out


def main():
    path = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    root, _, _ = build_tree(lines)
    ifaces = [n for n in root.children if n.text.startswith("interface ")]
    print(f"共 {len(ifaces)} 个 interface 块\n")

    parsed = [parse_interface(n) for n in ifaces]

    # ── 归属正确性交叉校验 ──
    # 每个接口的 Description / Mtu 是否与配置原文一致（抽查原文比对）
    print("=== 归属抽查：配置原文 vs 解析结果 ===")
    shown = ifaces[:limit] + ifaces[-1:]
    for node in shown:
        p = parse_interface(node)
        raw_desc = [c.text for c in node.children if c.text.startswith("description ")]
        raw_ip = [c.text for c in node.children if c.text.startswith("ip address ")]
        ok_desc = (not raw_desc) or (p.get("Description") == raw_desc[0][len("description "):])
        ok_ip = (not raw_ip) or (
            len(p.get("IpAddresses", [])) == len(raw_ip)
            and all(p["IpAddresses"][i]["Address"] == raw_ip[i].split()[2]
                    for i in range(len(raw_ip)))
        )
        print(f"\n[{ 'OK ' if ok_desc and ok_ip else 'BAD' }] {p['Name']}  (line {node.line_no}, "
              f"{len(node.children)} 条子命令)")
        print(f"     AdminStatus={p.get('AdminStatus')}  Mtu={p.get('Mtu')}  "
              f"IP={[x['Address'] for x in p.get('IpAddresses', [])]}")
        if p.get("Description"):
            print(f"     Description={p['Description'][:70]}")

    # ── 全量统计 ──
    print("\n=== 全量统计（802 个接口）===")
    print("有 AdminStatus 的:", sum(1 for p in parsed if "AdminStatus" in p))
    print("有 Description 的 :", sum(1 for p in parsed if "Description" in p))
    print("有 Mtu 的         :", sum(1 for p in parsed if "Mtu" in p))
    print("有 IpAddresses 的 :", sum(1 for p in parsed if "IpAddresses" in p))
    print("有 Ipv6Addresses 的:", sum(1 for p in parsed if "Ipv6Addresses" in p))
    print("完全空的接口      :", sum(1 for p in parsed if len(p) == 1))

    names = [p["Name"] for p in parsed]
    dup = [k for k, v in Counter(names).items() if v > 1]
    print(f"接口名重复        : {len(dup)} {dup[:5]}")

    # 空接口是谁
    for node, p in zip(ifaces, parsed):
        if len(p) == 1:
            print(f"  空接口: {p['Name']} (line {node.line_no})")


if __name__ == "__main__":
    main()
