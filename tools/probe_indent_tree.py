"""缩进树构建器验证脚本

目的：在写引擎之前，先用真实配置文件验证「缩进即层级」这条假设是否成立，
以及块边界是否能只靠缩进确定（不依赖 # 分隔符）。

用法：python tools/probe_indent_tree.py <配置文件路径>
"""

import sys
from collections import Counter, defaultdict


class Node:
    __slots__ = ("indent", "text", "children", "parent", "line_no")

    def __init__(self, indent, text, line_no, parent=None):
        self.indent = indent
        self.text = text
        self.children = []
        self.parent = parent
        self.line_no = line_no

    def head(self):
        """命令名（第一个 token）"""
        return self.text.split()[0] if self.text else ""

    def __repr__(self):
        return f"<{self.text!r} d={self.indent} children={len(self.children)}>"


def build_tree(lines):
    """按缩进构建树。栈里维护 (indent, node)。"""
    root = Node(-1, "<root>", 0)
    stack = [root]
    skips = Counter()
    jumps = []

    for i, raw in enumerate(lines, 1):
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        text = stripped

        # 回退到正确的父层级
        while len(stack) > 1 and stack[-1].indent >= indent:
            stack.pop()

        parent = stack[-1]
        node = Node(indent, text, i, parent)
        parent.children.append(node)

        # 记录缩进跳级（父 -> 子 差值 > 1）
        if parent is not root:
            gap = indent - parent.indent
            if gap > 1:
                jumps.append((i, parent.indent, indent, text[:60]))

        stack.append(node)
        skips[indent] += 1

    return root, skips, jumps


def main():
    path = sys.argv[1]
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    root, skips, jumps = build_tree(lines)

    total = sum(skips.values())
    print(f"文件: {path}")
    print(f"有效行: {total}   缩进分布: {dict(sorted(skips.items()))}")

    # ── 顶层块统计 ──
    tops = Counter(n.head() for n in root.children)
    print(f"\n顶层块命令种类: {len(tops)}   顶层块总数: {len(root.children)}")
    print("TOP 12:", ", ".join(f"{k}={v}" for k, v in tops.most_common(12)))

    # ── interface 块深度统计 ──
    ifaces = [n for n in root.children if n.head() == "interface"]
    print(f"\ninterface 块: {len(ifaces)}")
    if ifaces:
        child_counts = Counter(len(n.children) for n in ifaces)
        print(f"  每个块的子命令数分布: {dict(sorted(child_counts.items()))}")
        depths = Counter()
        for n in ifaces:
            d = 0
            cur = n
            while cur.children:
                cur = cur.children[0]
                d += 1
            depths[d] += 1
        print(f"  块内最大嵌套深度分布: {dict(sorted(depths.items()))}")

        # 抽样：三个接口块的完整结构
        for n in ifaces[:2] + ifaces[-1:]:
            print(f"\n  --- interface {n.text.split(None, 1)[1]} (line {n.line_no}) ---")
            for c in n.children:
                print(f"      {'  ' * (c.indent - 1)}{c.text}")

    # ── 全树最大深度 ──
    def max_depth(node):
        if not node.children:
            return node.indent
        return max(max_depth(c) for c in node.children)

    print(f"\n全树最大缩进层级: {max_depth(root)}")

    # ── 缩进跳级检查 ──
    print(f"\n缩进跳级处（父级与子级相差 >1 层）: {len(jumps)}")
    for ln, pi, ci, txt in jumps[:8]:
        print(f"  line {ln}: {pi} -> {ci}  {txt}")

    # ── 孤儿检查：任何节点的父链是否闭合 ──
    orphan = 0
    stack_nodes = list(root.children)
    while stack_nodes:
        n = stack_nodes.pop()
        if n.parent is None:
            orphan += 1
        stack_nodes.extend(n.children)
    print(f"\n父链断裂的节点: {orphan}")


if __name__ == "__main__":
    main()
