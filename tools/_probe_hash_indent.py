"""验证：`#` 分隔符的缩进层级，能否只靠模板 end 写法区分。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from netparse import parse_template, run

RAW = """interface Eth-Trunk4.1
 description TO-DaQiaoLu
 user-vlan 2500
 bas
 #
  access-type layer2-subscriber default-domain authentication zhuanxian-dhcp
  authentication-method web
  nas logic-ip 218.200.1.118
 #
#
interface Eth-Trunk4.9
 description ont-RMS
 bas
 #
  access-type layer2-subscriber default-domain authentication ont_rms_01
 #
#
"""

# 方案 B：每层用不同缩进锚点
TPL_B = """
Interfaces type(list) start(^interface\\s+) end(^#$)
[value]
Name type(str) reg(\\S+) option(Key)
Description type(str) reg(.+)
UserVlan type(str) reg(\\S+)
[rule]
^interface\\s+${Name}
^\\s*description\\s+${Description}\\s*$
^\\s*user-vlan\\s+${UserVlan}\\s*$

  Bas type(list) start(^\\s*bas\\s*$) end(^\\s#$)
  [value]
  AccessType type(str) reg(\\S+)
  Method type(str) reg(\\S+)
  NasLogicIp type(str) reg(\\S+)
  [rule]
  ^\\s*access-type\\s+(?P<AccessType>\\S+)
  ^\\s*authentication-method\\s+(?P<Method>\\S+)
  ^\\s*nas\\s+logic-ip\\s+(?P<NasLogicIp>\\S+)
"""

print("=== 方案 B：Interfaces end(^#$)，Bas end(^\\s#$) ===")
tpl = parse_template_from_text = None


def load(text):
    import tempfile
    fd, p = tempfile.mkstemp(suffix=".cmdb")
    os.close(fd)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        return parse_template(p)
    finally:
        os.unlink(p)


tpl = load(TPL_B)
res = run(tpl, RAW.splitlines())
import json
print(json.dumps(res, ensure_ascii=False, indent=2))
