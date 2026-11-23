"""验证 [__main__] 段 exec 端到端（临时验证脚本）。"""
import sys
sys.path.insert(0, r'C:/Users/duanf/WorkBuddy/2026-11-19-14-44-56/netparse/src')
from netparse import parse_template
import os
import tempfile

td = tempfile.mkdtemp()
tpl = os.path.join(td, 't.cmdb')
content = (
    "Device type(list) desc(d)\n"
    "[value]\n"
    "SysName type(str) reg(\\S+) option(Key)\n"
    "[rule]\n"
    "^sysname\\s+${SysName}\\s*\n"
    "\n"
    "[__main__]\n"
    'netparse = r"D:/tools/netparse.exe"\n'
    'cfgstr = """\n'
    "sysname TEST-01\n"
    "#\n"
    '"""\n'
    "results = run(colls, cfgstr.splitlines())\n"
    'assert results["Device"][0]["SysName"] == "TEST-01"\n'
    'print("selftest OK:", results["Device"][0]["SysName"])\n'
)
open(tpl, 'w', encoding='utf-8').write(content)
c = parse_template(tpl)
print("PASS 段解析成功:", len(c), "个集合")
