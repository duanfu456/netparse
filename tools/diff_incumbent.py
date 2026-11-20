"""在只读生产语料上比较 incumbent 与 netparse。

三侧都复用生产 build_excel.build_rows；incumbent 产物不重跑、不修改。
临时 JSON 保留原文件名中的采集时间戳，并在 finally 中清理。

用法：
  python tools/diff_incumbent.py --sample 20
  python tools/diff_incumbent.py --all --out-dir diff-report/full --jobs 12
  python tools/diff_incumbent.py --dry-run

`--jobs N` 只影响速度，不影响结果：单文件处理是纯函数（process_entry），
主进程按提交顺序合并（merge_entry），所以 `--jobs 1` 与 `--jobs N` 的产物逐字节一致。
"""

import argparse
import collections
import datetime
import hashlib
import json
import os
import random
import re
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, r"E:\配置文件\配置解析\华为")

import probe_stack as engine  # noqa: E402
from masking import SECRET_SHAPE_PATTERNS, is_secret_shape, mask_value  # noqa: E402
from netparse.adapters.incumbent import MAPPING_SPEC, to_incumbent_shape  # noqa: E402

try:
    import build_excel
except (ImportError, OSError) as exc:
    raise SystemExit("无法只读加载生产 build_excel.py：%r" % (exc,))

RAW_ROOT = r"E:\配置文件\BRAS&SR\原始配置"
PARSED_ROOT = r"E:\配置文件\BRAS&SR\解析后"
INDEX = os.path.join(PARSED_ROOT, "_index.json")
READONLY_ROOTS = [RAW_ROOT, PARSED_ROOT, r"E:\配置文件\配置解析\华为"]
CHANGE = os.path.join(ROOT, ".devforge", "spec", "changes", "netparse-v3-diff-and-mask")
BASELINE = os.path.join(CHANGE, "baseline-template.cmdb")
CURRENT = os.path.join(ROOT, "templates", "huawei-vrp.cmdb")
SIDES = ("incumbent", "netparse_before_fix", "netparse_after_fix")
COLUMNS = [name for name, _width in build_excel.COLUMNS]
CATEGORIES = ("EthTrunkMember", "Bgp", "BgpPeers", "Acls", "AclRules", "Nqas", "NqaSessions")
INTERFACE_NP = ("AdminStatus", "CarrierDownHoldTime", "Dcn", "Duplex", "EthTrunkMember",
                "Isis", "Mtu", "Speed", "Statistic", "Type", "UserVlan")
INTERFACE_INC = ("QOS", "VLAN", "bas", "multicast", "qos-profile", "traffic-policy")
DEFAULT_SAMPLE_SEED = 8440

# 命令族识别沿用 probe_coverage3 的 skeleton/family；regex 仅用于挑选相关真实 trace。
COLUMN_PATTERNS = {
    "接口描述": r"^\s*description\s+",
    "接口入流限速(M)": r"^\s*(?:qos\s+(?:car|profile)|qos-profile\s+)",
    "接口出流限速(M)": r"^\s*(?:qos\s+(?:car|profile)|qos-profile\s+)",
    "端口IPv4": r"^\s*ip\s+address\s+", "端口IPv6": r"^\s*ipv6\s+address\s+",
    "IPv4静态路由明细": r"^\s*ip\s+route-static\s+", "IPv6静态路由明细": r"^\s*ipv6\s+route-static\s+",
    # 实测 VPN 有两个形态：接口下 `ip binding vpn-instance X`（193 条）与
    # BAS 业务块内 `vpn-instance X`（81 条）。只认前者会漏掉后者的真实命令族。
    "VPN实例": r"^\s*(?:ip\s+binding\s+vpn-instance|vpn-instance)\s+",
    "域名名称": r"^\s*(?:access-type\s+layer2-subscriber\b|bas\s*$)",
    "组播类型": r"^\s*(?:multicast\b|pim\s+sm\b)",
    "入方向流量策略": r"^\s*traffic-policy\s+\S+\s+inbound\b",
    "出方向流量策略": r"^\s*traffic-policy\s+\S+\s+outbound\b",
    # 实测外层 VLAN 不是独立命令，而是与内层同行的 `user-vlan 1000 1999 qinq 2000`；
    # PVLAN 证据必须取含 qinq 的行，否则会把纯 user-vlan 行误当外层证据。
    "PVLAN": r"^\s*(?:qinq\b.*|user-vlan\s+.*\bqinq\b.*)",
    "CVLAN": r"^\s*(?:vlan-type|user-vlan|port\s+default\s+vlan)\b",
    "VLAN封装类型": r"^\s*(?:vlan-type|user-vlan|qinq|port\s+link-type)\b",
}
# 两侧台账都不收的形态：不能拿来当缺口证据，否则会误判成「解析丢了记录」。
#  · NULL0 是黑洞下一跳，不是显式出接口；
#  · 第3段参数就是 IPv4 地址 = 纯下一跳（后面跟 preference/description 也算），
#    这类路由两侧都不进第10列。
# 出接口形态第3段是接口名（Eth-Trunk185.100），不会被排除。
EXCLUDE_FROM_EVIDENCE = {
    "IPv4静态路由明细": re.compile(
        r"\bNULL0\b"
        # vpn 变体：vpn-instance <name> <dst> <mask> <nexthop:IPv4>
        r"|^ip\s+route-static\s+vpn-instance\s+\S+\s+\S+\s+\S+\s+\d{1,3}(?:\.\d{1,3}){3}(?:\s|$)"
        # plain 变体：<dst> <mask> <nexthop:IPv4>；用 (?!vpn-instance) 防止把关键字当 dst
        r"|^ip\s+route-static\s+(?!vpn-instance\s)\S+\s+\S+\s+\d{1,3}(?:\.\d{1,3}){3}(?:\s|$)",
        re.I),
}

REQUIRED_FIELDS = {
    "设备名称": "Device.SysName", "管理IP": "Device.RouterId",
    "接口": "Interfaces.Name", "接口描述": "Interfaces.Description",
    "接口入流限速(M)": "Interfaces.QOS / QosProfiles", "接口出流限速(M)": "Interfaces.QOS / QosProfiles",
    "端口IPv4": "Interfaces.IpAddress.Address,Mask", "端口IPv6": "Interfaces.IpAddress.Address,Mask",
    "IPv4静态路由明细": "RouteStatics.NextHopIface", "IPv6静态路由明细": "RouteStaticsV6（集合未声明）",
    "VPN实例": "Interfaces.IpBindingVpn", "域名名称": "Interfaces.bas.damian（未声明）",
    "组播类型": "Interfaces.multicast.type（未声明）", "入方向流量策略": "Interfaces.traffic-policy.in（未声明）",
    "出方向流量策略": "Interfaces.traffic-policy.out（未声明）", "PVLAN": "Interfaces.VLAN.QinQ（未声明）",
    "CVLAN": "Interfaces.VLAN.user-vlan / dot1q（结构不同）", "VLAN封装类型": "Interfaces.VLAN.type（结构不同）",
}
EFFORT = {
    "设备名称": 0.25, "管理IP": 0.25, "接口": 0.5, "接口描述": 0.25,
    "接口入流限速(M)": 1.0, "接口出流限速(M)": 1.0, "端口IPv4": 0.5, "端口IPv6": 0.5,
    "IPv4静态路由明细": 0.5, "IPv6静态路由明细": 0.75, "VPN实例": 0.25, "域名名称": 0.5,
    "组播类型": 0.5, "入方向流量策略": 0.5, "出方向流量策略": 0.5,
    "PVLAN": 1.0, "CVLAN": 1.0, "VLAN封装类型": 0.5,
}


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_index():
    if not os.path.isfile(INDEX):
        raise RuntimeError("incumbent 索引不存在：" + INDEX)
    idx = read_json(INDEX)
    entries, missing = [], []
    for item in idx.get("files", []):
        if item.get("status") != "ok":
            continue
        rel = item["file"].replace("/", os.sep).replace("\\", os.sep)
        inc = os.path.join(PARSED_ROOT, rel)
        stem = os.path.basename(rel)[:-5]
        candidates = [os.path.join(RAW_ROOT, os.path.dirname(rel), stem),
                      os.path.join(RAW_ROOT, os.path.dirname(rel), stem + ".txt")]
        raw = next((p for p in candidates if os.path.isfile(p)), None)
        if raw is None or not os.path.isfile(inc):
            missing.append(rel)
        else:
            entries.append({"json": inc, "raw": raw, "month": rel.split(os.sep)[0],
                            "stem": stem, "index": item})
    if missing:
        raise RuntimeError("原始配置 / incumbent JSON 映射缺失 %d 个，示例：%s" % (len(missing), missing[:20]))
    return idx, entries


def select_entries(entries, sample=None, month=None, sample_seed=DEFAULT_SAMPLE_SEED):
    """月份先打乱，月内随机不放回，再按月轮转；不重复文件凑数。"""
    if month:
        entries = [e for e in entries if month in e["month"]]
    if sample is None:
        return entries
    if sample < 1:
        raise RuntimeError("--sample 必须为正整数")
    rng = random.Random(sample_seed)
    by_month = collections.defaultdict(list)
    for entry in entries:
        by_month[entry["month"]].append(entry)
    months = sorted(by_month)
    rng.shuffle(months)
    groups = []
    for month_name in months:
        group = list(by_month[month_name])
        rng.shuffle(group)
        groups.append(group)
    selected = []
    while len(selected) < sample:
        progressed = False
        for group in groups:
            if group and len(selected) < sample:
                selected.append(group.pop())
                progressed = True
        if not progressed:
            break
    return selected


def sampling_meta(candidates, selected, requested, seed):
    months = sorted({e["month"] for e in candidates})
    expected = min(requested, len(months), len(candidates)) if requested is not None else len(months)
    return {"available_months": months, "available_month_count": len(months),
            "candidate_files": len(candidates), "requested_sample": requested,
            "expected_distinct_months": expected, "months_covered": sorted({e["month"] for e in selected}),
            "sample_seed": seed, "algorithm": "月份顺序固定seed打乱；月内随机不放回；按月轮转",
            "estimation_note": "月份分层样本；不得把抽样填充率直接宣称为全量无偏估计"}


def index_rows(rows):
    out = {}
    for row in rows:
        key = str(row[3]).casefold()
        if key in out:
            raise RuntimeError("同一侧出现重名接口：%s" % row[3])
        out[key] = row
    return out


def empty_side():
    return {"filled": collections.Counter(), "total": 0,
            "values": collections.Counter(), "both_nonempty_diff": collections.Counter()}


def zero_stats():
    return {side: empty_side() for side in SIDES}


def add_stats(dst, src):
    for side in SIDES:
        for key in ("filled", "values", "both_nonempty_diff"):
            dst[side][key].update(src[side][key])
        dst[side]["total"] += src[side]["total"]


def compare_three(inc_rows, before_rows, after_rows, samples):
    indices = [index_rows(rows) for rows in (inc_rows, before_rows, after_rows)]
    stats = zero_stats()
    for key in sorted(set().union(*(set(index) for index in indices))):
        rows = [index.get(key) for index in indices]
        for i, col in enumerate(COLUMNS):
            for side, row in zip(SIDES, rows):
                if row is None:
                    continue
                if i == 0:
                    stats[side]["total"] += 1
                value = row[i]
                if value not in (None, "", []):
                    stats[side]["filled"][col] += 1
                    # “值条数”是非空单元格数；不按逗号拆值（规格 Scenario 的逐行累加口径）。
                    stats[side]["values"][col] += 1
            if all(row is not None for row in rows):
                iv, bv, av = (row[i] for row in rows)
                if iv not in (None, "") and av not in (None, "") and iv != av:
                    stats["incumbent"]["both_nonempty_diff"][col] += 1
                if (iv, bv, av).count(iv) < 3 and len(samples[col]) < 20:
                    samples[col].append({"device": rows[0][0], "interface": rows[0][3],
                                         "incumbent": report_value(iv),
                                         "netparse_before_fix": report_value(bv),
                                         "netparse_after_fix": report_value(av)})
    both_after = len(set(indices[0]) & set(indices[2]))
    return stats, [len(set(indices[0]) & set(x)) for x in indices], \
        [len(set(indices[0]) - set(x)) for x in indices[1:]], \
        [len(set(x) - set(indices[0])) for x in indices[1:]], both_after


def skeleton(line):
    text = line.strip()
    text = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<ip>", text)
    return re.sub(r"\b\d+\b", "<n>", text)


def family(line):
    head = []
    for token in skeleton(line).split():
        if token.startswith("<"):
            break
        head.append(token)
        if len(head) == 2:
            break
    return " ".join(head) if head else "(空)"


def redact_text(text):
    """只用于证据样本；实际解析值仍由引擎在捕获时脱敏。"""
    result = text
    for pattern in SECRET_SHAPE_PATTERNS[:1]:
        result = pattern.sub(engine.MASK_VALUE, result)
    return result


def report_value(value):
    """展示值复用引擎脱敏策略；不影响统计，不写回incumbent，也不改变计数。

    两步：先按行内密文形态替换（证据里常是整行命令），再做整值高熵判定。
    只做后者会漏掉 `peer x password cipher %^%#...%^%#` 这种嵌在行中的凭据。
    """
    if isinstance(value, str):
        return mask_value(redact_text(value), None, engine.MASK_VALUE, True, count=False)
    return value


def trace_records(traces, column):
    """保留原始行用于规则复核，展示行单独脱敏；两者不混用。"""
    pattern = COLUMN_PATTERNS.get(column)
    if not pattern:
        return []
    rx = re.compile(pattern, re.I)
    return [{"line": no, "text": report_value(redact_text(line)), "raw_text": line,
             "hit": collection, "collection": collection, "stack": stack}
            for no, line, collection, stack in traces if rx.search(line)]


def compile_rules(colls):
    """一次编译全部规则，供真实规则复核复用；不修改引擎。"""
    compiled = {}
    for coll in colls:
        items = []
        for index, rule in enumerate(coll.rules, 1):
            try:
                items.append((index, engine.compile_rule(rule, coll), rule))
            except KeyError:
                items.append((index, None, rule))
        compiled[coll.name] = items
    return compiled


def rules_matching(compiled, line):
    """真实行在该模板下的全部命中规则（集合 + 规则序号 + 原文/目标）。"""
    hits = []
    for collection, items in compiled.items():
        for index, compiled_rule, rule in items:
            if compiled_rule is None:
                continue
            if compiled_rule.rx.match(line):
                hits.append({"collection": collection, "rule_index": index,
                             "pattern": rule.pattern, "target": rule.target})
    return hits


def rules_tried(compiled, line, collection, limit=80):
    """指定集合内每条候选规则的匹配/失配结果，用于区分「没规则」和「规则全失配」。

    limit 必须大于典型集合的规则数（Interfaces 有 20+ 条）：截断会把真正命中的
    后半段规则剪掉，产出「rule_matches 非空但 rules_tried 全失配」的矛盾证据。
    命中规则永远保留在前，保证截断也不会藏掉匹配项。
    """
    items = compiled.get(collection) or []
    results = []
    for index, compiled_rule, rule in items:
        results.append({"collection": collection, "rule_index": index, "pattern": rule.pattern,
                        "target": rule.target,
                        "matched": bool(compiled_rule and compiled_rule.rx.match(line))})
    results.sort(key=lambda item: (not item["matched"], item["rule_index"]))
    return results[:limit]


def relevant_collections(column):
    """从规格要求字段推导相关集合名（如 RouteStatics.NextHopIface → RouteStatics）。"""
    names = []
    for name in required_field_names(column):
        head = name.split(".")[0]
        if head and head not in names:
            names.append(head)
    return names


def required_field_names(column):
    """把 REQUIRED_FIELDS 拆成精确的字段名集合。

    必须按分隔符切并去括注，不能拿整串做子串包含 ——
    `"RouteStatics.NextHop" in "RouteStatics.NextHopIface"` 为 True，
    会把「只声明了 NextHop 的修前模板」误判成「已声明 NextHopIface」，
    进而把真实的记录丢失错判成「字段已捕获、缺口不在解析层」。
    """
    names = []
    for chunk in re.split(r"[/,]", REQUIRED_FIELDS.get(column, "")):
        name = chunk.split("（")[0].strip()
        if not name:
            continue
        # 逗号后的裸名（`Interfaces.IpAddress.Address,Mask`、`...user-vlan / dot1q`）
        # 要补上前一个字段的集合前缀，否则会变成永不匹配的孤立名。
        if "." not in name and names:
            name = names[-1].rsplit(".", 1)[0] + "." + name
        if name not in names:
            names.append(name)
    return names


def template_declaration(path, colls, column):
    required = REQUIRED_FIELDS.get(column, "")
    wanted_fields = set(required_field_names(column))
    declared = []
    for coll in colls:
        for field_path in coll.all_fields:
            qualified = coll.name + "." + field_path
            # 精确匹配，不用 in（见 required_field_names 里的说明）
            if qualified in wanted_fields or (column == "CVLAN" and qualified == "Interfaces.UserVlan"):
                declared.append(qualified)
    existing = {c.name: c for c in colls}
    wanted = relevant_collections(column)
    rules = [{"collection": name, "rule_index": i, "pattern": r.pattern, "target": r.target}
             for name in wanted if name in existing
             for i, r in enumerate(existing[name].rules, 1)]
    return {"path": path, "target": required, "target_declared": bool(declared),
            "declared_fields": declared, "candidate_rules": rules,
            "relevant_collections": wanted,
            "missing_collections": [name for name in wanted if name not in existing],
            "collection_count": len(colls)}


def evidence_for(entry, traces, template, column, compiled, collection_counts):
    """采集某列的缺口证据。

    选行规则（顺序敏感，决定根因是否成立）：
      1. 先排除**两侧都不入台账**的形态（第10列的 NULL0 黑洞路由），它不是缺口；
      2. 缺口证据优先选**未被任何规则捕获**的真实行 —— 这类行才解释「为什么少了」；
      3. 全都命中时退回首行，用于证明「捕获正常但列仍短」，交由门禁判待人工。
    否则会拿一条正常命中并产生记录的行去解释缺口，得出自相矛盾的结论
    （VPN 的 `ip binding vpn-instance`、CVLAN 的 `user-vlan` 都是这种情形）。
    """
    exclude = EXCLUDE_FROM_EVIDENCE.get(column)
    records = trace_records(traces, column)
    if exclude is not None:
        records = [r for r in records if not exclude.search(r["raw_text"])]
    evidence = {"column": column, "input": None, "template": template,
                "trace": {"source": "probe_stack.run(colls, lines, trace=trace)",
                          "command_family": None, "total_lines": len(records),
                          "hit_lines": sum(r["hit"] is not None for r in records),
                          "records": records[:5], "rule_matches": [], "rules_tried": [],
                          "collection_record_counts": collection_counts, "hit": False}}
    if not records:
        return evidence
    unmatched, matched = [], []
    for record in records:
        record_matches = rules_matching(compiled, record["raw_text"])
        (matched if record_matches else unmatched).append((record, record_matches))
    chosen, matches = unmatched[0] if unmatched else matched[0]
    tried = []
    for collection in [m["collection"] for m in matches] or template.get("relevant_collections", []):
        tried.extend(rules_tried(compiled, chosen["raw_text"], collection))
    evidence["input"] = {"file": entry["stem"], "raw_path": entry["raw"],
                         "line": chosen["line"], "text": chosen["text"]}
    evidence["trace"].update({"command_family": family(chosen["text"]),
                              "hit": chosen["hit"] is not None,
                              "rule_matches": matches,
                              "rules_tried": tried,
                              "matched_record": bool(matches),
                              "unmatched_line_count": len(unmatched),
                              "matched_line_count": len(matched)})
    return evidence


def evidence_complete(evidence):
    """三类证据缺一不可：真实输入、模板声明、真实 trace（含规则复核结果）。"""
    if not evidence:
        return False
    inp = evidence.get("input")
    template = evidence.get("template")
    trace = evidence.get("trace")
    return bool(inp and inp.get("file") and inp.get("line") and template and
                template.get("path") and trace and trace.get("command_family") and
                (trace.get("total_lines") or 0) > 0 and trace.get("hit_lines") is not None and
                ("rule_matches" in trace or "rules_tried" in trace))


def classify_gap(evidence):
    """证据不全一律待人工；映射表 root_cause_if_empty 不参与自动判定。

    顺序很关键：整条记录丢失要先于「模板未覆盖」，因为修前静态路由正是
    「有候选规则但全部失配 → 记录整条没进结果」，不是模板没有这条命令。
    """
    if not evidence_complete(evidence):
        return {"root_cause": ["待人工判定"], "evidence_status": "INSUFFICIENT",
                "evidence": evidence,
                "reason": "缺真实文件/行号、模板声明或真实规则复核/trace，不猜根因"}
    trace, template = evidence["trace"], evidence["template"]
    if evidence.get("join"):
        join = evidence["join"]
        if (join.get("captured_original_fields") and join.get("source_collection") and
                join.get("target_collection") and join.get("key") and
                join.get("source_value") is not None and join.get("target_value") is not None and
                join.get("before") is not None and join.get("after") is not None):
            return {"root_cause": ["join缺失"], "evidence_status": "VERIFIED", "evidence": evidence}
        return {"root_cause": ["待人工判定"], "evidence_status": "INSUFFICIENT", "evidence": evidence,
                "reason": "未证明 join 所需原始字段已捕获"}
    record_loss = evidence.get("record_loss")
    if record_loss:
        if (record_loss.get("incumbent") and record_loss.get("netparse_missing") and
                (trace.get("rule_matches") or trace.get("rules_tried"))):
            return {"root_cause": ["整条记录丢失"], "evidence_status": "VERIFIED", "evidence": evidence}
        return {"root_cause": ["待人工判定"], "evidence_status": "INSUFFICIENT", "evidence": evidence,
                "reason": "缺候选规则匹配/失配复核，无法解释记录为何缺失"}
    matches = trace.get("rule_matches") or []
    if not matches:
        # 该真实行在模板全部规则中 0 命中：说明模板没有能捕获这条命令的规则。
        # 注意不能用 target_declared 一概否定 —— 同列可能有多个命令形态
        # （CVLAN 既有 user-vlan 也有 vlan-type dot1q；VPN 既有 ip binding 也有 BAS 内 vpn-instance），
        # 字段声明存在但只覆盖其中一种，另一种就是「模板未覆盖该命令形态」。
        if template.get("missing_collections") or not template.get("target_declared"):
            return {"root_cause": ["模板未覆盖该命令"], "evidence_status": "VERIFIED",
                    "evidence": evidence,
                    "reason": "真实行 0 命中规则，且该列所需集合/字段未声明："
                              + repr(template.get("missing_collections") or template.get("target"))}
        return {"root_cause": ["模板未覆盖该命令"], "evidence_status": "VERIFIED",
                "evidence": evidence,
                "reason": "该真实行 0 命中任何规则；同列字段虽已声明，但没有覆盖这一命令形态（"
                          + (evidence.get("input") or {}).get("text", "") + "）"}
    if not template.get("target_declared"):
        return {"root_cause": ["字段丢失"], "evidence_status": "VERIFIED", "evidence": evidence}
    return {"root_cause": ["待人工判定"], "evidence_status": "INSUFFICIENT", "evidence": evidence,
            "reason": "目标字段已声明/已捕获，缺口不在解析层；现有证据不能唯一归入所列根因",
            "analysis": evidence.get("analysis")}


def surplus_evidence(entry, traces, template, column, compiled, collection_counts):
    """netparse 多出值的证据：真实行 + 模板声明 + 规则命中，用于「语义不同」。

    只对确有多出值的列调用；取第一条命中规则并已产生记录的真实行。
    """
    records = trace_records(traces, column)
    hits = [r for r in records if r["hit"] is not None]
    if not records or not hits:
        return None
    record = hits[0]
    matches = rules_matching(compiled, record["raw_text"])
    if not matches:
        return None
    return {"column": column,
            "input": {"file": entry["stem"], "raw_path": entry["raw"],
                      "line": record["line"], "text": record["text"]},
            "template": template,
            "trace": {"source": "probe_stack.run(colls, lines, trace=trace)",
                      "command_family": family(record["text"]),
                      "total_lines": len(records), "hit_lines": len(hits),
                      "records": hits[:3], "rule_matches": matches,
                      "rules_tried": rules_tried(compiled, record["raw_text"], matches[0]["collection"]),
                      "collection_record_counts": collection_counts, "hit": True},
            "surplus": {"direction": "netparse_more",
                        "samples": [r["text"] for r in hits[:3]],
                        "note": "netparse 命中该命令并产生记录，incumbent 未按同一口径计入"}}


def classify_surplus(evidence):
    """netparse 多出值：只有真实行命中并产生记录才可判「语义不同」。"""
    if not evidence_complete(evidence):
        return {"root_cause": ["待人工判定"], "evidence_status": "INSUFFICIENT",
                "evidence": evidence, "reason": "多出值缺真实行/模板声明/规则命中证据"}
    return {"root_cause": ["语义不同"], "evidence_status": "VERIFIED", "evidence": evidence}


def route_loss(incumbent, np_results):
    """逐文件只产出可累加的计数；百分比一律由 finalize_route_loss 事后算。

    百分比是比值，不能跨文件相加（9 个文件各 100% 会累加成 900%）。
    """
    expected = sum(1 for r in (incumbent.get("route-static") or {}).get("IPV4", [])
                   if r.get("interface") and r["interface"].upper() != "NULL0")
    out = {}
    for label, result in np_results.items():
        actual = sum(1 for r in result.get("RouteStatics", [])
                     if r.get("NextHopIface") and r["NextHopIface"].upper() != "NULL0")
        out[label] = {"explicit_interface_routes": expected, "captured": actual,
                      "lost": max(0, expected - actual)}
    return out


def finalize_route_loss(route):
    """把累加后的计数换算成百分比；分母为 0 时记 0 并标注无可评估路由。"""
    out = {}
    for label, counters in route.items():
        expected, lost = counters.get("explicit_interface_routes", 0), counters.get("lost", 0)
        item = dict(counters)
        item["loss_pct"] = round(100 * lost / expected, 2) if expected else 0
        if not expected:
            item["note"] = "该口径无显式出接口路由（已排除 NULL0），百分比不具意义"
        out[label] = item
    return out


def route_record_loss_evidence(entry, incumbent, before, traces, template, compiled, collection_counts):
    """修前静态路由整条丢失的证据：incumbent 有出接口路由 → netparse 无对应记录。

    必须同时证明：真实行存在、该行为何未产生记录（候选规则匹配/失配）。
    """
    missing = []
    before_keys = {(r.get("Prefix", ""), r.get("Mask", ""), r.get("NextHopIface", ""),
                    r.get("NextHop", ""), r.get("VpnInstance", "")) for r in before.get("RouteStatics", [])}
    for route in (incumbent.get("route-static") or {}).get("IPV4", []):
        if not route.get("interface") or route["interface"].upper() == "NULL0":
            continue
        cidr = "/".join(x for x in (route.get("dest", ""), route.get("mask", "")) if x)
        if not any(cidr.split("/")[0] == key[0] for key in before_keys):
            missing.append({"dest": route.get("dest", ""), "mask": route.get("mask", ""),
                            "interface": route.get("interface", "")})
    records = trace_records(traces, "IPv4静态路由明细")
    if not missing or not records:
        return None
    first = next((r for r in records
                  if any(str(m["dest"]) in r["text"] and str(m["interface"]) in r["text"] for m in missing)),
                 records[0])
    matches = rules_matching(compiled, first["raw_text"])
    tried = []
    for collection in [m["collection"] for m in matches] or template.get("relevant_collections", []):
        tried.extend(rules_tried(compiled, first["raw_text"], collection))
    return {"column": "IPv4静态路由明细",
            "input": {"file": entry["stem"], "raw_path": entry["raw"], "line": first["line"],
                      "text": first["text"]},
            "template": template,
            "trace": {"source": "probe_stack.run(colls, lines, trace=trace)",
                      "command_family": family(first["text"]), "total_lines": len(records),
                      "hit_lines": sum(r["hit"] is not None for r in records), "records": [first],
                      "rule_matches": matches, "rules_tried": tried,
                      "collection_record_counts": collection_counts, "hit": first["hit"] is not None},
            "record_loss": {"incumbent": len(missing), "netparse_missing": len(missing),
                            "examples": missing[:5]},
            "asset_layer_note": "interface 在命令层拆出；build_excel.build_route_index 只读取并跳过无 interface 的路由"}


def evidence_signature(evidence):
    """证据分桶键：同命令族 + 同命中规则的结构归为一组，避免同一成因刷屏。"""
    if not evidence:
        return None
    trace = evidence.get("trace") or {}
    matches = trace.get("rule_matches") or []
    key = (trace.get("command_family"),
           tuple(sorted((m["collection"], m["rule_index"]) for m in matches)),
           bool((evidence.get("template") or {}).get("target_declared")))
    return key


def add_evidence(buckets, evidence, per_signature=3, max_signatures=40):
    """按成因分桶留存证据；每桶保留有限样本，且限制桶总数（全量 8440 文件不能无界增长）。"""
    signature = evidence_signature(evidence)
    if signature is None:
        return
    if signature not in buckets and len(buckets) >= max_signatures:
        return
    bucket = buckets.setdefault(signature, [])
    if len(bucket) < per_signature:
        bucket.append(evidence)


def group_diagnoses(buckets, classifier):
    """把一列的全部证据按根因分组；每组各自带证据，不用单一标签掩盖多成因。"""
    groups = []
    for signature in sorted(buckets, key=lambda item: str(item)):
        samples = buckets[signature]
        if not samples:
            continue
        verdict = classifier(samples[0])
        groups.append({"root_cause": verdict["root_cause"],
                       "evidence_status": verdict["evidence_status"],
                       "reason": verdict.get("reason"),
                       "sample_count": len(samples),
                       "evidence": samples[0]})
    if not groups:
        return {"root_cause": ["待人工判定"], "evidence_status": "INSUFFICIENT",
                "reason": "本列无任何可定位的真实行/模板声明/trace 证据", "groups": []}
    causes, insufficient = [], False
    for group in groups:
        for cause in group["root_cause"]:
            if cause not in causes:
                causes.append(cause)
        if group["evidence_status"] != "VERIFIED":
            insufficient = True
    status = "INSUFFICIENT" if insufficient else "VERIFIED"
    if insufficient and "待人工判定" not in causes:
        causes.append("待人工判定")
    return {"root_cause": causes, "evidence_status": status,
            "reason": "同一列存在多组成因，分别见 groups；含未证实成因时为 INSUFFICIENT",
            "groups": groups,
            "verified_groups": [g["root_cause"] for g in groups if g["evidence_status"] == "VERIFIED"],
            "pending_groups": [g["root_cause"] for g in groups if g["evidence_status"] != "VERIFIED"],
            "evidence": groups[0]["evidence"]}


def count_records(result, name):
    """某集合在本文件实际产生的记录数（顶层 list/dict 与嵌套集合都算）。"""
    value = result.get(name)
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return 1 if value else 0
    return 0


def collection_record_counts(result):
    return {name: count_records(result, name)
            for name in ("Device", "Interfaces", "IpAddress", "RouteStatics",
                         "VpnInstances", "Bgp", "BgpPeers", "Acls", "AclRules",
                         "Nqas", "NqaSessions", "EthTrunkMember")}


def build_template(colls):
    return {"collections": [c.name for c in colls],
            "paths": sorted("%s.%s" % (c.name, path) for c in colls for path in c.all_fields)}


def count_category_records(value, category):
    """实际记录数；只看以该集合名为键的 list/dict 元素，不把容器自身算记录。"""
    total = 0
    if isinstance(value, dict):
        for key, child in value.items():
            if key == category:
                if isinstance(child, list):
                    total += len(child)
                elif isinstance(child, dict):
                    total += 1
            if isinstance(child, (dict, list)):
                total += count_category_records(child, category)
    elif isinstance(value, list):
        for child in value:
            total += count_category_records(child, category)
    return total


def capability(np_fields, inc_fields, category_counts, colls):
    matched = {"Name": "name", "Description": "description", "IpAddress": "IP", "IpBindingVpn": "vpn"}
    same = ["Device↔device", "Interfaces↔interfaces", "RouteStatics↔route-static"]
    diff = ["IpAddress↔interfaces[].IP", "VpnInstances↔interfaces[].vpn"]
    return {
        "interface_level_fields": {
            "_desc": "只统计接口记录直接子键，不含子集合内部字段；按业务语义对应。",
            "matched": list(matched.values()), "only_incumbent": list(INTERFACE_INC),
            "only_netparse": list(INTERFACE_NP),
            "coverage": {"incumbent": 10, "netparse": 15, "matched": 4,
                         "only_netparse": 11, "only_incumbent": 6},
            "observed_incumbent_fields": sorted(inc_fields), "observed_netparse_fields": sorted(np_fields),
            "content_correspondence_diff_shape": [{"incumbent": "VLAN", "netparse": "UserVlan",
                "note": "内容对应、结构不同；仅搬运已捕获 UserVlan，不合成 type/QinQ/dot1q"}],
        },
        "collection_level_fields": {
            "qos_profile_top": {"incumbent": "list[{name,in:{cir},out:{cir}}]", "netparse": None,
                                "note": "模板无任何 qos-profile 规则"},
            "route_static": {"incumbent": "{IPV4:[{dest,mask,interface?,nexthop,vpn}], IPV6:[...]}",
                             "netparse": "list[{Prefix,Mask,NextHop,NextHopIface,VpnInstance,...}]",
                             "after_fix": "NextHopIface 已补齐；IPv6 route-static 仍未覆盖"},
        },
        "asset_categories": {
            "_taxonomy": {"shared_same_shape": same, "shared_diff_shape": diff,
                          "netparse_only": list(CATEGORIES), "incumbent_only": ["qos-profile(顶层命名空间定义)"]},
            "incumbent": {"total_categories": 4, "shared_same_shape": 3, "shared_diff_shape": 0, "only": 1,
                          "list": ["device", "interfaces", "route-static", "qos-profile"]},
            "netparse": {"total_categories": 12, "shared_same_shape": 3, "shared_diff_shape": 2, "only": 7,
                         "list": ["Device", "Interfaces", "IpAddress", "EthTrunkMember", "RouteStatics",
                                  "VpnInstances", "Bgp", "BgpPeers", "Acls", "AclRules", "Nqas", "NqaSessions"]},
            "netparse_only_instances": [{"category": k, "instances": category_counts[k]} for k in CATEGORIES],
        },
        "extra_value_note": "netparse 多出的11个接口直接字段不在19列台账内，可用于MTU/速率/双工/管理状态/聚合成员/ISIS等CI建模；不能抵消台账缺口。",
        "declared_template": colls,
    }


def roots_check():
    missing = [p for p in READONLY_ROOTS if not os.path.isdir(p)]
    if missing:
        raise RuntimeError("只读目录不存在：" + repr(missing))
    proc = subprocess.run([sys.executable, os.path.join(HERE, "readonly_guard.py"), "--verify"],
                          capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError("只读校验失败：\n" + proc.stdout + proc.stderr)
    return {"status": "OK", "checked_paths": READONLY_ROOTS, "message": "READ-ONLY OK"}


def git_commit(path, fallback="WORKTREE"):
    try:
        out = subprocess.check_output(["git", "-C", ROOT, "log", "-1", "--format=%H", "--", path],
                                      text=True, stderr=subprocess.DEVNULL).strip()
        return out or fallback
    except (OSError, subprocess.CalledProcessError):
        return fallback


def file_sha1(path):
    digest = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def scan_secret_values(value, path="$", hits=None):
    hits = [] if hits is None else hits
    if isinstance(value, dict):
        for key, val in value.items():
            scan_secret_values(val, path + "." + str(key), hits)
    elif isinstance(value, list):
        for i, val in enumerate(value):
            scan_secret_values(val, "%s[%d]" % (path, i), hits)
    elif isinstance(value, str):
        for i, pattern in enumerate(SECRET_SHAPE_PATTERNS):
            if i == 1:
                # 与引擎完全同一判定入口，避免报告扫描规则漂移。
                if is_secret_shape(value):
                    hits.append({"path": path, "start": 0, "end": len(value)})
                continue
            for match in pattern.finditer(value):
                hits.append({"path": path, "start": match.start(), "end": match.end()})
    return hits


def safe_write(path, text, data=None):
    if data is not None:
        hits = scan_secret_values(data)
    elif path.endswith(".json"):
        hits = scan_secret_values(json.loads(text))
    else:
        hits = [{"path": "$text", "start": m.start(), "end": m.end()}
                for pattern in SECRET_SHAPE_PATTERNS[:1] for m in pattern.finditer(text)]
    if hits:
        if os.path.exists(path):
            os.unlink(path)
        raise RuntimeError("报告明文形态扫描失败（仅报告位置，不回显内容）：%r" % hits[:10])
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    with open(path, encoding="utf-8") as f:
        written = f.read()
    if path.endswith(".json"):
        verify_hits = scan_secret_values(json.loads(written))
    else:
        verify_hits = [m for pattern in SECRET_SHAPE_PATTERNS[:1] for m in pattern.finditer(written)]
    if verify_hits:
        os.unlink(path)
        raise RuntimeError("写盘后密文扫描失败，已删除：" + path)


def side_stats(stats, side, column):
    total = stats[side]["total"]
    filled = stats[side]["filled"][column]
    return {"filled": filled, "total": total, "rate": round(filled / total, 6) if total else 0}


def file_summary(entry, stats):
    return {"file": entry["stem"], "month": entry["month"],
            "columns": {col: {side: side_stats(stats, side, col) for side in SIDES} for col in COLUMNS}}


def diagnose_cell(buckets, classifier):
    """一列一侧的诊断入口：兼容单份证据（旧调用）与分桶证据（新采集）。"""
    if not buckets:
        return {"root_cause": ["待人工判定"], "evidence_status": "INSUFFICIENT",
                "reason": "本列无任何可定位的真实行/模板声明/trace 证据", "groups": [], "evidence": None}
    if "input" in buckets or "trace" in buckets:       # 单份证据
        verdict = classifier(buckets)
        verdict.setdefault("groups", [])
        return verdict
    return group_diagnoses(buckets, classifier)


def summary(stats, evidence_by_side=None, surplus_by_side=None):
    evidence_by_side = evidence_by_side or {}
    surplus_by_side = surplus_by_side or {}
    columns = {}
    for col in COLUMNS:
        sides = {side: side_stats(stats, side, col) for side in SIDES}
        values = {side: stats[side]["values"][col] for side in SIDES}
        deltas = {side: round(100 * abs(values["incumbent"] - values[side]) / values["incumbent"], 2)
                  if values["incumbent"] else (0 if values[side] == 0 else 100) for side in SIDES[1:]}
        red = any(value > 20 for value in deltas.values())
        diagnoses, surplus = {}, {}
        for side in SIDES[1:]:
            short = (stats[side]["filled"][col] < stats["incumbent"]["filled"][col] or
                     stats[side]["values"][col] < stats["incumbent"]["values"][col] or
                     stats[side]["total"] < stats["incumbent"]["total"])
            over = (stats[side]["filled"][col] > stats["incumbent"]["filled"][col] or
                    stats[side]["values"][col] > stats["incumbent"]["values"][col])
            diagnoses[side] = diagnose_cell(evidence_by_side.get(side, {}).get(col), classify_gap) if short \
                else {"root_cause": [], "evidence_status": "NOT_APPLICABLE", "evidence": None, "groups": []}
            # 正差异也必须被诊断：netparse 多出值时不能只把它标红就完事。
            surplus[side] = diagnose_cell(surplus_by_side.get(side, {}).get(col), classify_surplus) if over \
                else {"root_cause": [], "evidence_status": "NOT_APPLICABLE", "evidence": None, "groups": []}
        directions = []
        for side in SIDES[1:]:
            if values[side] < values["incumbent"]:
                directions.append("%s:netparse少" % side)
            elif values[side] > values["incumbent"]:
                directions.append("%s:netparse多" % side)
        columns[col] = dict(sides,
            global_values=values, delta_pct_before=deltas["netparse_before_fix"],
            delta_pct_after=deltas["netparse_after_fix"], flag="RED" if red else "OK",
            flag_reason="修前或修后全局值条数差 > 20%%；方向：%s" % ("、".join(directions) or "无") if red
            else "三侧全局值条数差均不超过20%",
            per_file_resolvable=not all(sides[side]["rate"] < .01 for side in SIDES),
            note="本列逐文件差异不具分辨力，请以全局值条数为准" if all(sides[side]["rate"] < .01 for side in SIDES) else "",
            diagnoses=diagnoses, surplus=surplus, required_field=REQUIRED_FIELDS.get(col),
            both_nonempty_diff=stats["incumbent"]["both_nonempty_diff"][col])
    return columns


def markdown(report):
    meta, ledger = report["meta"], report["part1_ledger"]
    columns = ledger["column_fill_rate"]
    lines = ["# netparse 与 incumbent 配置解析差异报告", "",
             "> 文件：%d；模式：%s；抽样种子 %s。三侧均复用生产 build_excel.build_rows。" %
             (meta["files_processed"], meta["mode"], meta.get("sample_seed")), "",
             "## 全局19列三栏（填充率 / 非空单元格值条数）", "",
             "| 列 | incumbent | 修前 | 修后 | 全局值条数 inc/before/after | 修前差 | 修后差 | 标记 | 修后缺口根因 | 修后多值根因 |",
             "|---|---:|---:|---:|---:|---:|---:|---|---|---|"]
    notes = []
    for col in COLUMNS:
        c = columns[col]
        rates = ["%.2f%%" % (100 * c[s]["rate"]) for s in SIDES]
        values = " / ".join(str(c["global_values"][s]) for s in SIDES)
        causes = "、".join(c["diagnoses"]["netparse_after_fix"]["root_cause"]) or "—"
        over = "、".join(c["surplus"]["netparse_after_fix"]["root_cause"]) or "—"
        lines.append("| %s | %s | %s | %s | %s | %.2f%% | %.2f%% | %s | %s | %s |" %
                     (col, rates[0], rates[1], rates[2], values, c["delta_pct_before"],
                      c["delta_pct_after"], "**RED**" if c["flag"] == "RED" else "OK", causes, over))
        if c["note"]:
            # 表格里插 colspan 单元格会破坏 Markdown 列对齐（标准 Markdown 不支持 colspan），
            # 所以备注放到表下方的脚注区，用列名索引。
            notes.append("- **%s**：%s" % (col, c["note"]))
    lines += ["", "逐文件19列三栏保存在JSON `per_file_column_fill_rate`；每文件结构为 file/month/columns。", ""]
    if notes:
        lines += ["**列备注：**", ""] + notes + [""]
    lines += ["## 根因证据门禁", "",
              "自动根因必须有真实文件/行号、模板声明（含候选集合与规则）和真实 trace 规则复核；"
              "不足为 `待人工判定 + INSUFFICIENT`。join缺失单列且先证明原始字段已捕获。"
              "netparse 多出值同样单独诊断方向，不只标红。", "",
              "```json", json.dumps({col: {"gap": c["diagnoses"], "surplus": c["surplus"]}
                                    for col, c in columns.items()
                                    if any(c["diagnoses"][s]["root_cause"] or c["surplus"][s]["root_cause"]
                                           for s in SIDES[1:])}, ensure_ascii=False, indent=2), "```", "",
              "## 静态路由修前/修后（两组口径不混算）", "", "```json",
              json.dumps(report["route_static_validation"], ensure_ascii=False, indent=2), "```", "",
              "## 接口与双向能力覆盖", ""]
    cov = report["part2_capability"]["interface_level_fields"]["coverage"]
    lines += ["- 接口 join（修后）：both=%d / only_incumbent=%d / only_netparse=%d。" %
              (ledger["interface_alignment"]["both"], ledger["interface_alignment"]["only_incumbent"], ledger["interface_alignment"]["only_netparse"]),
              "- 接口直接子键：netparse=%d / incumbent=%d / 共有=%d / netparse独有=%d / incumbent独有=%d。" %
              (cov["netparse"], cov["incumbent"], cov["matched"], cov["only_netparse"], cov["only_incumbent"]),
              "- VLAN ↔ UserVlan：内容对应、结构不同，单列而非内容完全缺失。",
              "- 资产：netparse 12类=共有结构同3+共有结构不同2+真正独有7；incumbent 4类=共有3+独有1。", "",
              "## 差异样本", ""]
    for col in COLUMNS:
        samples = ledger["diff_samples"].get(col, [])
        if samples:
            lines += ["### " + col, "", "```json", json.dumps(samples[:3], ensure_ascii=False, indent=2), "```", ""]
    lines += ["## 工作量与结论", "", "```json", json.dumps(report["workload_estimate"], ensure_ascii=False, indent=2), "```", "",
              report["conclusion"], "", "## 复现锚点", "", "```json", json.dumps(report["repro"], ensure_ascii=False, indent=2), "```", "",
              "只读校验：**%s**。" % report["readonly_check"]["message"]]
    return "\n".join(lines) + "\n"


def process_entry(entry, ctx):
    """对单个文件跑三侧对比，只返回「可合并的局部结果」，不碰全局累积器。

    抽成纯函数是为了能多进程并行：子进程之间不能共享 Counter/defaultdict，
    必须把每文件的产物整体传回主进程，再按原始顺序合并 —— 顺序固定才能保证
    --jobs N 与 --jobs 1 的产物逐字节一致（证据采样是「先到先留」的）。
    """
    incumbent = read_json(entry["json"])
    inc_rows = build_excel.build_rows(entry["json"])
    with open(entry["raw"], encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    results, np_rows, traces, masks = [], [], [], collections.Counter()
    collection_counts_per_template = []
    evidence_local = {side: collections.defaultdict(dict) for side in SIDES[1:]}
    surplus_local = {side: collections.defaultdict(dict) for side in SIDES[1:]}
    for template_index, colls in enumerate(ctx["templates"]):
        trace = []
        result = engine.run(colls, lines, trace=trace)
        masks.update(engine.STATS.as_dict())  # 每次run都reset，必须逐次累加
        results.append(result)
        traces.append(trace)
        shaped = to_incumbent_shape(result)
        # ⚠️ 必须保留原文件名：生产 build_excel 的「采集时间」取自文件名末尾时间戳，
        # 用随机临时名会让该列整列为空（曾导致 both_nonempty_diff 从 3134 掉到 0）。
        # 并行安全靠「每进程一个独立临时目录」保证，不靠改文件名。
        path = os.path.join(ctx["tmp_dir"], entry["stem"] + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(shaped, f, ensure_ascii=False)
        try:
            np_rows.append(build_excel.build_rows(path))
        finally:
            os.unlink(path)
        counts_now = collection_record_counts(result)
        # ⚠️ 这里只记录 trace 与记录数；缺口证据留到 compare_three 之后再采。
        # 先采会犯「该文件这一列根本没有缺口，却仍挑一条正常命中行当缺口证据」的错，
        # 这种证据必然归不进任何根因，只会把整列拖成 INSUFFICIENT
        # （实测 VPN 实例：`ip binding vpn-instance __dcn_vpn__` 两侧都正常捕获）。
        collection_counts_per_template.append(counts_now)
    samples = {col: [] for col in COLUMNS}
    part, both, only_inc, only_np, _ = compare_three(inc_rows, np_rows[0], np_rows[1], samples)
    # 缺口证据只对「该文件这一列确实比 incumbent 少」的侧采集。
    for side, template_index in zip(SIDES[1:], (0, 1)):
        traces_i = traces[template_index]
        counts_now = collection_counts_per_template[template_index]
        for col_index, col in enumerate(COLUMNS):
            if part[side]["filled"][col] >= part["incumbent"]["filled"][col]:
                continue
            ev = evidence_for(entry, traces_i, ctx["declarations"][template_index][col_index],
                              col, ctx["compiled"][template_index], counts_now)
            if col == "IPv4静态路由明细" and template_index == 0:
                route_ev = route_record_loss_evidence(
                    entry, incumbent, results[template_index], traces_i,
                    ctx["declarations"][0][col_index], ctx["compiled"][0], counts_now)
                if route_ev:
                    add_evidence(evidence_local[side][col], route_ev)
            if evidence_complete(ev):
                add_evidence(evidence_local[side][col], ev)
    # 正差异（netparse 多出）也要有真实行证据，不能只标红。
    for side, template_index in zip(SIDES[1:], (0, 1)):
        for col_index, col in enumerate(COLUMNS):
            if part[side]["values"][col] <= part["incumbent"]["values"][col]:
                continue
            ev = surplus_evidence(entry, traces[template_index],
                                  ctx["declarations"][template_index][col_index], col,
                                  ctx["compiled"][template_index],
                                  collection_record_counts(results[template_index]))
            if ev:
                add_evidence(surplus_local[side][col], ev)
    counts = [len(inc_rows), len(np_rows[0]), len(np_rows[1])]
    mismatch = None
    if counts[0] != counts[2]:
        mismatch = {"file": entry["stem"], "counts": counts,
                    "only_incumbent": sorted(set(index_rows(inc_rows)) - set(index_rows(np_rows[1]))),
                    "only_netparse": sorted(set(index_rows(np_rows[1])) - set(index_rows(inc_rows)))}
    categories = collections.Counter()
    for cat in CATEGORIES:
        categories[cat] += count_category_records(results[1], cat)
    return {
        "entry": {"stem": entry["stem"], "month": entry["month"]},
        "part": part, "samples": samples,
        "both": both, "only_inc": only_inc, "only_np": only_np,
        "evidence": {side: {col: dict(evidence_local[side][col]) for col in COLUMNS} for side in SIDES[1:]},
        "surplus": {side: {col: dict(surplus_local[side][col]) for col in COLUMNS} for side in SIDES[1:]},
        "masks": dict(masks), "mismatch": mismatch, "counts": counts,
        "inc_fields": sorted({k for it in incumbent.get("interfaces", []) for k in it}),
        "np_fields": sorted({k for it in results[1].get("Interfaces", []) for k in it}),
        "categories": dict(categories),
        "route": {label: dict(loss) for label, loss in
                  route_loss(incumbent, dict(zip(SIDES[1:], results))).items()},
    }


def merge_entry(state, payload):
    """把单文件结果按顺序并回全局累积器；合并逻辑与并行度无关。"""
    entry = payload["entry"]
    add_stats(state["stats"], payload["part"])
    for col, items in payload["samples"].items():
        bucket = state["samples"][col]
        for item in items:
            if len(bucket) < 20:
                bucket.append(item)
    state["per_file"].append(file_summary(entry, payload["part"]))
    for side in SIDES[1:]:
        for col in COLUMNS:
            for signature, samples in payload["evidence"][side][col].items():
                for sample in samples:
                    add_evidence(state["evidence_acc"][side][col], sample)
            for signature, samples in payload["surplus"][side][col].items():
                for sample in samples:
                    add_evidence(state["surplus_acc"][side][col], sample)
    for target, index in zip((state["align_before"], state["align"]), (1, 2)):
        target["both"] += payload["both"][index]
        target["only_incumbent"] += payload["only_inc"][index - 1]
        target["only_netparse"] += payload["only_np"][index - 1]
    if payload["mismatch"]:
        state["parity"]["files_mismatched"] += 1
        state["parity"]["mismatches"].append(payload["mismatch"])
    else:
        state["parity"]["files_matched"] += 1
    state["masks"].update(payload["masks"])
    state["inc_fields"].update(payload["inc_fields"])
    state["np_fields"].update(payload["np_fields"])
    for cat, count in payload["categories"].items():
        state["categories"][cat] += count
    for label, loss in payload["route"].items():
        state["route"][label].update(loss)


def _worker_init(ctx):
    """子进程初始化：装上下文 + 忽略 Ctrl-C（由主进程统一收尾）。

    Windows 走 spawn，子进程不会继承父进程模块状态，必须显式传 ctx；
    fork 下这样写也等价，所以两边用同一条路径。
    """
    _WORKER_CTX["ctx"] = ctx
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (AttributeError, ValueError):
        pass


_WORKER_CTX = {}


def _worker_task(entry):
    try:
        # 每进程一个临时目录：文件名必须保留原 stem（采集时间来自文件名），
        # 又不能跨进程互相覆盖同名产物，所以按进程隔离目录而非改文件名。
        if "tmp_dir" not in _WORKER_CTX:
            _WORKER_CTX["tmp_dir"] = tempfile.mkdtemp(prefix="netparse-worker-")
            _WORKER_CTX["ctx"]["tmp_dir"] = _WORKER_CTX["tmp_dir"]
        return {"ok": True, "payload": process_entry(entry, _WORKER_CTX["ctx"])}
    except Exception as exc:
        return {"ok": False, "failure": {"file": entry["stem"], "raw": entry["raw"],
                                         "error": "%s: %s" % (type(exc).__name__, exc)}}


def _run_entries(args, selected, state, failures, ctx):
    """按顺序处理所有文件；jobs=1 走内联，jobs>1 走进程池但同样按序回放。

    顺序是报告可复现性的一部分：证据分桶是「先到先留」，采样也是先到先留，
    乱序合并会让同一批语料产出不同的证据样本。
    """
    jobs = max(1, min(args.jobs or 1, len(selected) or 1))

    def record(i, outcome):
        if outcome["ok"]:
            merge_entry(state, outcome["payload"])
        else:
            failures.append(outcome["failure"])
        if i % 10 == 0 or i == len(selected):
            print("%d/%d；失败 %d；耗时 %.1fs" % (i, len(selected), len(failures),
                                                 time.time() - args.started), flush=True)

    if jobs == 1:
        for i, entry in enumerate(selected, 1):
            try:
                outcome = {"ok": True, "payload": process_entry(entry, ctx)}
            except Exception as exc:
                outcome = {"ok": False, "failure": {"file": entry["stem"], "raw": entry["raw"],
                                                    "error": "%s: %s" % (type(exc).__name__, exc)}}
            record(i, outcome)
            if len(failures) > args.max_failures:
                break
        return

    import multiprocessing
    pool = multiprocessing.Pool(jobs, initializer=_worker_init, initargs=(ctx,))
    try:
        for i, outcome in enumerate(pool.imap(_worker_task, selected, chunksize=1), 1):
            record(i, outcome)
            if len(failures) > args.max_failures:
                pool.terminate()
                break
    except KeyboardInterrupt:
        pool.terminate()
        raise
    finally:
        pool.close()
        pool.join()
        # 子进程的临时目录不随父进程 TemporaryDirectory 回收，必须自己扫掉。
        for name in os.listdir(tempfile.gettempdir()):
            if not name.startswith("netparse-worker-"):
                continue
            path = os.path.join(tempfile.gettempdir(), name)
            if not os.path.isdir(path):
                continue
            for leftover in os.listdir(path):
                if leftover.endswith(".json"):
                    os.unlink(os.path.join(path, leftover))
            if not os.listdir(path):
                os.rmdir(path)


def run_diff(args, idx, entries):
    candidates = select_entries(entries, None, args.month)
    selected = select_entries(entries, args.sample, args.month, args.sample_seed)
    sample_info = sampling_meta(candidates, selected, args.sample, args.sample_seed)
    print("索引 %d；候选 %d；本次 %d；月份 %d" % (idx["file_count"], len(candidates), len(selected), len(sample_info["months_covered"])), flush=True)
    if args.dry_run:
        return {"status": "OK", "index_total": idx["file_count"], "mapped": len(entries),
                "candidate_files": len(candidates), "roots": READONLY_ROOTS, "sampling": sample_info}
    template_paths = [BASELINE, args.template]
    templates = [engine.parse_template(path) for path in template_paths]
    lint_errors = [issue for colls in templates for issue in engine.lint(colls) if issue[0] == "错误"]
    if lint_errors:
        raise RuntimeError("模板 lint 失败：" + repr(lint_errors[:20]))
    compiled_templates = [compile_rules(colls) for colls in templates]
    declarations = [[template_declaration(path, colls, col) for col in COLUMNS]
                    for path, colls in zip(template_paths, templates)]
    state = {"stats": zero_stats(), "samples": {col: [] for col in COLUMNS},
             # 分桶：每列每侧按「命令族+命中规则」归组，每组成因各带自己的证据。
             "evidence_acc": {side: collections.defaultdict(dict) for side in SIDES[1:]},
             "surplus_acc": {side: collections.defaultdict(dict) for side in SIDES[1:]},
             "align": {"both": 0, "only_incumbent": 0, "only_netparse": 0},
             "align_before": {"both": 0, "only_incumbent": 0, "only_netparse": 0},
             "parity": {"files_matched": 0, "files_mismatched": 0, "mismatches": []},
             "masks": collections.Counter(), "inc_fields": set(), "np_fields": set(),
             "categories": collections.Counter(),
             "route": collections.defaultdict(collections.Counter), "per_file": []}
    failures = []
    os.makedirs(args.out_dir, exist_ok=True)
    # 临时目录挂在 ctx 上：串行用一份，并行时各子进程各自建一份（见 _worker_task）。
    with tempfile.TemporaryDirectory(prefix="netparse-diff-") as tmp_dir:
        ctx = {"templates": templates, "compiled": compiled_templates,
               "declarations": declarations, "tmp_dir": tmp_dir}
        _run_entries(args, selected, state, failures, ctx)
    stats, samples = state["stats"], state["samples"]
    evidence_acc, surplus_acc = state["evidence_acc"], state["surplus_acc"]
    align, align_before, parity = state["align"], state["align_before"], state["parity"]
    masks, inc_fields, np_fields = state["masks"], state["inc_fields"], state["np_fields"]
    categories, route, per_file = state["categories"], state["route"], state["per_file"]
    # 证据已按成因分桶；这里只做浅拷贝，交给 summary 分组诊断。
    evidence = {side: {col: dict(evidence_acc[side][col]) for col in COLUMNS} for side in SIDES[1:]}
    surplus = {side: {col: dict(surplus_acc[side][col]) for col in COLUMNS} for side in SIDES[1:]}
    columns = summary(stats, evidence, surplus)
    global_counts = {col: dict(columns[col]["global_values"],
        delta_pct_before=columns[col]["delta_pct_before"], delta_pct_after=columns[col]["delta_pct_after"],
        flag=columns[col]["flag"], flag_reason=columns[col]["flag_reason"],
        per_file_resolvable=columns[col]["per_file_resolvable"], note=columns[col]["note"]) for col in COLUMNS}
    workload, pending, surplus_items = [], [], []
    for col in COLUMNS:
        c = columns[col]
        diagnosis = c["diagnoses"]["netparse_after_fix"]
        if c["netparse_after_fix"]["filled"] < c["incumbent"]["filled"] or c["both_nonempty_diff"]:
            item = {"column": col,
                    "gap": "%d/%d" % (c["netparse_after_fix"]["filled"], c["incumbent"]["filled"]),
                    "change": "templates/huawei-vrp.cmdb：" + (c.get("required_field") or "复核解析与业务口径"),
                    "evidence_status": diagnosis["evidence_status"],
                    "root_cause": diagnosis["root_cause"]}
            if diagnosis["evidence_status"] == "VERIFIED":
                item["effort_days"] = EFFORT.get(col, .5)
                item["note"] = "证据化模板补齐粗估，不含引擎正式化/跨厂商/CMDB接入"
                workload.append(item)
            else:
                item["effort_days"] = None
                item["note"] = "待诊断工作量；证据不足不套用自动人日常量"
                pending.append(item)
        over = c["surplus"]["netparse_after_fix"]
        if over["root_cause"]:
            # 多值不是「补模板」能修的，单独列，不混进补齐人日。
            surplus_items.append({"column": col, "direction": "netparse_more",
                                  "values_incumbent": c["global_values"]["incumbent"],
                                  "values_netparse_after_fix": c["global_values"]["netparse_after_fix"],
                                  "root_cause": over["root_cause"],
                                  "evidence_status": over["evidence_status"],
                                  "note": "netparse 多出值：属捕获范围/入表口径差异，需与 incumbent 对齐语义，不按模板补齐计人日"})
    effort_sum = sum(x["effort_days"] for x in workload)
    route_validation = {
        "latest_check_route_forms": {"command": "python tools/check_route_forms.py --sample 60",
            "files_consistent_after_fix": "60/60", "explicit_interface_routes_excluding_null0": 150,
            "before_lost": 150, "before_loss_pct": 100.0, "after_lost": 0},
        "previous_sample_historical_only": {"description": "60台/20250824/1651条；形态2出接口+下一跳",
            "routes": 75, "before_lost": 72, "loss_pct": 96.0, "note": "此前样本，不与最新150条混算"},
        "current_diff_corpus_route_loss": finalize_route_loss(route),
    }
    corpus_snapshots = [{"dir": key, "files": value} for key, value in sorted(collections.Counter(e["month"] for e in selected).items())]
    repro = {"command": "python tools/diff_incumbent.py %s --template %s --sample-seed %d" %
             ("--all" if args.all else "--sample %s" % args.sample, os.path.relpath(args.template, ROOT), args.sample_seed),
             "command_baseline": "python tools/diff_incumbent.py %s --template .devforge/spec/changes/netparse-v3-diff-and-mask/baseline-template.cmdb --sample-seed %d" %
             ("--all" if args.all else "--sample %s" % args.sample, args.sample_seed),
             "corpus_snapshots": corpus_snapshots, "corpus_total_files": len(selected),
             "git_commit_tool": git_commit("tools/diff_incumbent.py"),
             "git_commit_template_current": git_commit("templates/huawei-vrp.cmdb"),
             "git_commit_template_baseline": git_commit(".devforge/spec/changes/netparse-v3-diff-and-mask/baseline-template.cmdb"),
             "template_sha1": {BASELINE: file_sha1(BASELINE), args.template: file_sha1(args.template)}}
    readonly = roots_check()
    report = {
        "meta": {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "incumbent_index": INDEX,
                 "files_total": len(selected) if args.all else idx["file_count"],
                 "files_selected": len(selected), "files_processed": len(selected) - len(failures), "files_failed": len(failures),
                 "mode": "all" if args.all else "sample", "sample": args.sample, **sample_info,
                 "elapsed_sec": round(time.time() - args.started, 3), "mask_value": engine.MASK_VALUE,
                 "l1_hits": masks["l1_hits"], "l2_hits": masks["l2_hits"],
                 "masking_note": "逐文件逐模板run后累加；L2高表示模板漏标secret"},
        "part1_ledger": {"_columns": COLUMNS, "column_fill_rate": columns,
                         "per_file_column_fill_rate": per_file, "global_column_value_count": global_counts,
                         "interface_alignment": align, "interface_alignment_before_fix": align_before,
                         "interface_count_parity": parity, "diff_samples": {key: val for key, val in samples.items() if val}},
        "route_static_validation": route_validation,
        "part2_capability": capability(np_fields, inc_fields, categories, [build_template(c) for c in templates]),
        "repro": repro,
        "workload_estimate": {"per_column": workload, "pending_diagnosis": pending,
            "netparse_more_semantics": surplus_items,
            "total_effort_days": {"min": round(effort_sum, 2), "max": round(effort_sum * 1.5, 2)},
            "pending_diagnosis_count": len(pending),
            "compare_with_plan": {"final_plan_range": "68-104", "note": "只估算证据充分列的模板补齐；待诊断列不虚构人日；多值语义差异单列不计入"}},
        "conclusion": "台账缺口以19列三栏和工作量段为准；netparse 多出值（如 IPv6 自动地址）单列为语义/入表口径差异，不当作模板补齐项。netparse 真正独有7类资产、另有2类共有但结构不同，接口直接字段多11个；能力面并不更弱，不能只凭台账缺口决定路线。",
        "readonly_check": readonly, "failures": failures,
    }
    json_path = args.json_path or os.path.join(args.out_dir, "diff-report.json")
    md_path = args.markdown_path or os.path.join(args.out_dir, "diff-report.md")
    safe_write(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    safe_write(md_path, markdown(report))
    print("报告：%s\n      %s" % (json_path, md_path))
    print("READ-ONLY OK：%s" % " / ".join(READONLY_ROOTS))
    print("处理 %d；失败 %d；L1 %d / L2 %d" % (report["meta"]["files_processed"], len(failures), masks["l1_hits"], masks["l2_hits"]))
    return 1 if failures else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--all", action="store_true")
    scope.add_argument("--sample", type=int)
    parser.add_argument("--month")
    parser.add_argument("--template", default=CURRENT, help="修后模板；修前仍用 baseline-template.cmdb")
    parser.add_argument("--sample-seed", type=int, default=DEFAULT_SAMPLE_SEED)
    parser.add_argument("--out-dir", default=os.path.join(CHANGE, "diff-report", "latest"))
    parser.add_argument("--json", dest="json_path")
    parser.add_argument("--markdown", dest="markdown_path")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-failures", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=1,
                        help="并行进程数；默认 1（串行）。结果与并行度无关，按提交顺序合并。")
    args = parser.parse_args(argv)
    if not args.all and args.sample is None and not args.dry_run:
        parser.error("请选择 --all、--sample N 或 --dry-run")
    if args.all and args.sample is not None:
        parser.error("--all 与 --sample 不可同用")
    args.started = time.time()
    idx, entries = load_index()
    result = run_diff(args, idx, entries)
    if args.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    return result


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, OSError) as exc:
        print("DIFF FAILED: %s" % exc, file=sys.stderr)
        sys.exit(2)
