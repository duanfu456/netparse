"""最小定位：注入的 run 单参调用报错点。"""
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
    "^sysname\\s+${SysName}\n"
    "\n"
    "[__main__]\n"
    "r1 = run([\"sysname A\"])\n"
    'print("r1 ok:", r1["Device"][0]["SysName"])\n'
)
open(tpl, 'w', encoding='utf-8').write(content)
try:
    c = parse_template(tpl)
except Exception as e:
    print(type(e).__name__, ":", str(e)[-300:])
