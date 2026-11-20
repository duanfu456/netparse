#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨厂商真实样本压力测试探针（只读）

本脚本**不修改**引擎 / 模板 / 已有探针，只读取它们并对真实配置样本做量化测量。

用法
----
    python tools/probe_sampling.py inventory            # 全量样本清单分布
    python tools/probe_sampling.py sample               # 生成分层抽样清单
    python tools/probe_sampling.py accuracy             # 接口解析准确率
    python tools/probe_sampling.py coverage             # 命令族覆盖率横向对比
    python tools/probe_sampling.py variants             # 格式变体识别
    python tools/probe_sampling.py quality              # 输出质量抽检
    python tools/probe_sampling.py cross                # 中兴套华为模板（语法差异量化）
    python tools/probe_sampling.py all                  # 全跑

抽样规则（可复现）
------------------
分层键 = (厂商, 月份, 设备角色类, 平台型号)。
每个 (厂商, 月份, 角色类) 取 **字典序第一个** 命中的文件 —— 纯确定性，无随机数。
诺基亚目录只有 7950XRS 一个平台 3 个文件 → 全部纳入。
"""

import json
import os
import re
import sys
import time
from collections import Counter, OrderedDict, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
# HERE 现在是 tools/legacy/，仓库根要再上两层（原来在 tools/ 时只上一层）
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)
from netparse import engine as P  # noqa: E402

RAW_ROOT = r"E:\配置文件\BRAS&SR\原始配置"
PARSE_ROOT = r"E:\配置文件\解析"
HW_TPL = os.path.join(ROOT, "templates", "huawei-vrp.cmdb")
NK_TPL = os.path.join(ROOT, "templates", "nokia-sros.cmdb")

VENDOR_HW = "华为"
VENDOR_ZTE = "中兴"
VENDOR_NK = "诺基亚"
VENDOR_H3C = "新华三"
VENDOR_RJ = "锐捷"


# ──────────────────────────────────────────────────────────
# 厂商识别 —— 按【文件内容特征】，不靠目录名/文件名
# ──────────────────────────────────────────────────────────
# 指纹按优先级顺序匹配；每个指纹 = (厂商, 正则, 说明)
VENDOR_SIGS = [
    (VENDOR_ZTE, re.compile(r"^!<(mim|system-config|if-intf)>", re.M), "ZTE M6000 分区标记"),
    (VENDOR_ZTE, re.compile(r"^commit-mode\s+default\s+automatic", re.M), "ZTE commit-mode"),
    (VENDOR_ZTE, re.compile(r"%Error 140303", re.M), "ZTE 9908 采集失败(错误捕获)"),
    (VENDOR_HW, re.compile(r"^!Software Version V\d{3}R", re.M), "Huawei VRP !Software Version"),
    (VENDOR_NK, re.compile(r"^#\s*Configuration format version", re.M), "Nokia SR OS 23.10 格式头"),
    (VENDOR_NK, re.compile(r"TiMOS-C-|ALCATEL SR|NOKIA SR |7750 SR", re.M), "Nokia TiMOS 版本横幅"),
    (VENDOR_H3C, re.compile(r"^\s*mdc\s+Admin id\s+\d+", re.M), "H3C Comware mdc Admin"),
    (VENDOR_H3C, re.compile(r"^\s*version\s+7\.\d+\.\d+", re.M), "H3C Comware version 7.x"),
    (VENDOR_RJ, re.compile(r"^Building configuration\.\.\.", re.M), "Ruijie Building configuration"),
    (VENDOR_RJ, re.compile(r"^version\s+11\.\d", re.M), "Ruijie version 11.x"),
]


def detect_vendor(path, head_bytes=65536):
    """按内容指纹判定厂商。返回 (厂商, 依据说明)。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(head_bytes)
    except OSError as e:
        return "未知", f"读取失败: {e}"
    for vendor, rx, why in VENDOR_SIGS:
        if rx.search(head):
            return vendor, why
    return "未知", "无匹配指纹"


def detect_platform_hint(path, head_bytes=65536):
    """从内容里抽取软件版本横幅（仅用于报告，不做厂商判定）。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(head_bytes)
    except OSError:
        return ""
    for rx in (r"^!Software Version (\S+)",
               r"TiMOS-C-([\w.]+)",
               r"^\s*version (\S+),\s*Release (\S+)",
               r"^version (\S+)",
               r"Configuration format version ([\d.]+)",
               r"^hostname (\S+)",
               r"^sysname (\S+)"):
        m = re.search(rx, head, re.M)
        if m:
            return " ".join(g for g in m.groups() if g)
    return ""



# ──────────────────────────────────────────────────────────
# 样本发现
# ──────────────────────────────────────────────────────────
def _norm_role(tok):
    """设备角色归一：BRAS02 / BAS03 / CE03 / SR01 → BRAS / BAS / CE / SR"""
    t = tok.upper()
    t = re.sub(r"\d+$", "", t)
    return t or tok.upper()


def _platform_of(fname):
    """文件名 <设备名>_<IP>.<时间戳> → 平台型号。

    取设备名最后一个 - 段；若该段以数字开头（M6000-18S / RG6510-1），
    则与前一段合并，避免把「18S」「1」当成独立型号。
    """
    name = fname.split("_")[0]
    parts = [p for p in name.split("-") if p]
    if not parts:
        return "?"
    last = parts[-1]
    if re.match(r"^\d+[A-Za-z]?$", last) and len(parts) >= 2:
        return (parts[-2] + "-" + last).upper()
    return last.upper()


RE_ROLE_TOKEN = re.compile(r"^[A-Z]{2,4}\d+$")


def _role_of(fname):
    """角色段 = 形如 BRAS02 / BAS03 / SR01 / CE03 的 token（字母前缀+数字）。

    命名结构在两个来源里不同：
      原始配置/  REGION-MC-CMNET-BRAS02-SITE-PLATFORM   → 角色在 index 3
      解析/      REGION-REGION2-CE03-NE40E-X8A          → 角色在 index 2
    所以不写死下标，改为扫描第一个匹配 `^[A-Z]{2,4}\\d+$` 的 token。
    """
    name = fname.split("_")[0]
    parts = name.split("-")
    for i, tok in enumerate(parts):
        if i >= 2 and RE_ROLE_TOKEN.match(tok):
            return _norm_role(tok)
    return "?"


def _month_of(path):
    m = re.search(r"cfgfile_(\d{8})", path)
    return m.group(1) if m else "?"


def discover(with_vendor_detect=True):
    """扫描真实样本目录，返回样本描述列表。

    厂商判定优先用**内容指纹**（detect_vendor）；目录名只作为参考字段保留。
    这样能发现「目录叫华为但实际是 H3C/锐捷」这类错配。
    """
    samples = []
    raw_dirs = []
    if os.path.isdir(RAW_ROOT):
        for d in sorted(os.listdir(RAW_ROOT)):
            if d.startswith("cfgfile_"):
                raw_dirs.append((os.path.join(RAW_ROOT, d), "原始配置"))
    if os.path.isdir(PARSE_ROOT):
        for d in sorted(os.listdir(PARSE_ROOT)):
            if d.startswith("cfgfile_"):
                raw_dirs.append((os.path.join(PARSE_ROOT, d), "解析"))

    for base, src in raw_dirs:
        # 原始配置/ 下再分 华为/中兴 两个子目录；解析/ 下文件直接躺在 cfgfile_* 里
        subs = [d for d in sorted(os.listdir(base))
                if os.path.isdir(os.path.join(base, d))]
        if subs:
            leaves = [(s, os.path.join(base, s)) for s in subs]
        else:
            leaves = [(".", base)]
        for sub, p in leaves:
            for fn in sorted(os.listdir(p)):
                full = os.path.join(p, fn)
                if not os.path.isfile(full):
                    continue
                item = {
                    "vendor": None,
                    "vendor_src": sub,
                    "vendor_why": "",
                    "month": _month_of(base),
                    "role": _role_of(fn),
                    "platform": _platform_of(fn),
                    "path": full,
                    "file": fn,
                    "src": src,
                    "banner": detect_platform_hint(full),
                }
                if with_vendor_detect:
                    v, why = detect_vendor(full)
                    item["vendor"] = v
                    item["vendor_why"] = why
                samples.append(item)
    return samples


def sample_key(s):
    return (s["vendor"], s["month"], s["role"])


def build_sample_set(samples=None, per_cell=1):
    """分层抽样（确定性，无随机数）。

    规则：
      阶段 1 —— 每个 (厂商, 月份, 角色类) 取 **路径字典序最小** 的 1 个文件；
      阶段 2 —— 保证该厂商下每个**平台型号**至少被抽中 1 次
                  （取该平台字典序最小文件，不满足则补入）。
    返回 (选中列表, 全量样本列表)
    """
    samples = samples if samples is not None else discover()
    cells = defaultdict(list)
    for s in samples:
        cells[sample_key(s)].append(s)
    chosen = []
    chosen_paths = set()
    for k in sorted(cells):
        group = sorted(cells[k], key=lambda x: x["path"])
        for s in group[:per_cell]:
            if s["path"] not in chosen_paths:
                chosen.append(s)
                chosen_paths.add(s["path"])
    # 阶段 2：平台覆盖
    covered = defaultdict(set)
    for s in chosen:
        covered[s["vendor"]].add(s["platform"])
    by_platform = defaultdict(list)
    for s in samples:
        by_platform[(s["vendor"], s["platform"])].append(s)
    for k in sorted(by_platform):
        vendor, plat = k
        if plat in covered[vendor]:
            continue
        s = sorted(by_platform[k], key=lambda x: x["path"])[0]
        if s["path"] not in chosen_paths:
            chosen.append(s)
            chosen_paths.add(s["path"])
            covered[vendor].add(plat)
    chosen.sort(key=lambda x: (x["vendor"], x["month"], x["role"], x["path"]))
    return chosen, samples


# ──────────────────────────────────────────────────────────
# 独立「真实接口数」统计 —— 不依赖解析器
# ──────────────────────────────────────────────────────────
RE_IFACE_ANY_COL0 = re.compile(r"^interface\s+(\S+)")
RE_IFACE_INDENT = re.compile(r"^\s+interface\s+(\S+)")
RE_IFACE_NK = re.compile(r'^\s*interface\s+"([^"]+)"')
RE_IFACE_RJ = re.compile(r"^\s*interface\s+(\S+)(?:\s+(\S+))?")


def truth_interfaces(path, vendor):
    """从配置原文独立统计真实 interface 数（不依赖解析器）。

    口径按厂商分别定义：
      华为/新华三 —— 顶格 `interface <name>`（子命令 1 空格缩进）
      中兴        —— `!<if-intf>` 分区内顶格 `interface <name>`
                     （其它分区的 interface 是【引用】不是定义，需排除）
      诺基亚      —— 任意层级 `interface "<name>"`（名字带引号）
      锐捷        —— 任意层级 `interface <Type> <num>`（类型与编号之间有空格）

    返回 dict：count / names / unique / naive_count / indent_refs
    """
    names = []
    naive = []
    indent_refs = []
    in_ifintf = False
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.rstrip("\r\n")
            if not s:
                continue
            if s.startswith("!<") or s.startswith("</"):
                in_ifintf = s.strip() in ("!<if-intf>", "!<if_intf>")
                continue
            if vendor == VENDOR_NK:
                m = RE_IFACE_NK.match(s)
                if m:
                    names.append(m.group(1))
                    naive.append(m.group(1))
                continue
            if vendor == VENDOR_RJ:
                m = RE_IFACE_RJ.match(s)
                if m and not s.startswith(" "):
                    naive.append(m.group(0))
                m2 = RE_IFACE_RJ.match(s)
                if m2:
                    names.append(m2.group(0).strip())
                continue
            m = RE_IFACE_ANY_COL0.match(s)
            if m:
                naive.append(m.group(1))
                if vendor == VENDOR_ZTE:
                    if in_ifintf:
                        names.append(m.group(1))
                else:
                    names.append(m.group(1))
                continue
            m = RE_IFACE_INDENT.match(s)
            if m:
                indent_refs.append(m.group(1))
    return {
        "count": len(names),
        "names": names,
        "unique": len(set(names)),
        "naive_count": len(naive),
        "indent_refs": len(indent_refs),
        "indent_ref_names": indent_refs,
    }


# ──────────────────────────────────────────────────────────
# 解析
# ──────────────────────────────────────────────────────────
TPL_FOR = {
    VENDOR_HW: HW_TPL,
    VENDOR_NK: NK_TPL,
    # 以下厂商【没有】专属模板，故意用华为模板跑，量化跨厂商语法差异
    VENDOR_ZTE: HW_TPL,
    VENDOR_H3C: HW_TPL,
    VENDOR_RJ: HW_TPL,
}
COLL_FOR = {
    VENDOR_HW: "Interfaces",
    VENDOR_NK: "NokiaInterfaces",
    VENDOR_ZTE: "Interfaces",
    VENDOR_H3C: "Interfaces",
    VENDOR_RJ: "Interfaces",
}
HAS_NATIVE_TPL = {VENDOR_HW: HW_TPL, VENDOR_NK: NK_TPL}
ALL_VENDORS = (VENDOR_HW, VENDOR_ZTE, VENDOR_NK, VENDOR_H3C, VENDOR_RJ)
_tpl_cache = {}


def get_tpl(path):
    if path not in _tpl_cache:
        _tpl_cache[path] = P.parse_template(path)
    return _tpl_cache[path]


_parse_cache = {}


def run_parse(cfg_path, vendor, tpl=None):
    """返回 (results, error)。error 非 None 表示解析异常（原样记录）。

    结果按 (路径, 模板) 缓存，避免 accuracy / coverage / quality 重复解析。
    """
    tpl = tpl or TPL_FOR[vendor]
    ck = (cfg_path, tpl)
    if ck in _parse_cache:
        return _parse_cache[ck]
    colls = get_tpl(tpl)
    try:
        with open(cfg_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        res = P.run(colls, lines)
        out = (res, None)
    except Exception as e:  # noqa: BLE001 —— 探针要原样记录崩溃
        import traceback
        out = (None, {
            "type": type(e).__name__,
            "msg": str(e),
            "trace": traceback.format_exc()[-2000:],
            "file": cfg_path,
        })
    _parse_cache[ck] = out
    return out


# ──────────────────────────────────────────────────────────
# 命令族（沿用 probe_coverage3 的口径）
# ──────────────────────────────────────────────────────────
def skeleton(line):
    s = line.strip()
    s = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<ip>", s)
    s = re.sub(r"\b\d+\b", "<n>", s)
    return s


def family(line):
    head = []
    for t in skeleton(line).split():
        if t.startswith("<"):
            break
        head.append(t)
        if len(head) == 2:
            break
    return " ".join(head) if head else "(空)"


# ──────────────────────────────────────────────────────────
# 子命令
# ──────────────────────────────────────────────────────────
def cmd_inventory(argv):
    samples = discover()
    print(f"总样本数: {len(samples)}")
    print("\n=== 按【内容指纹】判定的厂商分布 ===")
    by_vendor = Counter(s["vendor"] for s in samples)
    for v, n in by_vendor.most_common():
        print(f"  {v:<8} {n:>6}")
    print("\n=== 目录名 vs 内容指纹 的一致性（错配检测）===")
    cross = Counter((s["vendor_src"], s["vendor"]) for s in samples)
    for (d, v), n in sorted(cross.items()):
        mark = "  <<< 目录名与内容不一致" if (
            (d == "华为" and v != VENDOR_HW) or (d == "中兴" and v != VENDOR_ZTE)) else ""
        print(f"  目录[{d}] → 内容[{v}]: {n:>6}{mark}")
    print("\n=== 厂商 × 月份 × 角色 × 平台 ===")
    for vendor in ALL_VENDORS:
        sub = [s for s in samples if s["vendor"] == vendor]
        if not sub:
            continue
        print(f"\n--- {vendor}（{len(sub)} 个）---")
        agg = Counter((s["month"], s["role"], s["platform"]) for s in sub)
        for k, n in sorted(agg.items()):
            print(f"    {k[0]}  {k[1]:<8} {k[2]:<12} {n:>5}")
        print(f"    月份数={len(set(s['month'] for s in sub))} "
              f"角色数={len(set(s['role'] for s in sub))} "
              f"平台数={len(set(s['platform'] for s in sub))}")
    print("\n=== 软件版本横幅（每厂商取前 6 种）===")
    ban = defaultdict(Counter)
    for s in samples:
        ban[s["vendor"]][s["banner"]] += 1
    for v, c in ban.items():
        print(f"  {v}:")
        for b, n in c.most_common(6):
            print(f"    {n:>6}  {b or '(无)'}")
    return 0


def cmd_sample(argv):
    chosen, samples = build_sample_set()
    by_v = defaultdict(list)
    for s in chosen:
        by_v[s["vendor"]].append(s)
    print(f"抽样总数: {len(chosen)} / 全量 {len(samples)}")
    print("规则: 阶段1 每个(厂商×月份×角色类)取路径字典序最小者; "
          "阶段2 补齐每个平台型号至少 1 个\n")
    for v in ALL_VENDORS:
        if v not in by_v:
            continue
        plats = Counter(s["platform"] for s in by_v[v])
        months = sorted(set(s["month"] for s in by_v[v]))
        print(f"=== {v} ({len(by_v[v])} 个) 平台={dict(plats)} 月份数={len(months)} ===")
        for s in by_v[v]:
            print(f"  {s['month']}  {s['role']:<8} {s['platform']:<12} {s['file']}")
        print()
    out = os.path.join(HERE, "_sampling_manifest.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(chosen, fh, ensure_ascii=False, indent=2)
    print(f"清单已写入: {out}")
    return 0


def _load_manifest():
    p = os.path.join(HERE, "_sampling_manifest.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    chosen, _ = build_sample_set()
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(chosen, fh, ensure_ascii=False, indent=2)
    return chosen


def cmd_accuracy(argv):
    chosen = _load_manifest()
    rows = []
    print(f"{'厂商':<5}{'月份':<9}{'角色':<8}{'平台':<9}{'解析':>7}{'真实':>7}{'唯一':>7}{'准确率':>9}  文件")
    print("-" * 120)
    for s in chosen:
        t = truth_interfaces(s["path"], s["vendor"])
        res, err = run_parse(s["path"], s["vendor"])
        if err:
            print(f"{s['vendor']:<5}{s['month']:<9}{s['role']:<8}{s['platform']:<9}"
                  f"{'ERR':>7}{t['count']:>7}{t['unique']:>7}{'--':>9}  {s['file']}")
            print(f"      !! {err['type']}: {err['msg']}")
            rows.append(dict(s, parsed=None, truth=t["count"], unique=t["unique"],
                             acc=None, error=err))
            continue
        coll = COLL_FOR[s["vendor"]]
        parsed = len(res.get(coll, []))
        acc = parsed / t["count"] if t["count"] else None
        rows.append(dict(s, parsed=parsed, truth=t["count"], unique=t["unique"],
                         acc=acc, naive=t["naive_count"], indent_refs=t["indent_refs"],
                         names=t["names"]))
        flag = "" if acc is not None and abs(acc - 1) < 1e-9 else "  <<< 偏差"
        print(f"{s['vendor']:<5}{s['month']:<9}{s['role']:<8}{s['platform']:<9}"
              f"{parsed:>7}{t['count']:>7}{t['unique']:>7}"
              f"{(f'{acc*100:.2f}%' if acc is not None else '--'):>9}  {s['file'][:52]}{flag}")
    print()
    # 偏差样本汇总
    print("=== 偏差样本（解析数 != 真实数）===")
    bad = [r for r in rows if r.get("parsed") is not None and r["parsed"] != r["truth"]]
    if not bad:
        print("  无")
    for r in bad:
        d = r["parsed"] - r["truth"]
        print(f"  {r['vendor']} {r['month']} {r['platform']:<8} 解析={r['parsed']} "
              f"真实={r['truth']} 差={d:+d}  {r['file']}")
    errs = [r for r in rows if r.get("error")]
    if errs:
        print(f"\n=== 解析异常 {len(errs)} 个 ===")
        for r in errs:
            print(f"  {r['file']}\n    {r['error']['type']}: {r['error']['msg']}")
    # 按厂商汇总
    print("\n=== 按厂商汇总 ===")
    for v in ALL_VENDORS:
        sub = [r for r in rows if r["vendor"] == v and r.get("parsed") is not None]
        if not sub:
            continue
        accs = [r["acc"] for r in sub if r["acc"] is not None]
        p = sum(r["parsed"] for r in sub)
        t = sum(r["truth"] for r in sub)
        native = "有模板" if v in HAS_NATIVE_TPL else "无模板(用华为模板)"
        print(f"  {v:<5} [{native}] 样本={len(sub):>3}  合计 解析={p} 真实={t}  "
              f"整体准确率={p/t*100 if t else 0:.2f}%  "
              f"单样本 min={min(accs)*100 if accs else 0:.2f}% "
              f"max={max(accs)*100 if accs else 0:.2f}%")
    out = os.path.join(HERE, "_accuracy_rows.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump([{k: v for k, v in r.items() if k != "names"} for r in rows],
                  fh, ensure_ascii=False, indent=2)
    print(f"\n逐样本数据已写入: {out}")
    return 0


def cmd_coverage(argv):
    chosen = _load_manifest()
    scope_by_vendor = {VENDOR_HW: "Interfaces", VENDOR_ZTE: "Interfaces",
                       VENDOR_NK: "NokiaInterfaces", VENDOR_H3C: "Interfaces",
                       VENDOR_RJ: "Interfaces"}
    zero_by_family = defaultdict(set)     # family -> {file}
    seen_family = defaultdict(set)         # family -> {file}
    per_file = []
    for s in chosen:
        scope = scope_by_vendor[s["vendor"]]
        tpl = TPL_FOR[s["vendor"]]
        colls = get_tpl(tpl)
        try:
            with open(s["path"], encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            trace = []
            P.run(colls, lines, trace=trace)
        except Exception as e:  # noqa: BLE001
            print(f"  !! {s['file']}: {type(e).__name__}: {e}")
            continue
        in_t, in_h = Counter(), Counter()
        out_t, out_h = Counter(), Counter()
        for _no, line, hit, stack in trace:
            f = family(line)
            if scope in stack:
                in_t[f] += 1
                if hit:
                    in_h[f] += 1
            else:
                out_t[f] += 1
                if hit:
                    out_h[f] += 1
        it, ih = sum(in_t.values()), sum(in_h.values())
        ot, oh = sum(out_t.values()), sum(out_h.values())
        for f in in_t:
            seen_family[f].add(s["file"])
            if in_h[f] == 0:
                zero_by_family[f].add(s["file"])
        for f in out_t:
            seen_family[f].add(s["file"])
            if out_h[f] == 0:
                zero_by_family[f].add(s["file"])
        per_file.append({
            "vendor": s["vendor"], "file": s["file"],
            "in_total": it, "in_hit": ih,
            "in_pct": ih * 100.0 / it if it else 0.0,
            "out_total": ot, "out_hit": oh,
            "out_pct": oh * 100.0 / ot if ot else 0.0,
            "in_families": len(in_t), "out_families": len(out_t),
            "in_zero": sorted(f for f in in_t if in_h[f] == 0),
            "out_zero": sorted(f for f in out_t if out_h[f] == 0),
        })
        if os.environ.get("PROBE_SAMPLING_VERBOSE"):
            print(f"{s['vendor']:<5}{s['file'][:46]:<48} 块内 {ih:>5}/{it:<6} "
                  f"({ih*100.0/it if it else 0:5.1f}%)  块外 {oh:>5}/{ot:<6} "
                  f"({oh*100.0/ot if ot else 0:5.1f}%)")

    # 横向对比
    print("\n=== 各厂商块内/块外覆盖率（按厂商聚合所有样本行）===")
    agg = defaultdict(lambda: [0, 0, 0, 0])
    for r in per_file:
        a = agg[r["vendor"]]
        a[0] += r["in_hit"]; a[1] += r["in_total"]
        a[2] += r["out_hit"]; a[3] += r["out_total"]
    for v in ALL_VENDORS:
        if v not in agg:
            continue
        ih, it, oh, ot = agg[v]
        print(f"  {v:<5} 块内 {ih:>7}/{it:<8} ({ih*100.0/it if it else 0:5.1f}%)"
              f"   块外 {oh:>7}/{ot:<8} ({oh*100.0/ot if ot else 0:5.1f}%)")

    # ── 零覆盖族：按厂商看「稳定 vs 随机」
    print("\n=== 零覆盖命令族（按厂商分桶：稳定缺失 vs 随机缺失）===")
    by_vendor = defaultdict(list)
    for r in per_file:
        by_vendor[r["vendor"]].append(r)
    for v in ALL_VENDORS:
        rows = by_vendor.get(v)
        if not rows:
            continue
        n = len(rows)
        print(f"\n  ── {v}（{n} 个样本）──")
        for scope_name, key in (("块内", "in_zero"), ("块外", "out_zero")):
            agg2 = defaultdict(set)
            cnt = Counter()
            for r in rows:
                for f in r[key]:
                    agg2[f].add(r["file"])
                    cnt[f] += 1
            stable = sorted([(f, len(s)) for f, s in agg2.items() if len(s) == n],
                            key=lambda kv: -cnt[kv[0]])
            partial = sorted([(f, len(s)) for f, s in agg2.items() if len(s) < n],
                             key=lambda kv: (-cnt[kv[0]], -len(agg2[kv[0]])))
            print(f"    {scope_name}零覆盖族 {len(agg2)} 个；"
                  f"稳定缺失(该厂商全部文件都缺) {len(stable)} 个，"
                  f"随机缺失 {len(partial)} 个")
            if stable:
                print("      【稳定缺失 top 12】")
                for f, c in stable[:12]:
                    print(f"        ✗ {f:<34} 命中 {c} 个文件")
            print("      【随机缺失 top 12（按受影响文件数）】")
            for f, c in partial[:12]:
                print(f"        ~ {f:<34} 命中 {c}/{n} 个文件")
    out = os.path.join(HERE, "_coverage_rows.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(per_file, fh, ensure_ascii=False, indent=2)
    print(f"\n逐文件数据已写入: {out}")
    return 0


# ── 格式变体 ────────────────────────────────────────────
def _indent_profile(path):
    """统计缩进宽度分布 / 分隔符 / 注释风格"""
    widths = Counter()
    seps = Counter()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.rstrip("\r\n")
            if not s.strip():
                continue
            w = len(s) - len(s.lstrip(" "))
            if w:
                widths[w] += 1
            st = s.strip()
            if st in ("#", "$", "!", "!", "----"):
                seps[st] += 1
            elif set(st) <= {"-"} and len(st) >= 3:
                seps["----"] += 1
    return widths, seps


def cmd_variants(argv):
    chosen = _load_manifest()
    print("=== 缩进 / 分隔符 形态聚类（决定要维护几份模板）===")
    per_v = defaultdict(Counter)
    for s in chosen:
        w, sep = _indent_profile(s["path"])
        ws = tuple(sorted(w))
        ss = tuple(sorted(sep))
        per_v[s["vendor"]][(ws, ss)] += 1
    for v in ALL_VENDORS:
        if v not in per_v:
            continue
        print(f"\n  ── {v}（{sum(per_v[v].values())} 个样本，"
              f"{len(per_v[v])} 种「缩进+分隔符」组合）──")
        for (ws, ss), n in per_v[v].most_common():
            print(f"    {n:>3} 个文件  缩进宽度={list(ws)}  分隔符={list(ss)}")

    # 分区标记
    print("\n=== 中兴分区标记 (!<xxx>) 出现情况 ===")
    marks = defaultdict(Counter)
    zte_n = 0
    for s in chosen:
        if s["vendor"] != VENDOR_ZTE:
            continue
        zte_n += 1
        with open(s["path"], encoding="utf-8", errors="replace") as fh:
            txt = fh.read()
        for m in re.findall(r"^!<\/?([a-z0-9_-]+)>", txt, re.M):
            marks[m][s["file"]] += 1
    allm = sorted(marks, key=lambda k: -len(marks[k]))
    print(f"  分区标记种类: {len(allm)}（中兴样本数 = {zte_n}）")
    for m in allm:
        print(f"    !<{m:<24}> 出现在 {len(marks[m])}/{zte_n} 个中兴样本中")
    print(f"  所有样本都有的标记: {[m for m in allm if len(marks[m]) == zte_n]}")

    # 同一逻辑命令的写法变体
    print("\n=== 关键命令的写法变体（跨厂商 × 跨文件，按「语义等价形态」聚合）===")
    watch = ["ip address", "ipv6 address", "undo shutdown", "no shutdown",
             "shutdown", "description", "mtu", "ip binding vpn-instance",
             "eth-trunk", "port default vlan", "vlan-type", "undo dcn",
             "carrier down-hold-time", "interface", "address", "port"]
    variants = defaultdict(lambda: defaultdict(Counter))
    for s in chosen:
        with open(s["path"], encoding="utf-8", errors="replace") as fh:
            for line in fh:
                st = line.strip()
                if not st:
                    continue
                key = None
                for w in watch:
                    if st == w or st.startswith(w + " "):
                        key = w
                        break
                if key is None:
                    continue
                shape = re.sub(r"\S+", "<v>", st)
                variants[(s["vendor"], key)][shape][s["file"]] += 1
    for v in ALL_VENDORS:
        rows = [(k, vv) for k, vv in variants.items() if k[0] == v]
        if not rows:
            continue
        print(f"\n  ── {v} ──")
        for (vendor, w), shapes in sorted(rows, key=lambda kv: kv[0][1]):
            tot = sum(sum(c.values()) for c in shapes.values())
            nf = len({f for c in shapes.values() for f in c})
            print(f"    【{w}】写法变体 {len(shapes)} 种，出现在 {nf} 个文件，"
                  f"共 {tot} 行")
            for shape, c in sorted(shapes.items(), key=lambda kv: -sum(kv[1].values()))[:8]:
                print(f"       {sum(c.values()):>6} 行 / {len(c):>3} 文件  {shape}")

    # 接口命名形态
    print("\n=== 接口命令行的命名形态（各厂商）===")
    for v in ALL_VENDORS:
        kinds = Counter()
        for s in chosen:
            if s["vendor"] != v:
                continue
            with open(s["path"], encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    t = ln.rstrip("\r\n")
                    if v == VENDOR_NK:
                        m = RE_IFACE_NK.match(t)
                        if not m:
                            continue
                        nm = m.group(1)
                    else:
                        m = RE_IFACE_ANY_COL0.match(t)
                        if not m:
                            continue
                        nm = m.group(1)
                    kinds[re.sub(r"\d+", "<n>", nm)] += 1
        if not kinds:
            continue
        print(f"  {v:<5} 顶格/引号 interface 名称型别 top 8: "
              + ", ".join(f"{k}×{n}" for k, n in kinds.most_common(8)))
    return 0


# ── 输出质量 ────────────────────────────────────────────
def _field_types(colls):
    """返回 {coll_name: [(path, type, required, is_key)]}"""
    out = {}
    for c in colls:
        out[c.name] = [(f.path, f.type, f.required, f.is_key)
                       for f in c.all_fields.values()]
    return out


def _raw_interface_blocks(path, vendor):
    """从原文切出每个接口块的内容（用于逐字段对照，不依赖解析器）。

    返回 [(接口名, [块内原始行]), ...]，**按出现顺序、保留重复**——
    因为 Nokia/H3C 里同名接口会在不同上下文重复出现，必须逐次对齐。
    """
    blocks = []
    cur = None
    curlines = None
    in_ifintf = False

    def flush():
        if cur is not None:
            blocks.append((cur, curlines))

    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.rstrip("\r\n")
            if not s.strip():
                continue
            if s.startswith("!<") or s.startswith("</"):
                in_ifintf = s.strip() in ("!<if-intf>", "!<if_intf>")
                continue
            m = RE_IFACE_ANY_COL0.match(s)
            if m and (vendor != VENDOR_ZTE or in_ifintf):
                flush()
                cur, curlines = m.group(1), []
                continue
            if vendor == VENDOR_NK:
                m = RE_IFACE_NK.match(s)
                if m:
                    flush()
                    cur, curlines = m.group(1), []
                    continue
            if cur is not None:
                curlines.append(s)
    flush()
    return blocks


RE_RAW_IP = re.compile(r"^\s*ip(v6)?\s+address\s+(\S+)(?:\s+(\S+))?")
RE_RAW_NK_ADDR = re.compile(r"^\s*address\s+(\S+)(?:\s+(\S+))?")


def _norm_raw_ips(vendor, lines):
    """把原文里的地址行归一化成与模板字段同构的 (Address, Mask, Family, Mode)。

    华为 VRP：
      ip address  <A> <M> [sub]           → (A, M, ipv4, None)
      ipv6 address <A>/<len>              → (A, len, ipv6, None)
      ipv6 address auto <mode>            → (auto, None, ipv6, mode)
    诺基亚：
      address <A>/<len>                   → (A, len, None, None)
      address <A>                         → (A, None, None, None)
    """
    out = []
    for ln in lines:
        st = ln.strip()
        if vendor == VENDOR_NK:
            m = RE_RAW_NK_ADDR.match(ln)
            if not m:
                continue
            a = m.group(1)
            if "/" in a:
                a, _, pfx = a.partition("/")
                out.append((a, pfx, None, None))
            else:
                out.append((a, m.group(2), None, None))
            continue
        m = RE_RAW_IP.match(ln)
        if not m:
            continue
        fam = "ipv6" if m.group(1) else "ipv4"
        a, b = m.group(2), m.group(3)
        if fam == "ipv6":
            if a == "auto":
                out.append((a, None, fam, b))
            elif "/" in a:
                a, _, pfx = a.partition("/")
                out.append((a, pfx, fam, None))
            else:
                out.append((a, b, fam, None))
        else:
            if b == "sub":
                b = None
            out.append((a, b, fam, None))
    return out


def cmd_fieldcheck(argv):
    """逐接口逐字段与原文对照 —— 数量对不代表内容对。"""
    chosen = _load_manifest()
    total_iface = 0
    ip_total = 0
    ip_ok = 0
    ip_bad = []
    desc_total = 0
    desc_ok = 0
    desc_bad = []
    mtu_total = 0
    mtu_ok = 0
    missing_ip = []
    dup_name = []
    for s in chosen:
        vendor = s["vendor"]
        if vendor not in (VENDOR_HW, VENDOR_NK):
            continue
        res, err = run_parse(s["path"], vendor)
        if err:
            continue
        recs = res.get(COLL_FOR[vendor], [])
        blocks = _raw_interface_blocks(s["path"], vendor)
        recs = [r for r in recs if isinstance(r, dict)]
        if len(recs) != len(blocks):
            print(f"  [口径差异] {s['file'][:44]:<46} 原文接口块={len(blocks)} "
                  f"解析记录={len(recs)}")
        for idx, (name, lines) in enumerate(blocks):
            raw_ips = _norm_raw_ips(vendor, lines)
            raw_desc = None
            raw_mtu = None
            for ln in lines:
                st = ln.strip()
                if st.startswith("description "):
                    raw_desc = st[len("description "):].strip()
                if st.startswith("mtu "):
                    raw_mtu = st[4:].strip()
            total_iface += 1
            if idx >= len(recs):
                missing_ip.append((s["file"], name, len(raw_ips)))
                continue
            rec = recs[idx]
            got_ips = []
            for ip in (rec.get("IpAddress") or []) + (rec.get("NokiaAddresses") or []):
                if isinstance(ip, dict):
                    got_ips.append((ip.get("Address"), ip.get("Mask"),
                                    ip.get("Family"), ip.get("Mode")))
            ip_total += len(raw_ips)
            rawset = list(raw_ips)
            gotset = list(got_ips)
            if vendor == VENDOR_NK:
                rawset = [(a, m) for a, m, _f, _mo in rawset]
                gotset = [(a, m) for a, m, _f, _mo in gotset]
            if rawset == gotset:
                ip_ok += len(raw_ips)
            else:
                miss = [x for x in rawset if x not in gotset]
                extra = [x for x in gotset if x not in rawset]
                ip_bad.append((s["file"], name, rawset[:4], gotset[:4], miss[:3], extra[:3]))
            if raw_desc is not None:
                desc_total += 1
                gd = rec.get("Description")
                if gd == raw_desc:
                    desc_ok += 1
                else:
                    desc_bad.append((s["file"], name, raw_desc, gd))
            if raw_mtu is not None:
                mtu_total += 1
                if str(rec.get("Mtu")) == raw_mtu:
                    mtu_ok += 1
    print("=== 逐接口 IP 地址归属核对（解析结果 vs 配置原文）===")
    print(f"  接口块总数 = {total_iface}")
    print(f"  原文地址行数 = {ip_total}；逐条完全一致 = {ip_ok}；"
          f"不一致的接口数 = {len(ip_bad)}")
    if ip_total:
        print(f"  IP 归属正确率 = {ip_ok*100.0/ip_total:.2f}%")
    for f, name, r, g, miss, extra in ip_bad[:15]:
        print(f"    {f[:38]:<40} {name[:34]:<36}")
        print(f"       原文={r}")
        print(f"       解析={g}")
        if miss:
            print(f"       漏={miss}")
        if extra:
            print(f"       多={extra}")
    if missing_ip:
        print(f"\n  原文有接口但解析结果里没有: {len(missing_ip)}")
        for f, name, n in missing_ip[:10]:
            print(f"    {f[:40]:<42} {name[:40]:<42} 原文 {n} 个地址")
    if dup_name:
        print(f"\n  同名接口被解析成多条记录（Key 不唯一）: {len(dup_name)} 个接口名")
        for f, name, n in dup_name[:10]:
            print(f"    {f[:40]:<42} {name[:40]:<42} {n} 条")
    print(f"\n=== Description 逐条核对 ===")
    print(f"  原文有 description 的接口 = {desc_total}；与解析值完全相同 = {desc_ok}；"
          f"不一致 = {len(desc_bad)}")
    for f, name, r, g in desc_bad[:10]:
        print(f"    {f[:36]:<38} {name[:30]:<32} 原文={r!r} 解析={g!r}")
    print(f"\n=== MTU 逐条核对 ===")
    print(f"  原文有 mtu 的接口 = {mtu_total}；一致 = {mtu_ok}；不一致 = {mtu_total-mtu_ok}")
    return 0


def cmd_defects(argv):
    """针对已发现的三个疑似缺陷做量化（每个数字都可复算）。"""
    chosen = _load_manifest()
    # ── 直接用模板编译出来的正则去测真实行，证明「规则形态不匹配」
    colls_hw = get_tpl(HW_TPL)
    rs_col = next(c for c in colls_hw if c.name == "RouteStatics")
    rs_rules = [P.compile_rule(r, rs_col) for r in rs_col.rules]
    acl_col = next(c for c in colls_hw if c.name == "AclRules")
    acl_rules = [P.compile_rule(r, acl_col) for r in acl_col.rules]
    rs_line = 0
    rs_match = 0
    rs_unmatched = []
    acl_line = 0
    acl_match = 0
    acl_unmatched_shapes = Counter()
    acl_matched_shapes = Counter()
    for s in chosen:
        if s["vendor"] != VENDOR_HW:
            continue
        with open(s["path"], encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.rstrip("\r\n")
                if re.match(r"^\s*ip route-static", ln):
                    rs_line += 1
                    if any(cr.rx.match(ln) for cr in rs_rules):
                        rs_match += 1
                    elif len(rs_unmatched) < 8:
                        rs_unmatched.append(ln.strip())
                elif re.match(r"^\s*rule\s+\d+\s+(?:permit|deny)", ln):
                    acl_line += 1
                    toks = ln.split()
                    sh = " ".join(toks[2:4])
                    if any(cr.rx.match(ln) for cr in acl_rules):
                        acl_match += 1
                        acl_matched_shapes[sh] += 1
                    else:
                        acl_unmatched_shapes[sh] += 1
    print("=== D1 直接证据：用模板编译出的正则直接测真实配置行 ===")
    print(f"  真实 ip route-static 行 = {rs_line}")
    print(f"  能被模板 RouteStatics 规则【完整匹配】的行 = {rs_match}")
    print(f"  匹配不上（必然落成空记录 {{}}）= {rs_line - rs_match} "
          f"({(rs_line-rs_match)*100.0/max(rs_line,1):.1f}%)")
    for l in rs_unmatched:
        print(f"    ✗ {l}")
    print(f"\n  真实 ACL rule 行 = {acl_line}")
    print(f"  能被模板 AclRules 规则匹配 = {acl_match} "
          f"({acl_match*100.0/max(acl_line,1):.1f}%)，匹配不上 = {acl_line-acl_match} "
          f"({(acl_line-acl_match)*100.0/max(acl_line,1):.1f}%)")
    print("  匹配不上的形态 top 10:")
    for sh, n in acl_unmatched_shapes.most_common(10):
        print(f"    {n:>6}  rule N {sh}")
    print("  匹配上的形态:")
    for sh, n in acl_matched_shapes.most_common(10):
        print(f"    {n:>6}  rule N {sh}")

    # ── D1/D2: 静态路由
    rs_lines = 0
    rs_recs = 0
    rs_empty = 0
    rs_shapes = Counter()
    rs_shape_ok = Counter()
    rs_samples = []
    # ── D3: ACL 规则 Key=Sequence 合并
    acl_lines = 0
    acl_recs = 0
    acl_dups = 0
    acl_shapes = Counter()
    acl_shape_ok = Counter()
    for s in chosen:
        if s["vendor"] != VENDOR_HW:
            continue
        res, err = run_parse(s["path"], s["vendor"])
        if err:
            continue
        with open(s["path"], encoding="utf-8", errors="replace") as fh:
            txt = fh.read()
        # 静态路由
        lines = [l for l in txt.splitlines() if re.match(r"^\s*ip route-static", l)]
        rs_lines += len(lines)
        recs = res.get("RouteStatics", [])
        rs_recs += len(recs)
        emp = [r for r in recs if not r]
        rs_empty += len(emp)
        for l in lines:
            st = l.strip()
            toks = st.split()
            body = toks[2:]
            # 形态：prefix mask [出接口] nexthop ...   —— 出接口在第 3 个参数位
            # NULL0 / BlackHole 是「黑洞下一跳」，不是出接口
            SPECIAL_NH = ("NULL0", "BlackHole", "0.0.0.0")
            has_iface = (len(body) >= 3
                         and not re.match(r"^\d+\.\d+\.\d+\.\d+$", body[2])
                         and body[2] not in SPECIAL_NH
                         and body[2] != "vpn-instance")
            sh = "带出接口(prefix mask iface nexthop)" if has_iface else \
                 ("vpn-instance 形态" if body[:1] == ["vpn-instance"] else "无出接口(prefix mask nexthop)")
            rs_shapes[sh] += 1
        rs_samples.append((s["file"], len(lines), len(recs), len(emp)))
        # ACL
        alines = [l for l in txt.splitlines()
                  if re.match(r"^\s*rule\s+\d+\s+(?:permit|deny)", l)]
        acl_lines += len(alines)
        arecs = sum(len(a.get("AclRules", [])) for a in res.get("Acls", []))
        acl_recs += arecs
        cur = None
        per_acl = defaultdict(list)
        for l in txt.splitlines():
            m = re.match(r"^acl number\s+(\d+)", l)
            if m:
                cur = m.group(1)
                continue
            m = re.match(r"^\s*rule\s+(\d+)\s+(?:permit|deny)", l)
            if m and cur:
                per_acl[cur].append(m.group(1))
        for k, v in per_acl.items():
            c = Counter(v)
            acl_dups += sum(n - 1 for n in c.values() if n > 1)
        for l in alines:
            st = l.strip()
            toks = st.split()
            act = toks[2] if len(toks) > 2 else ""
            proto = toks[3] if len(toks) > 3 else ""
            sh = f"{act} {proto}".strip()
            acl_shapes[sh] += 1

    print("=== D1/D2 华为静态路由解析 ===")
    print(f"  真实 ip route-static 行 = {rs_lines}")
    print(f"  解析出的 RouteStatics 记录 = {rs_recs}")
    print(f"  其中【空记录 {{}}】（Prefix/Mask/NextHop 全缺）= {rs_empty} "
          f"({rs_empty*100.0/max(rs_recs,1):.1f}%)")
    print(f"  真实路由行形态分布: {dict(rs_shapes)}")
    print("  逐样本（前 12）:")
    for f, a, b, c in rs_samples[:12]:
        print(f"    {f[:46]:<48} 原文={a:>4} 记录={b:>4} 空={c:>4}")

    print("\n=== D3 ACL 规则：Key=Sequence 合并导致丢失 ===")
    print(f"  真实 ACL 规则行 = {acl_lines}")
    print(f"  解析出的 AclRules 记录 = {acl_recs}")
    print(f"  同一 acl 内序号重复的行 = {acl_dups}（这些会被 Key 聚合吃掉）")
    print("  真实规则形态分布 top 12:")
    for sh, n in acl_shapes.most_common(12):
        print(f"    {n:>6}  {sh}")

    print("\n=== D4 顶层集合 Key 全空记录未被丢弃（引擎语义与文档不符）===")
    n_empty = 0
    n_total = 0
    for s in chosen:
        res, err = run_parse(s["path"], s["vendor"])
        if err:
            continue
        for coll, arr in res.items():
            for r in arr:
                if not isinstance(r, dict):
                    continue
                n_total += 1
                if not r:
                    n_empty += 1
    print(f"  全部样本产出记录 {n_total} 条，其中空字典 {{}} = {n_empty}")
    return 0


def cmd_robust(argv):
    """全语料鲁棒性 + 全语料接口数核对：对每个真实样本跑一次解析。

    目的：(1) 证明/证伪「引擎在真实数据上不会崩」；
          (2) 把抽样准确率结论升级为【全语料 6344 个文件】的结论。
    """
    samples = discover()
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    if limit:
        byv = defaultdict(list)
        for s in samples:
            byv[s["vendor"]].append(s)
        samples = []
        for v in byv:
            samples.extend(byv[v][:limit])
    print(f"全语料扫描：{len(samples)} 个文件", flush=True)
    colls_by_tpl = {}
    errors = []
    ok = 0
    t0 = time.time()
    per_vendor = defaultdict(lambda: {"ok": 0, "err": 0, "parsed": 0, "truth": 0,
                                      "exact": 0, "mismatch": 0, "dup": 0})
    mismatches = []
    for i, s in enumerate(samples):
        tpl = TPL_FOR[s["vendor"]]
        if tpl not in colls_by_tpl:
            colls_by_tpl[tpl] = get_tpl(tpl)
        try:
            with open(s["path"], encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            res = P.run(colls_by_tpl[tpl], lines)
            json.dumps(res, ensure_ascii=False)      # 入库前必过的可序列化性
            ok += 1
            pv = per_vendor[s["vendor"]]
            pv["ok"] += 1
            t = truth_interfaces(s["path"], s["vendor"])
            coll = COLL_FOR[s["vendor"]]
            recs = res.get(coll, [])
            parsed = len(recs)
            uniq = len({r.get("Name") for r in recs if isinstance(r, dict)})
            pv["parsed"] += parsed
            pv["truth"] += t["count"]
            if parsed == t["count"]:
                pv["exact"] += 1
            else:
                pv["mismatch"] += 1
                if len(mismatches) < 40:
                    mismatches.append((s["vendor"], s["file"], parsed, t["count"]))
            if parsed != uniq:
                pv["dup"] += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            errors.append({
                "file": s["path"],
                "vendor": s["vendor"],
                "type": type(e).__name__,
                "msg": str(e),
                "trace": traceback.format_exc()[-1500:],
            })
            per_vendor[s["vendor"]]["err"] += 1
        if (i + 1) % 500 == 0:
            print(f"  ... {i+1}/{len(samples)}  用时 {time.time()-t0:.0f}s", flush=True)
    print(f"\n完成：成功 {ok} / {len(samples)}，异常 {len(errors)}，"
          f"用时 {time.time()-t0:.0f}s", flush=True)
    print("\n=== 全语料接口数核对（解析条数 vs 独立统计的真实数）===")
    print(f"  {'厂商':<8}{'文件数':>7}{'解析=真实':>11}{'有偏差':>8}"
          f"{'合计解析':>10}{'合计真实':>10}{'整体准确率':>11}{'含重复键文件':>12}")
    for v in ALL_VENDORS:
        pv = per_vendor.get(v)
        if not pv:
            continue
        acc = pv["parsed"] * 100.0 / pv["truth"] if pv["truth"] else 0
        print(f"  {v:<8}{pv['ok']:>7}{pv['exact']:>11}{pv['mismatch']:>8}"
              f"{pv['parsed']:>10}{pv['truth']:>10}{acc:>10.2f}%{pv['dup']:>12}")
    if mismatches:
        print("\n=== 有偏差的文件（前 40）===")
        for v, f, p, t in mismatches:
            print(f"  {v:<6}{f[:56]:<58} 解析={p:>5} 真实={t:>5} 差={p-t:+d}")
    if errors:
        print(f"\n=== 异常明细（前 10）===")
        for e in errors[:10]:
            print(f"  {e['vendor']} {e['file']}")
            print(f"    {e['type']}: {e['msg']}")
        out = os.path.join(HERE, "_robust_errors.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(errors, fh, ensure_ascii=False, indent=2)
        print(f"\n异常全量已写入: {out}")
    else:
        print("\n** 没有任何文件触发异常 **")
    return 0


def cmd_fullformat(argv):
    """全语料（6344 个文件，不抽样）格式指纹扫描 —— 回答「要维护几份模板」。"""
    samples = discover()
    print(f"全语料格式指纹扫描：{len(samples)} 个文件")
    by_v = defaultdict(Counter)
    for i, s in enumerate(samples):
        w, sep = _indent_profile(s["path"])
        key = (tuple(sorted(w)), tuple(sorted(sep)))
        by_v[s["vendor"]][key] += 1
        if (i + 1) % 1000 == 0:
            print(f"  ... {i+1}/{len(samples)}")
    print()
    for v in ALL_VENDORS:
        if v not in by_v:
            continue
        n = sum(by_v[v].values())
        print(f"=== {v}（{n} 个文件，{len(by_v[v])} 种「缩进宽度集+分隔符集」组合）===")
        for (ws, ss), c in by_v[v].most_common():
            print(f"   {c:>5} 个文件  缩进宽度集={list(ws)}  分隔符={list(ss)}")
        print()
    # 顶层命令词表对比：同一厂商的命令名集合有多少种
    print("=== 各厂商【接口声明行】的写法种类（全语料）===")
    for v in ALL_VENDORS:
        shapes = Counter()
        for s in samples:
            if s["vendor"] != v:
                continue
            with open(s["path"], encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    t = ln.rstrip("\r\n")
                    if v == VENDOR_NK:
                        m = RE_IFACE_NK.match(t)
                    else:
                        m = RE_IFACE_ANY_COL0.match(t)
                    if not m:
                        continue
                    rest = t[m.end():].strip()
                    if v == VENDOR_NK:
                        shapes["interface \"<name>\""
                               + (" create" if rest == "create" else
                                  (" +" + str(len(rest.split())) + " 参数" if rest else ""))] += 1
                    else:
                        shapes["interface <name>" + (" +" + str(len(rest.split())) + " 参数"
                                                    if rest else "")] += 1
        if not shapes:
            continue
        print(f"  {v:<6} " + ", ".join(f"{k}×{n}" for k, n in shapes.most_common()))
    return 0


def cmd_quality(argv):
    chosen = _load_manifest()
    colls_hw = get_tpl(HW_TPL)
    colls_nk = get_tpl(NK_TPL)
    type_bad = Counter()
    type_checked = Counter()
    req_missing = Counter()
    req_checked = Counter()
    key_dup = []
    mask_bad = []
    desc_quote = []
    desc_total_nk = 0
    route_dup = []
    acl_seq_dup = []
    acl_seq_total = 0
    acl_seq_dupn = 0
    reports = []

    for s in chosen:
        res, err = run_parse(s["path"], s["vendor"])
        if err:
            print(f"!! {s['file']}: {err['type']}: {err['msg']}")
            continue
        colls = colls_nk if s["vendor"] == VENDOR_NK else colls_hw
        ftypes = _field_types(colls)
        coll = COLL_FOR[s["vendor"]]
        recs = res.get(coll, [])
        for r in recs:
            if not isinstance(r, dict):
                continue
            for path, typ, req, isk in ftypes.get(coll, []):
                v = r
                for seg in path.split("."):
                    if isinstance(v, dict) and seg in v:
                        v = v[seg]
                    else:
                        v = None
                        break
                if v is None or typ == "list":
                    continue
                type_checked[(coll, typ)] += 1
                ok = True
                if typ == "int":
                    ok = isinstance(v, int) and not isinstance(v, bool)
                elif typ == "float":
                    ok = isinstance(v, (int, float)) and not isinstance(v, bool)
                elif typ == "bool":
                    ok = isinstance(v, bool)
                elif typ == "str":
                    ok = isinstance(v, str)
                if not ok:
                    type_bad[(coll, path, typ, type(v).__name__)] += 1
            for path, typ, req, isk in ftypes.get(coll, []):
                if not req:
                    continue
                v = r
                for seg in path.split("."):
                    if isinstance(v, dict) and seg in v:
                        v = v[seg]
                    else:
                        v = None
                        break
                req_checked[coll] += 1
                if v is None or v == "":
                    req_missing[coll] += 1
        # ── Key 唯一性：接口名
        keys = [r.get("Name") for r in recs if isinstance(r, dict)]
        dups = [k for k, n in Counter(keys).items() if n > 1]
        if dups:
            key_dup.append((s["vendor"], s["file"], coll, len(recs),
                            len(set(keys)), len(dups)))
        # ── Key 唯一性：RouteStatics 复合键
        rs = res.get("RouteStatics", [])
        if rs:
            kk = [(x.get("Prefix"), x.get("Mask"), x.get("VpnInstance")) for x in rs]
            dd = [k for k, n in Counter(kk).items() if n > 1]
            if dd:
                route_dup.append((s["file"], len(rs), len(set(kk)), len(dd), dd[:3]))
        # ── 脏数据：Nokia Description 带引号
        if s["vendor"] == VENDOR_NK:
            for r in recs:
                if isinstance(r, dict) and isinstance(r.get("Description"), str):
                    desc_total_nk += 1
                    if '"' in r["Description"]:
                        desc_quote.append((s["file"], r["Description"]))
        for r in recs:
            if not isinstance(r, dict):
                continue
            for ip in (r.get("IpAddress") or []) + (r.get("NokiaAddresses") or []):
                if isinstance(ip, dict):
                    for kk2 in ("Mask", "Address"):
                        vv = ip.get(kk2)
                        if isinstance(vv, str) and "/" in vv:
                            mask_bad.append((s["file"], kk2, vv))
        # ── ACL 序号在同一 acl 内是否唯一（Key=Sequence）
        with open(s["path"], encoding="utf-8", errors="replace") as fh:
            txt = fh.read()
        cur = None
        per_acl = defaultdict(list)
        for ln in txt.splitlines():
            m = re.match(r"^acl number\s+(\d+)", ln)
            if m:
                cur = m.group(1)
                continue
            m = re.match(r"^\s*rule\s+(\d+)\s+(?:permit|deny)", ln)
            if m and cur:
                per_acl[cur].append(m.group(1))
        for k, v in per_acl.items():
            c = Counter(v)
            acl_seq_total += len(v)
            acl_seq_dupn += sum(n - 1 for n in c.values() if n > 1)
        reports.append({
            "file": s["file"], "vendor": s["vendor"],
            "records": len(recs),
            "empty_name": sum(1 for r in recs if isinstance(r, dict) and not r.get("Name")),
        })

    print("=== 字段类型正确率（声明 type 的字段实际落库类型）===")
    for k in sorted(type_checked):
        coll_name, typ = k
        n = type_checked[k]
        bad = sum(v for kk, v in type_bad.items() if kk[:2] == k)
        print(f"  {coll_name:<18} 声明 type({typ:<5}) 检查 {n:>7} 个  错误 {bad:>6}  "
              f"({bad*100.0/n if n else 0:.3f}%)")
    if type_bad:
        print("  错误明细:")
        for k, v in type_bad.most_common():
            print(f"    {k}")

    print("\n=== 必填字段（option(Required)）缺失率 ===")
    for k in sorted(req_checked):
        n = req_checked[k]
        print(f"  {k:<20} 检查 {n:>7} 条记录  缺失 {req_missing[k]:>6}  "
              f"({req_missing[k]*100.0/n if n else 0:.3f}%)")
    print("  注：引擎对 Required 缺失的记录是【整条丢弃】，故上表只统计存活记录；")
    print("      被丢弃的条数由下面的「Required 丢弃率」单独衡量。")

    print("\n=== Required 丢弃率（真实 ACL 规则行 vs 解析出的 AclRules 记录）===")
    drops = []
    for s in chosen:
        if s["vendor"] == VENDOR_NK:
            continue
        res, err = run_parse(s["path"], s["vendor"])
        if err:
            continue
        with open(s["path"], encoding="utf-8", errors="replace") as fh:
            txt = fh.read()
        truth_rules = len(re.findall(r"^\s*rule\s+\d+\s+(?:permit|deny)", txt, re.M))
        truth_acl = len(re.findall(r"^acl number\s+\d+", txt, re.M))
        got_acl = len(res.get("Acls", []))
        got_rules = sum(len(a.get("AclRules", [])) for a in res.get("Acls", []))
        if truth_rules:
            drops.append((s["vendor"], s["file"], truth_acl, got_acl,
                          truth_rules, got_rules))
            print(f"  {s['vendor']:<4}{s['file'][:44]:<46} ACL={got_acl}/{truth_acl}  "
                  f"Rule={got_rules}/{truth_rules}  "
                  f"丢弃率={(truth_rules-got_rules)*100.0/truth_rules:5.1f}%")
    hw = [d for d in drops if d[0] == VENDOR_HW]
    if hw:
        tr = sum(d[4] for d in hw)
        gr = sum(d[5] for d in hw)
        print(f"\n  华为合计: 真实 ACL 规则行 {tr}，解析出 {gr}，"
              f"**丢弃率 {(tr-gr)*100.0/tr:.1f}%**（{len(hw)} 个样本）")

    print("\n=== Key 唯一性 ===")
    print("  [接口集合 Name]")
    if not key_dup:
        print("    全部样本无重复")
    for v, f, c, n, uniq, nd in key_dup:
        print(f"    {v:<4}{f[:42]:<44} 记录={n:>5} 唯一={uniq:>5} 重复键={nd:>4} "
              f"({nd*100.0/n if n else 0:.1f}%)")
    print("  [RouteStatics 复合键 Prefix+Mask+VpnInstance]")
    if not route_dup:
        print("    全部样本无重复")
    for f, n, uniq, nd, ex in route_dup[:10]:
        print(f"    {f[:48]:<50} 记录={n:>5} 唯一={uniq:>5} 重复={nd:>4} 例={ex}")
    print(f"  [AclRules Key=Sequence：同一 acl 内序号唯一性]")
    print(f"    真实 ACL 规则行 {acl_seq_total}，同 acl 内序号重复 {acl_seq_dupn}")

    print("\n=== 脏数据抽查 ===")
    print(f"  Nokia Description 字段总数 {desc_total_nk}，其中含字面双引号 "
          f"{len(desc_quote)} 条 ({len(desc_quote)*100.0/max(desc_total_nk,1):.1f}%)")
    for f, d in desc_quote[:5]:
        print(f"    {f[:36]:<38} {d!r}")
    print(f"  地址/掩码字段含 '/' （华为应为点分十进制，诺基亚应为前缀长度）: "
          f"{len(mask_bad)} 条")
    for f, k, v in mask_bad[:5]:
        print(f"    {f[:36]:<38} {k}={v!r}")

    out = os.path.join(HERE, "_quality_rows.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(reports, fh, ensure_ascii=False, indent=2)
    return 0


def cmd_cross(argv):
    """无模板厂商套用华为模板 —— 量化语法差异"""
    chosen = _load_manifest()
    colls = get_tpl(HW_TPL)
    hw_rules = sum(len(c.rules) for c in colls)
    compiled = []
    for c in colls:
        for r in c.rules:
            try:
                compiled.append((c.name, P.compile_rule(r, c)))
            except KeyError:
                pass

    for vendor in (VENDOR_ZTE, VENDOR_H3C, VENDOR_RJ):
        grp = [s for s in chosen if s["vendor"] == vendor]
        if not grp:
            continue
        print(f"\n{'=' * 96}")
        print(f"### {vendor} 套用【华为模板】—— 语法差异量化")
        print("=" * 96)
        total_lines = 0
        hit_lines = 0
        per_rule = Counter()
        fam_total = Counter()
        fam_covered = set()
        for s in grp:
            with open(s["path"], encoding="utf-8", errors="replace") as fh:
                lines = [l.rstrip("\r\n") for l in fh.readlines()]
            lines = [l for l in lines if l.strip()]
            total_lines += len(lines)
            hits = 0
            for line in lines:
                st = line.strip()
                if not st.startswith("!") and not st.startswith("#"):
                    fam_total[family(st)] += 1
                for cname, cr in compiled:
                    if cr.rx.match(line):
                        hits += 1
                        per_rule[(cname, cr.rx.pattern)] += 1
                        fam_covered.add(family(st))
                        break
            hit_lines += hits
            t = truth_interfaces(s["path"], vendor)
            print(f"  {s['file'][:44]:<46} 行={len(lines):>6} 命中={hits:>6} "
                  f"({hits*100.0/len(lines) if lines else 0:5.2f}%)  真实接口={t['count']}")
        print(f"\n  合计: {hit_lines}/{total_lines} 行命中华为模板任意规则 "
              f"({hit_lines*100.0/total_lines if total_lines else 0:.2f}%)")
        print(f"  华为模板规则总数 = {hw_rules}；被 {vendor} 文件命中过的规则 = {len(per_rule)}"
              f"（命中率 {len(per_rule)*100.0/hw_rules:.1f}%）")
        uncovered = sorted([(f, n) for f, n in fam_total.items() if f not in fam_covered],
                           key=lambda kv: -kv[1])
        tot_uncovered = sum(n for _, n in uncovered)
        print(f"  命令族：共 {len(fam_total)} 种，华为模板【一个都接不住】的 {len(uncovered)} 种，"
              f"涉及 {tot_uncovered} 行（占全部行 {tot_uncovered*100.0/total_lines:.1f}%）")
        for f, n in uncovered[:25]:
            print(f"    {n:>6}  {f}")

        # 接口级对比
        print(f"\n  === {vendor}：华为模板解析出的 Interfaces vs 真实接口 ===")
        agg_p = agg_t = 0
        for s in grp:
            res, err = run_parse(s["path"], vendor)
            if err:
                print(f"  !! {s['file']}: {err['type']}: {err['msg']}")
                continue
            t = truth_interfaces(s["path"], vendor)
            recs = res.get("Interfaces", [])
            names = [r.get("Name") for r in recs]
            tset = set(t["names"])
            pset = set(names)
            agg_p += len(recs)
            agg_t += t["count"]
            print(f"  {s['file'][:44]:<46} 真实={t['count']:>4} 解析={len(recs):>4} "
                  f"名字命中={len(tset & pset):>4} 多解析={len(pset-tset):>4} "
                  f"漏解析={len(tset-pset):>4}")
            if pset - tset:
                print(f"      多解析样例: {sorted(pset-tset)[:4]}")
            if tset - pset:
                print(f"      漏解析样例: {sorted(tset-pset)[:4]}")
        if agg_t:
            print(f"\n  合计: 真实接口 {agg_t}，解析出 {agg_p} "
                  f"(={agg_p*100.0/agg_t:.1f}% of 真实)")

    # 中兴「接口资产级」需要覆盖的命令族 —— 直接回答「要写多少条规则」
    zte_grp = [s for s in chosen if s["vendor"] == VENDOR_ZTE]
    if zte_grp:
        print(f"\n{'=' * 96}")
        print("### 中兴【接口资产级】需要新模板覆盖的规则量（只看 !<if-intf> 分区）")
        print("=" * 96)
        fam = Counter()
        fam_cov = set()
        tot = 0
        for s in zte_grp:
            insec = False
            with open(s["path"], encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    st = ln.strip()
                    if st.startswith("!<") or st.startswith("</"):
                        insec = st in ("!<if-intf>",)
                        continue
                    if not insec or not st:
                        continue
                    tot += 1
                    f = family(st)
                    fam[f] += 1
                    for cname, cr in compiled:
                        if cr.rx.match(ln.rstrip("\r\n")):
                            fam_cov.add(f)
                            break
        unc = [(f, n) for f, n in fam.items() if f not in fam_cov]
        unc.sort(key=lambda kv: -kv[1])
        print(f"  !<if-intf> 分区内有效行 = {tot}")
        print(f"  命令族总数 = {len(fam)}；华为模板能命中的族 = {len(fam_cov)}；"
              f"接不住的族 = {len(unc)}")
        print(f"  接不住族涉及行数 = {sum(n for _, n in unc)} "
              f"({sum(n for _, n in unc)*100.0/max(tot,1):.1f}%)")
        print("  新模板必须新增的规则（按命令族频次 top 30）:")
        for f, n in unc[:30]:
            print(f"    {n:>6}  {f}")

    # 中兴模板可行性：把「分区标记 + 缩进 + 命令名」的差异列成清单
    print(f"\n{'=' * 96}")
    print("### 中兴 vs 华为 格式差异清单（逐项，来自真实样本）")
    print("=" * 96)
    rows = [
        ("命令分隔符", "#（独占一行）", "$（独占一行）", "不影响引擎（引擎不看分隔符）"),
        ("子命令缩进", "1 空格", "2 空格",
         "模板规则用 ^\\s* 开头 → 缩进宽度不敏感，两边都能吃"),
        ("接口声明位置", "顶格 ^interface <name>", "顶格 ^interface <name>",
         "同形态；但中兴在 !<if-intf> 分区外也有顶格 interface（引用）"),
        ("分区标记", "无", "!<section> … </section>，共 85 种",
         "华为模板无分区概念 → 中兴的引用块被当成接口定义，多解析"),
        ("开关命令", "undo shutdown / shutdown", "no shutdown / shutdown",
         "华为模板只认 undo shutdown → 中兴的 no shutdown 命中不了（默认 up 兜底）"),
        ("地址命令", "ip address A M [sub] / ipv6 address A/L", "ip address A M …",
         "基本同形态，可复用"),
        ("接口名", "Eth-Trunk1 / GigabitEthernet0/0/1", "cgei-0/0/0/1 / smartgroup1",
         "命名空间不同，但正则 \\S+ 通用"),
        ("接口块闭合", "下一个 ^interface 顶格行", "下一个 ^interface 顶格行（分区外也换块）",
         "同形态，但边界语义不同"),
        ("ACL 语法", "acl number N + rule N permit ...", "!<ipv4-acl> + ipv4-access-list ...",
         "完全不同的两套语法，模板需重写"),
        ("路由语法", "ip route-static ...", "!<static> 段内 ip route-static …",
         "命令名相同，层级不同"),
    ]
    print(f"  {'维度':<14}{'华为':<28}{'中兴':<34}备注")
    for a, b, c, d in rows:
        print(f"  {a:<14}{b:<28}{c:<34}{d}")
    return 0


def cmd_all(argv):
    for fn in (cmd_inventory, cmd_sample, cmd_accuracy, cmd_coverage,
               cmd_variants, cmd_quality, cmd_cross):
        print("\n" + "=" * 100)
        print(f"### {fn.__name__}")
        print("=" * 100)
        fn(argv)
    return 0


SUBS = {
    "inventory": cmd_inventory,
    "sample": cmd_sample,
    "accuracy": cmd_accuracy,
    "coverage": cmd_coverage,
    "variants": cmd_variants,
    "quality": cmd_quality,
    "fieldcheck": cmd_fieldcheck,
    "defects": cmd_defects,
    "robust": cmd_robust,
    "fullformat": cmd_fullformat,
    "cross": cmd_cross,
    "all": cmd_all,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in SUBS:
        print(__doc__)
        print("可用子命令: " + ", ".join(SUBS))
        sys.exit(1)
    sys.exit(SUBS[sys.argv[1]](sys.argv[2:]))
