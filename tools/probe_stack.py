"""嵌套状态栈引擎 v2

语义（用户定义）
----------------
集合用缩进表达层级。每层是一个独立状态，有自己的 start / end / [value] / [rule]。

逐行处理：
  ① 从栈顶向下找第一个命中的 start（层 L）：
       · L 在栈上  → 存档 L 及其所有子层的当前记录，L 以下（更深）的层出栈
       · L 不在栈上 → 压入 L 成为新栈顶
       · 用 L 的 [rule] 匹配本行，结束
  ② 没命中任何 start：
       · 栈顶层的 end 命中 → 存档栈顶记录，栈顶出栈
       · 用栈顶的 [rule] 匹配本行；不匹配则依次向下（父层）尝试

输出结构：子集合的元素嵌在父元素的对应字段里（无需 inherit）。
聚合：同一父元素下，Key 相同的子记录合并成一个对象。

v2 变更
--------
1. Key / Required 移到**字段级**：`<字段> ... option(Required Key)`。
   集合级 `Key(...)` / `Required(...)` 已废弃，解析时报错。
2. `->` 目标默认落在**当前集合·当前记录**；跨集合才写 `-> 集合名{...}`。
3. `-> 字段=常量` / `-> 路径.字段=常量` 支持（此前被静默丢弃）。
4. `${A.B}` 支持嵌套字段路径（此前编译不出捕获组，规则永不命中）。
5. Key 字段缺失的记录丢弃；Required 字段缺失的记录丢弃。
6. `type(int/float/bool)` 真正转换；`default(...)` 真正填充；
   `type(list)` 的字段重复写入自动成数组。

用法：python tools/probe_stack.py <模板> <配置文件> [--json] [--lint]
"""

import json
import re
import sys
from collections import OrderedDict


# ── 词法 ──────────────────────────────────────────────
def split_tokens(s):
    """按空格切词，括号内的空格不切。"""
    out, buf, depth = [], "", 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == " " and depth == 0:
            if buf:
                out.append(buf)
                buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def parse_modifiers(toks):
    """解析声明行的选项。

    返回 (attrs, flags)：
      attrs —— key(...) / key=value 形式的具名选项
      flags —— 裸词与 option(...) 里的标记，如 Required / Key
    """
    attrs = {}
    flags = set()
    for t in toks[1:]:
        m = re.match(r"^([A-Za-z_]\w*)(?:\((.*)\))?$", t)
        if m:
            k, v = m.group(1), m.group(2)
            if k == "option":
                for f in re.split(r"[\s,]+", v or ""):
                    if f:
                        flags.add(f)
            elif v is not None:
                attrs[k] = v
            else:
                flags.add(k)
            continue
        if "=" in t:
            k, _, v = t.partition("=")
            attrs[k] = v
    return attrs, flags


# ── 模型 ──────────────────────────────────────────────
class Field:
    """[value] 段里的一个字段（缩进 = 嵌套）。"""

    def __init__(self, name, attrs, flags, parent=None):
        self.name = name
        self.parent = parent
        self.path = name if parent is None else parent.path + "." + name
        self.type = attrs.get("type", "str")
        self.default = attrs.get("default")
        self.reg = attrs.get("reg", r"\S+")
        self.desc = attrs.get("desc")
        self.inherit = attrs.get("inherit")
        self.required = "Required" in flags
        self.is_key = "Key" in flags
        self.children = OrderedDict()

    def __repr__(self):
        return f"<Field {self.path} type={self.type}>"


class Coll:
    """一个集合（一层状态）。"""

    def __init__(self, name, attrs, flags, indent, parent):
        self.name = name
        self.attrs = attrs
        self.flags = flags
        self.indent = indent
        self.parent = parent
        self.children = []              # 子集合
        self.fields = OrderedDict()     # 顶层字段
        self.all_fields = OrderedDict() # 路径 → 字段（含嵌套）
        self.rules = []
        self.type = attrs.get("type", "list")
        self.start = attrs.get("start")
        self.end = attrs.get("end")
        self.desc = attrs.get("desc")
        self.key_fields = []            # 由 option(Key) 派生
        self.start_re = re.compile(self.start) if self.start else None
        self.end_re = re.compile(self.end) if self.end else None

    def __repr__(self):
        return f"<Coll {self.name} d={self.indent} children={[c.name for c in self.children]}>"


class Rule:
    def __init__(self, pattern, target):
        self.pattern = pattern
        self.target = target            # None = 省略 ->

    def __repr__(self):
        return f"<Rule {self.pattern!r} -> {self.target!r}>"


class CompiledRule:
    def __init__(self, rx, group_paths, path_groups, target):
        self.rx = rx
        self.group_paths = group_paths  # 捕获组名 → 字段路径
        self.path_groups = path_groups  # 字段路径 → 捕获组名
        self.target = target


# ── 模板解析 ──────────────────────────────────────────
def is_coll_decl(toks):
    """集合声明判定：首 token 是合法标识符 + 第二个 token 是 type(...)

    集合： Interfaces type(list) Key(Name) start(...)
    字段： Name type(str) reg(\\S+)          ← 在 [value] 段内，不走这条路
    规则： ^interface\\s+${Name}            ← 首 token 不是标识符
    """
    if len(toks) < 2:
        return False
    if not re.match(r"^[A-Za-z_]\w*$", toks[0]):
        return False
    return toks[1].startswith("type(")


def parse_template(path):
    colls = []
    stack = []        # (indent, Coll)
    cur = None        # 当前集合
    mode = None       # None | value | rule
    base_indent = None
    pending = None    # 待落盘的规则（可能多行）
    field_stack = []  # (indent, Field)
    section_coll = None

    def new_collection(toks, indent):
        nonlocal stack, cur
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else None
        attrs, flags = parse_modifiers(toks)
        coll = Coll(toks[0], attrs, flags, indent, parent)
        colls.append(coll)
        if parent:
            parent.children.append(coll)
        stack.append((indent, coll))
        cur = coll
        return coll

    def flush_pending():
        nonlocal pending
        if pending is None:
            return
        pat = pending
        target = None
        m = re.match(r"^(.*?)\s*->\s*(.+)$", pat)
        if m:
            pat, target = m.group(1).strip(), m.group(2).strip()
        section_coll.rules.append(Rule(pat, target))
        pending = None

    for raw in open(path, encoding="utf-8"):
        s = raw.rstrip("\n").rstrip("\r")
        st = s.strip()
        if not st:
            flush_pending()
            mode = None
            base_indent = None
            field_stack = []
            continue
        if st.startswith("#"):
            continue
        low = st.lower()
        if low in ("[value]", "[vlaue]", "[rule]"):
            flush_pending()
            mode = "value" if low != "[rule]" else "rule"
            section_coll = cur
            base_indent = None
            field_stack = []
            continue
        if st.startswith("["):
            continue

        indent = len(s) - len(s.lstrip())
        toks = split_tokens(st)

        # 段外 → 集合声明
        if mode is None:
            new_collection(toks, indent)
            continue

        # [value] 段 → 字段声明（缩进 = 嵌套）
        if mode == "value":
            if section_coll is None:
                continue
            while field_stack and field_stack[-1][0] >= indent:
                field_stack.pop()
            parent_field = field_stack[-1][1] if field_stack else None
            attrs, flags = parse_modifiers(toks)
            f = Field(toks[0], attrs, flags, parent_field)
            if parent_field is None:
                section_coll.fields[f.name] = f
            else:
                parent_field.children[f.name] = f
            section_coll.all_fields[f.path] = f
            field_stack.append((indent, f))
            continue

        # [rule] 段 → 子集合声明 / 规则 / 规则续行
        if is_coll_decl(toks):
            flush_pending()
            new_collection(toks, indent)
            mode = None
            base_indent = None
            field_stack = []
            continue
        if pending is not None and base_indent is not None and indent > base_indent:
            pending = pending + st
            continue
        flush_pending()
        if base_indent is None:
            base_indent = indent
        pending = st

    flush_pending()

    for c in colls:
        c.key_fields = [f.path for f in c.all_fields.values()
                        if f.is_key and f.parent is None]
    return colls


# ── 规则编译 ──────────────────────────────────────────
def compile_rule(rule, coll):
    """把 ${字段} 展开成命名捕获组。

    返回 CompiledRule；字段未声明时抛 KeyError（由 lint 汇总）。
    """
    fields = coll.all_fields
    group_paths = OrderedDict()
    path_groups = OrderedDict()
    seen = set()

    def sub(m):
        ref = m.group(1)
        fld = fields.get(ref)
        if fld is None:
            raise KeyError(ref)
        group = fld.path.replace(".", "__")
        if group in seen and group_paths.get(group) != fld.path:
            raise KeyError("组名冲突: " + group)
        group_paths[group] = fld.path
        path_groups[fld.path] = group
        if group in seen:
            return f"(?P={group})"      # 重复引用 → 回溯引用
        seen.add(group)
        return f"(?P<{group}>{fld.reg})"

    try:
        pattern = re.sub(r"\$\{([A-Za-z_][\w.]*)\}", sub, rule.pattern)
    except KeyError as e:
        raise KeyError(f"{coll.name}: 字段 {e.args[0]} 未声明") from None
    return CompiledRule(re.compile(pattern), group_paths, path_groups, rule.target)


# ── 值处理 ────────────────────────────────────────────
def parse_literal(s):
    """常量字面量 → Python 值。"""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "none"):
        return None
    if re.match(r"^-?\d+$", s):
        return int(s)
    if re.match(r"^-?\d+\.\d+$", s):
        return float(s)
    return s


def convert(value, fld):
    """按字段声明的类型转换值。"""
    if fld is None or value is None:
        return value
    t = fld.type
    try:
        if t == "int":
            return int(value)
        if t == "float":
            return float(value)
        if t == "bool":
            if isinstance(value, bool):
                return value
            low = str(value).strip().lower()
            if low in ("true", "yes", "on", "1"):
                return True
            if low in ("false", "no", "off", "0"):
                return False
            return value
        if t in ("str", "dict", "list"):
            return value if isinstance(value, str) else str(value)
    except (TypeError, ValueError):
        return value          # 转不了就原样保留，不丢数据
    return value


def get_path(obj, path):
    cur = obj
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def set_path(obj, segs, value):
    for p in segs[:-1]:
        nxt = obj.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            obj[p] = nxt
        obj = nxt
    obj[segs[-1]] = value


def apply_writes(rec, writes, coll):
    """把 (字段路径, 值) 写进记录。

    同一路径首段若是 type(list) 的字段 → 追加一个新元素；
    否则直接覆盖。同一批 writes 里首段相同的归到同一个元素。
    """
    groups = OrderedDict()
    for path, value in writes:
        groups.setdefault(path.split(".")[0], []).append((path, value))

    for p0, items in groups.items():
        fld = coll.all_fields.get(p0)
        if fld is not None and fld.type == "list":
            elem = {}
            for path, value in items:
                segs = path.split(".")[1:]
                if not segs:
                    continue
                set_path(elem, segs, convert(value, coll.all_fields.get(path)))
            bucket = rec.setdefault(p0, [])
            if isinstance(bucket, dict):     # 容错：之前被写成 dict
                bucket = [bucket]
                rec[p0] = bucket
            bucket.append(elem)
        else:
            for path, value in items:
                segs = path.split(".")
                set_path(rec, segs, convert(value, coll.all_fields.get(path)))


# ── 目标解析 ──────────────────────────────────────────
def parse_target(target):
    """-> 后面内容的两种形态：

      ('members', base, [成员...])   -> 集合名{字段1,字段2=常量}
      ('assigns', [(名字, 值|None)]) -> 字段=常量 / 字段 / 路径.字段=常量
    """
    t = target.strip()
    if "{" in t:
        m = re.match(r"^([\w.]+)\{(.*)\}$", t, re.S)
        if not m:
            raise ValueError("目标写法无法解析: " + target)
        base = m.group(1)
        members = [x.strip() for x in m.group(2).split(",") if x.strip()]
        return "members", base, members
    items = []
    for part in t.split(","):
        part = part.strip()
        if not part:
            continue
        name, eq, val = part.partition("=")
        items.append((name.strip(), parse_literal(val.strip()) if eq else None))
    return "assigns", items, None


# ── 引擎 ──────────────────────────────────────────────
class Frame:
    """栈上的一层"""
    __slots__ = ("coll", "rec", "children")

    def __init__(self, coll):
        self.coll = coll
        self.rec = {}
        self.children = OrderedDict()   # 子集合名 -> [元素]


def check_required(rec, coll):
    """记录是否满足所有 option(Required) 字段。"""
    for fld in coll.all_fields.values():
        if not fld.required:
            continue
        v = get_path(rec, fld.path)
        if v is None or v == "":
            return False
    return True


def merge_by_key(elems, coll):
    """Key 聚合：Key 相同的子记录合并成一个。

    键 = 所有 option(Key) 字段值的元组：
      · 全部键字段都为空 → 记录无法识别 → 丢弃
      · 部分键字段为空（可选键，如路由的 VPN 实例）→ 空值参与键计算，不合并
    """
    if coll is None or not coll.key_fields:
        return elems
    merged, index = [], {}
    for el in elems:
        vals = tuple(get_path(el, kf) for kf in coll.key_fields)
        if all(v is None or v == "" for v in vals):
            continue                      # 键全空 → 丢弃
        if vals in index:
            for kk, vv in el.items():
                index[vals].setdefault(kk, vv)   # 后到的补空缺，不覆盖
        else:
            index[vals] = el
            merged.append(el)
    return merged


def apply_defaults(rec, coll):
    """填充声明了 default 但没抓到值的字段。"""
    for fld in coll.all_fields.values():
        if fld.default is None or fld.type == "list":
            continue
        if get_path(rec, fld.path) is None:
            set_path(rec, fld.path.split("."), convert(parse_literal(fld.default), fld))


def run(colls, lines, trace=None):
    """trace: 可选列表，传入后记录每一行的匹配情况 (行号, 原始行, 命中集合名或 None)。

    这是 lint「命令覆盖率」统计的数据来源——必须来自真实匹配过程，
    事后用正则重测会把作用域外的同名命令误判成已覆盖。
    """
    root = Coll("__root__", {}, set(), -1, None)
    compiled = {}
    for c in colls:
        items = []
        for r in c.rules:
            try:
                items.append(compile_rule(r, c))
            except KeyError:
                raise
        compiled[c.name] = items

    stack = [Frame(root)]
    frame_of = {}
    # 无 start 的集合 = 栈底常驻层（规则始终兜底匹配）
    for c in colls:
        if not c.start:
            fr = Frame(c)
            stack.insert(1, fr)
            frame_of[c.name] = fr

    results = OrderedDict()
    last = {}
    coll_by_name = {c.name: c for c in colls}

    def commit(fr):
        """提交一层的当前记录：子层元素合并进来，存到父帧或顶层"""
        coll = fr.coll
        if coll is root:
            return
        for cname, elems in fr.children.items():
            if not elems:
                continue
            cdef = coll_by_name.get(cname)
            elems = [e for e in elems if check_required(e, cdef)]
            elems = merge_by_key(elems, cdef)
            if not elems:
                continue
            fr.rec[cname] = elems[0] if (coll.type == "dict" and len(elems) == 1) else elems
        apply_defaults(fr.rec, coll)
        if not check_required(fr.rec, coll):
            return
        parent = frame_of.get(coll.parent.name) if coll.parent else None
        if parent is None:
            results.setdefault(coll.name, []).append(fr.rec)
        else:
            parent.children.setdefault(coll.name, []).append(fr.rec)

    for no, raw in enumerate(lines, 1):
        line = raw.rstrip("\n").rstrip("\r")
        if not line.strip():
            continue
        st = line.strip()
        if st.startswith("#") and len(st) <= 2:
            continue

        # ① 找命中的 start：从栈顶向下，每层先看自己、再看子集合；
        #    最后看尚未激活的顶层集合
        hit = None
        for depth in range(len(stack) - 1, -1, -1):
            fr = stack[depth]
            if fr.coll.start_re and fr.coll.start_re.search(line):
                hit = ("self", fr.coll, depth)
                break
            for child in fr.coll.children:
                if child.start_re and child.start_re.search(line):
                    hit = ("child", child, depth)
                    break
            if hit:
                break
        if hit is None:
            for c in colls:
                if c.parent is None and c.start_re and c.start_re.search(line):
                    hit = ("new", c, -1)
                    break

        hit_name = None
        if hit is not None:
            kind, coll, depth = hit
            if kind == "self":
                fr = stack[depth]
                while len(stack) > depth + 1:
                    commit(stack.pop())
                commit(fr)
                fr.rec = {}
                fr.children = OrderedDict()
                if _apply(fr, line, compiled, last):
                    hit_name = fr.coll.name
            elif kind == "child":
                idx = next((i for i, f in enumerate(stack) if f.coll is coll), None)
                if idx is None:
                    fr = Frame(coll)
                    stack.append(fr)
                else:
                    while len(stack) > idx + 1:
                        commit(stack.pop())
                    commit(stack[idx])
                    stack[idx].rec = {}
                    stack[idx].children = OrderedDict()
                    fr = stack[idx]
                frame_of[coll.name] = fr
                if _apply(fr, line, compiled, last):
                    hit_name = fr.coll.name
            else:   # new
                fr = Frame(coll)
                stack.append(fr)
                frame_of[coll.name] = fr
                if _apply(fr, line, compiled, last):
                    hit_name = fr.coll.name
            if trace is not None:
                trace.append((no, line, hit_name, [f.coll.name for f in stack]))
            continue

        # ② 栈顶的 end 命中 → 出栈
        top = stack[-1]
        if top.coll.end_re and top.coll.end_re.search(line):
            commit(top)
            stack.pop()
            frame_of.pop(top.coll.name, None)

        # ③ 栈顶向下匹配；不匹配继续父层
        for depth in range(len(stack) - 1, -1, -1):
            if _apply(stack[depth], line, compiled, last):
                hit_name = stack[depth].coll.name
                break
        if trace is not None:
            trace.append((no, line, hit_name, [f.coll.name for f in stack]))

    while stack:
        commit(stack.pop())

    # 顶层集合不做 Key 合并：Key 聚合的语义是「同一父元素下的子记录合并」，
    # 顶层集合没有父元素，重名对象（如 Nokia 不同 router 下的同名接口）必须各留一条。
    return results


def _apply(fr, line, compiled, last):
    """用 fr 的规则匹配 line。命中返回 True"""
    coll = fr.coll
    if coll.name not in compiled:
        return False
    for cr in compiled[coll.name]:
        m = cr.rx.match(line)
        if not m:
            continue
        gd = m.groupdict()
        writes = []
        for g, v in gd.items():
            if v is None:
                continue
            path = cr.group_paths.get(g, g)
            writes.append((path, v))
            last[(coll.name, path)] = v

        # inherit 补齐
        for fld in coll.all_fields.values():
            if not fld.inherit:
                continue
            if any(p == fld.path for p, _ in writes):
                continue
            src_coll, _, src_field = fld.inherit.partition(".")
            if (src_coll, src_field) in last:
                writes.append((fld.path, last[(src_coll, src_field)]))

        target = cr.target
        if target is None:
            # 省略 -> ：全部捕获组写进当前记录
            apply_writes(fr.rec, writes, coll)
            return True

        kind, a, b = parse_target(target)
        if kind == "members":
            base, members = a, b
            obj = OrderedDict()
            for mem in members:
                if "=" in mem:
                    k2, _, v2 = mem.partition("=")
                    obj[k2.strip()] = parse_literal(v2)
                else:
                    # 成员名先按完整路径找，再按相对 base 的路径找
                    grp = cr.path_groups.get(mem) or cr.path_groups.get(base + "." + mem)
                    if grp and gd.get(grp) is not None:
                        obj[mem] = gd[grp]
                    elif mem in gd and gd[mem] is not None:
                        obj[mem] = gd[mem]
            if base == coll.name:
                # 目标就是当前集合 → 写进当前记录
                apply_writes(fr.rec, [(k, v) for k, v in obj.items()], coll)
            elif base in coll.all_fields:
                # 目标是本层的嵌套字段（list → 追加元素，dict → 写进去）
                apply_writes(fr.rec, [(base + "." + k, v) for k, v in obj.items()], coll)
            else:
                # 目标是一个（未展开的）子集合 → 塞进当前记录
                fr.rec[base] = dict(obj)
        else:   # assigns
            items = a
            top_items = [(n, v) for n, v in items if "." not in n]
            path_items = [(n, v) for n, v in items if "." in n]
            out = []
            if top_items:
                # 顶层字段：先铺捕获组，再写常量
                out += writes
                out += top_items
            for n, v in path_items:
                # 嵌套路径：只写这一个路径，不铺其它捕获组
                if v is None:
                    grp = cr.path_groups.get(n)
                    val = gd.get(grp) if grp else gd.get(n)
                    if val is None:
                        continue
                    out.append((n, val))
                else:
                    out.append((n, v))
            apply_writes(fr.rec, out, coll)
        return True
    return False


# ── lint ──────────────────────────────────────────────
def lint(colls):
    """模板体检。返回 [(级别, 集合, 说明)]"""
    issues = []
    for c in colls:
        if "Key" in c.attrs or "Required" in c.attrs:
            issues.append(("错误", c.name,
                           "集合级 Key(...)/Required(...) 已废弃，请写到字段的 option(Key/Required)"))
        if "Key" in c.flags or "Required" in c.flags:
            issues.append(("错误", c.name,
                           "集合级 Key/Required 已废弃，请写到字段的 option(Key/Required)"))
        key_fields = []
        for fld in c.all_fields.values():
            if fld.is_key and fld.parent is not None:
                issues.append(("错误", c.name,
                               f"option(Key) 字段 {fld.path} 必须是本集合的顶层字段"))
            if fld.is_key:
                key_fields.append(fld.path)
        if key_fields:
            issues.append(("提示", c.name,
                           "option(Key) 字段 " + "/".join(key_fields)
                           + " 全部为空时，该记录会被丢弃"))
            if fld.required and fld.default is None:
                hit = any(fld.path in re.findall(r"\$\{([A-Za-z_][\w.]*)\}", r.pattern)
                          for r in c.rules)
                if not hit:
                    issues.append(("警告", c.name,
                                   f"option(Required) 字段 {fld.path} 没有规则会命中"))
        for r in c.rules:
            for ref in re.findall(r"\$\{([A-Za-z_][\w.]*)\}", r.pattern):
                if ref not in c.all_fields:
                    issues.append(("错误", c.name, f"规则引用了未声明的字段 {ref}"))
            if not r.target:
                continue
            kind, a, b = parse_target(r.target)
            if kind == "members":
                base, members = a, b
                for mem in members:
                    key = mem.split("=")[0].strip()
                    if key and key not in c.all_fields and base == c.name:
                        issues.append(("错误", c.name, f"目标里的字段 {key} 未声明"))
            else:
                for name, _v in a:
                    if "." not in name and name not in c.all_fields:
                        issues.append(("错误", c.name, f"目标字段 {name} 未声明"))
                    elif "." in name and name not in c.all_fields:
                        issues.append(("错误", c.name, f"目标路径 {name} 未声明"))
    return issues


# ── CLI ───────────────────────────────────────────────
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    want_json = "--json" in sys.argv
    want_lint = "--lint" in sys.argv
    if len(args) < 2:
        print("用法: python tools/probe_stack.py <模板> <配置文件> [--json] [--lint]")
        return 1

    colls = parse_template(args[0])

    issues = lint(colls)
    if issues:
        print("=== lint ===")
        for lvl, coll, msg in issues:
            print(f"  [{lvl}] {coll}: {msg}")
        print()
    if want_lint:
        return 0

    lines = open(args[1], encoding="utf-8", errors="replace").readlines()
    res = run(colls, lines)

    if want_json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    print("集合树:")
    for c in colls:
        pad = "  " * c.indent
        key = ("Key=" + ",".join(c.key_fields)) if c.key_fields else ""
        print(f"{pad}{c.name:<22} type={c.type:<6} start={c.start!r:<22} "
              f"fields={len(c.all_fields)} rules={len(c.rules)} {key}")

    print("\n=== 结果 ===")
    for k, arr in res.items():
        print(f"  {k:<22} {len(arr):>5} 条")

    for k in ("Interfaces", "IpAddress"):
        if res.get(k):
            print(f"\n=== {k} 前 2 条 ===")
            for it in res[k][:2]:
                print("  " + json.dumps(it, ensure_ascii=False)[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
