"""适配器映射表最小样例回归（tasks.md 2.1.3）

规矩：
  ① **每条 MAPPING_SPEC 映射都必须有一个最小样例** —— 缺样例 → 本脚本失败
     （映射表是人工维护的，漏一条就等于那一列的结论没人验过）
  ② 每个样例 = (netparse 形状的最小输入, 期望的 incumbent 形状输出)
  ③ 人为改错一条映射的 source → 对应样例必须失败

用法：
    python tests/test_adapter.py        # 退出码 0 = 全过
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "src"))

from netparse.adapters.incumbent import MAPPING_SPEC, to_incumbent_shape  # noqa: E402
import netparse.adapters.incumbent as adapter  # noqa: E402

FAILURES = []
PASSED = 0


def check(name, cond, extra=""):
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAILURES.append(f"{name}{(' :: ' + extra) if extra else ''}")


def eq(name, got, want):
    check(name, got == want, f"实际 {got!r} != 期望 {want!r}")


def empty_shape():
    """适配器输出的空形状（netparse 什么都没抓到时）。"""
    return {"device_name": "", "device_ip": "", "interfaces": [],
            "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}


def with_iface(**kw):
    """只有一个接口的 netparse 输入。"""
    d = {"Interfaces": [{"Name": "Eth-Trunk1"}]}
    d["Interfaces"][0].update(kw)
    return d


# ── 每个映射条目一个最小样例 ──────────────────────────────
# id → (netparse 输入, 期望的 incumbent 输出)
SAMPLES = {
    # 设备级
    "device_name": (
        {"Device": [{"SysName": "SNANK-MC-CMNET-BRAS22-NHXL-X16A"}]},
        {"device_name": "SNANK-MC-CMNET-BRAS22-NHXL-X16A", "device_ip": "",
         "interfaces": [], "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "device_ip": (
        {"Device": [{"RouterId": "111.19.1.6"}]},
        {"device_name": "", "device_ip": "111.19.1.6",
         "interfaces": [], "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),

    # 接口级
    "interfaces[].name": (
        with_iface(Name="GigabitEthernet1/0/0"),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "GigabitEthernet1/0/0"}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "interfaces[].description": (
        with_iface(Name="Eth-Trunk1", Description="TO-[SNANK]-Eth-Trunk2=9*10G"),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1", "description": "TO-[SNANK]-Eth-Trunk2=9*10G"}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),

    # 接口 IP
    "interfaces[].IP.IPV4[].ip": (
        with_iface(Name="Eth-Trunk1", IpAddress=[
            {"Address": "120.192.236.14", "Mask": "255.255.255.252", "Family": "ipv4"}]),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1",
                         "IP": {"IPV4": [{"ip": "120.192.236.14", "mask": "255.255.255.252"}],
                                "IPV6": []}}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "interfaces[].IP.IPV4[].mask": (
        with_iface(Name="Eth-Trunk1", IpAddress=[
            {"Address": "120.192.236.14", "Mask": "255.255.255.252", "Family": "ipv4"}]),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1",
                         "IP": {"IPV4": [{"ip": "120.192.236.14", "mask": "255.255.255.252"}],
                                "IPV6": []}}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "interfaces[].IP.IPV4[].type": (
        with_iface(Name="Eth-Trunk1", IpAddress=[
            {"Address": "111.19.27.126", "Mask": "255.255.255.252",
             "Family": "ipv4", "AddressType": "sub"}]),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1",
                         "IP": {"IPV4": [{"ip": "111.19.27.126", "mask": "255.255.255.252",
                                          "type": "sub"}],
                                "IPV6": []}}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "interfaces[].IP.IPV6[].ip": (
        with_iface(Name="Eth-Trunk1", IpAddress=[
            {"Address": "2409:8070:3A:1::7007", "Mask": "127", "Family": "ipv6"}]),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1",
                         "IP": {"IPV4": [],
                                "IPV6": [{"ip": "2409:8070:3A:1::7007", "mask": "127"}]}}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "interfaces[].IP.IPV6[].mask": (
        with_iface(Name="Eth-Trunk1", IpAddress=[
            {"Address": "2409:8070:3A:1::7007", "Mask": "127", "Family": "ipv6"}]),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1",
                         "IP": {"IPV4": [],
                                "IPV6": [{"ip": "2409:8070:3A:1::7007", "mask": "127"}]}}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),

    # VPN
    "interfaces[].vpn.name": (
        with_iface(Name="Eth-Trunk1", IpBindingVpn="ChinaMobile_CMNET_SN_IMS"),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1",
                         "vpn": {"name": "ChinaMobile_CMNET_SN_IMS"}}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),

    # 静态路由
    "route-static.IPV4[].dest": (
        {"RouteStatics": [{"Prefix": "112.46.205.0", "Mask": "255.255.255.128"}]},
        {"device_name": "", "device_ip": "", "interfaces": [],
         "route-static": {"IPV4": [{"dest": "112.46.205.0", "mask": "255.255.255.128",
                                    "nexthop": "", "interface": "", "vpn": ""}],
                          "IPV6": []},
         "qos-profile": []}),
    "route-static.IPV4[].mask": (
        {"RouteStatics": [{"Prefix": "112.46.205.0", "Mask": "255.255.255.128"}]},
        {"device_name": "", "device_ip": "", "interfaces": [],
         "route-static": {"IPV4": [{"dest": "112.46.205.0", "mask": "255.255.255.128",
                                    "nexthop": "", "interface": "", "vpn": ""}],
                          "IPV6": []},
         "qos-profile": []}),
    "route-static.IPV4[].nexthop": (
        {"RouteStatics": [{"Prefix": "10.0.0.0", "Mask": "255.0.0.0", "NextHop": "10.1.1.1"}]},
        {"device_name": "", "device_ip": "", "interfaces": [],
         "route-static": {"IPV4": [{"dest": "10.0.0.0", "mask": "255.0.0.0",
                                    "nexthop": "10.1.1.1", "interface": "", "vpn": ""}],
                          "IPV6": []},
         "qos-profile": []}),
    "route-static.IPV4[].interface": (
        {"RouteStatics": [{"Prefix": "112.46.205.0", "Mask": "255.255.255.128",
                           "NextHopIface": "Eth-Trunk83.3000"}]},
        {"device_name": "", "device_ip": "", "interfaces": [],
         "route-static": {"IPV4": [{"dest": "112.46.205.0", "mask": "255.255.255.128",
                                    "nexthop": "", "interface": "Eth-Trunk83.3000", "vpn": ""}],
                          "IPV6": []},
         "qos-profile": []}),
    "route-static.IPV4[].vpn": (
        {"RouteStatics": [{"Prefix": "10.0.0.0", "Mask": "255.0.0.0",
                           "VpnInstance": "ChinaMobile_CMNET_SN_IMS"}]},
        {"device_name": "", "device_ip": "", "interfaces": [],
         "route-static": {"IPV4": [{"dest": "10.0.0.0", "mask": "255.0.0.0",
                                    "nexthop": "", "interface": "",
                                    "vpn": "ChinaMobile_CMNET_SN_IMS"}],
                          "IPV6": []},
         "qos-profile": []}),

    # ── 以下是 netparse 侧**没有**对应能力的映射（source=None）──
    # 样例 = 输入里没有该字段 → 输出里也不该有该键（不得合成数据，D9）
    "route-static.IPV6[]": (
        {"RouteStatics": [{"Prefix": "10.0.0.0", "Mask": "255.0.0.0"}]},
        {"device_name": "", "device_ip": "", "interfaces": [],
         "route-static": {"IPV4": [{"dest": "10.0.0.0", "mask": "255.0.0.0",
                                    "nexthop": "", "interface": "", "vpn": ""}],
                          "IPV6": []},
         "qos-profile": []}),
    "qos-profile[]": (
        {"Interfaces": [{"Name": "Eth-Trunk1"}]},
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1"}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "interfaces[].qos-profile.in": (
        with_iface(Name="Eth-Trunk1"),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1"}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "interfaces[].qos-profile.out": (
        with_iface(Name="Eth-Trunk1"),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1"}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "interfaces[].QOS.in.car": (
        with_iface(Name="Eth-Trunk1"),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1"}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "interfaces[].QOS.out.car": (
        with_iface(Name="Eth-Trunk1"),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1"}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "interfaces[].VLAN.type": (
        with_iface(Name="Eth-Trunk1"),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1"}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "interfaces[].VLAN.QinQ[].qinq": (
        with_iface(Name="Eth-Trunk1"),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1"}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "interfaces[].VLAN.user-vlan[].user-vlan": (
        with_iface(Name="Eth-Trunk1", UserVlan=100),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1",
                         "VLAN": {"user-vlan": [{"user-vlan": [100]}]}}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "interfaces[].VLAN.dot1q": (
        with_iface(Name="Eth-Trunk1"),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1"}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "interfaces[].bas.damian": (
        with_iface(Name="Eth-Trunk1"),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1"}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "interfaces[].multicast.type": (
        with_iface(Name="Eth-Trunk1"),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1"}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "interfaces[].traffic-policy.in": (
        with_iface(Name="Eth-Trunk1"),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1"}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
    "interfaces[].traffic-policy.out": (
        with_iface(Name="Eth-Trunk1"),
        {"device_name": "", "device_ip": "",
         "interfaces": [{"name": "Eth-Trunk1"}],
         "route-static": {"IPV4": [], "IPV6": []}, "qos-profile": []}),
}


def test_mapping_spec_shape():
    check("映射表非空", len(MAPPING_SPEC) > 0)
    check(f"映射表条目数 >= 26（实际 {len(MAPPING_SPEC)}）", len(MAPPING_SPEC) >= 26)
    for e in MAPPING_SPEC:
        check(f"映射条目有 path: {e.get('id')}", bool(e.get("path")))
        check(f"映射条目有 root_cause_if_empty: {e.get('id')}",
              e.get("root_cause_if_empty") not in (None, ""))
        check(f"映射条目有 source 键: {e.get('id')}", "source" in e)
        check(f"映射条目有 note: {e.get('id')}", bool(e.get("note")))
    ids = [e.get("id") for e in MAPPING_SPEC]
    check("映射表 id 无重复", len(ids) == len(set(ids)), f"{ids}")


def test_every_mapping_has_sample():
    """缺样例 → 失败（映射表是人工维护的，漏验一条 = 那一列的结论没人看过）。"""
    missing = [e["id"] for e in MAPPING_SPEC if e["id"] not in SAMPLES]
    check("每条映射都有最小样例" + (f"（缺: {missing}）" if missing else ""),
          not missing)
    extra = [k for k in SAMPLES if k not in ids_of(MAPPING_SPEC)]
    check("没有多余的样例" + (f"（多: {extra}）" if extra else ""), not extra)


def ids_of(spec):
    return [e["id"] for e in spec]


def test_samples():
    for mid, (np_in, expect) in SAMPLES.items():
        got = to_incumbent_shape(np_in)
        eq(f"样例 [{mid}] 的映射结果", got, expect)


def test_no_synthesis():
    """D9：适配器不得合成数据 —— 输入没有的，输出不能有。"""
    out = to_incumbent_shape({})
    eq("空输入 → 空形状", out, empty_shape())
    check("空输入不产生 interfaces", out["interfaces"] == [])
    check("空输入不产生 qos-profile 条目", out["qos-profile"] == [])
    # 接口无 IP → 不产生 IP 键
    out2 = to_incumbent_shape({"Interfaces": [{"Name": "Eth-Trunk1"}]})
    check("接口无 IP → 无 IP 键", "IP" not in out2["interfaces"][0])
    # 接口无 VPN → 不产生 vpn 键
    check("接口无 VPN → 无 vpn 键", "vpn" not in out2["interfaces"][0])
    check("接口无 VLAN 来源 → 无 VLAN 空壳", "VLAN" not in out2["interfaces"][0])
    untouched = {"Interfaces": [{"Name": "x", "UserVlan": 100}]}
    import copy
    snapshot = copy.deepcopy(untouched)
    first = to_incumbent_shape(untouched)
    second = to_incumbent_shape(untouched)
    check("适配器重复调用逐字节相同", json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True))
    check("适配器不修改输入", untouched == snapshot)
    check("VLAN 映射不合成 type", "type" not in first["interfaces"][0]["VLAN"])
    check("VLAN 映射不合成 QinQ", "QinQ" not in first["interfaces"][0]["VLAN"])
    check("VLAN 映射不合成 dot1q", "dot1q" not in first["interfaces"][0]["VLAN"])
    # 路由缺 NextHop → 映射成空串（与 incumbent 对齐，design.md 明确允许）
    out3 = to_incumbent_shape({"RouteStatics": [{"Prefix": "1.1.1.0", "Mask": "255.255.255.0"}]})
    eq("路由缺 NextHop → 空串（对齐 incumbent）",
       out3["route-static"]["IPV4"][0]["nexthop"], "")
    eq("路由缺 NextHopIface → 空串（对齐 incumbent）",
       out3["route-static"]["IPV4"][0]["interface"], "")


def test_real_shape_smoke():
    """真实形态冒烟：一个含 IP / VPN / 静态路由 / secondary 地址的输入。"""
    np_in = {
        "Device": [{"SysName": "SNANK-MC-CMNET-BRAS22-NHXL-X16A", "RouterId": "111.19.1.6"}],
        "Interfaces": [{
            "Name": "Eth-Trunk1",
            "Description": "TO-[SNBJI]-Eth-Trunk2=9*10G-UPLINKCR01",
            "IpBindingVpn": "ChinaMobile_CMNET_SN_IMS",
            "IpAddress": [
                {"Address": "120.192.236.14", "Mask": "255.255.255.252", "Family": "ipv4"},
                {"Address": "111.19.27.126", "Mask": "255.255.255.252", "Family": "ipv4",
                 "AddressType": "sub"},
                {"Address": "2409:8070:3A:1::7007", "Mask": "127", "Family": "ipv6"},
            ],
        }],
        "RouteStatics": [{"Prefix": "112.46.205.0", "Mask": "255.255.255.128",
                          "NextHopIface": "Eth-Trunk83.3000", "NextHop": "111.21.81.154"}],
    }
    expect = {
        "device_name": "SNANK-MC-CMNET-BRAS22-NHXL-X16A",
        "device_ip": "111.19.1.6",
        "interfaces": [{
            "name": "Eth-Trunk1",
            "description": "TO-[SNBJI]-Eth-Trunk2=9*10G-UPLINKCR01",
            "vpn": {"name": "ChinaMobile_CMNET_SN_IMS"},
            "IP": {
                "IPV4": [{"ip": "120.192.236.14", "mask": "255.255.255.252"},
                         {"ip": "111.19.27.126", "mask": "255.255.255.252", "type": "sub"}],
                "IPV6": [{"ip": "2409:8070:3A:1::7007", "mask": "127"}],
            },
        }],
        "route-static": {"IPV4": [{"dest": "112.46.205.0", "mask": "255.255.255.128",
                                    "nexthop": "111.21.81.154",
                                    "interface": "Eth-Trunk83.3000", "vpn": ""}],
                          "IPV6": []},
        "qos-profile": [],
    }
    eq("真实形态冒烟", to_incumbent_shape(np_in), expect)
    # 输出必须是可 JSON 序列化的（diff 工具要写临时文件给 build_rows 读）
    try:
        json.dumps(to_incumbent_shape(np_in), ensure_ascii=False)
        check("输出可 JSON 序列化", True)
    except (TypeError, ValueError) as e:
        check("输出可 JSON 序列化", False, repr(e))


def test_source_is_authoritative():
    original = adapter.ENTRY["device_name"]["source"]
    try:
        adapter.ENTRY["device_name"]["source"] = "Device[0].RouterId"
        np_in, expect = SAMPLES["device_name"]
        check("改错source后对应样例确实失败", to_incumbent_shape(np_in) != expect)
    finally:
        adapter.ENTRY["device_name"]["source"] = original
    eq("source恢复后样例恢复", to_incumbent_shape(np_in), expect)


def main():
    for fn in (test_mapping_spec_shape, test_every_mapping_has_sample, test_samples,
               test_no_synthesis, test_real_shape_smoke, test_source_is_authoritative):
        try:
            fn()
        except Exception as e:                   # noqa: BLE001
            import traceback
            FAILURES.append(f"{fn.__name__} 抛异常: {e!r}\n{traceback.format_exc()}")

    print(f"适配器样例回归：{PASSED} 项通过 / {len(FAILURES)} 项失败")
    if FAILURES:
        print("失败明细：")
        for f in FAILURES:
            print("  -", f)
        return 1
    print(f"全部通过（{len(SAMPLES)} 个映射样例 / {len(MAPPING_SPEC)} 条映射）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
