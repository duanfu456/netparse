"""netparse 结果 → incumbent 形状 的**纯结构**适配器（design.md D1 / D9）

为什么需要它
------------
netparse 的模板风格（PascalCase 字段名 + 声明式集合）是它相对 incumbent 的**唯一优势**；
为了比对而把模板改成 incumbent 的 snake_case，等于自毁优势（D1）。
所以加一层**一次性、单向、可测**的纯映射：netparse JSON → incumbent JSON 形状，
然后两侧都喂给 incumbent 的 `build_rows`（口径一致，diff 才可信）。

D9 铁律：**只做「键改名 + 结构重排 + 值搬运」**
  · 禁止推导、计算、默认值填充
  · 禁止把一个字段拆成多个 / 把多个字段合并
  · **禁止**"netparse 没抓到 → 适配器补一个合理默认值"
    （唯一例外是 design.md 明确允许的：路由缺 `NextHop`/`NextHopIface` → 空串 "",
     因为 incumbent 侧就是空串 —— 这是「值搬运」不是合成）

映射表 `MAPPING_SPEC` 是**权威表**，不是注释
--------------------------------------------
代码里的取值**全部经由本表的 `source` 路径解析** ⇒ 改错一条 `source`，
对应样例立刻失败（tasks.md 2.1.3 的验收）。`root_cause_if_empty` 同时充当
D8 的**待验证根因假设**。报告不得直接据此自动判定根因；
自动分类必须另有真实输入、模板声明与真实 trace 证据。

`source` 路径语法
-----------------
    "Device[0].SysName"              根级取值（[0] = 取第 0 个）
    "Interfaces[].Name"              逐集合元素取值
    "Interfaces[].IpAddress[].Address"  两层 list 展开
    可选 filter={"field": "Family", "value": "ipv4"} —— 作用在**最后一层 list 展开**的元素上
"""

# 段二（能力口径）用：netparse 的字段名 → incumbent 的字段名（语义别名，非大小写折叠）
# design.md：比较前必须按语义别名表归一化；PascalCase ↔ snake_case 不能靠折叠大小写
FIELD_ALIASES = {
    "Name": "name",
    "Description": "description",
    "IpAddress": "IP",
    "IpBindingVpn": "vpn",
}

#: 映射表：id → (incumbent 路径, netparse 来源, 台账列, 缺值根因, 备注)
MAPPING_SPEC = [
    # ── 设备级 ───────────────────────────────────────────
    {"id": "device_name", "path": "device_name", "source": "Device[0].SysName",
     "column": "设备名称", "root_cause_if_empty": "字段丢失",
     "note": "顶层设备级；netparse 的 Device 集合是 dict 型"},
    {"id": "device_ip", "path": "device_ip", "source": "Device[0].RouterId",
     "column": "管理IP", "root_cause_if_empty": "字段丢失",
     "note": "顶层设备级"},

    # ── 接口级 ───────────────────────────────────────────
    {"id": "interfaces[].name", "path": "interfaces[].name",
     "source": "Interfaces[].Name", "column": "接口",
     "root_cause_if_empty": "字段丢失",
     "note": "台账主键；两侧按此 join（D11）"},
    {"id": "interfaces[].description", "path": "interfaces[].description",
     "source": "Interfaces[].Description", "column": "接口描述",
     "root_cause_if_empty": "字段丢失",
     "note": "netparse 没有该字段时不产生 description 键（与 incumbent 的键存在性一致）"},

    # ── 接口 IP ──────────────────────────────────────────
    {"id": "interfaces[].IP.IPV4[].ip", "path": "interfaces[].IP.IPV4[].ip",
     "source": "Interfaces[].IpAddress[].Address",
     "filter": {"field": "Family", "value": "ipv4"},
     "column": "端口IPv4", "root_cause_if_empty": "字段丢失",
     "note": "IP 是 dict（IPV4/IPV6 两个列表）"},
    {"id": "interfaces[].IP.IPV4[].mask", "path": "interfaces[].IP.IPV4[].mask",
     "source": "Interfaces[].IpAddress[].Mask",
     "filter": {"field": "Family", "value": "ipv4"},
     "column": "端口IPv4", "root_cause_if_empty": "字段丢失",
     "note": "掩码转 CIDR 由 build_rows 做，适配器不碰"},
    {"id": "interfaces[].IP.IPV4[].type", "path": "interfaces[].IP.IPV4[].type",
     "source": "Interfaces[].IpAddress[].AddressType",
     "filter": {"field": "Family", "value": "ipv4"},
     "column": "端口IPv4", "root_cause_if_empty": "字段丢失",
     "note": "D3：secondary 地址（sub）。主地址无该键、副地址为 'sub' —— "
             "与 incumbent 的键存在性语义逐字一致；修前是模板未覆盖（sub 被正则吃掉）"},
    {"id": "interfaces[].IP.IPV6[].ip", "path": "interfaces[].IP.IPV6[].ip",
     "source": "Interfaces[].IpAddress[].Address",
     "filter": {"field": "Family", "value": "ipv6"},
     "column": "端口IPv6", "root_cause_if_empty": "字段丢失",
     "note": "incumbent 实测 IPv6 元素键是 ip（不是 addr），build_rows 两者都认"},
    {"id": "interfaces[].IP.IPV6[].mask", "path": "interfaces[].IP.IPV6[].mask",
     "source": "Interfaces[].IpAddress[].Mask",
     "filter": {"field": "Family", "value": "ipv6"},
     "column": "端口IPv6", "root_cause_if_empty": "字段丢失",
     "note": "前缀长度（incumbent 侧叫 mask）"},

    # ── VPN ─────────────────────────────────────────────
    {"id": "interfaces[].vpn.name", "path": "interfaces[].vpn.name",
     "source": "Interfaces[].IpBindingVpn", "column": "VPN实例",
     "root_cause_if_empty": "字段丢失",
     "note": "无值则不产生 vpn 键（incumbent 侧也是键缺失）"},

    # ── 静态路由 ─────────────────────────────────────────
    {"id": "route-static.IPV4[].dest", "path": "route-static.IPV4[].dest",
     "source": "RouteStatics[].Prefix", "column": "IPv4静态路由明细",
     "root_cause_if_empty": "字段丢失", "note": "route-static 在 incumbent 侧是 dict 不是 list"},
    {"id": "route-static.IPV4[].mask", "path": "route-static.IPV4[].mask",
     "source": "RouteStatics[].Mask", "column": "IPv4静态路由明细",
     "root_cause_if_empty": "字段丢失", "note": "掩码转 CIDR 由 build_rows 做"},
    {"id": "route-static.IPV4[].nexthop", "path": "route-static.IPV4[].nexthop",
     "source": "RouteStatics[].NextHop", "column": "IPv4静态路由明细",
     "root_cause_if_empty": "字段丢失",
     "note": "「只有出接口」形态下 incumbent 是空串、netparse 是键缺失 → 映射成空串（D9 允许的值搬运）"},
    {"id": "route-static.IPV4[].interface", "path": "route-static.IPV4[].interface",
     "source": "RouteStatics[].NextHopIface", "column": "IPv4静态路由明细",
     "root_cause_if_empty": "整条记录丢失",
     "note": "根因是「整条记录丢失」不是「字段丢失」—— 修前带出接口的路由整条没进结果；"
             "台账第 10 列只收带出接口的路由，所以影响 ≈ 100%"},
    {"id": "route-static.IPV4[].vpn", "path": "route-static.IPV4[].vpn",
     "source": "RouteStatics[].VpnInstance", "column": "IPv4静态路由明细",
     "root_cause_if_empty": "字段丢失", "note": "VPN 实例名"},
    {"id": "route-static.IPV6[]", "path": "route-static.IPV6[]", "source": None,
     "column": "IPv6静态路由明细", "root_cause_if_empty": "模板未覆盖该命令",
     "note": "netparse 模板无 ipv6 route-static 规则；台账第 11 列必然空"},

    # ── 限速三层（缺一不可）──────────────────────────────
    {"id": "qos-profile[]", "path": "qos-profile[]", "source": None,
     "column": "接口入流限速(M)/接口出流限速(M)", "root_cause_if_empty": "模板未覆盖该命令",
     "note": "限速第 1/3 层：顶层命名空间级定义 list[{name,in:{cir},out:{cir}}]"},
    {"id": "interfaces[].qos-profile.in", "path": "interfaces[].qos-profile.in", "source": None,
     "column": "接口入流限速(M)", "root_cause_if_empty": "模板未覆盖该命令",
     "note": "限速第 2/3 层：接口级引用名（值是名字）"},
    {"id": "interfaces[].qos-profile.out", "path": "interfaces[].qos-profile.out", "source": None,
     "column": "接口出流限速(M)", "root_cause_if_empty": "模板未覆盖该命令",
     "note": "限速第 2/3 层：接口级引用名（值是名字）"},
    {"id": "interfaces[].QOS.in.car", "path": "interfaces[].QOS.in.car", "source": None,
     "column": "接口入流限速(M)", "root_cause_if_empty": "模板未覆盖该命令",
     "note": "限速第 3/3 层：接口级直给数值"},
    {"id": "interfaces[].QOS.out.car", "path": "interfaces[].QOS.out.car", "source": None,
     "column": "接口出流限速(M)", "root_cause_if_empty": "模板未覆盖该命令",
     "note": "限速第 3/3 层：接口级直给数值"},

    # ── VLAN 三列 ───────────────────────────────────────
    {"id": "interfaces[].VLAN.user-vlan[].user-vlan",
     "path": "interfaces[].VLAN.user-vlan[].user-vlan",
     "source": "Interfaces[].UserVlan", "column": "CVLAN",
     "root_cause_if_empty": "待人工判定",
     "note": "VLAN ↔ UserVlan：只搬运已捕获的标量，不合成 type/QinQ/dot1q"},
    {"id": "interfaces[].VLAN.type", "path": "interfaces[].VLAN.type", "source": None,
     "column": "VLAN封装类型", "root_cause_if_empty": "模板未覆盖该命令",
     "note": "dot1q / user-vlan / QinQ"},
    {"id": "interfaces[].VLAN.QinQ[].qinq", "path": "interfaces[].VLAN.QinQ[].qinq", "source": None,
     "column": "PVLAN", "root_cause_if_empty": "模板未覆盖该命令",
     "note": "外层（业务叠加层）"},
    {"id": "interfaces[].VLAN.dot1q", "path": "interfaces[].VLAN.dot1q", "source": None,
     "column": "CVLAN", "root_cause_if_empty": "模板未覆盖该命令",
     "note": "内层兜底"},

    # ── 业务属性三列 ─────────────────────────────────────
    {"id": "interfaces[].bas.damian", "path": "interfaces[].bas.damian", "source": None,
     "column": "域名名称", "root_cause_if_empty": "模板未覆盖该命令",
     "note": "bas 块实测 514 行、模板 0 命中"},
    {"id": "interfaces[].multicast.type", "path": "interfaces[].multicast.type", "source": None,
     "column": "组播类型", "root_cause_if_empty": "模板未覆盖该命令",
     "note": "multicast 块"},
    {"id": "interfaces[].traffic-policy.in", "path": "interfaces[].traffic-policy.in",
     "source": None, "column": "入方向流量策略",
     "root_cause_if_empty": "模板未覆盖该命令", "note": "traffic-policy 块"},
    {"id": "interfaces[].traffic-policy.out", "path": "interfaces[].traffic-policy.out",
     "source": None, "column": "出方向流量策略",
     "root_cause_if_empty": "模板未覆盖该命令", "note": "traffic-policy 块"},
]

ENTRY = {e["id"]: e for e in MAPPING_SPEC}


# ── 路径解析（映射表的 source → 实际取值）────────────────
def _walk(node, tokens, filt, out, parent=None):
    """按 tokens 逐层取值；在最后一个 list 展开时套用 filter。"""
    if not tokens:
        out.append((parent, node))
        return
    tok = tokens[0]
    rest = tokens[1:]
    if tok.endswith("[]"):
        name = tok[:-2]
        items = node.get(name) if isinstance(node, dict) else None
        last_list = not any(t.endswith("[]") for t in rest)
        for it in (items or []):
            if filt and last_list:
                if not isinstance(it, dict) or it.get(filt["field"]) != filt["value"]:
                    continue
            _walk(it, rest, filt, out, it)
        return
    if tok.endswith("]") and "[" in tok:
        name, _, idx = tok[:-1].partition("[")
        val = node.get(name) if isinstance(node, dict) else None
        try:
            val = val[int(idx)]
        except (TypeError, ValueError, IndexError):
            val = None
        _walk(val, rest, filt, out, val)
        return
    val = node.get(tok) if isinstance(node, dict) else None
    _walk(val, rest, filt, out, parent)


def _tokens(source):
    return source.split(".")


def resolve_values(root, source, filt=None):
    """从根取值 → [(父元素, 值)] 列表。"""
    tokens = _tokens(source)
    out = []
    _walk(root, tokens, filt, out)
    return out


def resolve_item(item, source, filt=None):
    """从**集合元素**取值（source 的第一段是集合名，其余是元素内相对路径）。"""
    tokens = _tokens(source)
    rel = tokens[1:] if len(tokens) > 1 else []
    out = []
    _walk(item, rel, filt, out)
    return out


def _first(pairs, default=None):
    for _parent, val in pairs:
        return val
    return default


def _src(mid):
    return ENTRY[mid]["source"]


def _filt(mid):
    return ENTRY[mid].get("filter")


def _addr_entries(it, family):
    """某族（ipv4/ipv6）的地址条目。字段名来自映射表，族别来自映射表的 filter。"""
    p = "interfaces[].IP.%s" % ("IPV4" if family == "ipv4" else "IPV6")
    mid_ip, mid_mask = p + "[].ip", p + "[].mask"
    f_ip = _src(mid_ip).split(".")[-1]
    f_mask = _src(mid_mask).split(".")[-1]
    # incumbent 的 type=sub 只属于 IPv4；IPv6 不得凭空添加该字段。
    type_spec = ENTRY.get(p + "[].type")
    f_type = type_spec["source"].split(".")[-1] if type_spec and type_spec["source"] else None
    filt = _filt(mid_ip) or {"field": "Family", "value": family}
    out = []
    for parent, val in resolve_item(it, _src(mid_ip), filt):
        elem = parent if isinstance(parent, dict) else {}
        entry = {"ip": val if val is not None else elem.get(f_ip, ""),
                 "mask": elem.get(f_mask, "")}
        if f_type is not None and f_type in elem:
            entry["type"] = elem[f_type]
        out.append(entry)
    return out


def to_incumbent_shape(np_result):
    """netparse 顶层结果 → incumbent 顶层 JSON 形状。

    纯结构映射（D9）：键改名 + 结构重排 + 值搬运；不合成、不推导、不填默认值。
    """
    np_result = np_result or {}
    out = {
        "device_name": _first(resolve_values(np_result, _src("device_name"))) or "",
        "device_ip": _first(resolve_values(np_result, _src("device_ip"))) or "",
        "interfaces": [],
        "route-static": {"IPV4": [], "IPV6": []},
        # 顶层 qos-profile（限速第 1 层）：netparse 模板没有 qos-profile 规则 → 空列表
        "qos-profile": [],
    }

    for it in (np_result.get("Interfaces") or []):
        d = {"name": _first(resolve_item(it, _src("interfaces[].name"))) or ""}
        desc = _first(resolve_item(it, _src("interfaces[].description")))
        if desc is not None:
            d["description"] = desc
        ip4 = _addr_entries(it, "ipv4")
        ip6 = _addr_entries(it, "ipv6")
        if ip4 or ip6:
            d["IP"] = {"IPV4": ip4, "IPV6": ip6}
        vpn = _first(resolve_item(it, _src("interfaces[].vpn.name")))
        if vpn:
            d["vpn"] = {"name": vpn}
        user_vlan = _first(resolve_item(
            it, _src("interfaces[].VLAN.user-vlan[].user-vlan")))
        if user_vlan is not None:
            # 标量 -> incumbent 的嵌套单值列表；其余 VLAN 子键不合成。
            d["VLAN"] = {"user-vlan": [{"user-vlan": [user_vlan]}]}
        out["interfaces"].append(d)

    for r in (np_result.get("RouteStatics") or []):
        out["route-static"]["IPV4"].append({
            "dest": _first(resolve_item(r, _src("route-static.IPV4[].dest"))) or "",
            "mask": _first(resolve_item(r, _src("route-static.IPV4[].mask"))) or "",
            "nexthop": _first(resolve_item(r, _src("route-static.IPV4[].nexthop"))) or "",
            "interface": _first(resolve_item(r, _src("route-static.IPV4[].interface"))) or "",
            "vpn": _first(resolve_item(r, _src("route-static.IPV4[].vpn"))) or "",
        })

    return out
