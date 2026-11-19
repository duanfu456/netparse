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
import os
import re
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from masking import MASK_DEFAULT, STATS, mask_value  # noqa: E402

# ── 脱敏配置（模块级；run() 签名不变，遮蔽参数从这里进）──
MASK_VALUE = MASK_DEFAULT      # 掩码串，CLI --mask-value 可覆盖
MASK_HEURISTIC = True          # L2 形态兜底开关，CLI --no-mask-heuristic 可关闭

#: 字段同时声明了多个 type（如 type(secret) type(int)）时记这个标记
TYPE_CONFLICT_KEY = "__type_conflict__"

#: lint 用的凭据关键字（词边界匹配，见 lint 的 _rule_has_credential_keyword）
CREDENTIAL_KEYWORDS = (
    "password", "cipher", "secret", "shared-key", "md5-password",
    "irreversible-cipher", "key-string", "authentication-key",
)


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

    ⚠️ 一个字段只能声明一种 type（secret 与其它类型互斥）。
    重复声明时保留**第一个** type，并打上冲突标记（由 lint 报错误、引擎不产出该字段）。
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
                if k == "type" and "type" in attrs:
                    attrs[TYPE_CONFLICT_KEY] = True      # 保留第一个 type
                else:
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
        self.type_conflict = bool(attrs.get(TYPE_CONFLICT_KEY))
        self.children = OrderedDict()

    def is_secret(self):
        """type == 'secret' —— 该字段的值是凭据，落盘前必须遮蔽。"""
        return self.type == "secret"

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
    if fld.is_secret():
        # 遮蔽发生在 convert 之前：secret 字段的声明类型被强制视为 str，
        # 否则掩码串会被 int/float 转换搞坏（转换失败回退原值 → 掩码失效）。
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

    ★ 脱敏点：遮蔽发生在 convert() **之前**（design.md D6）。
      值一旦进入记录对象就已经是掩码 → rec / children / last 缓存 / 异常栈里都没有明文。
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
                tf = coll.all_fields.get(path)
                if tf is not None and tf.type_conflict:
                    continue        # type 声明冲突 → 该字段按缺字段处理，不产出
                set_path(elem, segs,
                         convert(mask_value(value, tf, MASK_VALUE, MASK_HEURISTIC), tf))
            bucket = rec.setdefault(p0, [])
            if isinstance(bucket, dict):     # 容错：之前被写成 dict
                bucket = [bucket]
                rec[p0] = bucket
            bucket.append(elem)
        else:
            for path, value in items:
                segs = path.split(".")
                tf = coll.all_fields.get(path)
                if tf is not None and tf.type_conflict:
                    continue        # type 声明冲突 → 该字段按缺字段处理，不产出
                set_path(rec, segs,
                         convert(mask_value(value, tf, MASK_VALUE, MASK_HEURISTIC), tf))


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
            val = parse_literal(fld.default)
            if fld.is_secret():
                val = mask_value(val, fld, MASK_VALUE, MASK_HEURISTIC)
            set_path(rec, fld.path.split("."), convert(val, fld))


def run(colls, lines, trace=None):
    """trace: 可选列表，传入后记录每一行的匹配情况 (行号, 原始行, 命中集合名或 None)。

    这是 lint「命令覆盖率」统计的数据来源——必须来自真实匹配过程，
    事后用正则重测会把作用域外的同名命令误判成已覆盖。

    每次调用会把脱敏统计（masking.STATS）复位 → 读完即"本次运行"的统计。
    """
    STATS.reset()
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
        # ★ 脱敏点（写入时）：捕获到的值在这里就变成掩码，
        #   之后 writes / last 缓存 / 记录对象里都不再有明文。
        #   last 缓存必须存遮蔽后的值 —— 否则 inherit= 会把明文复制到普通字段，绕过遮蔽。
        masked = OrderedDict()
        for g, v in gd.items():
            if v is None:
                continue
            path = cr.group_paths.get(g, g)
            masked[path] = mask_value(v, coll.all_fields.get(path),
                                      MASK_VALUE, MASK_HEURISTIC)
        writes = list(masked.items())
        for path, mv in writes:
            last[(coll.name, path)] = mv

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
                    if grp and cr.group_paths.get(grp) in masked:
                        obj[mem] = masked[cr.group_paths[grp]]
                    elif mem in masked:
                        obj[mem] = masked[mem]
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
                    if n not in masked:
                        continue
                    out.append((n, masked[n]))
                else:
                    out.append((n, v))
            apply_writes(fr.rec, out, coll)
        return True
    return False


# ── lint ──────────────────────────────────────────────
def _rule_tokens(pattern):
    """把规则文本切成"词"：非字母数字非连字符的字符都是分隔符。

    连字符保留 → `default-password-template` 是一个整词，不会被拆出 "password"。
    """
    return [t for t in re.split(r"[^A-Za-z0-9-]+", pattern) if t]


def _rule_has_credential_keyword(pattern):
    """规则文本里有没有凭据关键字（整词匹配，避免 default-password-template 误报）。"""
    for tok in _rule_tokens(pattern):
        low = tok.lower()
        for kw in CREDENTIAL_KEYWORDS:
            if low == kw or low.startswith(kw + "-"):
                return True
    return False


def _name_is_credential(name):
    """字段名本身像不像凭据（词边界 → Password 算，PasswordType 不算）。"""
    low = name.lower()
    for kw in CREDENTIAL_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", low):
            return True
    return False


def _rule_fields(c, rule, coll_by_name):
    """规则引用到的字段（Field 对象）：${X} 引用 + -> 目标{} 里的字段。"""
    out = []
    for ref in re.findall(r"\$\{([A-Za-z_][\w.]*)\}", rule.pattern):
        f = c.all_fields.get(ref)
        if f is not None:
            out.append(f)
    if not rule.target:
        return out
    kind, a, b = parse_target(rule.target)
    if kind != "members":
        return out
    base, members = a, b
    if base == c.name:
        for mem in members:
            key = mem.split("=")[0].strip()
            f = c.all_fields.get(key)
            if f is not None:
                out.append(f)
    elif base in c.all_fields:
        for mem in members:
            key = mem.split("=")[0].strip()
            f = c.all_fields.get(base + "." + key)
            if f is not None:
                out.append(f)
    else:
        tc = coll_by_name.get(base)
        if tc is not None:
            for mem in members:
                key = mem.split("=")[0].strip()
                f = tc.all_fields.get(key)
                if f is not None:
                    out.append(f)
    return out


def lint(colls):
    """模板体检。返回 [(级别, 集合, 说明)]"""
    issues = []
    coll_by_name = {c.name: c for c in colls}
    for c in colls:
        if "Key" in c.attrs or "Required" in c.attrs:
            issues.append(("错误", c.name,
                           "集合级 Key(...)/Required(...) 已废弃，请写到字段的 option(Key/Required)"))
        if "Key" in c.flags or "Required" in c.flags:
            issues.append(("错误", c.name,
                           "集合级 Key/Required 已废弃，请写到字段的 option(Key/Required)"))
        key_fields = []
        for fld in c.all_fields.values():
            if fld.type_conflict:
                issues.append(("错误", c.name,
                               f"字段 {fld.path} 声明了多个 type：一个字段只能声明一种 type"
                               "（secret 与其它类型互斥）"))
            if fld.is_key and fld.parent is not None:
                issues.append(("错误", c.name,
                               f"option(Key) 字段 {fld.path} 必须是本集合的顶层字段"))
            if fld.is_key:
                key_fields.append(fld.path)
            # ① secret 字段不能当聚合键：遮蔽后所有记录的该字段值相同 → 聚合键全同 → 记录被合并丢失
            if fld.is_secret() and fld.is_key:
                issues.append(("错误", c.name,
                               f"字段 {fld.path} 同时声明了 option(Key) 和 type(secret)："
                               "遮蔽后所有记录的该字段值相同，聚合键会全部相同、不同记录被合并成一条"
                               "（静默丢数据）。请去掉该字段的 Key 标记，或改用非 secret 字段做键"))
            # ② inherit 不能指向 secret 字段：会把凭据复制到未标记 secret 的字段，绕过遮蔽
            if fld.inherit:
                src_coll, _, src_field = fld.inherit.partition(".")
                tgt = coll_by_name.get(src_coll)
                if tgt is not None:
                    tf = tgt.all_fields.get(src_field)
                    if tf is None and "." in src_field:
                        tf = tgt.all_fields.get(src_field.split(".")[-1])
                    if tf is not None and tf.is_secret():
                        issues.append(("错误", c.name,
                                       f"字段 {fld.path} 的 inherit={fld.inherit} 指向 secret 字段："
                                       "inherit 会把凭据复制到未标记 secret 的字段，绕过遮蔽"))
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
            # ③ 疑似漏标：规则文本含凭据关键字，但捕获的字段没声明 type(secret)
            if _rule_has_credential_keyword(r.pattern):
                refs = _rule_fields(c, r, coll_by_name)
                cred_bad = [f for f in refs if not f.is_secret() and _name_is_credential(f.name)]
                if cred_bad:
                    culprits = cred_bad
                elif any(f.is_secret() for f in refs):
                    culprits = []
                else:
                    culprits = [f for f in refs if not f.is_secret()]
                if culprits:
                    issues.append(("警告", c.name,
                                   f"规则 '{r.pattern}' 含凭据关键字"
                                   f"（{'/'.join(CREDENTIAL_KEYWORDS)}），但字段 "
                                   + "/".join(f.path for f in culprits)
                                   + " 未声明 type(secret)"))
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
    global MASK_VALUE, MASK_HEURISTIC

    argv = sys.argv[1:]
    args = []
    want_json = False
    want_lint = False
    mask_value = MASK_VALUE
    heuristic = MASK_HEURISTIC
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            want_json = True
        elif a == "--lint":
            want_lint = True
        elif a == "--mask-value":
            i += 1
            if i < len(argv):
                mask_value = argv[i]
        elif a == "--no-mask-heuristic":
            heuristic = False
        elif not a.startswith("--"):
            args.append(a)
        i += 1

    if len(args) < 2:
        print("用法: python tools/probe_stack.py <模板> <配置文件> [--json] [--lint] "
              "[--mask-value <掩码串>] [--no-mask-heuristic]")
        return 1

    MASK_VALUE = mask_value
    MASK_HEURISTIC = heuristic

    colls = parse_template(args[0])

    issues = lint(colls)
    if issues:
        # lint 报告是诊断信息：--json 时走 stderr，保证 stdout 是纯 JSON
        # （否则 `probe_stack.py ... --json > out.json` 出来的文件无法 json.load）
        report = ["=== lint ==="]
        report += [f"  [{lvl}] {coll}: {msg}" for lvl, coll, msg in issues]
        report.append("")
        if want_json:
            sys.stderr.write("\n".join(report) + "\n")
        else:
            print("\n".join(report))
    if want_lint:
        return 0

    lines = open(args[1], encoding="utf-8", errors="replace").readlines()
    res = run(colls, lines)

    # 脱敏统计写 stderr：stdout 留给 JSON / 文本输出，不污染
    sys.stderr.write(f"[mask] L1={STATS.l1_hits} L2={STATS.l2_hits} "
                     f"mask={MASK_VALUE!r} heuristic={MASK_HEURISTIC}\n")

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
