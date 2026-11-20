"""脱敏单元测试（能力 A · secret-mask）

覆盖 specs/secret-mask/spec.md 的 11 个 Requirement：
  L1 显式标记 · L2 密文形态兜底 · 掩码串可配 · 关闭兜底 · 键存在性语义 ·
  Required 校验不受影响 · lint 三条检查 · 脱敏在所有出口生效

用法:
    python tests/test_masking.py       # 退出码 0 = 全过
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from netparse import engine as P     # noqa: E402
from netparse import masking as M    # noqa: E402

FAILURES = []
PASSED = 0


def check(name, cond, extra=""):
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAILURES.append(f"{name}{(' :: ' + extra) if extra else ''}")


def eq(name, got, want):
    check(name, got == want, f"实际 {got!r} != 期望 {want!r}")


# ── 最小模板 / 配置构造 ────────────────────────────────
TPL_PEER = """\
BgpPeers type(list) start(^\\s*peer) desc(邻居)
[value]
Peer         type(str) reg(\\S+) desc(对端) option(Key)
AsNumber     type(int) reg(\\d+) desc(对端 AS)
PasswordType type(str) reg(\\S+) desc(密码类型)
Password     type(secret) reg(.+) desc(密码)
[rule]
^\\s*peer\\s+${Peer}\\s+password\\s+cipher\\s+${Password}\\s*$ -> BgpPeers{Peer,PasswordType="cipher",Password}
^\\s*peer\\s+${Peer}\\s+as-number\\s+${AsNumber}\\s*$ -> BgpPeers{Peer,AsNumber}
"""

TPL_PEER_PLAIN = TPL_PEER.replace("type(secret)", "type(str)")

TPL_INHERIT = """\
Src type(dict) desc(源)
[value]
Secret type(secret) reg(\\S+) desc(密钥)
[rule]
^set-key\\s+${Secret}\\s*$

Dst type(dict) desc(目标)
[value]
Copied type(str) reg(\\S+) desc(复制) inherit=Src.Secret
[rule]
^copy-key\\s*$
"""

TPL_LINT_KEY = """\
C type(list) desc(集合)
[value]
Password type(secret) reg(\\S+) desc(密码) option(Key)
[rule]
^peer\\s+${Password}\\s*$
"""

TPL_LINT_INHERIT = """\
Other type(dict) desc(其他)
[value]
Secret type(secret) reg(\\S+) desc(密钥)
[rule]
^set\\s+${Secret}\\s*$

Mine type(dict) desc(本集合)
[value]
Copied type(str) reg(\\S+) desc(复制) inherit=Other.Secret
[rule]
^copy\\s*$
"""

TPL_LINT_WARN = """\
C type(list) desc(集合)
[value]
Peer     type(str) reg(\\S+) desc(对端) option(Key)
Password type(str) reg(\\S+) desc(密码)
[rule]
^peer\\s+${Peer}\\s+password\\s+cipher\\s+${Password}\\s*$ -> C{Peer,Password}
"""

TPL_LINT_WARN_OK = TPL_LINT_WARN.replace("Password type(str)", "Password type(secret)")

TPL_LINT_NOWARN = """\
C type(list) desc(集合)
[value]
X type(str) reg(\\S+) desc(模板名) option(Key)
[rule]
^default-password-template\\s+${X}\\s*$
"""

CFG_PEER_WITH = """\
bgp 64650
 peer 218.200.1.10 password cipher %^%#;}4v<YDuLCwX%^%#
"""

CFG_PEER_WITHOUT = """\
bgp 64650
 peer 218.200.31.228 as-number 64650
"""

CFG_INHERIT = """\
set-key %^%#;}4v<YDuLCwX%^%#
copy-key
"""


def write_tmp(text, suffix):
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def parse(tpl_text, cfg_text):
    tpl = write_tmp(tpl_text, ".cmdb")
    cfg = write_tmp(cfg_text, ".cfg")
    try:
        colls = P.parse_template(tpl)
        with open(cfg, encoding="utf-8") as f:
            lines = f.readlines()
        return P.run(colls, lines)
    finally:
        os.unlink(tpl)
        os.unlink(cfg)


def lint_of(tpl_text):
    tpl = write_tmp(tpl_text, ".cmdb")
    try:
        return P.lint(P.parse_template(tpl))
    finally:
        os.unlink(tpl)


def run_cli(tpl_text, cfg_text, extra=()):
    tpl = write_tmp(tpl_text, ".cmdb")
    cfg = write_tmp(cfg_text, ".cfg")
    try:
        cmd = [sys.executable, os.path.join(ROOT, "tools", "probe_stack.py"), tpl, cfg,
               "--json"] + list(extra)
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        return p
    finally:
        os.unlink(tpl)
        os.unlink(cfg)


# ── A · L1 显式标记 ───────────────────────────────────
def test_l1_explicit():
    f = P.Field("Password", {"type": "secret"}, set())
    check("A1 secret 字段 is_secret() 为 True", f.is_secret() is True)
    eq("A2 secret 字段 type == 'secret'", f.type, "secret")
    f2 = P.Field("X", {}, set())
    eq("A3 默认 type 仍是 str", f2.type, "str")
    check("A4 普通字段 is_secret() 为 False", f2.is_secret() is False)

    eq("A5 convert() 对 secret 原样返回",
       P.convert("******", P.Field("P", {"type": "secret"}, set())), "******")
    eq("A6 convert() 对 int 字段行为不变（转不了回退原值）",
       P.convert("******", P.Field("P", {"type": "int"}, set())), "******")

    # 端到端：secret 字段被遮蔽
    res = parse(TPL_PEER, CFG_PEER_WITH)
    peer = res["BgpPeers"][0]
    eq("A7 secret 字段写入掩码串", peer.get("Password"), M.MASK_DEFAULT)
    check("A8 结果中不含密文原文", "%^%#" not in json.dumps(res, ensure_ascii=False))
    check("A9 结果中不含原始密码子串",
          "};4v<YDuLCwX" not in json.dumps(res, ensure_ascii=False))

    # 键存在性语义
    res2 = parse(TPL_PEER, CFG_PEER_WITHOUT)
    check("A10 无密码的记录里 Password 键不存在",
          "Password" not in res2["BgpPeers"][0])

    # Required 校验：被遮蔽的值视为已填写
    tpl = TPL_PEER.replace("Password     type(secret)", "Password     type(secret) option(Required)")
    res3 = parse(tpl, CFG_PEER_WITH)
    check("A11 secret + Required 的记录不被丢弃",
          len(res3.get("BgpPeers", [])) == 1)

    # secret 与其它类型互斥：lint 报错，字段不产出，不抛异常
    tpl2 = TPL_PEER.replace("Password     type(secret)", "Password     type(secret) type(int)")
    issues = lint_of(tpl2)
    check("A12 secret 与其它类型互斥 → lint 报错误",
          any(lv == "错误" and "type" in msg for lv, _c, msg in issues),
          str(issues))
    try:
        res4 = parse(tpl2, CFG_PEER_WITH)
        check("A13 类型冲突的字段不产出（按缺字段处理）",
              "Password" not in res4["BgpPeers"][0])
    except Exception as e:                       # noqa: BLE001
        check("A13 类型冲突的字段不产出（按缺字段处理）", False, f"抛异常: {e!r}")


# ── B · L2 密文形态兜底 ────────────────────────────────
def test_l2_shape():
    eq("B1 华为密文形态命中", M.is_secret_shape("%^%#;}4v<YDuLCwX%^%#"), True)
    eq("B2 加密套件名不命中", M.is_secret_shape("aes256_gcm"), False)
    eq("B3 长描述不命中",
       M.is_secret_shape("TO-[SNANK-PC-CMNET-RT01-NE5000E]-Eth-Trunk6=6*10GE"), False)
    eq("B4 IP 地址不命中", M.is_secret_shape("111.19.27.122"), False)
    eq("B5 普通词不命中", M.is_secret_shape("car5000m"), False)
    eq("B6 超长描述不命中",
       M.is_secret_shape("TO-[SNANK-PC-CMNET-RT01-NE5000E]-Eth-Trunk6=6*10GEUPLINKCR01"), False)
    eq("B7 纯数字不命中", M.is_secret_shape("12345678901234567890123456"), False)
    eq("B8 纯字母不命中", M.is_secret_shape("abcdefghijklmnopqrstuvwxyz"), False)
    eq("B9 长十六进制小写串不命中（2 类字符）",
       M.is_secret_shape("5d41402abc4b2a76b9719d911017c592"), False)

    eq("B9a 纯拼音长描述不命中",
       M.is_secret_shape("didianZCWanYaoBaoCaiYou35KVBian"), False)
    eq("B9b 多样无分隔符密文仍命中",
       M.is_secret_shape("A1b2C3d4E5f6G7h8I9j0K1l2"), True)

    # 漏标 → 兜底遮蔽
    res = parse(TPL_PEER_PLAIN, CFG_PEER_WITH)
    eq("B10 模板漏标时兜底遮蔽生效",
       res["BgpPeers"][0].get("Password"), M.MASK_DEFAULT)

    # 兜底命中计数
    M.reset_stats()
    parse(TPL_PEER_PLAIN, CFG_PEER_WITH)
    check("B11 兜底命中被计数", M.STATS.l2_hits >= 1, f"l2={M.STATS.l2_hits}")
    M.reset_stats()
    parse(TPL_PEER, CFG_PEER_WITH)
    check("B12 L1 显式标注不计入兜底命中", M.STATS.l2_hits == 0, f"l2={M.STATS.l2_hits}")
    check("B13 L1 显式标注计入 L1 计数", M.STATS.l1_hits >= 1, f"l1={M.STATS.l1_hits}")

    # 关键字误报：default-password-template iptv（模板有对应字段）
    tpl = """\
Templates type(list) desc(模板)
[value]
Name type(str) reg(\\S+) desc(名字) option(Key)
[rule]
^default-password-template\\s+${Name}\\s*$ -> Templates{Name}
"""
    res = parse(tpl, "default-password-template iptv\n")
    eq("B14 default-password-template 不被遮蔽",
       res["Templates"][0].get("Name"), "iptv")
    M.reset_stats()
    parse(tpl, "default-password-template iptv\n")
    check("B15 关键字误报不计入兜底命中", M.STATS.l2_hits == 0, f"l2={M.STATS.l2_hits}")

    # 形态误报：aes256_gcm
    tpl2 = """\
Suites type(list) desc(套件)
[value]
Cipher type(str) reg(\\S+) desc(套件) option(Key)
[rule]
^ssh\\s+server\\s+cipher\\s+${Cipher}\\s*$ -> Suites{Cipher}
"""
    res = parse(tpl2, "ssh server cipher aes256_gcm\n")
    eq("B16 加密套件不被遮蔽", res["Suites"][0].get("Cipher"), "aes256_gcm")

    # 关闭兜底（单元级）
    plain_fld = P.Field("Password", {"type": "str"}, set())
    eq("B17 关闭兜底时漏标字段保持明文",
       M.mask_value("%^%#;}4v<YDuLCwX%^%#", plain_fld, heuristic=False),
       "%^%#;}4v<YDuLCwX%^%#")


# ── C · 掩码串可配 ─────────────────────────────────────
def test_mask_value_configurable():
    fld = P.Field("Password", {"type": "secret"}, set())
    eq("C1 mask 参数可自定义",
       M.mask_value("%^%#x%^%#", fld, mask="REDACTED"), "REDACTED")
    eq("C2 默认掩码串是 6 个星号", M.MASK_DEFAULT, "******")
    eq("C3 非 secret 字段不受掩码串影响",
       M.mask_value("abc", P.Field("X", {"type": "str"}, set()), mask="REDACTED"), "abc")

    p = run_cli(TPL_PEER, CFG_PEER_WITH, ["--mask-value", "REDACTED"])
    check("C4 --mask-value 退出码 0", p.returncode == 0, p.stderr[-400:])
    d = json.loads(p.stdout)
    eq("C5 --mask-value 自定义掩码串生效",
       d["BgpPeers"][0].get("Password"), "REDACTED")
    check("C6 --mask-value 时无密文残留", "%^%#" not in p.stdout)


# ── D · 关闭启发式 ─────────────────────────────────────
def test_heuristic_switch():
    p = run_cli(TPL_PEER_PLAIN, CFG_PEER_WITH, ["--no-mask-heuristic"])
    check("D1 --no-mask-heuristic 退出码 0", p.returncode == 0, p.stderr[-400:])
    d = json.loads(p.stdout)
    eq("D2 --no-mask-heuristic 时漏标字段保持明文",
       d["BgpPeers"][0].get("Password"), "%^%#;}4v<YDuLCwX%^%#")

    # L1 不受 --no-mask-heuristic 影响
    p = run_cli(TPL_PEER, CFG_PEER_WITH, ["--no-mask-heuristic"])
    d = json.loads(p.stdout)
    eq("D3 --no-mask-heuristic 不影响 L1 显式标注",
       d["BgpPeers"][0].get("Password"), M.MASK_DEFAULT)


# ── E · last 缓存不含明文（inherit）────────────────────
def test_inherit_no_plaintext():
    res = parse(TPL_INHERIT, CFG_INHERIT)
    eq("E1 inherit 取到的是遮蔽后的值",
       res["Dst"][0].get("Copied"), M.MASK_DEFAULT)
    check("E2 inherit 未把明文复制出去",
          "};4v<YDuLCwX" not in json.dumps(res, ensure_ascii=False))


# ── F · lint 三条检查 ──────────────────────────────────
def test_lint():
    issues = lint_of(TPL_LINT_KEY)
    check("F1 option(Key) + type(secret) → 错误",
          any(lv == "错误" and "Password" in msg for lv, _c, msg in issues), str(issues))
    check("F2 错误信息给出修法",
          any(lv == "错误" and ("Key" in msg and "secret" in msg)
              for lv, _c, msg in issues), str(issues))

    issues = lint_of(TPL_LINT_INHERIT)
    check("F3 inherit 指向 secret 字段 → 错误",
          any(lv == "错误" and "Copied" in msg for lv, _c, msg in issues), str(issues))

    issues = lint_of(TPL_LINT_WARN)
    check("F4 疑似漏标 → 警告",
          any(lv == "警告" and "Password" in msg for lv, _c, msg in issues), str(issues))
    check("F5 警告不是错误级",
          not any(lv == "错误" for lv, _c, _m in issues), str(issues))

    issues = lint_of(TPL_LINT_WARN_OK)
    check("F6 已标 secret → 无漏标警告",
          not any(lv == "警告" for lv, _c, _m in issues), str(issues))

    issues = lint_of(TPL_LINT_NOWARN)
    check("F7 default-password-template 不误报",
          not any(lv == "警告" for lv, _c, _m in issues), str(issues))

    issues = lint_of(TPL_LINT_INHERIT.replace("Copied type(str) reg(\\S+) desc(复制) inherit=Other.Secret",
                                               "Copied type(str) reg(\\S+) desc(复制) inherit=Other.Plain"))
    check("F8 inherit 指向非 secret 字段不报错",
          not any(lv == "错误" for lv, _c, _m in issues), str(issues))


# ── G · 脱敏在所有出口生效 ─────────────────────────────
def test_all_outputs_masked():
    p = run_cli(TPL_PEER, CFG_PEER_WITH)
    check("G1 CLI --json 退出码 0", p.returncode == 0, p.stderr[-400:])
    check("G2 CLI 输出不含密文形态串", "%^%#" not in p.stdout)
    check("G3 CLI 输出含掩码串", M.MASK_DEFAULT in p.stdout)

    res = parse(TPL_PEER, CFG_PEER_WITH)     # Python API 直取
    check("G4 Python API 返回值已脱敏",
          "%^%#" not in json.dumps(res, ensure_ascii=False))


def main():
    for fn in (test_l1_explicit, test_l2_shape, test_mask_value_configurable,
               test_heuristic_switch, test_inherit_no_plaintext, test_lint,
               test_all_outputs_masked):
        try:
            fn()
        except Exception as e:                   # noqa: BLE001
            import traceback
            FAILURES.append(f"{fn.__name__} 抛异常: {e!r}\n{traceback.format_exc()}")

    print(f"脱敏单元测试：{PASSED} 项通过 / {len(FAILURES)} 项失败")
    if FAILURES:
        print("失败明细：")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
