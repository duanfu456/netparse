"""验证 [__main__] 脚本模式最终形态：run(cfgstr) / run(cfgpath) 直传。"""
import sys
sys.path.insert(0, r'C:/Users/duanf/WorkBuddy/2026-11-19-14-44-56/netparse/src')
from netparse import parse_template
import os
import tempfile

td = tempfile.mkdtemp()
# 外部配置文件
open(os.path.join(td, 'test.cfg'), 'w', encoding='utf-8').write(
    "sysname FROM-FILE\n#\n")

tpl = os.path.join(td, 't.cmdb')
content = (
    "Device type(list) desc(d)\n"
    "[value]\n"
    "SysName type(str) reg(\\S+) option(Key)\n"
    "[rule]\n"
    "^sysname\\s+${SysName}\\s*\n"
    "\n"
    "[__main__]\n"
    'cfgstr = """\n'
    "sysname FROM-STR\n"
    "#\n"
    '"""\n'
    'cfgpath = "test.cfg"\n'
    "r1 = run(cfgstr)\n"
    'assert r1["Device"][0]["SysName"] == "FROM-STR", r1\n'
    "r2 = run(cfgpath)\n"
    'assert r2["Device"][0]["SysName"] == "FROM-FILE", r2\n'
    'print("[__main__] OK: run(文本) 与 run(路径) 双形态通过")\n'
)
open(tpl, 'w', encoding='utf-8').write(content)
c = parse_template(tpl)
print("PASS 段解析成功:", len(c), "个集合")
