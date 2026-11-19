"""状态机语义原型 —— 验证「集合=状态，命中 start 即存档」

语义（用户定义）：
  遍历所有行
    ① 某行匹配到某集合的 start 正则
         → 存档当前记录
         → 当前状态 = 该集合，开始新记录
         → 本行继续用该集合的 [rule] 匹配
    ② 否则用当前状态的 [rule] 匹配本行
  EOF 时存档最后一条记录

  字段 filldown = 继承上一次匹配到的值（对应 textfsm 的 Filldown）

用法：python tools/probe_state.py <模板> <配置文件>
"""

import json
import re
import sys
from collections import OrderedDict


# ── 模板解析 ──────────────────────────────────────────
def split_tokens(s):
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


class Field:
    def __init__(self, name, attrs):
        self.name = name
        self.attrs = attrs
        self.default = attrs.get("default")
        self.reg = attrs.get("reg", r"\S+")
        # filldown            → 继承全局同名上次值
        # filldown=Coll.Field → 继承指定集合指定字段的上次值
        self.required = attrs.get("Required") is True
        self.inherit = attrs.get("inherit")


class Coll:
    def __init__(self, name, attrs):
        self.name = name
        self.attrs = attrs
        self.type = attrs.get("type", "list")
        kv = attrs.get("Key") or attrs.get("key")
        self.key = kv[0] if isinstance(kv, list) else kv
        rv = attrs.get("Required")
        self.required_fields = rv if isinstance(rv, list) else []
        self.start = attrs.get("start")
        self.end = attrs.get("end")
        self.fields = OrderedDict()
        self.rules = []



MOD_NOARG = {"Required"}
MOD_ARG   = {"Key"}
MOD_LIST  = {"Key", "Required"}


def parse_modifiers(toks):
    """解析集合/字段的属性。

    约定：
      Required            → 字段级必填（无参）
      Required <字段>     → 集合级必填字段
      Key <字段>          → 集合级唯一键
      type(x) reg(x) default(x) desc(x) start(x)  → 括号形式
      inherit=集合.字段    → 继承源（替代 textfsm 的 Filldown）
    """
    attrs = {}
    i = 1
    while i < len(toks):
        t = toks[i]
        m = re.match(r"^([A-Za-z_]\w*)(?:\((.*)\))?$", t)
        if m:
            k, v = m.group(1), m.group(2)
            if k in MOD_LIST and v is not None:
                attrs[k] = v.split()          # Key(A B) / Required(A B) → 字段名列表
            else:
                attrs[k] = v
            i += 1
            continue
        if "=" in t:
            k, _, v = t.partition("=")
            attrs[k] = v
            i += 1
            continue
        if t in MOD_ARG and i + 1 < len(toks):
            attrs[t] = toks[i + 1]
            i += 2
            continue
        if t in MOD_NOARG:
            attrs[t] = True
            i += 1
            continue
        i += 1
    return attrs


def parse_template(path):
    """解析模板。

    结构：
      · 空行 → 集合结束（mode 重置）
      · [value] / [rule] → 段标记
      · 段内行 → 字段 / 规则；缩进 > 段基准 → 上一条的续行
      · 段外行 → 集合声明
    """
    colls, cur, mode = OrderedDict(), None, None
    base_indent = None
    pending = None

    def flush_pending():
        nonlocal pending
        if pending is None:
            return
        kind, payload, coll = pending
        if kind == "rule":
            pat, target, at = payload
            m = re.match(r"^(.*?)\s*->\s*(.+)$", pat)
            if m:
                pat, target = m.group(1).strip(), m.group(2).strip()
            coll.rules.append((pat, target, at))
        else:
            coll.fields[payload.name] = payload
        pending = None

    for raw in open(path, encoding="utf-8"):
        s = raw.rstrip("\n")
        st = s.strip()
        if not st:
            flush_pending()
            mode = None
            base_indent = None
            continue
        if st.startswith("#"):
            continue
        low = st.lower()
        if low in ("[value]", "[vlaue]", "[rule]"):
            flush_pending()
            mode = "value" if low != "[rule]" else "rule"
            base_indent = None
            continue
        if st.startswith("["):
            continue

        indent = len(s) - len(s.lstrip())
        toks = split_tokens(st)

        if pending is not None and base_indent is not None and indent > base_indent:
            kind, payload, coll = pending
            if kind == "rule":
                pat, target, at = payload
                pending = ("rule", (pat + st, target, at), coll)
            continue

        flush_pending()

        if mode is None:
            cur = Coll(toks[0], parse_modifiers(toks))
            colls[cur.name] = cur
            continue

        if mode == "value":
            if base_indent is None:
                base_indent = indent
            pending = ("field", Field(toks[0], parse_modifiers(toks)), cur)
        elif mode == "rule":
            if base_indent is None:
                base_indent = indent
            pending = ("rule", (st, None, {}), cur)

    flush_pending()
    return colls


def compile_rule(pattern, fields):
    """把 ${Field} 编译成命名捕获组"""
    seen = set()
    def sub(m):
        nm = m.group(1)
        if nm in seen:
            return f"(?P={nm})"
        seen.add(nm)
        fld = fields.get(nm)
        reg = fld.reg if fld else r"\S+"
        return f"(?P<{nm}>{reg})"
    return re.compile(re.sub(r"\$\{(\w+)\}", sub, pattern))


# ── 状态机 ────────────────────────────────────────────
def run(colls, lines, default_end=None):
    # 预编译每个集合的 start 正则与规则
    starts, opens = [], []
    for c in colls.values():
        if c.start:
            starts.append((c, re.compile(c.start)))
        else:
            opens.append(c)
        # end 可选：省略 = 用格式探测到的分隔符（由调用方注入 default_end）
        # end 可选：没写或写 end() → 不启用 end 匹配
        c.end_re = re.compile(c.end) if c.end else None
    compiled = {}
    for c in colls.values():
        compiled[c.name] = [(compile_rule(p, c.fields), t, o) for p, t, o in c.rules]

    state = None          # 当前集合（状态型）
    record = None         # 当前记录 {字段: 值}
    results = OrderedDict()
    last = {}             # (集合名, 字段名) -> 上次匹配到的值
    open_rec = {}         # 常开集合 -> 各自的记录（不随状态切换而存档）

    def flush():
        nonlocal record
        if record and state is not None:
            arr = results.setdefault(state.name, [])
            arr.append(record)
        record = None

    for no, raw in enumerate(lines, 1):
        line = raw.rstrip("\n").rstrip("\r")      # 保留缩进！
        if not line or (line.startswith("#") and len(line) <= 2):
            continue

        # ① 命中 start？→ 存档当前记录 → 切状态 → 开新记录
        hit_start = False
        for c, rx in starts:
            if rx.search(line):
                flush()
                state = c
                record = {}
                hit_start = True
                break

        # ①b 未命中 start 时，检查当前状态的 end → 离开该集合
        if not hit_start and state is not None and state.end_re is not None:
            if state.end_re.search(line):
                flush()
                state = None

        # ② 依次尝试：当前状态的规则 → 常开集合的规则
        cands = []
        if state is not None:
            cands.append((state, record))
        for oc in opens:
            cands.append((oc, None))       # None = 每命中产出一个独立元素

        for cstate, crec in cands:
            hit = False
            for rx, target, opts in compiled[cstate.name]:
                m = rx.match(line)
                if not m:
                    continue
                gd = m.groupdict()
                for k, v in gd.items():
                    if v is not None:
                        last[(cstate.name, k)] = v
                # inherit 继承
                for k, fld in cstate.fields.items():
                    if k in gd or not fld.inherit:
                        continue
                    cn2, _, fn2 = fld.inherit.partition(".")
                    if (cn2, fn2) in last:
                        gd[k] = last[(cn2, fn2)]
                # 目标组装
                tgt = crec if crec is not None else {}
                if target:
                    tm = re.match(r"^([\w.]+)(?:\{([^}]*)\})?$", target)
                    if tm:
                        base, members = tm.groups()
                        if members:
                            obj = {}
                            for mem in members.split(","):
                                mem = mem.strip()
                                if "=" in mem:
                                    k2, _, v2 = mem.partition("=")
                                    obj[k2.strip()] = v2.strip().strip(chr(34))
                                elif mem in gd:
                                    obj[mem] = gd[mem]
                            if base == cstate.name:
                                tgt.update(obj)      # 元素字段散落
                            else:
                                _set(tgt, base, obj)
                        else:
                            _set(tgt, base, list(gd.values())[0] if gd else None)
                else:
                    for k, v in gd.items():
                        if v is not None:
                            tgt[k] = v
                    # 省略 -> 时自动补上 inherit 继承来的字段
                    for k, fld in cstate.fields.items():
                        if k in tgt or not fld.inherit:
                            continue
                        cn2, _, fn2 = fld.inherit.partition(".")
                        if (cn2, fn2) in last:
                            tgt[k] = last[(cn2, fn2)]
                hit = True
                break
            if hit:
                if crec is None:
                    # 常开集合：每次命中产出一个元素
                    if cstate.type == "dict":
                        # dict 型：按 key 合并成一条
                        d = results.setdefault(cstate.name, {})
                        if isinstance(d, list):
                            d = d[0] if d else {}
                            results[cstate.name] = d
                        for k, v in tgt.items():
                            d[k] = v
                    else:
                        results.setdefault(cstate.name, []).append(tgt)
                break

    flush()
    for cn, rec in open_rec.items():
        if rec:
            results.setdefault(cn, []).append(rec)
    return results


def _set(obj, path, value):
    parts = path.split(".")
    for p in parts[:-1]:
        obj = obj.setdefault(p, {})
    obj[parts[-1]] = value


def main():
    colls = parse_template(sys.argv[1])
    print("集合:")
    for c in colls.values():
        print(f"  {c.name:<20} type={c.type:<6} start={c.start!r:<26} "
              f"fields={len(c.fields)} rules={len(c.rules)}")

    lines = open(sys.argv[2], encoding="utf-8", errors="replace").readlines()

    res = run(colls, lines)

    print("\n=== 状态机结果 ===")
    for k, arr in res.items():
        print(f"  {k:<20} {len(arr):>5} 条")

    ifaces = res.get("Interfaces", [])
    if ifaces:
        print(f"\n=== 前 3 个接口 ===")
        for it in ifaces[:3]:
            print("  " + json.dumps(it, ensure_ascii=False))
        ips = res.get("IpAddress", [])
        print(f"\n=== IP 前 3 个 ===")
        for it in ips[:3]:
            print("  " + json.dumps(it, ensure_ascii=False))


if __name__ == "__main__":
    main()
