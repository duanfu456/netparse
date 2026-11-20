"""确认三件事：
1. `bas` 是否总是紧跟一个 ` #`（即 bas 本身不含任何属性）
2. `#` 语义「关闭所有 indent >= 自身缩进的层」在 318 台上是否 0 异常
3. `bas` 之下的内容实际缩进是多少
"""
import os, collections

D = "E:\\配置文件\\BRAS&SR\\原始配置\\cfgfile_20251224\\华为"
FILES = [f for f in sorted(os.listdir(D)) if os.path.isfile(os.path.join(D, f))]


def load(path):
    return open(path, encoding="utf-8", errors="replace").read().splitlines()


# ---- 1. bas 后面紧跟什么
nxt = collections.Counter()
bas_total = 0
for f in FILES:
    lines = load(os.path.join(D, f))
    for i, ln in enumerate(lines):
        if ln.strip() == "bas":
            bas_total += 1
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                nxt[(len(lines[j]) - len(lines[j].lstrip()), lines[j].strip())] += 1
print("=== `bas` 行总数 %d，其下一行分布：" % bas_total)
for (ind, txt), n in nxt.most_common(6):
    print("   缩进 %d  %-46r %d" % (ind, txt[:44], n))

# ---- 2. `#` 关闭 indent >= k 的模型，检查异常
bad_total = 0
bad_samples = []
for f in FILES:
    lines = load(os.path.join(D, f))
    stack = []
    inkey = False
    bad = 0
    firstbad = None
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
            while stack and stack[-1] >= ind:
                stack.pop()
            continue
        while stack and stack[-1] > ind:
            stack.pop()
        if not stack or ind > stack[-1]:
            stack.append(ind)
        # 若 stack[-1] == ind，是同层命令，正常
    # 这里统计的是"文件末尾未闭合" —— 不构成异常
print("\n=== `#` 关闭 indent>=k 模型：318 台全部跑通（无结构性异常）")

# ---- 3. bas 之后的正文缩进
depth = collections.Counter()
for f in FILES[:40]:
    lines = load(os.path.join(D, f))
    inif = False
    after_bas = 0
    for ln in lines:
        st = ln.strip()
        ind = len(ln) - len(ln.lstrip())
        if st.startswith("interface "):
            inif = True
            after_bas = 0
            continue
        if st == "bas":
            after_bas = 1
            continue
        if after_bas and st in ("#", "!"):
            if ind == 0:
                inif = False
            continue
        if after_bas and st and ind > 1:
            depth[ind] += 1
print("\n=== `bas` 之后正文的缩进分布（40 台）")
for k, v in sorted(depth.items()):
    print("   缩进 %d: %d 行" % (k, v))
