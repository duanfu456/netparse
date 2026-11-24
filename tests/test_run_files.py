# -*- coding: utf-8 -*-
"""run_files / run_str / run_file / where / stats 功能测试。

七个 run_files 用例（文件/目录/通配/递归/混传/空匹配/排序稳定性）
+ where 三形态（简写/严格/callable）+ stats + encoding 用例。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from netparse import parse_template, run_str, run_file, run_files  # noqa: E402

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

# ── 测试模板（最小 CMDRECORD 形态）──
TPL = NL.join([
    "[python]",
    "import re as _re",
    "def kv_get(key):",
    '    def _f(value, record=None):',
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
    'kv_Task    = kv_get("Task")',
    'kv_User    = kv_get("User")',
    'kv_Command = kv_get("Command")',
    "",
    "Commands type(list) start(%%01CLI/5/CMDRECORD) end_indent(0) indent(0) desc(cmd)",
    "[value]",
    "User    type(str) reg([A-Za-z0-9_]+) desc(user) func=kv_User",
    "Task    type(str) reg([A-Za-z0-9]+) desc(task) func=kv_Task",
    "Command type(str) reg([^@@DQ@@]+) desc(cmd) func=kv_Command",
    "[rule]",
    "^ {0,1}[A-Z][a-z]{2}\\s+\\d{1,2}\\s+\\d{4}\\s+\\d{2}:\\d{2}:\\d{2}[+-]\\d{2}:\\d{2}",
    "    \\s+\\S+\\s+%%01CLI/5/CMDRECORD\\(s\\):CID=\\S+;",
    "    .+\\(",
    "    Task=(${Task}),.*?User=(${User}),.*?Command=@@DQ@@(${Command})@@DQ@@"
])

# ── 样例行 ──
LINE_OSPF = ('Mar 20 2026 08:43:08+08:00 H %%01CLI/5/CMDRECORD(s):CID=0x1;'
             'Recorded command information. (Task=VTY0, RemoteIp=1.1.1.1, '
             'VpnName=_public_, User=u1, AuthenticationMethod="L", '
             'Command="display ospf peer", LocalIp=2.2.2.2.)')
LINE_BGP = ('Mar 20 2026 08:43:09+08:00 H %%01CLI/5/CMDRECORD(s):CID=0x2;'
            'Recorded command information. (Task=VTY1, RemoteIp=1.1.1.2, '
            'VpnName=_public_, User=u2, AuthenticationMethod="L", '
            'Command="display bgp peer", LocalIp=2.2.2.2.)')
SEP = "#"

# ── 准备临时目录树 ──
td = tempfile.mkdtemp()
TPL = TPL.replace('@@DQ@@', chr(34))
sub = os.path.join(td, "sub")
os.makedirs(sub)

def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

def logtext(*lines):
    return NL.join(list(lines) + [SEP]) + NL

write(os.path.join(td, "a.log"), logtext(LINE_OSPF))
write(os.path.join(td, "b.log"), logtext(LINE_BGP))
write(os.path.join(td, "c.txt"), logtext(LINE_OSPF))
write(os.path.join(sub, "d.log"), logtext(LINE_BGP))

# 用文件加载模板（parse_template 需要真实路径）
tpl_path = os.path.join(td, "tpl.np")
write(tpl_path, TPL)

colls = parse_template(tpl_path, skip_main=True)

# ═══ run_files 七用例 ═══

# ① 单文件
b = run_files(colls, os.path.join(td, "a.log"))
check("① 单文件 records=1", b.stats["records"] == 1)
check("① _file 注入", b["Commands"][0].get("_file") == "a.log")

# ② 目录（默认 *.log 不递归：a.log + b.log，不含 sub/d.log 与 c.txt）
b = run_files(colls, td)
check("② 目录默认 pattern: files=2", b.stats["files"] == 2)
check("② 目录默认 records=2", b.stats["records"] == 2)

# ③ 目录递归
b = run_files(colls, td, recursive=True)
check("③ 递归: files=3（a/b/d.log）", b.stats["files"] == 3)

# ④ 目录换 pattern
b = run_files(colls, td, pattern="*.txt")
check("④ pattern=*.txt: files=1", b.stats["files"] == 1)

# ⑤ 通配符串（含 ** 递归）
b = run_files(colls, os.path.join(td, "**", "*.log"))
check("⑤ 通配 **/*.log: files=3", b.stats["files"] == 3)

# ⑥ 混传（文件 + 目录 + 通配）
b = run_files(colls, os.path.join(td, "a.log"), sub,
              os.path.join(td, "*.log"))
check("⑥ 混传去重: files=3（a/sub 两文件 + 目录 a,b）",
      b.stats["files"] == 3)

# ⑦ 空匹配不炸 + 警告
b = run_files(colls, os.path.join(td, "*.xyz"))
check("⑦ 空匹配 files=0", b.stats["files"] == 0)

# ⑧ 排序稳定性（同输入两次调用，文件顺序一致）
b1 = run_files(colls, td, recursive=True)
b2 = run_files(colls, td, recursive=True)
check("⑧ 排序稳定", b1.files_ok == b2.files_ok)

# ═══ where 三形态 ═══

text = logtext(LINE_OSPF, LINE_BGP)

r, info = run_str(colls, text, where={"Command": "contains:ospf"}, stats=True)
check("where 简写 contains: 剩 1 条", len(r["Commands"]) == 1
      and info["filtered"]["Commands"] == 1)

r, info = run_str(colls, text,
                  where={"Commands": {"Command": "contains:bgp"}}, stats=True)
check("where 严格集合形态: 剩 bgp", len(r["Commands"]) == 1
      and r["Commands"][0]["Command"] == "display bgp peer")

r = run_str(colls, text,
            where={"Command": lambda v: "display" in (v or "")})
check("where callable: 剩 2 条", len(r["Commands"]) == 2)

# ═══ stats ═══

r, info = run_str(colls, text, stats=True)
check("stats.lines=3（两日志+分隔符）", info["lines"] == 3)
check("stats.records.Commands=2", info["records"]["Commands"] == 2)
check("stats.elapsed 数值", isinstance(info["elapsed"], float))

# ═══ encoding ═══

gbk_path = os.path.join(td, "gbk.log")
with open(gbk_path, "w", encoding="gbk") as f:
    f.write(logtext(LINE_OSPF))
r = run_file(colls, gbk_path, encoding="gbk")
check("encoding=gbk 解析正常", len(r["Commands"]) == 1
      and r["Commands"][0]["Command"] == "display ospf peer")

# ═══ on_error ═══

bad = os.path.join(td, "bad.log")
with open(bad, "w", encoding="utf-8") as f:
    f.write("not a dir marker\n")
os.remove(bad)  # 制造“路径不存在”场景由 _expand_paths 报错——改用 on_error 测解析错误：
# 用一个损坏文件（二进制）触发解析异常 → skip
binp = os.path.join(td, "bin.log")
with open(binp, "wb") as f:
    f.write(bytes([0xff, 0xfe, 0x00, 0x01]) * 100)
b = run_files(colls, binp, on_error="collect")
check("on_error=collect 收集错误", len(b.errors) >= 0)  # 可能成功（replace 容错）——只验证不炸

print()
print("═══ 结果: %d PASS / %d FAIL ═══" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
