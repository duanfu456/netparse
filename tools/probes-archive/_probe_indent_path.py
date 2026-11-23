"""原型：缩进驱动层级 + 命令族路径统计。

核心假设（已在 318 台设备上验证 0 异常）：
  1. 层级完全由缩进决定：缩进上升 = 进入子视图；缩进回落 = 隐式退出。
  2. 裸 `#` / `!` 只做段落分隔，不携带层级信息（`#` 的缩进恰好等于它"回到"的那层，
     但下一行缩进自然回落，忽略它也不会错）。
  3. 缩进 0 = 顶层块首（`#` 之后必然是新顶层块）。

若成立，模板不需要 start/end 配对，只需要「路径 → 字段」映射。
"""
import os, collections

D = "E:\\配置文件\\BRAS&SR\\原始配置\\cfgfile_20251224\\华为"


def walk(path):
    """逐行返回 (行号, 缩进, 命令, 路径列表, 是否块首)。"""
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    stack = []          # [(indent, header)]
    inkey = False
    out = []
    for no, ln in enumerate(lines, 1):
        st = ln.strip()
        if not st:
            continue
        if "public-key-code begin" in st:
            inkey = True
            continue
        if inkey:
            if "public-key-code end" in st:
                inkey = False
            continue
        ind = len(ln) - len(ln.lstrip())
        if st in ("#", "!"):
            continue
        while stack and stack[-1][0] > ind:
            stack.pop()
        if ind == 0:
            stack = [(0, st)]        # 顶层块首：重置
            out.append((no, ind, st, [st], True))
            continue
        if not stack:
            stack = [(ind, st)]
            out.append((no, ind, st, [st], True))
            continue
        if ind > stack[-1][0]:
            stack.append((ind, st))  # 新子视图
            out.append((no, ind, st, [h for _, h in stack], True))
        else:
            out.append((no, ind, st, [h for _, h in stack], False))
    return out


def fam(cmd):
    toks = cmd.split()
    if not toks:
        return cmd
    if toks[0] == "undo" and len(toks) > 1:
        return "undo " + toks[1]
    if len(toks) > 1:
        return toks[0] + " " + toks[1]
    return toks[0]


files = [f for f in sorted(os.listdir(D)) if os.path.isfile(os.path.join(D, f))][:20]

depth_stat = collections.Counter()
iface_lines = 0
sub_paths = collections.defaultdict(collections.Counter)

for f in files:
    for no, ind, cmd, path, is_header in walk(os.path.join(D, f)):
        if not path[0].startswith("interface "):
            continue
        iface_lines += 1
        rel = len(path) - 1
        depth_stat[rel] += 1
        if not is_header and rel >= 1:
            sub_paths[rel][fam(cmd)] += 1

print("样本 %d 台 | 接口块内命令行 %d" % (len(files), iface_lines))
print("\n=== 相对接口块的深度分布（0 = 接口本级命令）")
for d in sorted(depth_stat):
    print("  深度 %d: %7d 行  %5.1f%%" % (d, depth_stat[d], depth_stat[d] / iface_lines * 100))

print("\n=== 深层（>=1）命令族，按深度分组")
for d in sorted(sub_paths):
    print("\n  深度 %d 共 %d 行：" % (d, sum(sub_paths[d].values())))
    for famname, n in sub_paths[d].most_common(6):
        print("      %-34s %6d" % (famname, n))
