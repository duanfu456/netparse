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
             "fields": len(c.all_fields), "rules": len(c.rules)} for c in colls]
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
