"""块数守恒：模板解析出的块数 vs 配置里真实的块数（按 `#` 切）。

口径必须逐台比，不是总量比 —— 总量相等可能是此消彼长的假象。
"""
import os, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)
from netparse import parse_template, run
import _cfginspect as ci

TPL = os.path.join(HERE, "..", "templates", "huawei-vrp.cmdb")
tpl = parse_template(TPL)

# 块首命令 -> 模板集合名
HEADS = {
    "interface ": "Interfaces",
    "ip vpn-instance ": "VpnInstances",
    "bgp ": "Bgp",
    "acl number ": "Acls",
    "nqa ": "Nqas",
    "ip route-static ": "RouteStatics",
}

def count_real(p):
    """按行首统计真实块数。"""
    c = collections.Counter()
    for ln in ci.load(p):
        for head, name in HEADS.items():
            if ln.startswith(head):
                c[name] += 1
                break
    return c

bad = 0
tot_real, tot_parsed = collections.Counter(), collections.Counter()
for dev in ci.devices(limit=20):
    p = os.path.join(ci.DEFAULT_ROOT, dev)
    real = count_real(p)
    res = run(tpl, ci.load(p))
    parsed = collections.Counter({k: len(v) for k, v in res.items()})
    for name in HEADS.values():
        r, q = real[name], parsed[name]
        tot_real[name] += r
        tot_parsed[name] += q
        if r != q:
            bad += 1
            print("  ✗ %-46s %-16s 真实 %3d / 解析 %3d" % (dev[:46], name, r, q))
print("\n逐台核对 %d 台 × %d 类块" % (20, len(HEADS)))
print("%-16s %8s %8s %s" % ("集合", "真实", "解析", "结论"))
for name in HEADS.values():
    ok = "一致" if tot_real[name] == tot_parsed[name] else "不一致"
    print("%-16s %8d %8d %s" % (name, tot_real[name], tot_parsed[name], ok))
print("\n%s" % ("全部守恒" if bad == 0 else "有 %d 处不守恒" % bad))
