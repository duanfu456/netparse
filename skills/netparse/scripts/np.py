# -*- coding: utf-8 -*-
"""netparse skill 脚本：单入口封装，输出机器可读 JSON。

用法：
  python scripts/np.py parse  <模板> <配置文件|文件夹> [输出路径]   解析
  python scripts/np.py lint   <模板>                                模板体检
  python scripts/np.py fields <模板>                                列出模板全部字段
  python scripts/np.py info   <模板> <配置文件>                     摘要（集合/条数/字段统计）

所有输出都是 UTF-8 JSON（stdout），错误也结构化（{"ok": false, "error": ...}）。
优先用仓库 dist/netparse.exe；不存在则回退 python -m netparse / 直接调引擎。
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))          # skill/scripts -> 仓库根
EXE = os.path.join(REPO, "dist", "netparse.exe")


def _emit(obj):
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _exe(args):
    """走 exe（无 Python 依赖），输出纯文本时转 JSON 不便——仅用于透传场景。"""
    cmd = [EXE] + args
    r = subprocess.run(cmd, capture_output=True)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def _engine():
    sys.path.insert(0, os.path.join(REPO, "src"))
    from netparse import engine, masking   # noqa
    return engine


def cmd_parse(template, config, output=None):
    engine = _engine()
    if not os.path.exists(template):
        return _emit({"ok": False, "error": "模板不存在: %s" % template})
    if not os.path.exists(config):
        return _emit({"ok": False, "error": "配置路径不存在: %s" % config})

    colls = engine.parse_template(template)
    issues = [{"level": lv, "coll": c, "message": m} for lv, c, m in engine.lint(colls)]

    if os.path.isdir(config):
        root = os.path.abspath(config)
        files, failures, results = [], [], {}
        for dp, _dn, fns in os.walk(root):
            for fn in sorted(fns):
                files.append(os.path.join(dp, fn))
        for fp in sorted(files):
            rel = os.path.relpath(fp, root)
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    res = engine.run(colls, f.readlines())
                results[rel] = res
            except Exception as e:
                failures.append({"file": rel, "error": str(e)})
        payload = {"ok": not failures, "mode": "batch",
                   "source": root, "template": os.path.abspath(template),
                   "files": len(files), "ok": len(files) - len(failures),
                   "failed": len(failures), "failures": failures,
                   "results": results, "lint": issues}
        if output:
            os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
            for rel, res in results.items():
                out_f = os.path.join(output, os.path.splitext(rel)[0] + ".json")
                os.makedirs(os.path.dirname(os.path.abspath(out_f)), exist_ok=True)
                with open(out_f, "w", encoding="utf-8") as fh:
                    json.dump(res, fh, ensure_ascii=False, indent=2)
            payload["written"] = sorted(
                os.path.splitext(r)[0] + ".json" for r in results)
        return _emit(payload)

    with open(config, encoding="utf-8", errors="replace") as f:
        res = engine.run(colls, f.readlines())
    payload = {"ok": True, "mode": "single", "config": os.path.abspath(config),
               "template": os.path.abspath(template), "result": res, "lint": issues}
    if output:
        os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
        with open(output, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)
        payload["written"] = output
    return _emit(payload)


def cmd_lint(template):
    if not os.path.exists(template):
        return _emit({"ok": False, "error": "模板不存在: %s" % template})
    engine = _engine()
    colls = engine.parse_template(template)
    issues = [{"level": lv, "coll": c, "message": m} for lv, c, m in engine.lint(colls)]
    errors = [i for i in issues if i["level"] == "错误"]
    tree = [{"name": c.name, "type": c.type, "parent": c.parent.name if c.parent else "",
             "fields": len(c.all_fields), "rules": len(c.rules),
             "keyFields": c.key_fields} for c in colls]
    return _emit({"ok": not errors, "template": os.path.abspath(template),
                  "errors": len(errors),
                  "issues": issues, "collections": tree})


def cmd_fields(template):
    if not os.path.exists(template):
        return _emit({"ok": False, "error": "模板不存在: %s" % template})
    engine = _engine()
    colls = engine.parse_template(template)
    out = []
    for c in colls:
        flds = []
        for p, f in c.all_fields.items():
            flds.append({"path": p, "type": f.type, "key": f.is_key,
                         "required": f.required, "func": f.func,
                         "default": f.default, "desc": f.desc})
        out.append({"collection": c.name, "type": c.type,
                    "start": c.start, "end": c.end, "fields": flds})
    return _emit({"ok": True, "template": os.path.abspath(template), "collections": out})


def cmd_info(template, config):
    engine = _engine()
    colls = engine.parse_template(template)
    with open(config, encoding="utf-8", errors="replace") as f:
        res = engine.run(colls, f.readlines())
    summary = {}
    for name, records in res.items():
        recs = records if isinstance(records, list) else [records]
        summary[name] = {"count": len(recs),
                         "sample": recs[0] if recs else None}
    return _emit({"ok": True, "config": os.path.abspath(config),
                  "collections": summary})


def main():
    argv = sys.argv[1:]
    if not argv:
        return _emit({"ok": False, "error": "用法: np.py parse|lint|fields|info ..."})
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "parse" and len(rest) >= 2:
            return cmd_parse(rest[0], rest[1], rest[2] if len(rest) > 2 else None)
        if cmd == "lint" and len(rest) == 1:
            return cmd_lint(rest[0])
        if cmd == "fields" and len(rest) == 1:
            return cmd_fields(rest[0])
        if cmd == "info" and len(rest) == 2:
            return cmd_info(rest[0], rest[1])
        return _emit({"ok": False,
                      "error": "参数不匹配。用法: parse <模板> <配置> [输出] / lint <模板> / fields <模板> / info <模板> <配置>"})
    except SystemExit as e:
        return _emit({"ok": False, "error": str(e)})
    except Exception as e:
        return _emit({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})


if __name__ == "__main__":
    main()
