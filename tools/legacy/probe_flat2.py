"""探针 v2：扁平化方案可行性验证

模拟「子集合提升到顶层 + 注入父键外键」，逐项检查可行性：
  1. 外键来源是否齐全（父的 option(Key) 或显式指定）
  2. 注入的父键名与子集合自己的字段名是否冲突
  3. 记录数守恒（提升前后总数一致）
  4. 外键值是否可区分（同数组内是否唯一到父记录）
  5. 孤儿检查

用法: python tools/probe_flat2.py <模板> <配置文件>
"""

import os
import sys
from collections import Counter, OrderedDict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))
from netparse import engine as P  # noqa: E402


def flatten(res, colls, parent_key_of=None):
    """模拟扁平化：子集合提升到顶层，注入父键。

    parent_key_of: {子集合名: 父记录里的字段路径}，显式指定外键来源
    """
    parent_key_of = parent_key_of or {}
    by_name = {c.name: c for c in colls}
    out = OrderedDict()
    for name, arr in res.items():
        out[name] = [dict(r) for r in arr]

    for c in colls:
        if c.parent is None:
            continue
        parent = c.parent
        # 外键来源：显式指定 > 父的 option(Key)
        src = parent_key_of.get(c.name)
        if src is None and parent.key_fields:
            src = parent.key_fields[0]
        fk_name = src or "?"

        rows = []
        for prec in res.get(parent.name, []):
            elems = prec.get(c.name)
            if not elems:
                continue
            if not isinstance(elems, list):
                elems = [elems]
            fk_val = P.get_path(prec, fk_name) if src else None
            for e in elems:
                row = dict(e)
                row["__fk"] = (fk_name, fk_val)
                rows.append(row)
        out[c.name] = rows
    return out


def main():
    tpl, cfg = sys.argv[1], sys.argv[2]
    colls = P.parse_template(tpl)
    by_name = {c.name: c for c in colls}
    lines = open(cfg, encoding="utf-8", errors="replace").readlines()
    res = P.run(colls, lines)

    # Bgp / Nqas 没有 option(Key) → 显式指定外键来源（方案里的 parent(...) 声明）
    explicit = {"BgpPeers": "AsNumber", "NqaSessions": "InstanceId"}

    print("=== 1. 外键来源 ===")
    for c in colls:
        if c.parent is None:
            continue
        src = explicit.get(c.name)
        auto = c.parent.key_fields[0] if c.parent.key_fields else None
        final = src or auto
        mark = "✅" if final else "❌"
        how = "显式 parent()" if src else ("自动取父的 option(Key)" if auto else "无来源")
        # 外键字段在父集合里是否真的存在
        exists = final is not None and final in c.parent.all_fields
        print(f"  {mark} {c.parent.name:<12} → {c.name:<16} 外键={final!s:<12} ({how})"
              f"{'' if exists or not final else '  ❌ 父集合里没这个字段!'}")

    flat = flatten(res, colls, explicit)

    print("\n=== 2. 记录数守恒 ===")
    for c in colls:
        if c.parent is None:
            continue
        nested = 0
        for prec in res.get(c.parent.name, []):
            e = prec.get(c.name)
            if not e:
                continue
            nested += len(e) if isinstance(e, list) else 1
        flatn = len(flat.get(c.name, []))
        print(f"  {c.name:<18} 嵌套 {nested:>5} → 扁平 {flatn:>5}  {'✓' if nested == flatn else '❌ 不一致'}")

    print("\n=== 3. 父键名冲突（注入的键 vs 子集合自己的字段）===")
    for c in colls:
        if c.parent is None:
            continue
        src = explicit.get(c.name) or (c.parent.key_fields[0] if c.parent.key_fields else None)
        if not src:
            continue
        own = set(c.all_fields.keys())
        clash = src in own or src.split(".")[0] in own
        print(f"  {c.name:<18} 注入键 {src!s:<12} {'❌ 与自身字段 ' + src + ' 冲突' if clash else '✓'}")

    print("\n=== 4. 外键区分度（同数组内，父键值能否定位父记录）===")
    for c in colls:
        if c.parent is None:
            continue
        rows = flat.get(c.name, [])
        vals = Counter(r["__fk"][1] for r in rows)
        dup = sum(1 for v, n in vals.items() if n > 1)
        print(f"  {c.name:<18} {len(rows):>5} 条 → 父键取值 {len(vals)} 种"
              f"（{dup} 个键对应多条子记录：正常，1 个父多子）")

    print("\n=== 5. 孤儿检查（父键为空 → 子记录无归属）===")
    for c in colls:
        if c.parent is None:
            continue
        rows = flat.get(c.name, [])
        orphan = [r for r in rows if r["__fk"][1] in (None, "")]
        print(f"  {c.name:<18} 孤儿 {len(orphan):>4} 条"
              + (f"  例: {orphan[0]}" if orphan else "  ✓"))

    print("\n=== 6. 扁平后的顶层数组（CMDB 表）===")
    for name, arr in flat.items():
        keys = set()
        for r in arr:
            keys |= set(r.keys())
        keys.discard("__fk")
        print(f"  {name:<18} {len(arr):>5} 行  列: {sorted(keys)}")


if __name__ == "__main__":
    main()
