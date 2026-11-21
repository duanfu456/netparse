# -*- coding: utf-8 -*-
"""netparse 模板编辑器 —— 本地 Web 服务。

启动：
    python editor/server.py                # 默认 127.0.0.1:8765
    python editor/server.py --port 9000

零第三方依赖：HTTP 用 http.server，lint/解析直接调 netparse.engine。
前端 editor/index.html 由本服务托管。

API：
    GET  /                     → 编辑器页面
    GET  /api/file?path=...    → 读文件（模板或配置，限仓库内白名单目录）
    POST /api/lint             → body {template: "..."} → lint 结果 JSON
    POST /api/parse            → body {template: "...", config: "..."} → 解析 JSON + trace 摘要
"""
import argparse
import json
import os
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

from netparse import engine  # noqa: E402
from netparse.masking import CONFIG as _MASK_CFG  # noqa: E402  (触发遮蔽初始化)

# 只允许读这些目录下的文件（防目录穿越）
_ALLOWED_DIRS = [
    os.path.join(_ROOT, "templates"),
    os.path.join(_ROOT, "tests", "fixtures"),
]


def _safe_path(rel):
    rel = rel.replace("\\", "/").lstrip("/")
    for d in _ALLOWED_DIRS:
        p = os.path.realpath(os.path.join(d, rel))
        if p.startswith(os.path.realpath(d) + os.sep) or p == os.path.realpath(d):
            if os.path.isfile(p):
                return p
    return None


def do_lint(template_text):
    """lint 一段模板文本，返回结构化结果（永不抛异常）。"""
    tmp = os.path.join(_HERE, "_live.cmdb")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(template_text)
    try:
        colls = engine.parse_template(tmp)
    except Exception as e:
        return {"ok": False, "errors": [{"level": "错误", "message": "模板解析失败: %s" % e}],
                "warnings": [], "tree": []}
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    problems = engine.lint(colls)          # [(level, coll_name, msg), ...] level ∈ 错误/警告/提示
    errors = [{"level": lv, "coll": cn, "message": msg}
              for lv, cn, msg in problems if lv == "错误"]
    warnings = [{"level": lv, "coll": cn, "message": msg}
                for lv, cn, msg in problems if lv != "错误"]
    tree = [{"name": c.name, "type": c.type,
             "start": c.start or "", "end": c.end or "",
             "parent": c.parent.name if c.parent else "",
             "fields": len(c.all_fields),
             "fieldNames": sorted(c.all_fields.keys()),
             "rules": len(c.rules)} for c in colls]
    # fieldNames 含嵌套路径（QosCar.Cir / Vrrp.VirtualIp.Address），
    # ${ 补全直接可用 —— all_fields 的 key 就是字段路径。
    return {"ok": not errors, "errors": errors, "warnings": warnings, "tree": tree}


def do_parse(template_text, config_text):
    """模板 + 配置 → 解析结果（含每行命中归属摘要）。"""
    tpath = os.path.join(_HERE, "_live.cmdb")
    cpath = os.path.join(_HERE, "_live_cfg.txt")
    with open(tpath, "w", encoding="utf-8") as fh:
        fh.write(template_text)
    with open(cpath, "w", encoding="utf-8") as fh:
        fh.write(config_text)
    try:
        colls = engine.parse_template(tpath)
        trace = []
        result = engine.run(colls, config_text.splitlines(), trace=trace)
    except Exception as e:
        return {"ok": False, "error": str(e), "result": None, "traceSummary": None}
    finally:
        for p in (tpath, cpath):
            try:
                os.remove(p)
            except OSError:
                pass
    # trace 摘要：只保留命中的行（行号、命令、命中集合）
    hits = [{"line": no, "text": ln.strip()[:120], "coll": hit}
            for no, ln, hit, _stk in trace if hit]
    size = len(json.dumps(result, ensure_ascii=False))
    return {"ok": True, "error": None, "result": result,
            "traceSummary": {"totalLines": len(trace), "hitLines": len(hits),
                             "hits": hits[:500], "resultBytes": size}}


def do_parse_fragment(fragment_text, config_text):
    """把选中片段包进临时上下文后试运行。

    片段可能是：① 独立集合声明块（自带 start/rule）→ 原样解析
    ② 接口内的零散规则行（无集合声明）→ 包一层 Interfaces 容器再跑
    """
    frag = fragment_text.strip()
    if not frag:
        return {"ok": False, "error": "片段为空", "result": None, "traceSummary": None}

    has_decl = re.match(r"^[A-Z]\w*\s+type\(", frag) is not None
    if has_decl:
        template = frag
    else:
        # 零散规则行：包进一个无 start 的临时容器（栈底常驻层）
        template = ("FragmentTest type(dict) desc(片段试运行临时容器)\n"
                    "[rule]\n" + frag)

    tpath = os.path.join(_HERE, "_live.cmdb")
    cpath = os.path.join(_HERE, "_live_cfg.txt")
    with open(tpath, "w", encoding="utf-8") as fh:
        fh.write(template)
    with open(cpath, "w", encoding="utf-8") as fh:
        fh.write(config_text)
    try:
        colls = engine.parse_template(tpath)
        trace = []
        result = engine.run(colls, config_text.splitlines(), trace=trace)
    except Exception as e:
        return {"ok": False, "error": str(e), "result": None, "traceSummary": None,
                "resolvedTemplate": template}
    finally:
        for p in (tpath, cpath):
            try:
                os.remove(p)
            except OSError:
                pass
    hits = [{"line": no, "text": ln.strip()[:120], "coll": hit}
            for no, ln, hit, _stk in trace if hit]
    return {"ok": True, "error": None, "result": result,
            "resolvedTemplate": template,
            "traceSummary": {"totalLines": len(trace), "hitLines": len(hits),
                             "hits": hits[:500], "resultBytes":
                             len(json.dumps(result, ensure_ascii=False))}}


def do_save(rel, content):
    """保存模板到白名单目录（templates / tests/fixtures）。"""
    p = _safe_path(rel)
    if not p:
        return {"ok": False, "error": "路径不在白名单目录"}
    # 只允许 .cmdb / .txt
    if not p.endswith((".cmdb", ".txt")):
        return {"ok": False, "error": "只允许保存 .cmdb / .txt"}
    # 保存前先 lint：有错误则拒绝落盘（防写坏生产模板）
    tmp = p + ".autosave-check"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
    try:
        colls = engine.parse_template(tmp)
        problems = [x for x in engine.lint(colls) if x[0] == "错误"]
    except Exception as e:
        problems = [("错误", "", str(e))]
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if problems:
        return {"ok": False,
                "error": "lint 未通过，拒绝保存: " + "; ".join(x[2] for x in problems[:3])}
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    return {"ok": True, "path": rel, "bytes": len(content.encode("utf-8"))}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(_HERE, "index.html"), "rb") as fh:
                return self._send(200, fh.read(), "text/html; charset=utf-8")
        if self.path.startswith("/api/words"):
            with open(os.path.join(_HERE, "words.json"), encoding="utf-8") as fh:
                return self._send(200, fh.read())
        if self.path.startswith("/api/file"):
            from urllib.parse import urlparse, parse_qs, unquote
            qs = parse_qs(urlparse(self.path).query)
            rel = unquote(qs.get("path", [""])[0])
            p = _safe_path(rel)
            if not p:
                return self._send(404, json.dumps({"error": "文件不存在或不在白名单目录"}))
            with open(p, encoding="utf-8", errors="replace") as fh:
                return self._send(200, json.dumps({"path": rel, "content": fh.read()}))
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        try:
            body = self._json_body()
        except Exception as e:
            return self._send(400, json.dumps({"error": "bad json: %s" % e}))
        if self.path == "/api/lint":
            return self._send(200, json.dumps(do_lint(body.get("template", "")),
                                              ensure_ascii=False))
        if self.path == "/api/parse":
            return self._send(200, json.dumps(
                do_parse(body.get("template", ""), body.get("config", "")),
                ensure_ascii=False))
        if self.path == "/api/parse-frag":
            return self._send(200, json.dumps(
                do_parse_fragment(body.get("fragment", ""), body.get("config", "")),
                ensure_ascii=False))
        if self.path == "/api/save":
            return self._send(200, json.dumps(do_save(body.get("path", ""),
                                                      body.get("content", "")),
                                              ensure_ascii=False))
        return self._send(404, json.dumps({"error": "not found"}))

    def log_message(self, fmt, *args):  # 安静模式
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("netparse 模板编辑器: http://127.0.0.1:%d/  (Ctrl+C 退出)" % args.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
