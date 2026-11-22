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
