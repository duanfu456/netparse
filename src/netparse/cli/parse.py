"""parse / lint 子命令实现。

这两个子命令纯靠引擎，不需要生产脚本，也不碰只读语料 —— 是库最先要能跑起来的部分。
"""

import json
import os
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


def _parse_fields(spec):
    """解析 --fields 字符串 → run() 的 fields dict 形态。

    例：Interfaces=Name,Type,IpAddress;RouteStatics=Prefix,Mask,NextHop
    集合名不带 = → None（保留全部字段）
    """
    if not spec:
        return None
    out = {}
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        cname, _, flds = part.partition("=")
        cname = cname.strip()
        if flds.strip():
            out[cname] = {x.strip() for x in flds.split(",") if x.strip()}
        else:
            out[cname] = None
    return out or None


def _parse_where(spec):
    """解析 --where 字符串 → run() 的 where 形态（简写：字段 → 条件）。

    支持两种写法（首个空格分词）：
      "Field contains xxx"  → {"Field": "contains:xxx"}
      "Field=value"         → {"Field": "value"}（精确匹配）
    """
    if not spec:
        return None
    spec = spec.strip()
    if "=" in spec and " contains " not in spec:
        fld, _, val = spec.partition("=")
        return {fld.strip(): val.strip()}
    parts = spec.split(None, 2)
    if len(parts) == 3 and parts[1].lower() == "contains":
        return {parts[0]: "contains:" + parts[2]}
    raise SystemExit("错误：--where 语法应为 '字段 contains 子串' 或 '字段=精确值'，收到: %r" % spec)


def _write_csv(res, out_csv):
    """第一个 list 型集合 → CSV（utf-8-sig，Excel 直接打开）。"""
    import csv
    for k, arr in res.items():
        if isinstance(arr, list) and arr and isinstance(arr[0], dict):
            with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(arr[0].keys()))
                w.writeheader()
                w.writerows(arr)
            return k
    raise SystemExit("错误：--csv 需要至少一个非空 list 型集合")


def _collect_config_files(config):
    """config 参数展开：文件 → [自身]；文件夹 → 递归全部文件（按路径排序）。"""
    if os.path.isfile(config):
        return [config], False
    if os.path.isdir(config):
        root = os.path.abspath(config)
        files = []
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in sorted(filenames):
                files.append(os.path.join(dirpath, fn))
        files.sort()
        if not files:
            raise SystemExit("错误：文件夹里没有任何文件：%s" % config)
        return files, True
    raise SystemExit("错误：配置路径不存在：%s" % config)


def main(args):
    """`netparse parse <模板> <配置文件|配置文件夹> [输出路径]`"""
    if args.mask_value is not None:
        masking.CONFIG.value = args.mask_value
    if args.no_mask_heuristic:
        masking.CONFIG.heuristic = False

    # 解释器模式透传：--selftest 时段内 cfg=剩余配置路径、argv=剩余参数
    main_cfg = args.config if getattr(args, "selftest", False) and args.config else None
    main_argv = []
    if getattr(args, "selftest", False):
        rest = [a for a in (sys.argv[1:] or []) if a not in ("-m",)]
        # 取模板之后的参数（剔除已知 flag 开头的）
        i = rest.index(args.template) + 1 if args.template in rest else len(rest)
        main_argv = [a for a in rest[i:] if not a.startswith("-") and a != "--json" and a != "--selftest"]
    # ── -t/--test：跑 [__test__.NN] 测试段（不执行 [__main__]）──
    test_ids = getattr(args, "test", None)
    if test_ids:
        ids = None if test_ids == "all" else [x.strip() for x in test_ids.split(",") if x.strip()]
        engine.parse_template(args.template, tests_only=True, test_ids=ids)
        return 0

    colls = engine.parse_template(args.template, main_cfg=main_cfg, main_argv=main_argv)  # [__main__] 段在此执行

    # ── --selftest：只加载模板并执行 [__main__] 段，成功即 PASS ──
    if getattr(args, "selftest", False):
        if args.config:
            sys.stderr.write("[netparse] --selftest 忽略 config 参数: %s\n"
                             % args.config)
        issues = engine.lint(colls)
        errs = [x for x in issues if x[0] == "错误"]
        for lv, coll, msg in issues:
            sys.stderr.write("  [%s] %s: %s\n" % (lv, coll, msg))
        if errs:
            print("[FAIL] 模板加载/自测试失败（见上方错误，共 %d 条）" % len(errs))
            return 1
        print("[PASS] %s: [__main__] 自测试通过" % os.path.basename(args.template))
        return 0

    # ── --glob：多文件通配批量（run_files：目录/通配/递归/编码/过滤/CSV 一站式）──
    if getattr(args, "glob", None):
        import time as _time
        _t0 = _time.perf_counter()
        _where = _parse_where(getattr(args, "where", None))
        if _where:
            known = {f.path for c in colls for f in c.all_fields.values()}
            for _wf in _where:
                if _wf not in known and not any(_wf in k or k.startswith(_wf + ".") for k in known):
                    sys.stderr.write("[警告] --where 字段 %r 不在模板字段清单中"
                                     "（可能过滤出 0 条）\n" % _wf)
        b = engine.run_files(colls, args.glob,
                             encoding=getattr(args, "encoding", "utf-8"),
                             on_error="skip", provenance="file",
                             fields=_parse_fields(getattr(args, "fields", None)),
                             where=_parse_where(getattr(args, "where", None)))
        sys.stderr.write("[mask] L1=%d L2=%d\n"
                         % (masking.STATS.l1_hits, masking.STATS.l2_hits))
        if getattr(args, "stats", False):
            st = dict(b.stats)
            sys.stderr.write("[stats] files=%d ok=%d records=%d elapsed=%ss\n"
                             % (st.get("files", 0), st.get("files_ok", 0),
                                st.get("records", 0), st.get("elapsed", 0)))
        if args.csv:
            cname = _write_csv(b.records, args.csv)
            sys.stderr.write("[csv] %s → %s\n" % (cname, args.csv))
        out_path = args.output or args.config   # --glob 时 output 可落在 config 位置
        if out_path:
            os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(dict(b.records), fh, ensure_ascii=False, indent=2)
            print("批量解析完成（--glob）→ %s" % out_path)
        else:
            print(json.dumps(dict(b.records), ensure_ascii=False, indent=2))
        return 0


    if not args.config:
        sys.stderr.write("netparse parse: 缺少 config 参数"
                         "（只加载/自测模板请加 --selftest）\n")
        return 2

    # lint 是诊断信息：--json / 输出文件时走 stderr，保证 stdout / 文件是纯 JSON
    # （否则 `netparse parse ... --json > out.json` 出来的文件无法 json.load）
    issues = engine.lint(colls)
    if issues:
        report = ["=== lint ==="]
        report += ["  [%s] %s: %s" % (lv, coll, msg) for lv, coll, msg in issues]
        report.append("")
        stream = sys.stderr if (args.json or args.output) else sys.stdout
        stream.write("\n".join(report) + "\n")

    files, is_batch = _collect_config_files(args.config)

    # ── 批量模式：config 是文件夹 → 每个文件一个同名 .json，写入输出目录 ──
    if is_batch:
        out_dir = args.output
        if not out_dir:
            raise SystemExit("错误：config 是文件夹时必须指定输出目录（第三个参数）")
        os.makedirs(out_dir, exist_ok=True)
        root = os.path.abspath(args.config)
        failures = []
        for fp in files:
            rel = os.path.relpath(fp, root)
            try:
                with open(fp, encoding=getattr(args, "encoding", "utf-8"),
                          errors="replace") as f:
                    lines = f.readlines()
                res = engine.run(colls, lines,
                                 fields=_parse_fields(getattr(args, "fields", None)),
                                 where=_parse_where(getattr(args, "where", None)))
                # 相对路径映射到输出目录（保留子目录结构），扩展名换成 .json
                out_file = os.path.join(out_dir, os.path.splitext(rel)[0] + ".json")
                os.makedirs(os.path.dirname(os.path.abspath(out_file)), exist_ok=True)
                with open(out_file, "w", encoding="utf-8") as fh:
                    json.dump(res, fh, ensure_ascii=False, indent=2)
            except Exception as e:
                failures.append({"file": rel, "error": str(e)})
                sys.stderr.write("[fail] %s: %s\n" % (rel, e))
        sys.stderr.write("[mask] L1=%d L2=%d mask=%r heuristic=%s\n"
                         % (masking.STATS.l1_hits, masking.STATS.l2_hits,
                            masking.CONFIG.value, masking.CONFIG.heuristic))
        ok = len(files) - len(failures)
        print("批量解析完成：%d 文件 / 成功 %d / 失败 %d → %s"
              % (len(files), ok, len(failures), os.path.abspath(out_dir)))
        if failures:
            for f in failures:
                print("  失败: %s (%s)" % (f["file"], f["error"]))
        return 0 if not failures else 1

    # ── 单文件模式 ──
    with open(args.config, encoding=getattr(args, "encoding", "utf-8"),
              errors="replace") as f:
        lines = f.readlines()
    import time as _time
    _t0 = _time.perf_counter()
    res = engine.run(colls, lines, fields=_parse_fields(getattr(args, "fields", None)),
                     where=_parse_where(getattr(args, "where", None)))
    _elapsed = _time.perf_counter() - _t0

    # 脱敏统计写 stderr：stdout 留给 JSON / 文本输出，不污染
    sys.stderr.write("[mask] L1=%d L2=%d mask=%r heuristic=%s\n"
                     % (masking.STATS.l1_hits, masking.STATS.l2_hits,
                        masking.CONFIG.value, masking.CONFIG.heuristic))
    if getattr(args, "stats", False):
        rec_n = {k: len(v) for k, v in res.items() if isinstance(v, list)}
        sys.stderr.write("[stats] lines=%d records=%s elapsed=%.2fs\n"
                         % (len(lines), rec_n, _elapsed))

    if getattr(args, "csv", None):
        cname = _write_csv(res, args.csv)
        sys.stderr.write("[csv] %s → %s\n" % (cname, args.csv))

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)
        print("解析完成 → %s" % args.output)
        return 0

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
    """`netparse lint <模板>`——纯体检：加载收集但不执行 [__main__] 段（无副作用）"""
    colls = engine.parse_template(args.template, skip_main=True)
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
