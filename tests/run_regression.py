"""模板语法回归测试 + 脱敏断言

跑两个 fixture：
  sample-vrp.txt         —— 通用语法回归（无凭据；明文扫描在它上面是空断言）
  sample-vrp-secret.txt  —— 脱敏专用：带密码的 peer / 不带密码的 peer /
                            带 sub 的地址 / 不带 sub 的地址 / 长的非凭据描述 /
                            形似凭据但不是凭据的命令（default-password-template）

明文扫描断言（tasks.md 1.6.2/1.6.3）：
  ① 逐值扫：命中密文形态（华为 %^%#…%^%# / 高熵长串）→ 报命中路径，退出码非 0
  ② 全文扫：结果 JSON 文本里出现 "%^%#" → 报命中位置，退出码非 0
  ③ 防空断言：脱敏 fixture 里必须真的出现掩码值，否则"扫描没东西可扫"= 没测到

用法：
    python tests/run_regression.py            # 比对
    python tests/run_regression.py --update   # 用当前输出覆盖基线
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from netparse import engine as P      # noqa: E402
from netparse.masking import MASK_DEFAULT, is_secret_shape  # noqa: E402

TEMPLATE = os.path.join(ROOT, "templates", "huawei-vrp.np")
FIXTURES = [
    ("sample-vrp", "sample-vrp.txt", "sample-vrp.expected.json"),
    ("sample-vrp-secret", "sample-vrp-secret.txt", "sample-vrp-secret.expected.json"),
]

CIPHER_MARKER = "%^%#"      # 华为密文包裹标记


def fixture_path(name):
    return os.path.join(HERE, "fixtures", name)


def parse(sample, heuristic=True):
    """解析一个 fixture。

    heuristic=False → 关掉 L2 形态兜底，只留 L1 显式标注。
    用来证明"模板标注齐全 ⇒ 零泄露"这条可审计的保证是真的（见 scan_plaintext 的用法）。
    """
    colls = P.parse_template(TEMPLATE)
    with open(sample, encoding="utf-8") as f:
        lines = f.readlines()
    old = P.MASK_HEURISTIC
    P.MASK_HEURISTIC = heuristic
    try:
        return P.run(colls, lines)
    finally:
        P.MASK_HEURISTIC = old


def diff(path, got, want, out):
    if isinstance(want, dict) and isinstance(got, dict):
        for k in sorted(set(want) | set(got)):
            if k.startswith("_"):
                continue
            diff(f"{path}.{k}", got.get(k), want.get(k), out)
    elif isinstance(want, list) and isinstance(got, list):
        if len(want) != len(got):
            out.append(f"{path}: 长度 {len(got)} != 基线 {len(want)}")
            return
        for i, (g, w) in enumerate(zip(got, want)):
            diff(f"{path}[{i}]", g, w, out)
    elif got != want:
        out.append(f"{path}: 实际 {got!r} != 基线 {want!r}")


def walk_values(obj, path=""):
    """遍历结果里所有字符串值，产出 (路径, 值)。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_values(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_values(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def scan_plaintext(res):
    """扫描解析结果里的明文。

    返回 (密文形态命中列表, 掩码值个数)
    """
    hits = []
    masked = 0
    for path, val in walk_values(res):
        if val == MASK_DEFAULT:
            masked += 1
        if is_secret_shape(val):
            hits.append((path, val))
    return hits, masked


def check_fixture(name, sample, baseline, problems, stats):
    res = parse(sample)
    with open(baseline, encoding="utf-8") as f:
        want = json.load(f)

    diff("", res, want, problems)

    # ① 逐值密文形态扫描
    hits, masked = scan_plaintext(res)
    for path, val in hits:
        problems.append(f"[{name}] 明文扫描命中（密文形态）: {path} = {val!r}")

    # ② 全文扫描（华为包裹标记）
    text = json.dumps(res, ensure_ascii=False)
    idx = text.find(CIPHER_MARKER)
    if idx >= 0:
        problems.append(f"[{name}] 明文扫描命中（{CIPHER_MARKER}）: 位置 {idx} 附近: "
                        f"...{text[max(0, idx - 40):idx + 40]}...")

    # ③ L1-only 扫描：关掉 L2 形态兜底后再扫一遍。
    #    L1（模板显式 type(secret)）才是可审计的保证 —— "模板标注齐全 ⇒ 零泄露"。
    #    这一步让"把 type(secret) 改回 type(str)"必然让回归失败（否则 L2 会兜住，
    #    明文扫描断言就成了永远通过的空断言）。
    hits_l1, _masked_l1 = scan_plaintext(parse(sample, heuristic=False))
    for path, val in hits_l1:
        problems.append(f"[{name}] 明文扫描命中（L1-only，关掉形态兜底后仍出现）: "
                        f"{path} = {val!r} → 该凭据字段没有声明 type(secret)")

    # ④ 防空断言：脱敏 fixture 必须真的被遮蔽过，否则扫描是空断言
    if name == "sample-vrp-secret":
        if masked == 0:
            problems.append(f"[{name}] 防空断言失败：结果里没有任何掩码值 → "
                            f"明文扫描断言是空断言（等于没测）")
        with open(sample, encoding="utf-8") as f:
            if CIPHER_MARKER not in f.read():
                problems.append(f"[{name}] 防空断言失败：fixture 里没有密文 → "
                                f"明文扫描断言是空断言（等于没测）")
        # 形似凭据但不是凭据的值不得出现（模板没这条命令 → 不该进输出）
        if "iptv" in text:
            problems.append(f"[{name}] default-password-template 的值意外出现在输出里")
        # 长描述必须原样保留（不能被 L2 误伤）
        long_desc = "TO-[SNANK-PC-CMNET-RT01-NE5000E]-Eth-Trunk6=6*10GEUPLINKCR01"
        if long_desc not in text:
            problems.append(f"[{name}] 长非凭据描述丢失或被遮蔽: {long_desc}")
        # secondary 地址：带 sub 的有 AddressType 键，不带 sub 的**没有**该键
        # （与 incumbent 的键存在性语义一致；删掉 fixture 里的 sub → 本断言失败）
        sub_cnt = 0; nosub_cnt = 0
        for it in res.get("Interfaces", []) or []:
            for ip in it.get("IpAddress", []) or []:
                if "AddressType" in ip:
                    sub_cnt += 1
                    if ip["AddressType"] != "sub":
                        problems.append(f"[{name}] AddressType 取值异常: {ip['AddressType']!r}")
                else:
                    nosub_cnt += 1
        if sub_cnt == 0:
            problems.append(f"[{name}] AddressType 断言失败：没有捕获到任何 sub 地址 → "
                            f"secondary 地址覆盖是空断言")
        if nosub_cnt == 0:
            problems.append(f"[{name}] AddressType 断言失败：没有非 sub 地址 → "
                            f"「键不存在」语义没被覆盖")

    stats.append((name, res, masked))
    return res


def main():
    problems = []
    stats = []

    if "--update" in sys.argv:
        for name, sample, baseline in FIXTURES:
            res = parse(fixture_path(sample))
            out = {"_comment": f"{sample} 用 templates/huawei-vrp.np 解析的期望输出"
                                 "（嵌套形状：子集合嵌在父元素里）。模板语法的回归基线。"}
            out.update(res)
            path = fixture_path(baseline)
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
            print(f"基线已更新: {path}")
        return 0

    for name, sample, baseline in FIXTURES:
        check_fixture(name, fixture_path(sample), fixture_path(baseline), problems, stats)

    # lint 必须没有错误
    issues = [i for i in P.lint(P.parse_template(TEMPLATE)) if i[0] == "错误"]
    for lvl, coll, msg in issues:
        problems.append(f"lint[{coll}]: {msg}")

    if problems:
        print(f"回归失败，共 {len(problems)} 处差异：")
        for p in problems[:40]:
            print("  -", p)
        return 1

    total = sum(len(r) for _n, r, _m in stats)
    print(f"回归通过：{len(stats)} 个 fixture / {total} 条记录，与基线一致")
    for name, res, masked in stats:
        print(f"  {name:<20} {len(res)} 个集合 / "
              f"{sum(len(v) for v in res.values())} 条记录，遮蔽 {masked} 处")
    return 0


if __name__ == "__main__":
    sys.exit(main())
