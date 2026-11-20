"""parse / lint 子命令实现。

这两个子命令纯靠引擎，不需要生产脚本，也不碰只读语料 —— 是库最先要能跑起来的部分。
"""

import json
import sys

from .. import engine
from .. import masking


def parse_file(template, config, mask_value=None, heuristic=True):
    """解析一个配置文件，返回 (集合树, 结果字典)。

    库用法入口：不想走命令行时直接调这个。
    """
    if mask_value is not None:
        masking.CONFIG.value = mask_value
    masking.CONFIG.heuristic = heuristic

    colls = engine.parse_template(template)
    with open(config, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    return colls, engine.run(colls, lines)


def _print_lint(issues, as_json):
    if as_json:
        print(json.dumps([{"level": lv, "collection": c, "message": m} for lv, c, m in issues],
                         ensure_ascii=False, indent=2))
        return
    print("=== lint ===")
    for lv, coll, msg in issues:
        print("  [%s] %s: %s" % (lv, coll, msg))


def main(args):
    """`netparse parse <模板> <配置文件>`"""
    if args.mask_value is not None:
        masking.CONFIG.value = args.mask_value
    if args.no_mask_heuristic:
        masking.CONFIG.heuristic = False

    colls = engine.parse_template(args.template)

    # lint 是诊断信息：--json 时走 stderr，保证 stdout 是纯 JSON
    # （否则 `netparse parse ... --json > out.json` 出来的文件无法 json.load）
    issues = engine.lint(colls)
    if issues:
        report = ["=== lint ==="]
        report += ["  [%s] %s: %s" % (lv, coll, msg) for lv, coll, msg in issues]
        report.append("")
        stream = sys.stderr if args.json else sys.stdout
        stream.write("\n".join(report) + "\n")

    with open(args.config, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    res = engine.run(colls, lines)

    # 脱敏统计写 stderr：stdout 留给 JSON / 文本输出，不污染
    sys.stderr.write("[mask] L1=%d L2=%d mask=%r heuristic=%s\n"
                     % (masking.STATS.l1_hits, masking.STATS.l2_hits,
                        masking.CONFIG.value, masking.CONFIG.heuristic))

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    print("集合树:")
    for c in colls:
        pad = "  " * c.indent
        key = ("Key=" + ",".join(c.key_fields)) if c.key_fields else ""
        print("%s%-22s type=%-6s start=%-22r fields=%d rules=%d %s"
              % (pad, c.name, c.type, c.start, len(c.all_fields), len(c.rules), key))

    print("\n=== 结果 ===")
    for k, arr in res.items():
        print("  %-22s %5d 条" % (k, len(arr)))

    for k in ("Interfaces", "IpAddress"):
        if res.get(k):
            print("\n=== %s 前 2 条 ===" % k)
            for it in res[k][:2]:
                print("  " + json.dumps(it, ensure_ascii=False)[:300])
    return 0


def main_lint(args):
    """`netparse lint <模板>`"""
    colls = engine.parse_template(args.template)
    issues = engine.lint(colls)
    _print_lint(issues, args.json)

    errors = [i for i in issues if i[0] == "错误"]
    warnings = [i for i in issues if i[0] == "警告"]
    if not args.json:
        print("\n%d 个错误 / %d 个警告" % (len(errors), len(warnings)))

    if errors:
        return 1
    if args.strict and warnings:
        return 1
    return 0
