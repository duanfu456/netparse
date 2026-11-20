"""静态路由多形态校验（tasks.md 2.2b.3 / 2.2b.4 / 2.2b.5）。

可重跑的只读校验脚本，回答三个问题：

  ① 边界形态（2.2b.4）：7 种输入形态，netparse 的取值是否与 incumbent 的判据一致
  ② 逐字段一致（2.2b.3）：真实文件上 (dest, mask, interface, nexthop, vpn)
     集合是否与 incumbent 落盘产物**完全相同**
  ③ 丢失率复测（2.2b.5）：带**显式出接口**的路由，修前丢多少、修后丢多少

⚠️ 判定"带显式出接口"时**必须排除 NULL0**（`interface NULL0` 在 60/60 个文件里存在；
   不排除会把分母从 76 污染到 930、丢失率从 94.7% 算成 5.6%）

用法::

    netparse route-forms                     # 边界形态 + 60 文件丢失率
    netparse route-forms --sample 120        # 扩大样本
    netparse route-forms --file "<真实配置>"  # 单文件逐字段比对
"""

import argparse
import json
import os
import re
import sys

from .. import engine
from ..paths import baseline_template, default_template

CUR_TPL = default_template()
BASE_TPL = baseline_template()
RAW_ROOT = "E:////配置文件////BRAS&SR////原始配置"
PARSED_ROOT = "E:////配置文件////BRAS&SR////解析后"

# incumbent 的判据（parse_huawei_vrp.py:509-537）：首 token 不是 IP、也不是 NULL0 → 出接口
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
def looks_like_ip(tok):
    return ":" in tok or bool(_IPV4_RE.match(tok))


def split_iface_nexthop(tokens):
    """incumbent 的判据复刻（只读参考实现，不改 incumbent）。"""
    if not tokens:
        return "", ""
    head = tokens[0]
    if not looks_like_ip(head) and head.upper() != "NULL0":
        return head, (tokens[1] if len(tokens) > 1 else "")
    return "", head


def config_routes(cfg_path):
    """从配置原文按 incumbent 的判据算出 (dest, mask, iface, nexthop, vpn) 列表。"""
    out = []
    for line in open(cfg_path, encoding="utf-8", errors="replace"):
        if not line.startswith("ip route-static "):
            continue
        tokens = line.split()[2:]
        vpn = ""
        if tokens and tokens[0] == "vpn-instance":
            vpn = tokens[1]
            tokens = tokens[2:]
        if len(tokens) < 3:
            continue
        iface, nexthop = split_iface_nexthop(tokens[2:])
        out.append((tokens[0], tokens[1], iface, nexthop, vpn))
    return out


def np_routes(tpl_path, cfg_path):
    """netparse 解析出的 (dest, mask, iface, nexthop, vpn) 集合。"""
    colls = engine.parse_template(tpl_path)
    lines = open(cfg_path, encoding="utf-8", errors="replace").readlines()
    res = engine.run(colls, lines)
    out = set()
    for r in res.get("RouteStatics", []) or []:
        out.add((r.get("Prefix", ""), r.get("Mask", ""),
                 r.get("NextHopIface", ""), r.get("NextHop", ""),
                 r.get("VpnInstance", "")))
    return out


def incumbent_routes(json_path):
    j = json.load(open(json_path, encoding="utf-8"))
    out = set()
    for r in (j.get("route-static") or {}).get("IPV4") or []:
        out.add((r.get("dest", ""), r.get("mask", ""),
                 r.get("interface", ""), r.get("nexthop", ""),
                 r.get("vpn", "")))
    return out


# ── ① 边界形态（2.2b.4）──────────────────────────────────
BOUNDARY = [
    # (配置行, 期望 (NextHop, NextHopIface, VpnInstance, Description))
    ("ip route-static 10.0.0.0 255.0.0.0 10.1.1.1 description pure",
     ("10.1.1.1", "", "", "pure")),
    ("ip route-static 10.0.0.1 255.0.0.0 NULL0",
     ("NULL0", "", "", "")),
    ("ip route-static 10.0.0.2 255.0.0.0 Eth-Trunk188 192.168.0.70 description both",
     ("192.168.0.70", "Eth-Trunk188", "", "both")),
    ("ip route-static 10.0.0.3 255.0.0.0 Eth-Trunk99",
     ("", "Eth-Trunk99", "", "")),
    ("ip route-static 10.0.0.4 255.0.0.0 1.2.3.4 5.6.7.8",
     ("1.2.3.4", "", "", "")),
    ("ip route-static vpn-instance V1 10.0.0.5 255.0.0.0 Eth-Trunk77 172.16.0.1",
     ("172.16.0.1", "Eth-Trunk77", "V1", "")),
    ("ip route-static vpn-instance V2 10.0.0.6 255.0.0.0 10.7.7.7",
     ("10.7.7.7", "", "V2", "")),
]


def check_boundary():
    print("=== ① 边界形态（7 种，incumbent 判据 = 首 token 非 IP 且非 NULL0 → 出接口）===")
    cfg = "\n".join(line for line, _ in BOUNDARY) + "\n"
    tmp = os.path.join(HERE, "_boundary_tmp.cfg")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(cfg)
    try:
        colls = engine.parse_template(CUR_TPL)
        res = engine.run(colls, open(tmp, encoding="utf-8").readlines())
        got = {r.get("Prefix"): r for r in res.get("RouteStatics", []) or []}
        ok = True
        for line, (nh, nhif, vpn, desc) in BOUNDARY:
            toks = line.split()
            prefix = toks[4] if toks[2] == "vpn-instance" else toks[2]
            r = got.get(prefix)
            if r is None:
                print(f"  ❌ {line}\n       → 没抓到这条路由")
                ok = False
                continue
            actual = (r.get("NextHop", ""), r.get("NextHopIface", ""),
                      r.get("VpnInstance", ""), r.get("Description", ""))
            if actual == (nh, nhif, vpn, desc):
                print(f"  ✅ {line}\n       → NextHop={actual[0]!r} NextHopIface={actual[1]!r} "
                      f"VpnInstance={actual[2]!r} Description={actual[3]!r}")
            else:
                print(f"  ❌ {line}\n       → 实际 {actual} != 期望 {(nh, nhif, vpn, desc)}")
                ok = False
        return ok
    finally:
        os.unlink(tmp)


# ── ② ③ 真实文件校验 ────────────────────────────────────
def find_pairs(month="cfgfile_20250824"):
    """(原始配置, incumbent JSON) 对。"""
    raw_dir = os.path.join(RAW_ROOT, month, "华为")
    inc_dir = os.path.join(PARSED_ROOT, month, "华为")
    pairs = []
    for f in sorted(os.listdir(inc_dir)):
        if not f.endswith(".json"):
            continue
        stem = f[:-5]
        raw = os.path.join(raw_dir, stem)
        if not os.path.exists(raw):
            raw = os.path.join(raw_dir, stem + ".txt")
        if not os.path.exists(raw):
            continue
        pairs.append((raw, os.path.join(inc_dir, f)))
    return pairs


def check_real(sample, month="cfgfile_20250824"):
    pairs = find_pairs(month)[:sample]
    print(f"=== ②③ 真实文件校验（{len(pairs)} 个文件，{month}）===")
    exact = 0; mismatch = []
    inc_iface_total = 0; before_iface = 0; after_iface = 0
    lost_examples = []
    for raw, inc in pairs:
        i_set = incumbent_routes(inc)
        a_set = np_routes(CUR_TPL, raw)
        b_set = np_routes(BASE_TPL, raw)
        if i_set == a_set:
            exact += 1
        else:
            only_i = i_set - a_set
            only_a = a_set - i_set
            mismatch.append((os.path.basename(raw), len(only_i), len(only_a),
                             list(only_i)[:2], list(only_a)[:2]))
        # 带显式出接口的路由（排除 NULL0）
        inc_iface = {(d, m, i) for (d, m, i, _n, _v) in i_set if i and i.upper() != "NULL0"}
        inc_iface_total += len(inc_iface)
        after_iface += len({(d, m, i) for (d, m, i, _n, _v) in a_set if i and i.upper() != "NULL0"})
        before_iface += len({(d, m, i) for (d, m, i, _n, _v) in b_set if i and i.upper() != "NULL0"})
        lost = inc_iface - {(d, m, i) for (d, m, i, _n, _v) in a_set if i and i.upper() != "NULL0"}
        if lost and len(lost_examples) < 3:
            lost_examples.append((os.path.basename(raw), list(lost)[:2]))

    print(f"  逐字段集合完全一致的文件: {exact}/{len(pairs)}")
    if mismatch:
        print(f"  ⚠️ 不一致文件（前 5 个）:")
        for m in mismatch[:5]:
            print(f"     {m[0]}: 仅 incumbent 有 {m[1]} 条 / 仅 netparse 有 {m[2]} 条")
            print(f"        仅inc: {m[3]}")
            print(f"        仅net: {m[4]}")
    print(f"  带显式出接口的路由（排除 NULL0）：incumbent {inc_iface_total} 条")
    print(f"     修前 netparse 抓到 {before_iface} 条 → 丢 {inc_iface_total - before_iface} 条 "
          f"({100.0 * (inc_iface_total - before_iface) / max(1, inc_iface_total):.1f}%)")
    print(f"     修后 netparse 抓到 {after_iface} 条 → 丢 {inc_iface_total - after_iface} 条 "
          f"({100.0 * (inc_iface_total - after_iface) / max(1, inc_iface_total):.1f}%)")
    if lost_examples:
        print(f"  修后仍丢的样例: {lost_examples}")
    return exact == len(pairs), inc_iface_total, before_iface, after_iface


def add_arguments(parser):
    parser.add_argument("--sample", type=int, default=60, help="真实文件样本数（默认 60）")
    parser.add_argument("--month", default="cfgfile_20250824")
    parser.add_argument("--file", help="单文件逐字段比对（给真实配置路径）")
    return parser


def main(args):
    if args.file:
        return single_file(args.file)

    ok = check_boundary()
    print()
    exact, total, before, after = check_real(args.sample, args.month)
    print()
    print("RESULT:", "PASS" if (ok and exact) else "FAIL")
    return 0 if (ok and exact) else 1


def single_file(raw):
    """单文件逐字段比对：配置原文 vs 修前/修后模板。

    这个分支原脚本在 docstring 里写了 `--file` 但**根本没实现**，参数被 argparse 拒掉。
    现在补上：直接对真实配置跑两侧模板，把 (dest, mask, iface, nexthop, vpn) 集合打出来，
    并带 incumbent 判据算出的期望集合 —— 差异一眼可见。
    """
    if not os.path.isfile(raw):
        print("文件不存在：%s" % raw, file=sys.stderr)
        return 2
    expected = config_routes(raw)
    after = np_routes(CUR_TPL, raw)
    before = np_routes(BASE_TPL, raw)

    print("=== 单文件逐字段比对 ===")
    print("文件：%s" % raw)
    print("incumbent 判据期望：%d 条" % len(expected))
    print("修后模板：          %d 条" % len(after))
    print("修前模板：          %d 条" % len(before))
    print()
    print("修后 == 期望：%s" % ("是" if after == set(expected) else "否"))
    if after != set(expected):
        only_e = set(expected) - after
        only_a = after - set(expected)
        print("  期望有而修后无（%d）：%r" % (len(only_e), sorted(only_e)[:5]))
        print("  修后有而期望无（%d）：%r" % (len(only_a), sorted(only_a)[:5]))
    return 0 if after == set(expected) else 1


if __name__ == "__main__":       # python -m netparse.cli.route_forms 直跑
    from . import main as _cli_main
    sys.exit(_cli_main(["route-forms"] + sys.argv[1:]))
