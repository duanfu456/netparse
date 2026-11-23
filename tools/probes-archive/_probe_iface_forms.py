"""把接口块内每个命令族的真实形态打出来，用于写模板规则。"""
import os, re, collections

D = "E:\\配置文件\\BRAS&SR\\原始配置\\cfgfile_20251224\\华为"
files = [f for f in sorted(os.listdir(D)) if os.path.isfile(os.path.join(D, f))][:20]

# 收集接口块内、缩进 1 和缩进 2 的命令
lvl1 = collections.Counter()
lvl2 = collections.Counter()

for name in files:
    lines = open(os.path.join(D, name), encoding="utf-8", errors="replace").read().splitlines()
    inif = False
    for ln in lines:
        st = ln.strip()
        ind = len(ln) - len(ln.lstrip())
        if st.startswith("interface "):
            inif = True
            continue
        if inif and st in ("#", "!"):
            if ind == 0:
                inif = False
            continue
        if not inif or not st:
            continue
        # 归一化：只替换「参数位」的数字与接口名，保留命令头
        toks = st.split()
        head = []
        body = []
        for i, t in enumerate(toks):
            if i < 2 and re.match(r"^[a-z][\w-]*$", t):
                head.append(t)          # 命令词（小写命令）
                continue
            body.append(t)
        sk = " ".join(head + [re.sub(r"\d+", "N", b) for b in body])
        sk = re.sub(r"N\.N\.N\.N", "IP", sk)

        if ind == 1:
            lvl1[sk] += 1
        elif ind == 2:
            lvl2[sk] += 1

print("=" * 100)
print("缩进 1（接口本级命令）共 %d 种形态 / %d 行" % (len(lvl1), sum(lvl1.values())))
print("=" * 100)
for k, v in lvl1.most_common(40):
    print("%6d  %s" % (v, k))
print()
print("=" * 100)
print("缩进 2（bas 视图正文）共 %d 种形态 / %d 行" % (len(lvl2), sum(lvl2.values())))
print("=" * 100)
for k, v in lvl2.most_common(30):
    print("%6d  %s" % (v, k))
