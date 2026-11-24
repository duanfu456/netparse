# -*- coding: utf-8 -*-
"""netparse Python 调用示例——覆盖最常见的四种用法。

安装：pip install netparse-cmdb   （或指向本地 src/ 目录）
import 名：netparse
"""
import json

# ══════════════════════════════════════════════════════════
# 0. 导入
# ══════════════════════════════════════════════════════════
from netparse import parse_template, run

# parse_template(模板路径)   → 加载模板（返回模板对象 colls）
# run(colls, 行列表或字符串) → 真正执行解析（返回 OrderedDict 结果）

# ══════════════════════════════════════════════════════════
# 1. 配置文件解析（华为 VRP 配置 → 结构化 JSON）
# ══════════════════════════════════════════════════════════
colls = parse_template("templates/huawei/00-device.np")

# 方式 A：直接传行列表
result = run(colls, [
    "sysname SMOKE-01",
    "#",
])
print("1A 配置解析:", json.dumps(dict(result), ensure_ascii=False))
# → {"Device": [{"SysName": "SMOKE-01"}]}

# 方式 B：传整段配置字符串（多行文本也可以，引擎按行拆）
config_text = """
sysname SMOKE-02
#
"""
result = run(colls, config_text)
print("1B 字符串解析:", json.dumps(dict(result), ensure_ascii=False))

# 方式 C：真机配置文件
result = run(colls, "设备配置.txt")   # 传文件路径也可以（自动识别）
# 注意：字符串 vs 文件路径的区分是引擎自动判断的——路径存在则按文件读

# ══════════════════════════════════════════════════════════
# 2. syslog 日志解析（CMDRECORD 操作指令提取）
# ══════════════════════════════════════════════════════════
syslog_colls = parse_template(
    r"E:\配置文件\解析模板\华为\syslog解析\syslog-huawei.np",
    skip_main=True,   # 跳过 [__main__] 段（它做批量 CSV，库调用不需要）
)

log_line = ('Mar 20 2026 08:43:08+08:00 SNBJI-SR01/NE40E-X16A '
            '%%01CLI/5/CMDRECORD(s):CID=0x1;Recorded command information. '
            '(Task=VTY0, RemoteIp=1.2.3.4, VpnName=_public_, User=test_user, '
            'AuthenticationMethod="Local-user", Command="display version", '
            'LocalIp=5.6.7.8.)')
result = run(syslog_colls, [log_line])
print("2 syslog 解析:", json.dumps(dict(result), ensure_ascii=False, indent=1))
# → {"Commands": [{"Time": "2026-03-20 08:43:08", "Host": "...", "Task": "VTY0",
#                 "RemoteIp": "1.2.3.4", "VpnName": "_public_", "User": "test_user",
#                 "AuthMethod": "Local-user", "Command": "display version",
#                 "LocalIp": "5.6.7.8"}]}

# ══════════════════════════════════════════════════════════
# 3. 整个日志文件批量解析
# ══════════════════════════════════════════════════════════
logfile = r"E:\项目管理\陕西移动\其他工作\000-日志分析\宝鸡SR-设备日志\宝鸡PSR1\log_17_20260325114347.log"
with open(logfile, encoding="utf-8", errors="replace") as f:
    lines = f.readlines()
result = run(syslog_colls, lines)
commands = result["Commands"]
print("3 批量解析:", len(commands), "条指令")
print("   第 1 条的 Command:", commands[0]["Command"])

# ══════════════════════════════════════════════════════════
# 4. 结果处理：转 JSON / 转 CSV
# ══════════════════════════════════════════════════════════
# 转 JSON 字符串
js = json.dumps(dict(result), ensure_ascii=False, indent=1)

# 转 DataFrame（pandas 可用 then to_excel/to_csv）
import csv
with open("操作指令.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(commands[0].keys()))
    w.writeheader()
    w.writerows(commands)
print("4 已写出 操作指令.csv")

# ══════════════════════════════════════════════════════════
# 5. 辅助 API
# ══════════════════════════════════════════════════════════
import netparse.engine as engine

# 模板体检（lint）：返回 [(级别, 集合名, 说明), ...]
issues = engine.lint(colls)
print("5 lint 问题数:", len(issues))

# 命令覆盖率 trace：trace[i] = (行号, 原始行, 命中集合名, 帧栈)
trace = []
result = run(colls, ["sysname X"], trace=trace)
print("5 trace:", trace)
print("5 trace:", trace)
