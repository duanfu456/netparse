"""验证「精确缩进锚点」方案：不改引擎，只用 `^ {n}` 前缀区分层级。

华为配置的缩进是严格递增的（1 空格/层，318 台 0 异常）：
  顶层块首 indent=0
  接口本级命令 indent=1
  bas 视图正文 indent=2

若模板规则改用 `^ {n}` 锚定缩进，就能在同一个集合里区分不同层级，
不需要为 bas 建独立子集合，也不需要 end 配对。
"""
import os, re, sys, json, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from netparse import parse_template, run

D = "E:\\配置文件\\BRAS&SR\\原始配置\\cfgfile_20251224\\华为"

TPL = """
Interfaces type(list) start(^interface\\s+) end(^#$)
[value]
Name type(str) reg(\\S+) option(Key)
Description type(str) reg(.+)
UserVlan type(str) reg(\\S+)
VlanType type(str) reg(\\S+)
EthTrunk type(str) reg(\\S+)
Bas type(dict) desc(BAS 接入)
  AccessType type(str) reg(\\S+)
  AuthDomain type(str) reg(\\S+)
  Method type(str) reg(\\S+)
  NasLogicIp type(str) reg(\\S+)
  NasLogicPort type(str) reg(.+)
  AccessDelay type(int) reg(\\d+)
  DelayMode type(str) reg(\\S+)
  Option82 type(str) reg(.+)
  ArpTrigger type(bool) default(true)
  IpTrigger type(bool) default(true)
[rule]
^interface\\s+${Name}
^ {1}description\\s+${Description}\\s*$
^ {1}user-vlan\\s+${UserVlan}(?:\\s+.*)?\\s*$
^ {1}vlan-type\\s+${VlanType}(?:\\s+.*)?\\s*$
^ {1}eth-trunk\\s+${EthTrunk}(?:\\s+.*)?\\s*$
^ {2}access-type\\s+${Bas.AccessType}\\s+default-domain\\s+authentication\\s+${Bas.AuthDomain}(?:\\s+.*)?\\s*$
^ {2}access-type\\s+${Bas.AccessType}(?:\\s+.*)?\\s*$
^ {2}authentication-method\\s+${Bas.Method}(?:\\s+.*)?\\s*$
^ {2}nas logic-ip\\s+${Bas.NasLogicIp}(?:\\s+.*)?\\s*$
^ {2}nas logic-port\\s+${Bas.NasLogicPort}(?:\\s+.*)?\\s*$
^ {2}access-delay\\s+${Bas.AccessDelay}(?:\\s+(?P<DelayMode>\\S+))?(?:\\s+.*)?\\s*$
^ {2}client-option82\\s+${Bas.Option82}(?:\\s+.*)?\\s*$  -> Bas.Option82
^ {2}arp-trigger\\s*$   -> Bas.ArpTrigger=true
^ {2}ip-trigger\\s*$    -> Bas.IpTrigger=true
"""

RAW = """interface Eth-Trunk4.1
 description TO-DaQiaoLu
 user-vlan 2500
 bas
 #
  access-type layer2-subscriber default-domain authentication zhuanxian-dhcp
  authentication-method web
  ip-trigger
  arp-trigger
 #
#
interface Eth-Trunk4.9
 description ont-RMS
 user-vlan 9
 bas
 #
  access-type layer2-subscriber default-domain authentication ont_rms_01
  authentication-method bind
  arp-proxy
  nas logic-ip 218.200.1.118
  nas logic-port GigabitEthernet 6/1/0
  access-delay 300 odd-mac
  client-option82 basinfo-insert cn-telecom
 #
#
interface Eth-Trunk4.190
 vlan-type dot1q 190
 description TO-DianXinJu-Manager
#

"""

fd, p = tempfile.mkstemp(suffix=".cmdb")
os.close(fd)
open(p, "w", encoding="utf-8").write(TPL)
tpl = parse_template(p)
os.unlink(p)

res = run(tpl, RAW.splitlines())
print(json.dumps(res, ensure_ascii=False, indent=2))
