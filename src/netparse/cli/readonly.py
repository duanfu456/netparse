import argparse
import hashlib
import json
import os
import sys
import time

from ..paths import change_dir

DEFAULT_BASELINE = os.path.join(change_dir(), "readonly-baseline.json")

# 只读根目录：(候选路径列表, 是否算 sha1)
# ⚠️ 规格里写的是 `E:\配置文件\原始配置\`，实测不存在，
#    真实路径是 `E:\配置文件\BRAS&SR\原始配置\`（26 个月目录 / 10054 个文件 / 3.3GB）。
#    两个候选都留着，谁存在用谁 —— 记录进基线，事后可核对。
ROOTS = [
    ([r"E:\配置文件\BRAS&SR\原始配置", r"E:\配置文件\原始配置"], True),
    ([r"E:\配置文件\BRAS&SR\解析后"], False),
    ([r"E:\配置文件\配置解析\华为"], False),
]

CHUNK = 1024 * 1024


def sha1_of(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def pick_root(candidates):
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def snapshot(root, with_sha1):
    """遍历 root，返回 {相对路径: [size, mtime_ns, sha1或None]}"""
    out = {}
    base = os.path.abspath(root)
    for dp, _dn, fn in os.walk(base):
        for f in fn:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, base).replace(os.sep, "/")
            try:
                st = os.stat(p)
            except OSError:
                continue
            digest = None
            if with_sha1:
                try:
                    digest = sha1_of(p)
                except OSError:
                    digest = None
            out[rel] = [st.st_size, st.st_mtime_ns, digest]
    return out


def capture(baseline_path):
    roots_out = []
    for candidates, with_sha1 in ROOTS:
        root = pick_root(candidates)
        if root is None:
            roots_out.append({"candidates": candidates, "exists": False,
                              "sha1": with_sha1, "files": {}})
            print(f"  [缺失] {candidates[0]}")
            continue
        t0 = time.time()
        snap = snapshot(root, with_sha1)
        roots_out.append({"candidates": candidates, "path": root, "exists": True,
                          "sha1": with_sha1, "files": snap})
        print(f"  {root}: {len(snap)} 个文件"
              f"（{'size+mtime_ns+sha1' if with_sha1 else 'size+mtime_ns'}）"
              f" {time.time() - t0:.1f}s")
    data = {
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "roots": roots_out,
    }
    os.makedirs(os.path.dirname(baseline_path), exist_ok=True)
    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    total = sum(len(r["files"]) for r in roots_out)
    print(f"基线已写入: {baseline_path}（{total} 个文件条目）")
    return 0


def verify(baseline_path, only_root=None):
    if not os.path.isfile(baseline_path):
        print(f"基线不存在: {baseline_path}（先跑 --capture）")
        return 1
    with open(baseline_path, encoding="utf-8") as f:
        data = json.load(f)

    problems = []
    for r in data["roots"]:
        root = r.get("path")
        if only_root and root and only_root.lower() not in root.lower():
            continue
        if not r.get("exists") or not root or not os.path.isdir(root):
            problems.append(f"根目录不存在或未采集: {r.get('candidates')}")
            continue
        cur = snapshot(root, r.get("sha1", False))
        base = r["files"]
        for rel, meta in base.items():
            if rel not in cur:
                problems.append(f"文件消失: {root}\\{rel}")
                continue
            c = cur[rel]
            if c[0] != meta[0]:
                problems.append(f"size 变化: {root}\\{rel} {meta[0]} -> {c[0]}")
            if c[1] != meta[1]:
                problems.append(f"mtime 变化: {root}\\{rel}")
            if meta[2] and c[2] and meta[2] != c[2]:
                problems.append(f"sha1 变化: {root}\\{rel}")
        for rel in cur:
            if rel not in base:
                problems.append(f"新增文件: {root}\\{rel}")

    if problems:
        print(f"READ-ONLY 校验失败，共 {len(problems)} 处差异：")
        for p in problems[:40]:
            print("  -", p)
        if len(problems) > 40:
            print(f"  ...（另有 {len(problems) - 40} 处）")
        return 1
    print("READ-ONLY OK")
    return 0


def add_arguments(parser):
    parser.add_argument("--capture", action="store_true", help="采集基线")
    parser.add_argument("--verify", action="store_true", help="与基线比对")
    parser.add_argument("--baseline", default=DEFAULT_BASELINE, help="基线文件路径")
    parser.add_argument("--root", default=None, help="--verify 时只查这一个根")
    return parser


def main(args=None):
    if args is None:                     # 允许 `python -m netparse.cli.readonly` 直跑
        args = add_arguments(argparse.ArgumentParser()).parse_args()

    if args.capture:
        return capture(args.baseline)
    if args.verify:
        return verify(args.baseline, args.root)
    print("请给出 --capture 或 --verify", file=sys.stderr)
    return 1


if __name__ == "__main__":
    from . import main as _cli_main
    sys.exit(_cli_main(["readonly"] + sys.argv[1:]))
