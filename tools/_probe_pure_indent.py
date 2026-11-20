"""验证：华为配置的层级是否完全由缩进决定，`#` 只是段分隔符（不携带层级信息）。"""
import os, collections

D = "E:\\配置文件\\BRAS&SR\\原始配置\\cfgfile_20251224\\华为"


def pure_indent(path):
    """纯缩进模型：缩进上升=入栈，回落=出栈，`#` 一律忽略。
    返回 (异常列表, 块数, 顶层块数)"""
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    stack = []
    bad = []
    blocks = 0
    tops = 0
    inkey = False
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
            continue            # 完全忽略 # 的层级作用
        while stack and stack[-1]["ind"] > ind:
            stack.pop()
            blocks += 1
        if not stack or ind > stack[-1]["ind"]:
            if not stack and ind == 0:
                tops += 1
            if ind > (stack[-1]["ind"] if stack else -1):
                stack.append({"ind": ind, "cmd": st})
        elif ind == stack[-1]["ind"]:
            pass                # 同层命令
        else:
            bad.append((no, ln, ind, [s["cmd"] for s in stack[-3:]]))
    return bad, blocks, tops


files = [f for f in sorted(os.listdir(D)) if os.path.isfile(os.path.join(D, f))]
tot_bad = 0
tot_blocks = 0
tot_tops = 0
samples = []
for f in files:
    bad, blocks, tops = pure_indent(os.path.join(D, f))
    tot_bad += len(bad)
    tot_blocks += blocks
    tot_tops += tops
    if bad and len(samples) < 6:
        samples.append((f, bad[:3]))

print("文件 %d 台" % len(files))
print("纯缩进模型：块 %d，顶层块 %d，异常 %d" % (tot_blocks, tot_tops, tot_bad))
for f, b in samples:
    print("===", f)
    for x in b:
        print("   ", x)
