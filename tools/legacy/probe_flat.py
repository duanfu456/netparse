"""探针：扁平化（子集合提升到顶层 + 父键外键）可行性验证

要回答：
  1. 每个子集合的父是谁？父有没有可用的外键来源（option(Key) 字段）？
  2. 父记录被丢弃时，子记录会不会变成孤儿？
  3. 一个子集合挂在多少个父下面？提升到顶层后靠外键能不能区分？
  4. 有没有多层嵌套（提升后要不要带祖先外键）？

用法: python tools/probe_flat.py <模板> <配置文件>
"""

import os
import sys
from collections import Counter, OrderedDict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))
from netparse import engine as P  # noqa: E402


def main():
    tpl, cfg = sys.argv[1], sys.argv[2]
    colls = P.parse_template(tpl)
    by_name = {c.name: c for c in colls}

    print("=== 1. 子集合的外键来源 ===")
    child_colls = [c for c in colls if c.parent is not None]
    if not child_colls:
        print("  没有子集合")
    for c in child_colls:
        parent = c.parent
        keys = parent.key_fields
        if keys:
            src = "父的 option(Key): " + "/".join(keys)
        else:
            src = "❌ 父没有 option(Key) → 外键无来源，必须显式指定"
        print(f"  {parent.name:<16} → {c.name:<18} {src}")

    print("\n=== 2. 嵌套深度 ===")
    depth = Counter()
    for c in colls:
        d, p = 0, c.parent
        while p:
            d += 1
            p = p.parent
        depth[d] += 1
    print("  " + ", ".join(f"{d} 层: {n} 个集合" for d, n in sorted(depth.items())))
    print("  → 提升到顶层时，2 层子集合只需带直接父的键；3 层以上需要祖先链")

    # ── 跑引擎，看实际挂载关系 ──────────────────────
    lines = open(cfg, encoding="utf-8", errors="replace").readlines()
    res = P.run(colls, lines)

    print("\n=== 3. 子集合挂在多少个父下面（提升后靠外键区分）===")
    for c in child_colls:
        parents = 0
        children = 0
        for rec in res.get(c.parent.name, []):
            elems = rec.get(c.name)
            if not elems:
                continue
            parents += 1
            children += len(elems) if isinstance(elems, list) else 1
        print(f"  {c.name:<18} 父记录 {parents:>4} 个 / 子记录 {children:>5} 条 "
              f"→ 提升后同一顶层数组里 {children} 条，靠父键区分")

    print("\n=== 4. 孤儿检查（父被丢弃但子留下）===")
    orphan = 0
    for c in child_colls:
        for rec in res.get(c.parent.name, []):
            if not rec.get(c.name):
                continue
            # 父记录存在即其子元素合法；此处统计父记录缺必需字段的情况
            for fld in c.parent.all_fields.values():
                if fld.required and P.get_path(rec, fld.path) is None:
                    orphan += 1
    print(f"  孤儿（父缺 Required 字段却带着子记录）: {orphan}")
    print("  引擎行为：父记录不满足 Required → 整条（含子记录）一起丢弃 → 天然无孤儿")

    print("\n=== 5. 顶层集合（无父，不需要外键）===")
    for c in colls:
        if c.parent is None:
            n = len(res.get(c.name, []))
            keys = "/".join(c.key_fields) or "（无 Key）"
            print(f"  {c.name:<18} {n:>5} 条   Key={keys}")


if __name__ == "__main__":
    main()
