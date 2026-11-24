# -*- coding: utf-8 -*-
"""func 挂 list/dict 本身（洋葱时序 + 类型契约）测试。

- 内层子字段 func（每次写入）→ 外层 list/dict func（commit 时拿完整值）
- list func 返回 list[dict] / None；dict func 返回 dict / None
- 类型违约 → 保留原值 + 警告；[] 合法；None 合法
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from netparse import parse_template, run  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS]", name)
    else:
        FAIL += 1
        print("[FAIL]", name)


NL = chr(10)
BS = chr(92)
DQ = chr(34)

TPL = NL.join([
    "[python]",
    "import re as _re",
    "def kv_get(key):",
    "    def _f(value, record=None):",
    "        if not value:",
    "            return None",
    '        for p in _re.split(r",\\s*(?=[A-Za-z][A-Za-z0-9_]*=)", value):',
    '            if p.startswith(key + "="):',
    "                v = p[len(key) + 1:].strip()",
    "                return v.strip(chr(34)) or None",
    "        return None",
    "    _f.__name__ = 'kv_' + key",
    "    return _f",
    "",
    "kv_IfName  = kv_get('IfName')",
    "kv_Status  = kv_get('Status')",
    "kv_K       = kv_get('K')",
    "",
    "def filter_down(value, record=None):",
    '    """外层 list func：过滤 Status=down 的元素（value=完整 list[dict]）"""',
    "    return [x for x in value if x.get('Status') != 'down']",
    "",
    "def norm_sub(value, record=None):",
    '    """外层 dict func：整表替换"""',
    "    if not isinstance(value, dict):",
    "        return None",
    '    return {"K": (value.get("K") or "").upper()}',
    "",
    "def bad_list(value, record=None):",
    '    return ["not", "dicts"]',
    "",
    "Dev type(list) start(sysname) end(#\\s*$) indent(0) desc(d)",
    "[value]",
    "Name type(str) desc(name)",
    "Ifs type(list) desc(ifs) func=filter_down",
    "Ifs.IfName type(str) desc(ifname) func=kv_IfName",
    "Ifs.Status type(str) desc(status) func=kv_Status",
    "Sub type(dict) desc(sub) func=norm_sub",
    "Sub.K type(str) desc(k) func=kv_K",
    "[rule]",
    "^sysname\\s+${Name}",
    "^cmd\\s+${Ifs.IfName}\\s+${Ifs.Status}\\s+${Sub.K}",
])

td = tempfile.mkdtemp()
tpl_path = os.path.join(td, "tpl.np")
with open(tpl_path, "w", encoding="utf-8") as f:
    f.write(TPL)
colls = parse_template(tpl_path, skip_main=True)

LINE2 = ('Mar 20 2026 08:43:08+08:00 H %%01CLI/5/CMDRECORD(s):CID=0x1;'
         'Recorded command information. (Task=VTY0, IfName=G0/0/0, '
         'K=abc, Status=up)')

r = run(colls, ["sysname R1",
                "cmd x up abc",          # Ifs 元素1 (IfName=x?) — 注意: IfName=x
                "cmd G0/0/1 down z",     # Ifs 元素2 (down → 外层过滤掉)
                "cmd G0/0/2 up y",       # Ifs 元素3
                "#"])
d = r["Dev"][0]
ifs = d.get("Ifs", [])

# ① 洋葱时序：内层 func（kv）先跑完（IfName/Status 已提取），外层 filter_down
#    在 commit 时拿到完整元素并过滤 down
check("① 外层 list func 过滤 down", all(x.get("Status") != "down" for x in ifs))
check("① 内层 kv func 已提取（元素含 IfName/Status）",
      all("IfName" in x and "Status" in x for x in ifs))
check("① 保留 up 元素 2 条", len(ifs) == 2)

# ② 外层 dict func：整表替换（K 大写）
check("② dict func 整表替换 K 大写", d.get("Sub", {}).get("K") == "Y")

# ③ None 语义：list func 返回 None → 字段留空（用独立模板验证）
TPL_NONE = TPL.replace("def filter_down(value, record=None):",
                       "def filter_down(value, record=None):")
TPL_NONE = TPL_NONE.replace(
    "    return [x for x in value if x.get('Status') != 'down']",
    "    return None")
tpl2 = os.path.join(td, "tpl2.np")
with open(tpl2, "w", encoding="utf-8") as f:
    f.write(TPL_NONE)
c2 = parse_template(tpl2, skip_main=True)
r2 = run(c2, ["sysname R1", "cmd x up abc", "#"])
check("③ list func 返回 None → 字段留空",
      r2["Dev"][0].get("Ifs") is None)

# ④ 类型契约：list func 返回 list[str] → 保留原值 + 警告
TPL_BAD = TPL.replace("def filter_down(value, record=None):",
                      "def filter_down(value, record=None):")
TPL_BAD = TPL_BAD.replace(
    "    return [x for x in value if x.get('Status') != 'down']",
    "    return ['not', 'dicts']")
tpl3 = os.path.join(td, "tpl3.np")
with open(tpl3, "w", encoding="utf-8") as f:
    f.write(TPL_BAD)
c3 = parse_template(tpl3, skip_main=True)
r3 = run(c3, ["sysname R1", "cmd x up abc", "#"])
warned = any("list[dict]" in w for w in c3.transform_warnings)
check("④ 类型违约保留原值", r3["Dev"][0].get("Ifs") == [{"IfName": "x", "Status": "up"}])
check("④ 类型违约有警告", warned)

# ⑤ 空列表 [] 合法（清空）
TPL_EMPTY = TPL.replace(
    "    return [x for x in value if x.get('Status') != 'down']",
    "    return []")
tpl4 = os.path.join(td, "tpl4.np")
with open(tpl4, "w", encoding="utf-8") as f:
    f.write(TPL_EMPTY)
c4 = parse_template(tpl4, skip_main=True)
r4 = run(c4, ["sysname R1", "cmd x up abc", "#"])
check("⑤ [] 合法（清空）", r4["Dev"][0].get("Ifs") == [])

# ⑥ dict func 返回 None → dict 字段留空
TPL_SUBNONE = TPL.replace(
    '    return {"K": (value.get("K") or "").upper()}',
    "    return None")
tpl5 = os.path.join(td, "tpl5.np")
with open(tpl5, "w", encoding="utf-8") as f:
    f.write(TPL_SUBNONE)
c5 = parse_template(tpl5, skip_main=True)
r5 = run(c5, ["sysname R1", "cmd x up abc", "#"])
check("⑥ dict func 返回 None → 留空", r5["Dev"][0].get("Sub") is None)

print()
print("═══ 结果: %d PASS / %d FAIL ═══" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
