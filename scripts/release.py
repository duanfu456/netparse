# -*- coding: utf-8 -*-
"""发布脚本：bump 版本 → 全套测试 → 打包 src.zip。

用法：
    python scripts/release.py 0.10.0     # 指定版本号
    python scripts/release.py            # 只跑测试+打包（用当前版本）
"""
import re
import subprocess
import sys
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "netparse")
DIST = os.path.join(ROOT, "dist")


def sh(cmd, **kw):
    print("+", cmd)
    r = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", **kw)
    if r.returncode != 0:
        print(r.stdout[-1500:])
        print(r.stderr[-1500:])
        sys.exit("命令失败: " + cmd)
    return r.stdout


def cur_version():
    m = re.search(r'__version__ = "([^"]+)"',
                  open(os.path.join(SRC, "__init__.py"), encoding="utf-8").read())
    return m.group(1)


def set_version(v):
    p = os.path.join(SRC, "__init__.py")
    s = open(p, encoding="utf-8").read()
    s = re.sub(r'__version__ = "[^"]+"', '__version__ = "%s"' % v, s)
    open(p, "w", encoding="utf-8").write(s)
    print("版本 →", v)


def run_tests():
    sh("python tests/run_regression.py")
    for t in ["test_transform.py", "test_masking.py", "test_engine_scope.py",
              "test_adapter.py", "test_cli.py", "test_run_files.py",
              "test_func_container.py"]:
        sh("python tests/" + t)
    sh("python -m netparse -m all templates/huawei/main.np")
    print("── 全套测试通过 ──")


def pack_src(version):
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "netparse-src-%s.zip" % version)
    skip_dirs = {"__pycache__", ".git", ".devforge", "node_modules", "dist"}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                if f.endswith((".pyc", ".pyo")):
                    continue
                fp = os.path.join(root, f)
                arc = os.path.relpath(fp, ROOT)
                z.write(fp, arc)
    print("打包 →", out)


def main():
    args = sys.argv[1:]
    if args:
        set_version(args[0])
    v = cur_version()
    print("当前版本:", v)
    run_tests()
    pack_src(v)
    print("── 发布完成（git commit 请手动执行）──")


if __name__ == "__main__":
    main()
