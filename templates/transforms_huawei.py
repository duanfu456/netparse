# -*- coding: utf-8 -*-
"""华为 VRP 模板的字段 transform 函数。

由 [python_file] 加载；签名统一 def f(value, record=None)。
"""


def iface_type(value, record=None):
    """从接口名推断接口类型（台账『接口类型』列的数据源）。"""
    name = value or (record or {}).get("Name") or ""
    v = name.lower()
    if v.startswith("eth-trunk"):
        return "链路聚合"
    if v.startswith(("gigabitethernet", "hp-ge")):
        return "以太光口"
    if v.startswith("100ge"):
        return "100GE 光口"
    if v.startswith("virtual-template"):
        return "虚拟模板"
    if v.startswith("virtual-ethernet"):
        return "虚拟以太"
    if v.startswith("loopback"):
        return "环回"
    if v.startswith("vlanif"):
        return "VLAN 网关"
    if v.startswith("null"):
        return "空接口"
    if "." in name:
        return "子接口"
    return None


def _copy_in(field, record=None):
    """通用：从 In 同名字段抄值（无方向 car 行 = in/out 同参）。"""
    rec = record or {}
    return rec.get("In", {}).get(field)


def out_cir(value, record=None):    return value if value is not None else _copy_in("Cir", record)
def out_pir(value, record=None):    return value if value is not None else _copy_in("Pir", record)
def out_cbs(value, record=None):    return value if value is not None else _copy_in("Cbs", record)
def out_pbs(value, record=None):    return value if value is not None else _copy_in("Pbs", record)
def out_green(value, record=None):  return value if value is not None else _copy_in("Green", record)
def out_yellow(value, record=None): return value if value is not None else _copy_in("Yellow", record)
def out_red(value, record=None):    return value if value is not None else _copy_in("Red", record)


def _pick_view(rows, want_dir, record):
    """从 QosCarRows 按方向挑参数；无方向的行视作双向（in/out 都可用）。"""
    for row in rows or []:
        d = row.get("Direction")
        if d == want_dir or d == "both":
            return {k: v for k, v in row.items()
                    if k not in ("Direction", "UqCir", "UqPir", "UqDir")}
    return None


def in_view(value, record=None):
    """In 视图：优先 inbound 行；无则用无方向（both）行。"""
    rows = (record or {}).get("QosCarRows")
    return _pick_view(rows, "inbound", record)


def out_view(value, record=None):
    """Out 视图：优先 outbound 行；无则用无方向（both）行。"""
    rows = (record or {}).get("QosCarRows")
    return _pick_view(rows, "outbound", record)
