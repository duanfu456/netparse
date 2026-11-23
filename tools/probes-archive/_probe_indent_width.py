"""统计 318 台设备的缩进宽度分布 —— 决定模板能否用 `^ {n}` 定宽锚点。"""
import os, collections

D = "E:\\配置文件\\BRAS&SR\\原始配置\\cfgfile_20251224\\华为"
files = [f for f in sorted(os.listdir(D)) if os.path.isfile(os.path.join(D, f))]

ind_all = collections.Counter()
ind_cmd = collections.Counter()      # 只看非 # 的命令行
dev_has_tab = 0
dev_indents = collections.defaultdict(set)

for f in files:
    lines = open(os.path.join(D, f), encoding="utf-8", errors="replace").read().splitlines()
    seen = set()
    inkey = False
    for ln in lines:
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
        if "\t" in ln[:len(ln) - len(ln.lstrip())]:
            dev_has_tab += 1
        ind = len(ln) - len(ln.lstrip())
        if st in ("#", "!"):
            ind_all[ind] += 1
        else:
            ind_cmd[ind] += 1
        seen.add(ind)
    dev_indents[f] = seen

print("设备 %d 台" % len(files))
print("含 Tab 缩进的设备: %d" % dev_has_tab)
print()
print("=== 命令行（非 #）缩进宽度分布")
for k in sorted(ind_cmd):
    print("   缩进 %2d 空格: %9d 行" % (k, ind_cmd[k]))
print()
print("=== # 分隔符缩进宽度分布")
for k in sorted(ind_all):
    print("   缩进 %2d 空格: %9d 行" % (k, ind_all[k]))
print()
print("=== 每台设备出现过的缩进宽度集合（Top 8）")
c = collections.Counter(tuple(sorted(v)) for v in dev_indents.values())
for k, n in c.most_common(8):
    print("   %-18s %d 台" % (str(k), n))
