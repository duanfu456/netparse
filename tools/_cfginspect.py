"""测量工具共用的配置结构解析。

与 templates 无关 —— 这里只按**华为配置自身的缩进规则**切块，
用来跟模板的解析结果做对照（覆盖率、块数守恒、字段填充率）。

⚠️ 视图入口的判据（踩过的坑，别改）：
   不是「缩进上升」就是新视图。华为配置里缩进 1 的命令有几十条并列
   （description / mtu / bas / user-vlan …），它们都是同一层。
   真正的新视图首行只有两种：
     · 紧跟顶层 `#` 之后的缩进 0 行（新顶层块）
     · 紧跟子层 `#` 之后的缩进 k 行（新的平级子视图，如连续的 bas 块）
   判错会让 description 被当成子视图，把后面所有同级命令挂到错误路径下。
"""
import os

DEFAULT_ROOT = "E:\\配置文件\\BRAS&SR\\原始配置\\cfgfile_20251224\\华为"


def load(path):
    return open(path, encoding="utf-8", errors="replace").read().splitlines()


def walk(path):
    """按缩进切块，返回 [(行号, 命令, 路径列表, 是否视图入口)]。"""
    lines = load(path)
    stack = []
    inkey = False
    out = []
    just_after_hash = False
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
            just_after_hash = True
            continue
        while stack and stack[-1][0] > ind:
            stack.pop()
        if ind == 0:
            stack = [(0, st)]
            out.append((no, st, [st], True))
            just_after_hash = False
            continue
        if not stack:
            stack = [(ind, st)]
            out.append((no, st, [st], True))
            just_after_hash = False
            continue
        if ind > stack[-1][0]:
            stack.append((ind, st))
            out.append((no, st, [h for _, h in stack], True))
        elif just_after_hash:
            stack[-1] = (ind, st)
            out.append((no, st, [h for _, h in stack], True))
        else:
            out.append((no, st, [h for _, h in stack], False))
        just_after_hash = False
    return out


def fam(cmd):
    """命令族：头两个 token（undo 特殊处理）。"""
    toks = cmd.split()
    if not toks:
        return ""
    if toks[0] == "undo" and len(toks) > 1:
        return "undo " + toks[1]
    return toks[0] + " " + toks[1] if len(toks) > 1 else toks[0]


def devices(root=None, limit=None):
    """按名称排序的配置文件列表（保证可复现）。"""
    root = root or DEFAULT_ROOT
    files = [f for f in sorted(os.listdir(root)) if os.path.isfile(os.path.join(root, f))]
    return files[:limit] if limit else files
