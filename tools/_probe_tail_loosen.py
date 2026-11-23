"""实验：只把 Interfaces 集合规则的尾部从 `\\s*$` 放宽成 `(?:\\s+.*)?\\s*$`，
看覆盖率提升多少 —— 用来隔离「写法规格」这一单一变量。"""
import os, re, sys, tempfile, collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from netparse import parse_template, run

D = "E:\\配置文件\\BRAS&SR\\原始配置\\cfgfile_20251224\\华为"
FILES = [f for f in sorted(os.listdir(D)) if os.path.isfile(os.path.join(D, f))][:20]
TPL = os.path.join(HERE, "..", "templates", "huawei-vrp.np")


def walk(path):
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    stack = []
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
            stack = [(0, st)]
            out.append((no, st, [st], True))
            continue
        if not stack:
            stack = [(ind, st)]
            out.append((no, st, [st], True))
            continue
        if ind > stack[-1][0]:
            stack.append((ind, st))
            out.append((no, st, [h for _, h in stack], True))
        else:
            out.append((no, st, [h for _, h in stack], False))
    return out


def measure(tpl_path, label):
    tpl = parse_template(tpl_path)
    eaten_total = 0
    total = 0
    for f in FILES:
        p = os.path.join(D, f)
        lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
        trace = []
        run(tpl, lines, trace=trace)
        eaten = {no for no, line, h, stack in trace if h is not None and line.strip()}
        for no, cmd, path, is_header in walk(p):
            if not path[0].startswith("interface ") or is_header:
                continue
            total += 1
            if no in eaten:
                eaten_total += 1
    print("%-34s %7d / %7d = %5.1f%%" % (label, eaten_total, total, eaten_total / total * 100))
    return eaten_total, total


print("样本 %d 台（接口块内命令行口径）\n" % len(FILES))
measure(TPL, "原模板")

# 生成放宽版：只针对 Interfaces 段内的规则（第 63~86 行）
src = open(TPL, encoding="utf-8").read().splitlines()
out = []
inside = False
for ln in src:
    if ln.startswith("Interfaces type(list)"):
        inside = True
    elif ln.startswith("[rule]") is False and inside and re.match(r"^\S", ln) and not ln.startswith("#"):
        if not ln.startswith("Interfaces"):
            inside = False
    if inside and ln.strip().endswith(r"\s*$") and "${" in ln:
        ln = ln[: -len(r"\s*$")] + r"(?:\s+.*)?\s*$"
    out.append(ln)

fd, tmp = tempfile.mkstemp(suffix=".cmdb")
os.close(fd)
open(tmp, "w", encoding="utf-8").write("\n".join(out))
measure(tmp, "尾部放宽后")
os.unlink(tmp)
