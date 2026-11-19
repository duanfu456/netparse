"""文件格式自动探测

不同厂商的配置文件，块界定机制不同：
  华为 VRP   缩进 1 空格 + 分隔符 #
  中兴 M6000 缩进 2 空格 + 分隔符 $ + <!section> 分区标记
  思科 IOS   无缩进    + 分隔符 !

目标：引擎在零配置下自动识别格式；模板可显式覆盖。

用法：python tools/probe_format.py <配置文件...>
"""

import re
import sys
from collections import Counter

# 单独成行、只含同一种符号 → 可能是分隔符
SEP_RE = re.compile(r"^([^\w\s])\1{0,1}$")
# 分区标记：中兴的 <!if-intf> / </system-config>
SECTION_RE = re.compile(r"^!?<?/?[A-Za-z0-9_\-]+>$")
# 注释：整行以符号开头且后面有内容
COMMENT_RE = re.compile(r"^([!#/;]+)\s*\S")
CMD_FIRST_RE = re.compile(r"^([A-Za-z])")


def classify(line):
    s = line.strip()
    if not s:
        return "blank", None
    if SECTION_RE.match(s):
        return "section", None
    m = SEP_RE.match(s)
    if m and not CMD_FIRST_RE.match(s):
        return "separator", m.group(1)
    m = COMMENT_RE.match(s)
    if m:
        return "comment", m.group(1)
    return "command", None


def detect(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    kinds = Counter()
    seps = Counter()
    comments = Counter()
    sections = Counter()
    indents = Counter()

    for raw in lines:
        kind, sym = classify(raw.rstrip("\n"))
        kinds[kind] += 1
        if kind == "separator":
            seps[sym] += 1
        elif kind == "comment":
            comments[sym] += 1
        elif kind == "section":
            sections["<>" ] += 1
        if kind in ("command", "section"):
            indents[len(raw) - len(raw.lstrip())] += 1

    # 缩进步长：相邻两个不同缩进值之差的最小值
    vals = sorted(v for v in indents if v > 0)
    step = None
    for a, b in zip(vals, vals[1:]):
        d = b - a
        if d > 0:
            step = d if step is None else min(step, d)
            break

    return kinds, seps, comments, sections, indents, step


def main():
    for path in sys.argv[1:]:
        kinds, seps, comments, sections, indents, step = detect(path)
        print("=" * 70)
        print(path.split("/")[-1][:60])
        print("=" * 70)
        print(f"  行分类      : {dict(kinds)}")
        print(f"  分隔符候选  : {dict(seps)}   → 判定: {seps.most_common(1)[0][0]!r}"
              if seps else "  分隔符候选  : 无")
        print(f"  注释符候选  : {dict(comments)}")
        print(f"  分区标记    : {dict(sections) if sections else '无'}")
        print(f"  缩进分布    : {dict(sorted(indents.items()))}")
        print(f"  缩进步长    : {step}  (每级 {step} 空格)" if step else "  缩进步长    : 无缩进")
        print()


if __name__ == "__main__":
    main()
