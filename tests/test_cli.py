"""CLI 与包结构验收。

覆盖三件事：
  ① 包能 import，公共 API 齐全（库用法本身要成立）
  ② `netparse <子命令>` 能跑通（parse / lint 不依赖生产语料）
  ③ `tools/` 下的兼容 shim 仍然可用（旧文档命令不能断）
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import netparse  # noqa: E402

TEMPLATE = os.path.join(ROOT, "templates", "huawei-vrp.cmdb")
FIXTURE = os.path.join(HERE, "fixtures", "sample-vrp-secret.txt")

FAILURES = []
PASSED = 0


def check(name, condition, extra=""):
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILURES.append("%s %s" % (name, extra))


def run_cli(args):
    """跑 `python -m netparse`，返回 (返回码, stdout, stderr)。"""
    env = dict(os.environ)
    env["PYTHONPARSE"] = "1"
    env["PYTHONPATH"] = os.path.join(ROOT, "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run([sys.executable, "-m", "netparse"] + args,
                          capture_output=True, text=True, env=env, cwd=ROOT)
    return proc.returncode, proc.stdout, proc.stderr


# ── ① 包与公共 API ───────────────────────────────────
PUBLIC = ["parse_template", "run", "lint", "compile_rule",
          "Field", "Coll", "Rule", "CompiledRule", "Frame",
          "split_tokens", "parse_modifiers", "is_coll_decl", "parse_literal",
          "parse_target", "convert", "get_path", "set_path", "apply_writes",
          "check_required", "merge_by_key", "apply_defaults",
          "CONFIG", "MASK_DEFAULT", "STATS", "MaskingStats",
          "is_secret_shape", "mask_value", "reset_stats",
          "repo_root", "templates_dir", "default_template", "baseline_template"]

missing = [n for n in PUBLIC if not hasattr(netparse, n)]
check("公共 API 齐全", not missing, "缺 %r" % missing)
check("版本号存在", isinstance(netparse.__version__, str) and netparse.__version__)

check("包在 src 下能被找到", os.path.isfile(os.path.join(ROOT, "src", "netparse", "engine.py")))
check("引擎不含 sys.path hack", "sys.path.insert" not in
      open(os.path.join(ROOT, "src", "netparse", "engine.py"), encoding="utf-8").read())
check("pyproject 存在且声明了入口",
      os.path.isfile(os.path.join(ROOT, "pyproject.toml")) and
      'netparse = "netparse.cli:main"' in
      open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read())

# ── ② CLI ────────────────────────────────────────────
code, out, err = run_cli(["--help"])
check("netparse --help 可用", code == 0 and "parse" in out and "lint" in out)

code, out, err = run_cli(["--version"])
check("netparse --version 输出版本", code == 0 and netparse.__version__ in out)

code, out, err = run_cli(["lint", TEMPLATE])
check("netparse lint 通过", code == 0, err[-300:])
check("lint 报错数为 0", "0 个错误" in out, out[-200:])

code, out, err = run_cli(["lint", TEMPLATE, "--json"])
check("lint --json 是合法 JSON", code == 0 and isinstance(json.loads(out), list))

code, out, err = run_cli(["parse", TEMPLATE, FIXTURE])
check("netparse parse 通过", code == 0, err[-300:])
check("parse 打印接口条数", "Interfaces" in out)

code, out, err = run_cli(["parse", TEMPLATE, FIXTURE, "--json"])
parsed = None
try:
    parsed = json.loads(out)
except ValueError:
    pass
check("parse --json 的 stdout 是纯 JSON", parsed is not None, out[:200])
check("parse --json 不含明文密文", "%^%#" not in out)
check("parse --json 含掩码", "******" in out)

# 关闭 L2 兜底后，L1 显式标注仍然遮蔽
code, out, err = run_cli(["parse", TEMPLATE, FIXTURE, "--json", "--no-mask-heuristic"])
check("--no-mask-heuristic 仍遮蔽 L1 字段", code == 0 and "******" in out and "%^%#" not in out)

# ── ③ 兼容 shim ──────────────────────────────────────
code, out, err = run_cli(["parse", TEMPLATE, FIXTURE])
shim = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "probe_stack.py"),
                       TEMPLATE, FIXTURE],
                      capture_output=True, text=True, cwd=ROOT)
check("tools/probe_stack.py 仍可运行", shim.returncode == 0, shim.stderr[-300:])

shim_lint = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "probe_stack.py"),
                            TEMPLATE, FIXTURE, "--lint"],
                           capture_output=True, text=True, cwd=ROOT)
check("tools/probe_stack.py --lint 参数被翻译成 lint 子命令",
      shim_lint.returncode == 0 and "0 个错误" in shim_lint.stdout, shim_lint.stderr[-300:])

# shim 的模块级全局必须代理到 CONFIG，否则 test_masking 那类赋值会静默失效
probe = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, 'tools');"
     "import probe_stack as P;"
     "import netparse;"
     "P.MASK_HEURISTIC = False;"
     "print(netparse.CONFIG.heuristic)"],
    capture_output=True, text=True, cwd=ROOT)
check("shim 的 MASK_HEURISTIC 写入会落到 CONFIG", probe.stdout.strip() == "False",
      probe.stderr[-300:])

# ── ④ legacy 探针也能跑 ─────────────────────────────
legacy = subprocess.run([sys.executable,
                         os.path.join(ROOT, "tools", "legacy", "probe_coverage3.py"),
                         TEMPLATE, FIXTURE],
                        capture_output=True, text=True, cwd=ROOT)
check("tools/legacy 探针可用", legacy.returncode == 0 and "覆盖率" in legacy.stdout,
      legacy.stderr[-300:])

# ── 汇总 ─────────────────────────────────────────────
print("CLI 与包结构验收：%d 项通过 / %d 项失败" % (PASSED, len(FAILURES)))
if FAILURES:
    for f in FAILURES:
        print("  - " + f)
    sys.exit(1)
print("全部通过")
