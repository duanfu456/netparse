"""嵌套状态栈解析引擎（netparse 核心实现）。

语义（用户定义）
----------------
集合用缩进表达层级。每层是一个独立状态，有自己的 start / end / [value] / [rule]。

逐行处理：
  ① 从栈顶向下找第一个命中的 start（层 L）：
       · L 在栈上  → 存档 L 及其所有子层的当前记录，L 以下（更深）的层出栈
       · L 不在栈上 → 压入 L 成为新栈顶
       · 用 L 的 [rule] 匹配本行，结束
  ② 没命中任何 start：
       · end 从栈顶向下找第一个匹配层 → 收起该层及其所有子层
       · 用栈顶的 [rule] 匹配本行；不匹配则依次向下（父层）尝试

块分隔符：裸 `#` / `!`（长度 ≤ 2）是**块分隔符**，从栈顶向下反复收起所有 end 能匹配的层；
          更长的 `#` 行仍是注释。

输出结构：子集合的元素嵌在父元素的对应字段里（无需 inherit）。
聚合：同一父元素下，Key 相同的子记录合并成一个对象。

用法（库）::

    from netparse import parse_template, run
    colls = parse_template("templates/huawei-vrp.cmdb")
    result = run(colls, open("device.cfg", encoding="utf-8").readlines())

用法（命令行）::

    netparse parse <模板> <配置文件> [--json] [--lint]
"""
import os
import re
import sys as _sys
import types as _types
from collections import OrderedDict

from . import masking as _masking
from .masking import MASK_DEFAULT, STATS, mask_value

# ── 脱敏配置 ──────────────────────────────────────────
# 配置只存一份，放在 masking.CONFIG（原因见 MaskingConfig 的文档字符串）。
# 本模块**不**再定义 MASK_VALUE / MASK_HEURISTIC 两个全局 —— 一旦定义，
# `from netparse.engine import MASK_HEURISTIC` 就会拿到副本，赋值静默失效。
# 兼容旧写法：模块级 __getattr__ / __setattr__ 把 `engine.MASK_VALUE = x`
# 转发到 CONFIG，所以 `P.MASK_HEURISTIC = False` 仍然生效。
CONFIG = _masking.CONFIG

#: 字段同时声明了多个 type（如 type(secret) type(int)）时记这个标记
TYPE_CONFLICT_KEY = "__type_conflict__"

# ── transform 注册表 ─────────────────────────────────
# func= 引用的符号表：{名字: 可调用}。
# 来源：[python_file] 各模块公开符号 + [python] 段内联符号。
# 内联 > 文件（就近覆盖）。随 parse_template 结果存放在 Coll 之外的全局不可行
# （多模板并存），所以挂在 parse_template 返回的**列表容器属性**上。
import importlib.util as _imp_util

_RUNTIME_WARN_SEEN = set()   # (字段路径, 异常摘要) 去重，避免编辑器实时 lint 重复报

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
        self.func = attrs.get("func")      # transform 函数名（注册表查找）
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
    """一个集合（一层状态）。

    层级有三种表达方式，可以混用：

    1. start / end 正则配对（原有方式，适合 Nokia 的 exit、Cisco 的 !）
    2. indent(n) —— 精确缩进锚定。声明后，本集合的**所有规则**只在
       恰好 n 个前导空格的行上生效。华为配置缩进严格（318 台全部 0/1/2 层），
       用它可以把「接口本级」和「bas 视图正文」放进同一个集合而互不串味。
    3. end_indent(n) —— 裸 `#` / `!` 分隔符的层级语义。声明后，只有
       恰好 n 个前导空格的 `#` 才关闭本层；未声明则维持旧行为
       （任意 `#` 都可关闭本层）。

    indent 与 end_indent 都是 **opt-in**：不写就完全保持旧行为，老模板零影响。
    """

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

        # indent(n)：规则级缩进锚定。n 是**命令行的前导空格数**。
        self.anchor_indent = None
        self.indent_re = None
        raw = attrs.get("indent")
        if raw is not None:
            try:
                n = int(raw)
            except (TypeError, ValueError):
                raise ValueError(
                    "集合 %s 的 indent(...) 必须是整数，收到 %r" % (name, raw))
            self.anchor_indent = n
            self.indent_re = re.compile(r"^ {%d}\S" % n)

        # end_indent(n)：裸分隔符 `#` / `!` 的层级语义（opt-in）。
        self.end_indent = None
        raw = attrs.get("end_indent")
        if raw is not None:
            try:
                self.end_indent = int(raw)
            except (TypeError, ValueError):
                raise ValueError(
                    "集合 %s 的 end_indent(...) 必须是整数，收到 %r" % (name, raw))


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


def _parse_template_file(path, colls, py_file_lines, py_blocks,
                         seen_paths, imported_names, importing_from=None):
    """解析单个模板文件（include/import 的递归工作体）。

    colls/py_file_lines/py_blocks 是【主模板累计容器】：本文件的产物追加进去。
    seen_paths：已处理绝对路径集合（循环引用检测）。
    imported_names：已注册的顶层集合名 → 来源文件（同名冲突检测）。
    importing_from：非 None 时为集合名集合（from ... import A, B 语法），
        只保留点名顶层集合（子集合随父）。
    返回：实际并入的顶层集合名列表。
    """
    tpath = os.path.abspath(path)
    if tpath in seen_paths["on_stack"]:
        raise TemplateFileError(
            "%s: 循环 include/import 检测到（已在解析链中）" % path)
    if tpath in seen_paths["done"]:
        return []                # 菱形依赖：已完整加载过 → 幂等跳过
    seen_paths["on_stack"].add(tpath)

    colls_before = len(colls)
    stack = []
    cur = None
    mode = None
    base_indent = None
    pending = None
    field_stack = []
    section_coll = None
    cur_py = None
    top_colls = []   # 本文件声明的顶层集合（判断 from 选择名单用）

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
        else:
            top_colls.append(coll)
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
            if mode not in ("rule", "python", "python_file"):
                mode = None
                field_stack = []
            base_indent = None
            continue
        if st.startswith("#"):
            continue
        low = st.lower()

        # ── include / from-import（仅段外有效）──
        if mode is None:
            m_imp = re.match(r"^import\s+(.+)$", st)
            m_from = re.match(r"^from\s+(\S+)\s+import\s+(.+)$", st)
            if m_from:
                rel, names_s = m_from.group(1), m_from.group(2)
                if not os.path.splitext(rel)[1]:
                    rel += ".cmdb"          # from x == from x.cmdb
                sub = os.path.normpath(
                    os.path.join(os.path.dirname(tpath), rel))
                if not os.path.isfile(sub):
                    raise TemplateFileError(
                        "%s: import 的模板不存在: %s" % (path, sub))
                names = [x.strip() for x in names_s.split(",") if x.strip()]
                sel = None if "*" in names else set(names)
                _parse_template_file(sub, colls, py_file_lines, py_blocks,
                                     seen_paths, imported_names,
                                     importing_from=sel)
                continue
            if m_imp:
                rel = m_imp.group(1).strip()
                if not os.path.splitext(rel)[1]:
                    rel += ".cmdb"          # import x == import x.cmdb
                sub = os.path.normpath(
                    os.path.join(os.path.dirname(tpath), rel))
                if not os.path.isfile(sub):
                    raise TemplateFileError(
                        "%s: import 的模板不存在: %s" % (path, sub))
                _parse_template_file(sub, colls, py_file_lines, py_blocks,
                                     seen_paths, imported_names, None)
                continue

        if low in ("[value]", "[vlaue]", "[rule]", "[python]", "[python_file]"):
            flush_pending()
            if low == "[python]":
                if cur_py:
                    py_blocks.append("\n".join(cur_py))
                cur_py = []
                mode = "python"
                continue
            if low == "[python_file]":
                if cur_py:
                    py_blocks.append("\n".join(cur_py))
                cur_py = None
                mode = "python_file"
                continue
            if cur_py:
                py_blocks.append("\n".join(cur_py))
                cur_py = None
            mode = "value" if low != "[rule]" else "rule"
            section_coll = cur
            base_indent = None
            field_stack = []
            continue
        if st.startswith("["):
            if mode == "python":
                if cur_py is None:
                    cur_py = []
                cur_py.append(s)
                continue
            raise ValueError(
                "%s: 未知的段头 %r（支持 [value] / [rule] / [python] / [python_file]）"
                % (path, st))
        if mode == "python":
            if re.match(r"^[A-Z]\w*\s+type\(", s):
                if cur_py:
                    py_blocks.append("\n".join(cur_py))
                cur_py = None
                mode = None
            else:
                if cur_py is None:
                    cur_py = []
                cur_py.append(s)
                continue
        if mode == "python_file":
            if re.match(r"^[A-Z]\w*\s+type\(", s):
                mode = None
            else:
                py_file_lines.append(s)
                continue

        indent = len(s) - len(s.lstrip())
        toks = split_tokens(st)

        if mode is None:
            new_collection(toks, indent)
            continue

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

        if is_coll_decl(toks):
            flush_pending()
            coll = new_collection(toks, indent)
            section_coll = coll
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
    if cur_py:
        py_blocks.append("\n".join(cur_py))

    for c in colls:
        c.key_fields = [f.path for f in c.all_fields.values()
                        if f.is_key and f.parent is None]

    # ── from 选择：只保留点名顶层集合（子集合随父），其余移除 ──
    merged_names = []
    if importing_from is not None:
        wanted = set(importing_from)
        keep = set()
        for tc in top_colls:
            if tc.name in wanted:
                keep.add(tc.name)
                # 子树全部保留
                todo = [tc]
                while todo:
                    cur_c = todo.pop()
                    for ch in cur_c.children:
                        keep.add(ch.name)
                        todo.append(ch)
        missing = wanted - {tc.name for tc in top_colls}
        if missing:
            raise TemplateFileError(
                "%s: import 的集合不存在: %s（可导入: %s）"
                % (path, "、".join(sorted(missing)),
                   "、".join(tc.name for tc in top_colls) or "（无）"))
        kept = []
        for c in colls[colls_before:]:
            if c.parent is None or c.name in keep or (
                    c.parent is not None and c.parent.name in keep):
                kept.append(c)
        # 按父链精确过滤：只留 wanted 集合与其全部后代
        keep_exact = set()
        todo = []
        for c in colls[colls_before:]:
            if c.parent is None and c.name in wanted:
                keep_exact.add(c.name)
                todo.append(c)
        while todo:
            cur_c = todo.pop()
            for ch in cur_c.children:
                keep_exact.add(ch.name)
                todo.append(ch)
        kept = [c for c in colls[colls_before:] if c.name in keep_exact]
        colls[:] = colls[:colls_before] + kept
        merged_names = [tc.name for tc in top_colls if tc.name in keep_exact]
    else:
        merged_names = [tc.name for tc in top_colls]

    # ── 同名冲突检测（跨文件顶层集合；已完成加载的文件跳过 → 不重复检测）──
    if tpath in seen_paths["done"]:
        return []
    for nm in merged_names:
        if nm in imported_names and imported_names[nm] != tpath:
            raise TemplateFileError(
                "集合 %s 重名：%s 与 %s 都定义了它（同一解析对象禁止两份定义）"
                % (nm, imported_names[nm], tpath))
        imported_names[nm] = tpath
    seen_paths["on_stack"].discard(tpath)
    seen_paths["done"].add(tpath)
    return merged_names


def parse_template(path):
    colls = []
    py_file_lines = []   # [python_file] 段的路径行（声明顺序）
    py_blocks = []       # [python] 段代码块（物理顺序，处理时统一按出现顺序执行）
    seen_paths = {"on_stack": set(), "done": set()}
    imported_names = {}
    _parse_template_file(path, colls, py_file_lines, py_blocks,
                         seen_paths, imported_names, None)
    tpath = os.path.abspath(path)

    # ── transform：加载 [python_file] + 执行 [python] ──
    registry = {}
    load_warnings = []
    tpl_dir = os.path.dirname(tpath)
    added_syspath = []
    try:
        for rel in py_file_lines:
            p = os.path.normpath(os.path.join(tpl_dir, rel))
            if not os.path.isfile(p):
                raise TemplateFileError(
                    "%s: [python_file] 指向的文件不存在: %s" % (path, p))
            if tpl_dir not in _sys.path:
                _sys.path.insert(0, tpl_dir)
                added_syspath.append(tpl_dir)
            modname = "_np_tf_" + re.sub(r"[^\w]", "_", os.path.splitext(rel)[0])
            spec = _imp_util.spec_from_file_location(modname, p)
            mod = _imp_util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for sym in dir(mod):
                if not sym.startswith("_") and callable(getattr(mod, sym)):
                    registry[sym] = getattr(mod, sym)
    finally:
        for d in added_syspath:
            try:
                _sys.path.remove(d)
            except ValueError:
                pass

    inline_ns = {"__name__": "_np_inline_%d" % id(colls)}
    for i, code in enumerate(py_blocks):
        try:
            exec(compile(code, "<%s [python] #%d>" % (path, i + 1), "exec"), inline_ns)
        except SyntaxError as e:
            raise TemplateFileError(
                "%s: [python] 段 #%d 语法错误: %s" % (path, i + 1, e)) from None
    for sym in list(inline_ns):
        if not sym.startswith("__") and callable(inline_ns[sym]):
            registry[sym] = inline_ns[sym]

    return _ParsedTemplate(colls, registry, load_warnings)


class _ParsedTemplate(list):
    """parse_template 的返回值：兼容 list（历史用法），额外携带 transform 注册表。"""

    def __init__(self, colls, registry, warnings):
        super().__init__(colls)
        self.registry = registry
        self.transform_warnings = warnings


class TemplateFileError(Exception):
    """[python_file] 缺失 / [python] 语法错误等模板级致命问题。"""


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

    # 手写的命名组也允许带点：`(?P<Sub.C>...)` 是字段路径的自然写法，
    # 但 Python re 的组名只能是标识符，点会直接 PatternError。
    # 这里统一把组名里的点换成 `__`，并在 group_paths 里登记回原路径，
    # 这样 `_apply` 写回记录时仍然落在 Sub.C 上。
    def norm_group(m):
        defined, referenced = m.group(1), m.group(2)
        name = defined if defined is not None else referenced
        if "." not in name:
            return m.group(0)
        flat = name.replace(".", "__")
        group_paths.setdefault(flat, name)
        path_groups.setdefault(name, flat)
        return f"(?P<{flat}>" if defined is not None else f"(?P={flat})"

    pattern = re.sub(r"\(\?P(?:<([^>=]+)>|=([\w.]+))", norm_group, pattern)

    # 尾部放宽自动补齐（2026-11-21）：规则若以 \s*$ / $ 结尾但没有「尾部放宽组」
    # (?:\s+.*)?，就在 \s*$ 前插入一个。解决「末字段是 \S+/\d+、行尾还有残余
    # token 时整行失配」——实测去掉手写尾巴会丢 2.9% 命中（qos car / ip urpf 重灾区）。
    # 已手写 (?:\s+.*)? 的规则不受影响（不重复插入）。
    stripped = pattern.rstrip()
    if stripped.endswith(("$", "\\s*$")) and "(?:\\s+.*)?" not in pattern:
        # 找到最后一个 $\ 前的位置：把 \s*$ 或 $ 替换为 (?:\s+.*)?\s*$
        if stripped.endswith("\\s*$"):
            body = stripped[:-len("\\s*$")]
            pattern = body + "(?:\\s+.*)?\\s*$"
        else:
            pattern = stripped[:-1] + "(?:\\s+.*)?$"

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


def apply_func(value, fld, registry, record, warnings, coll_name):
    """convert 之后执行字段声明的 func= transform。

    语义（design.md §2.3）：
    - registry 无此名字 → 保持原值（lint 负责报"引用不存在"，这里不炸）
    - 返回 None → 保持原值
    - 抛异常 → 捕获、保留原值、按 (字段路径, 异常摘要) 去重记警告
    """
    if fld is None or fld.func is None or value is None:
        return value
    fn = registry.get(fld.func)
    if fn is None:
        return value
    try:
        out = fn(value, record)
    except Exception as e:
        key = (fld.path, "%s: %s" % (type(e).__name__, e))
        if key not in _RUNTIME_WARN_SEEN:
            _RUNTIME_WARN_SEEN.add(key)
            warnings.append("[transform] %s.%s: 函数 %s 执行失败 %s: %s（保留原值 %r）"
                            % (coll_name, fld.path, fld.func,
                               type(e).__name__, e, value))
        return value
    if out is None:
        return value
    return out


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


def apply_writes(rec, writes, coll, registry=None, tw=None, coll_name=""):
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
                cv = convert(mask_value(value, tf, CONFIG.value, CONFIG.heuristic), tf)
                cv = apply_func(cv, tf, registry, rec, tw if tw is not None else [], coll_name)
                set_path(elem, segs, cv)
            bucket = rec.setdefault(p0, [])
            if isinstance(bucket, dict):     # 容错：之前被写成 dict
                bucket = [bucket]
                rec[p0] = bucket
            bucket.append(elem)
        else:
            # 首段是 dict，但**内部可能有 list 字段**（如
            # Ipv4Family.VpnTargets）。裸 set_path 会把 list 字段反复覆盖成
            # 一个 dict —— 三条 vpn-target 只剩最后一条。
            #
            # 语义与顶层 list 保持一致：同一次写入（= 同一行命令）里挂在
            # 同一个 list 前缀下的字段，归到**同一个新元素**；不同行的写入
            # 各追加一个元素。
            list_groups = OrderedDict()
            for path, value in items:
                segs = path.split(".")
                tf = coll.all_fields.get(path)
                if tf is not None and tf.type_conflict:
                    continue        # type 声明冲突 → 该字段按缺字段处理，不产出
                converted = convert(
                    mask_value(value, tf, CONFIG.value, CONFIG.heuristic), tf)
                converted = apply_func(converted, tf, registry, rec, tw if tw is not None else [], coll_name)
                lpre = _list_prefix(rec, segs, coll)
                if lpre is None:
                    set_path(rec, segs, converted)
                else:
                    key = tuple(lpre)
                    list_groups.setdefault(
                        key, []).append((segs[len(lpre):], converted))
            for lkey, sub in list_groups.items():
                elem = {}
                for segs, converted in sub:
                    set_path(elem, segs, converted)
                _append_to_list_path(rec, list(lkey), elem)


def _list_prefix(rec, segs, coll):
    """segs 路径里**第一个指向 type(list) 字段**的前缀（含该字段本身）。

    返回 None 表示这条路径一路都是 dict/标量，不涉及 list。
    """
    for i in range(1, len(segs)):
        fpath = ".".join(segs[:i + 1])
        fld = coll.all_fields.get(fpath)
        if fld is not None and fld.type == "list":
            return segs[:i + 1]
    return None


def _append_to_list_path(rec, lpre, elem):
    """沿 dict 路径走到 lpre 的父级，把 elem 追加到该 list 字段。"""
    cur = rec
    for p in lpre[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    bucket = cur.get(lpre[-1])
    if isinstance(bucket, dict):        # 容错：之前被写成 dict
        bucket = [bucket]
        cur[lpre[-1]] = bucket
    elif bucket is None:
        bucket = []
        cur[lpre[-1]] = bucket
    bucket.append(elem)


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


def derive_funcs(rec, coll, registry, tw, coll_name):
    """对『声明了 func 但记录里没有值』的字段补跑派生（value=None 传入）。

    典型场景：Type 由 func=iface_type(Name) 从同记录的 Name 派生——
    Type 在 [rule] 里没有对应捕获行，正常流程永远不会执行到它。
    规则：只有当函数**返回非 None** 才写入（函数自己决定缺依据时返回 None）。
    """
    for fld in coll.all_fields.values():
        if fld.func is None or fld.type == "list":
            continue
        if get_path(rec, fld.path) is not None:
            continue
        fn = registry.get(fld.func)
        if fn is None:
            continue
        segs = fld.path.split(".")
        if len(segs) > 1 and get_path(rec, ".".join(segs[:-1])) is None:
            continue                     # 父对象不存在 → 不凭空产出
        try:
            out = fn(None, rec)
        except Exception as e:
            key = (fld.path, "%s: %s" % (type(e).__name__, e))
            if key not in _RUNTIME_WARN_SEEN:
                _RUNTIME_WARN_SEEN.add(key)
                tw.append("[transform] %s.%s: 派生 %s 执行失败 %s: %s（字段留空）"
                          % (coll_name, fld.path, fld.func,
                             type(e).__name__, e))
            continue
        if out is not None:
            set_path(rec, segs, out)


def apply_defaults(rec, coll):
    """填充声明了 default 但没抓到值的字段。

    ⚠️ 嵌套字段（如 Bas.ArpTrigger）只在**父对象已经存在**时才填。
    否则一个根本没有 bas 块的接口也会凭空长出 `Bas: {ArpTrigger: true}` ——
    「没出现」和「出现但取默认」是两件事，不能混。
    """
    for fld in coll.all_fields.values():
        if fld.default is None or fld.type == "list":
            continue
        if get_path(rec, fld.path) is not None:
            continue
        segs = fld.path.split(".")
        if len(segs) > 1 and get_path(rec, ".".join(segs[:-1])) is None:
            continue                     # 父对象不存在 → 整个子结构都不产出
        val = parse_literal(fld.default)
        if fld.is_secret():
            val = mask_value(val, fld, CONFIG.value, CONFIG.heuristic)
        set_path(rec, segs, convert(val, fld))


def run(colls, lines, trace=None, fields=None):
    """trace: 可选列表，传入后记录每一行的匹配情况 (行号, 原始行, 命中集合名或 None)。

    这是 lint「命令覆盖率」统计的数据来源——必须来自真实匹配过程，
    事后用正则重测会把作用域外的同名命令误判成已覆盖。

    每次调用会把脱敏统计（masking.STATS）复位 → 读完即"本次运行"的统计。

    transform 警告收集在 colls.transform_warnings（每次 run 覆盖）。

    fields: 可选字段白名单（输出层裁剪，解析始终全量）。
      形态 1  dict: {"Interfaces": {"Name","Type","IpAddress"}, "Bgp": None}
              值 None/空集合 = 保留该集合全部字段
      形态 2  list: ["Interfaces.Name", "Interfaces.Type", "Bgp"]（点路径前缀）
      语义：未列出的集合 → 整个不输出；列出的集合未列字段 → 裁掉；
            Key 字段自动保留（记录身份不能丢）。
    """
    STATS.reset()
    registry = getattr(colls, "registry", {})
    _RUNTIME_WARN_SEEN.clear()
    tw = []
    colls.transform_warnings = tw
    ctx = {"registry": registry, "tw": tw}
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
            # 折叠条件看**子集合自己**的 type，不是父集合的。
            # 写错成 coll.type 时：Interfaces 是 list → 永远不折叠，
            # 声明为 type(dict) 的 Bas 也会被输出成 [{...}]（实测 10,462 条全是 list1）。
            as_dict = cdef is not None and cdef.type == "dict" and len(elems) == 1
            fr.rec[cname] = elems[0] if as_dict else elems
        apply_defaults(fr.rec, coll)
        derive_funcs(fr.rec, coll, registry, tw, coll.name)
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
        # 裸 `#` / `!` / `$` 是**块分隔符**，不是注释：华为全量配置 100% 用 `#`
        # （222 万行抽样：`#` 249282 次、`!` 0 次）；中兴 M6000 用 `$`
        # （64 台实测每台数百处，`$` 还带缩进变体：顶格关接口、缩进 2 关子块）。
        # 它必须参与 end 出栈，否则模板声明的 end(^#$) / end(^\s*\$\s*$) 永远不生效
        # —— 分层级会一路挂着不收起，后续无 start 的块（如 aaa）里的
        # description 会被归到上一个接口上。
        if len(st) <= 2 and st[:1] in ("#", "!", "$"):
            # 分隔符的层级语义分两档：
            #   · 集合声明了 end_indent(k) → 只有「恰好 k 个前导空格」的 # 才关它（opt-in，新语义）
            #   · 未声明 → 任意 # 都能关（旧行为，保证老模板与既有 fixture 不回归）
            # 为什么要分层：华为配置的 # 自带缩进，1 个空格 = 只关 1 层子视图。
            # 旧行为「一个 # 关整条嵌套块」会让 `bas` 块内的 # 连接口一起关掉。
            sep_indent = len(line) - len(line.lstrip())
            while len(stack) > 1:
                stop = None
                for depth in range(len(stack) - 1, 0, -1):
                    fr = stack[depth]
                    if not fr.coll.end_re:
                        continue
                    want = getattr(fr.coll, "end_indent", None)
                    if want is not None and sep_indent != want:
                        continue        # 缩进对不上，这个 # 关不掉这一层
                    if fr.coll.end_re.search(line):
                        stop = depth
                        break
                if stop is None:
                    break
                while len(stack) > stop + 1:
                    fr = stack.pop()
                    commit(fr)
                    frame_of.pop(fr.coll.name, None)
                fr = stack.pop()
                commit(fr)
                frame_of.pop(fr.coll.name, None)
            if trace is not None:
                trace.append((no, line, None, [f.coll.name for f in stack]))
            continue
        if st.startswith("#"):
            continue        # 非裸 `#` 的长行仍按注释处理

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
            # start 命中即算「本行已被消费」：`bas` / `interface xxx` 这类视图入口行
            # 由 start 语义吞掉，集合自己的 [rule] 里通常**没有**对应规则。
            # 若把 hit_name 绑在 _apply 的返回值上，这些行会被 trace 记成未命中。
            if kind == "self":
                fr = stack[depth]
                while len(stack) > depth + 1:
                    commit(stack.pop())
                commit(fr)
                fr.rec = {}
                fr.children = OrderedDict()
                _apply(fr, line, compiled, last, from_start=True, ctx=ctx)
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
                _apply(fr, line, compiled, last, from_start=True, ctx=ctx)
                hit_name = fr.coll.name
            else:   # new
                fr = Frame(coll)
                stack.append(fr)
                frame_of[coll.name] = fr
                _apply(fr, line, compiled, last, from_start=True, ctx=ctx)
                hit_name = fr.coll.name
            if trace is not None:
                trace.append((no, line, hit_name, [f.coll.name for f in stack]))
            continue

        # ② end 命中 → 出栈：从栈顶向下找「第一个 end 能匹配」的层，收起它连同其上所有子层。
        #    只收最内层（方案 X），不一路收到底 —— 一路收到底是分隔符 `#` 的专属语义，
        #    普通 end 命令（如 Nokia 的 exit）应该只关当前最内层块。
        #    为什么不能只查栈顶：Nokia 的 interface 块内常有 ipv6/egress 等子块，
        #    栈顶是子集合时父集合的 end 会匹配不到，导致 exit 永远关不掉接口块。
        stop = None
        for depth in range(len(stack) - 1, 0, -1):
            if stack[depth].coll.end_re and stack[depth].coll.end_re.search(line):
                stop = depth
                break
        if stop is not None:
            while len(stack) > stop + 1:
                fr = stack.pop()
                commit(fr)
                frame_of.pop(fr.coll.name, None)
            fr = stack.pop()
            commit(fr)
            frame_of.pop(fr.coll.name, None)

        # ③ 栈顶向下匹配；不匹配继续父层
        for depth in range(len(stack) - 1, -1, -1):
            if _apply(stack[depth], line, compiled, last, ctx=ctx):
                hit_name = stack[depth].coll.name
                break
        if trace is not None:
            trace.append((no, line, hit_name, [f.coll.name for f in stack]))

    while stack:
        commit(stack.pop())

    # 顶层集合不做 Key 合并：Key 聚合的语义是「同一父元素下的子记录合并」，
    # 顶层集合没有父元素，重名对象（如 Nokia 不同 router 下的同名接口）必须各留一条。
    if fields:
        return _filter_fields(results, fields, coll_by_name)
    return results


def _filter_fields(results, fields, coll_by_name):
    """字段白名单过滤（输出层）。解析始终全量，这里只裁剪视图。

    fields 归一化为 {集合名: 允许字段名集合 or None(全部)}。
    Key 字段自动保留；子集合字段用 点路径（IpAddress.Address）声明。
    """
    # 归一化
    norm = {}
    if isinstance(fields, dict):
        for coll_name, flds in fields.items():
            norm[coll_name] = set(flds) if flds else None
    else:   # list：点路径前缀
        for item in fields:
            c, _, f = item.partition(".")
            if c not in norm:
                norm[c] = None
            if f:
                if norm[c] is None:
                    norm[c] = set()
                norm[c].add(f)

    # 引用了不存在的集合名 → 报错并列出可用名（拼错静默吞数据比报错更糟）
    known = set(coll_by_name.keys())
    unknown = set(norm.keys()) - known
    if unknown:
        raise ValueError(
            "fields 引用了不存在的集合: %s（可用集合: %s）"
            % ("、".join(sorted(unknown)), "、".join(sorted(known))))

    out = {}
    for cname, records in results.items():
        allowed = norm.get(cname, "__absent__")
        if allowed == "__absent__":
            continue                     # 未列出的集合 → 不输出
        cdef = coll_by_name.get(cname)
        if allowed is None or cdef is None:
            out[cname] = records
            continue
        # Key 字段自动并入
        keep = set(allowed)
        for kf in (cdef.key_fields or []):
            keep.add(kf.split(".")[-1])
        def prune(rec):
            return {k: v for k, v in rec.items()
                    if k in keep or (cdef.all_fields.get(k) is not None
                                     and "." not in k and k not in keep
                                     and False)}
        def prune_deep(obj, prefix=""):
            row = {}
            for k, v in obj.items():
                path = (prefix + "." + k) if prefix else k
                fld = cdef.all_fields.get(path)
                # 子集合/视图（dict 或 list 容器字段）被点名 → 整棵子树保留
                if prefix == "" and k in keep and isinstance(v, (list, dict)):
                    row[k] = v
                    continue
                if isinstance(v, dict):
                    sub = prune_deep(v, path)
                    if sub:
                        row[k] = sub
                elif isinstance(v, list):
                    rows = []
                    for el in v:
                        if isinstance(el, dict):
                            sub = prune_deep(el, path)
                            if sub:
                                rows.append(sub)
                        else:
                            if path.split(".")[-1] in keep or (
                                    fld is not None and fld.is_key):
                                rows.append(el)
                    if rows:
                        row[k] = rows
                else:
                    if k in keep or (fld is not None and fld.is_key):
                        row[k] = v
            return row
        pruned = []
        for rec in records:
            pruned.append(prune_deep(rec))
        out[cname] = pruned
    return out


def _apply(fr, line, compiled, last, from_start=False, ctx=None):
    """用 fr 的规则匹配 line。命中返回 True。

    from_start=True 表示这一行是刚命中 start 的视图入口行（如 `bas`）。
    此时**不做缩进校验**：indent(n) 约束的是「本集合的规则只在缩进 n 生效」，
    而视图入口行本身属于父层级（`bas` 在缩进 1，而 BasView 正文在缩进 2）。
    把两者混在一起会让 bas 的 start 命中后立刻被缩进校验打回。

    ctx: run() 传入的 {'registry': ..., 'tw': [...]},transform 用。
    """
    ctx = ctx or {}
    registry = ctx.get("registry", {})
    tw = ctx.get("tw") if ctx.get("tw") is not None else []
    coll = fr.coll
    if coll.name not in compiled:
        return False
    # indent(n)：本集合只在自己那一层缩进上生效。
    # 华为配置里 `user-vlan`（缩进 1）和 `access-type`（缩进 2）都可能出现在
    # 同一个接口块下，靠这条卡住才不会被上一层的规则误收。
    if not from_start:
        anchor = getattr(coll, "indent_re", None)
        if anchor is not None and not anchor.match(line):
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
                                      CONFIG.value, CONFIG.heuristic)
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
            apply_writes(fr.rec, writes, coll, registry, tw, coll.name)
            return True

        # 多 target：'-> A{...};B{...}' 用 ';' 分割，每段独立执行完整 target 语义
        for tgt in [t.strip() for t in target.split(";")] or [target]:
            if not tgt:
                continue
            kind, a, b = parse_target(tgt)
            if kind == "members":
                base, members = a, b
                obj = OrderedDict()
                for mem in members:
                    if "=" in mem:
                        k2, _, v2 = mem.partition("=")
                        v2 = v2.strip()
                        # 引用赋值：右侧是裸标识符（捕获组/字段路径）→ 取捕获值
                        # 例：Out{Cir=In.Cir} → obj["Cir"] = In.Cir 捕获组的值
                        if re.fullmatch(r"[A-Za-z_][\w.]*", v2):
                            grp = (cr.path_groups.get(v2)
                                   or cr.path_groups.get(v2))
                            if grp and cr.group_paths.get(grp) in masked:
                                obj[k2.strip()] = masked[cr.group_paths[grp]]
                            elif v2 in masked:
                                obj[k2.strip()] = masked[v2]
                            # 引用不存在 → 不写该键（与 members 缺成员语义一致）
                        else:
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
                    apply_writes(fr.rec, [(k, v) for k, v in obj.items()], coll, registry, tw, coll.name)
                elif base in coll.all_fields:
                    # 目标是本层的嵌套字段（list → 追加元素，dict → 写进去）
                    apply_writes(fr.rec, [(base + "." + k, v) for k, v in obj.items()], coll, registry, tw, coll.name)
                else:
                    # 目标是一个（未展开的）子集合 → 塞进当前记录
                    fr.rec[base] = dict(obj)
            else:   # assigns
                items = a
                top_items = [(n, v) for n, v in items if "." not in n]
                path_items = [(n, v) for n, v in items if "." in n]
                out = []
                if top_items:
                    # 顶层字段：先铺捕获组，再写常量。
                    # ⚠️ v 为 None 表示 `-> A,B` 这种「只点名、不给常量」的写法，
                    #    值应当来自同名捕获组（已在 writes 里）。若直接 append
                    #    (n, None) 会把捕获到的真值覆盖成 None —— 实测让
                    #    `access-type … authentication ont_rms_01` 的 AccessType /
                    #    AuthDomain 双双击成 null。这里只补 writes 里没有的常量。
                    out += writes
                    written = {p for p, _ in writes}
                    for n, v in top_items:
                        if v is None:
                            if n in written:
                                continue        # 捕获组已经写过，别覆盖
                            if n in masked:
                                out.append((n, masked[n]))
                            continue
                        out.append((n, v))
                for n, v in path_items:
                    # 嵌套路径：只写这一个路径，不铺其它捕获组
                    if v is None:
                        if n not in masked:
                            continue
                        out.append((n, masked[n]))
                    else:
                        out.append((n, v))
                apply_writes(fr.rec, out, coll, registry, tw, coll.name)
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
    for tgt in [t.strip() for t in rule.target.split(";")] or [rule.target]:
        if not tgt:
            continue
        kind, a, b = parse_target(tgt)
        if kind != "members":
            continue
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
    registry = getattr(colls, "registry", {})
    # ── transform 检查 ──
    # ① func= 引用必须存在于注册表
    seen_funcs = {}
    for c in colls:
        for fld in c.all_fields.values():
            if fld.func is None:
                continue
            if fld.is_secret():
                issues.append(("错误", c.name,
                               f"secret 字段 {fld.path} 禁止 func=（防止凭据经函数外泄）"))
                continue
            if fld.func not in registry:
                issues.append(("错误", c.name,
                               f"字段 {fld.path} 的 func={fld.func} 未定义"
                               f"（检查 [python] 段或 [python_file] 文件）"))
                continue
            seen_funcs.setdefault(fld.func, []).append(c.name)
    # ② python_file 解析路径可见性（安全模型：可见即约束）
    for w in getattr(colls, "transform_warnings", []) or []:
        issues.append(("警告", "", w))
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
            for tgt in [t.strip() for t in r.target.split(";")] or [r.target]:
                if not tgt:
                    continue
                kind, a, b = parse_target(tgt)
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


# ── 兼容层：把旧的模块级全局名转发到 CONFIG ────────────
# PEP 562 的模块 __getattr__ 只能兜读；赋值要靠替换模块类。
# 这样 `engine.MASK_VALUE` / `engine.MASK_HEURISTIC = False` 的旧用法都还能用，
# 而且读和写都落到同一个 CONFIG 上，不会出现"设了没生效"。
_REMOVED_GLOBALS = {"MASK_VALUE": "value", "MASK_HEURISTIC": "heuristic"}


def __getattr__(name):
    field = _REMOVED_GLOBALS.get(name)
    if field is not None:
        return getattr(CONFIG, field)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


class _EngineModule(_types.ModuleType):
    def __setattr__(self, name, value):
        field = _REMOVED_GLOBALS.get(name)
        if field is not None:
            setattr(CONFIG, field, value)
            return
        super().__setattr__(name, value)


_sys.modules[__name__].__class__ = _EngineModule
