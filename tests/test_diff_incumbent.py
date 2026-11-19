"""diff 工具的本地小样本验收；不读取或修改生产配置文件。"""

import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import diff_incumbent as d  # noqa: E402

FAILURES = []
PASSED = 0


def check(name, condition):
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILURES.append(name)


def row(name, device="D1", values=None):
    out = [device, "10.0.0.1", "20250101000000", name] + [""] * 15
    for index, value in (values or {}).items():
        out[index] = value
    return out


def verified(command_hit=0, target_declared=False, matched_rules=None):
    """构造具备三类证据的最小结构；规则复核结果按需给出。

    matched_rules 为空 = 没有候选规则复核证据（此时 rules_tried 也必须为空，
    否则会假装做过规则复核）。
    """
    matches = matched_rules if matched_rules is not None else (
        [{"collection": "Interfaces", "rule_index": 1, "pattern": "qos car", "target": None}]
        if command_hit else [])
    tried = [{"collection": "Interfaces", "rule_index": 1, "pattern": "qos car",
              "matched": bool(command_hit)}] if matches else []
    return {
        "input": {"file": "sample", "line": 12, "text": "qos car inbound"},
        "template": {"path": "template.cmdb", "collection": "Interfaces",
                     "target_declared": target_declared,
                     "relevant_collections": ["Interfaces"], "rules": []},
        "trace": {"command_family": "qos car", "total_lines": 1,
                  "hit_lines": command_hit, "hit": bool(command_hit),
                  "rule_matches": matches, "rules_tried": tried,
                  "collection_record_counts": {"Interfaces": 1 if command_hit else 0}},
    }


def main():
    check("19 列来自生产 build_rows 定义", len(d.COLUMNS) == 19)
    trace_stats = d.compare_three([row("A")], [row("A")], [row("A")], {c: [] for c in d.COLUMNS})[0]
    check("零缺口不产生根因", d.summary(trace_stats, {})["接口入流限速(M)"]["diagnoses"]["netparse_after_fix"]["evidence_status"] == "NOT_APPLICABLE")
    inc = [row("Eth-Trunk1", values={7: "10.1.1.1/30,10.1.1.2/30", 9: "0.0.0.0/0"}),
           row("Only-Inc")]
    before = [row("Only-NP"), row("eth-trunk1", values={7: "10.1.1.1/30"})]
    after = [row("eth-trunk1", values={7: "10.1.1.1/30,10.1.1.2/30", 9: "0.0.0.0/0"}), row("Only-NP")]
    samples = {c: [] for c in d.COLUMNS}
    stats, both, only_inc, only_np, _ = d.compare_three(inc, before, after, samples)
    check("按接口名大小写不敏感 join", both == [2, 1, 1])
    check("接口缺失计数", only_inc == [1, 1] and only_np == [1, 1])
    check("修前路由字段为空", stats["netparse_before_fix"]["filled"]["IPv4静态路由明细"] == 0)
    check("修后路由字段恢复", stats["netparse_after_fix"]["filled"]["IPv4静态路由明细"] == 1)
    check("全局值条数按非空单元格而非逗号拆分", stats["incumbent"]["values"]["端口IPv4"] == 1)
    # 加入真实字段缺口；仅接口join缺口必须单列，不能伪装成19列全空。
    stats["incumbent"]["filled"]["接口入流限速(M)"] = 1
    stats["incumbent"]["values"]["接口入流限速(M)"] = 1
    check("差异样本三栏", samples["IPv4静态路由明细"][0]["netparse_after_fix"] == "0.0.0.0/0")
    try:
        d.index_rows([row("A"), row("a")])
        FAILURES.append("重名接口未拒绝")
    except RuntimeError:
        check("重名接口显式拒绝", True)

    columns = d.summary(stats, {})
    check("19 列全局三栏完整", len(columns) == 19 and all(
        set(columns[c][s]) >= {"filled", "total", "rate"}
        for c in d.COLUMNS for s in d.SIDES))
    check("无证据不得自动判根因", columns["接口入流限速(M)"]["diagnoses"]["netparse_after_fix"]["root_cause"] == ["待人工判定"])
    check("无证据状态 INSUFFICIENT", columns["接口入流限速(M)"]["diagnoses"]["netparse_after_fix"]["evidence_status"] == "INSUFFICIENT")
    per_file = d.file_summary({"stem": "s", "month": "m"}, stats)
    check("逐文件 19 列三栏完整", len(per_file["columns"]) == 19 and all(
        set(per_file["columns"][c]) >= set(d.SIDES) for c in d.COLUMNS))

    ev = verified(command_hit=0, target_declared=False)
    diagnosis = d.classify_gap(ev)
    check("真实0命中+无声明才可判模板未覆盖", diagnosis["root_cause"] == ["模板未覆盖该命令"])
    check("自动根因证据状态", diagnosis["evidence_status"] == "VERIFIED")
    ev = verified(command_hit=1, target_declared=False)
    check("真实命中但缺目标字段归字段丢失", d.classify_gap(ev)["root_cause"] == ["字段丢失"])

    # 静态路由修前真实形态：行命中 start 但出接口未拆出 → 记录整条丢失，不是模板未覆盖。
    loss = verified(command_hit=1, target_declared=True)
    loss["record_loss"] = {"incumbent": 3, "netparse_missing": 3, "examples": []}
    check("有候选规则复核才可判整条记录丢失", d.classify_gap(loss)["root_cause"] == ["整条记录丢失"])
    without_rules = verified(command_hit=1, target_declared=True, matched_rules=[])
    without_rules["record_loss"] = {"incumbent": 3, "netparse_missing": 3}
    check("无候选规则复核不得判整条记录丢失",
          d.classify_gap(without_rules)["root_cause"] == ["待人工判定"])
    check("缺规则复核证据状态 INSUFFICIENT",
          d.classify_gap(without_rules)["evidence_status"] == "INSUFFICIENT")
    # 命中但目标字段已声明 → 不是所列任一自动根因，必须留给人工。
    declared = verified(command_hit=1, target_declared=True)
    check("目标字段已声明且无记录丢失 → 待人工判定",
          d.classify_gap(declared)["root_cause"] == ["待人工判定"])

    join = verified(command_hit=1, target_declared=True)
    join["join"] = {"source_collection": "QosProfiles", "target_collection": "Interfaces",
                    "key": "Name", "captured_original_fields": True,
                    "source_value": "p1", "target_value": "p1", "before": 1, "after": 0}
    check("原始字段齐全才单列 join缺失", d.classify_gap(join)["root_cause"] == ["join缺失"])
    del join["join"]["captured_original_fields"]
    check("未证明原始字段捕获不得归 join缺失", "join缺失" not in d.classify_gap(join)["root_cause"])

    # 正差异必须单独诊断，不能被标红后遗忘。值条数按非空单元格计，
    # 因此用「多一列有值」构造 netparse 多出的真实形态（如 IPv6 自动地址入表）。
    over_stats = d.compare_three([row("A", values={7: "10.1.1.1/30"})],
                                 [row("a", values={7: "10.1.1.1/30"})],
                                 [row("a", values={7: "10.1.1.1/30", 8: "2001:db8::1/64"})],
                                 {c: [] for c in d.COLUMNS})[0]
    over_cols = d.summary(over_stats, {})
    check("netparse 多出值被识别为正差异方向",
          over_cols["端口IPv6"]["global_values"]["netparse_after_fix"] == 1
          and over_cols["端口IPv6"]["global_values"]["incumbent"] == 0)
    check("多出值无证据时为待人工判定",
          over_cols["端口IPv6"]["surplus"]["netparse_after_fix"]["root_cause"] == ["待人工判定"])
    surplus_ev = verified(command_hit=1, target_declared=True)
    over_cols2 = d.summary(over_stats, {}, {"netparse_after_fix": {"端口IPv6": surplus_ev}})
    check("有真实行证据的多出值判语义不同",
          over_cols2["端口IPv6"]["surplus"]["netparse_after_fix"]["root_cause"] == ["语义不同"])
    check("缺口根因与多值根因分栏",
          over_cols2["端口IPv6"]["diagnoses"]["netparse_after_fix"]["evidence_status"] == "NOT_APPLICABLE")
    check("正差异标红", over_cols2["端口IPv6"]["flag"] == "RED")

    # PVLAN 外层证据必须取含 qinq 的真实行，不能拿纯 user-vlan 行充数。
    pvlan_rx = d.COLUMN_PATTERNS["PVLAN"]
    check("PVLAN 证据可取 qinq 同行形态",
          bool(d.re.match(pvlan_rx, " user-vlan 1000 1999 qinq 2000")))
    check("PVLAN 证据不得取纯 user-vlan 行",
          not d.re.match(pvlan_rx, " user-vlan 2000"))

    # 第10列缺口证据不得取「两侧都不入表」的形态，否则会把正常解析误判成记录丢失。
    route_ex = d.EXCLUDE_FROM_EVIDENCE["IPv4静态路由明细"]
    for line, excluded in (
            ("ip route-static 120.192.203.8 255.255.255.252 172.16.90.250 preference 100", True),
            ("ip route-static vpn-instance VPN_A 10.114.170.0 255.255.255.0 10.114.219.222", True),
            ("ip route-static 111.20.48.112 255.255.255.240 NULL0 preference 100", True),
            ("ip route-static 0.0.0.0 0.0.0.0 111.1.1.1", True),
            ("ip route-static 112.46.139.128 255.255.255.128 Eth-Trunk185.100 192.168.100.2", False),
            ("ip route-static vpn-instance VPN_A 1.1.1.0 255.255.255.0 Eth-Trunk1", False),
            ("ip route-static vpn-instance VPN_A 1.1.1.0 255.255.255.0 Eth-Trunk1 10.0.0.1", False)):
        check("路由证据排除形态：" + line[:52], bool(route_ex.search(line)) is excluded)

    # 百分比是比值，不能跨文件累加：9 个文件各 100% 曾累加成 900%。
    acc = {}
    for _ in range(9):
        for label, loss in d.route_loss({"route-static": {"IPV4": [
                {"dest": "1.1.1.0", "mask": "255.255.255.0", "interface": "Eth-Trunk1",
                 "nexthop": "10.0.0.1"}]}}, {"netparse_before_fix": {"RouteStatics": []}}).items():
            acc.setdefault(label, d.collections.Counter()).update(loss)
    finalized = d.finalize_route_loss(acc)
    check("逐文件只累加计数不累加百分比",
          finalized["netparse_before_fix"]["explicit_interface_routes"] == 9
          and finalized["netparse_before_fix"]["lost"] == 9)
    check("跨文件百分比按合计数重算", finalized["netparse_before_fix"]["loss_pct"] == 100.0)
    empty = d.finalize_route_loss({"x": d.collections.Counter(
        {"explicit_interface_routes": 0, "captured": 0, "lost": 0})})
    check("无可评估路由不产出误导百分比",
          empty["x"]["loss_pct"] == 0 and "无显式出接口路由" in empty["x"]["note"])

    # 并行与串行必须产出同一份结果：证据分桶与采样都是「先到先留」，
    # 乱序合并会让同一批语料冒出不同证据样本。
    probe = d.process_entry.__doc__ or ""
    check("单文件处理是可独立合并的纯函数", "可合并的局部结果" in probe)
    payload = {"entry": {"stem": "s", "month": "m"},
               "part": d.zero_stats(), "samples": {c: [] for c in d.COLUMNS},
               "both": [0, 0, 0], "only_inc": [0, 0], "only_np": [0, 0],
               "evidence": {s: {c: {} for c in d.COLUMNS} for s in d.SIDES[1:]},
               "surplus": {s: {c: {} for c in d.COLUMNS} for s in d.SIDES[1:]},
               "masks": {}, "mismatch": None, "counts": [0, 0, 0],
               "inc_fields": [], "np_fields": [], "categories": {}, "route": {}}
    state = {"stats": d.zero_stats(), "samples": {c: [] for c in d.COLUMNS},
             "evidence_acc": {s: d.collections.defaultdict(dict) for s in d.SIDES[1:]},
             "surplus_acc": {s: d.collections.defaultdict(dict) for s in d.SIDES[1:]},
             "align": {"both": 0, "only_incumbent": 0, "only_netparse": 0},
             "align_before": {"both": 0, "only_incumbent": 0, "only_netparse": 0},
             "parity": {"files_matched": 0, "files_mismatched": 0, "mismatches": []},
             "masks": d.collections.Counter(), "inc_fields": set(), "np_fields": set(),
             "categories": d.collections.Counter(),
             "route": d.collections.defaultdict(d.collections.Counter), "per_file": []}
    d.merge_entry(state, payload)
    check("合并后逐文件数计入", len(state["per_file"]) == 1)
    check("合并后接口计数归零不误增", state["parity"]["files_matched"] == 1)
    # 临时产物必须保留原文件名：采集时间取自文件名末尾时间戳。
    src = open(os.path.join(ROOT, "tools", "diff_incumbent.py"), encoding="utf-8").read()
    check("临时产物沿用原文件名",
          'entry["stem"] + ".json"' in src and "netparse-entry-" not in src)

    # Markdown 表格每行必须列数一致：标准 Markdown 不支持 colspan，
    # 在表格里插说明行会让整张表在渲染时错位。
    md = d.markdown({"meta": {"files_processed": 1, "mode": "sample", "sample_seed": 1},
                     "part1_ledger": {"column_fill_rate": columns, "diff_samples": {},
                                      "interface_alignment": {"both": 0, "only_incumbent": 0,
                                                              "only_netparse": 0}},
                     "route_static_validation": {}, "part2_capability": {"interface_level_fields": {"coverage": {
                         "netparse": 15, "incumbent": 10, "matched": 4, "only_netparse": 11, "only_incumbent": 6}}},
                     "workload_estimate": {}, "conclusion": "", "repro": {},
                     "readonly_check": {"message": "READ-ONLY OK"}})
    table_rows = [line for line in md.split("\n") if line.startswith("|")]
    check("Markdown 表格列数一致", all(row.count("|") == 11 for row in table_rows))
    check("Markdown 不用 colspan", "colspan" not in md)
    check("列备注移到表外脚注", "**列备注：**" in md)

    entries = [{"month": "m%d" % (i % 26), "stem": str(i)} for i in range(260)]
    selected = d.select_entries(entries, 20, sample_seed=7)
    check("抽样 20 覆盖 20 月", len({e["month"] for e in selected}) == 20)
    check("抽样不放回", len({e["stem"] for e in selected}) == 20)
    check("抽样固定 seed 可复现", selected == d.select_entries(entries, 20, sample_seed=7))
    selected30 = d.select_entries(entries, 30, sample_seed=7)
    check("N>月份数仍按月加深", len(selected30) == 30 and len({e["month"] for e in selected30}) == 26)
    short = d.select_entries(entries, 300, sample_seed=7)
    check("候选不足不重复凑数", len(short) == 260 and len({e["stem"] for e in short}) == 260)
    check("抽样元数据期望公式", d.sampling_meta(entries, selected30, 30, 7)["expected_distinct_months"] == 26)

    fake = {"Interfaces": [{"Name": "x", "EthTrunkMember": [{"Trunk": "1"}, {"Trunk": "2"}]}],
            "Bgp": [{"AsNumber": 1, "BgpPeers": [{"Peer": "1"}]}]}
    check("嵌套资产类别按真实记录数", d.count_category_records(fake, "EthTrunkMember") == 2)
    check("嵌套 BGP peer 被计数", d.count_category_records(fake, "BgpPeers") == 1)
    cap = d.capability(set(), set(), {c: 0 for c in d.CATEGORIES}, [])
    cov = cap["interface_level_fields"]["coverage"]
    check("接口直接子键口径固定15/10/4/11/6", cov == {
        "incumbent": 10, "netparse": 15, "matched": 4,
        "only_netparse": 11, "only_incumbent": 6})
    check("VLAN对应关系单列", cap["interface_level_fields"]["content_correspondence_diff_shape"][0]["netparse"] == "UserVlan")
    check("资产三分类7项", len(cap["asset_categories"]["netparse_only_instances"]) == 7)
    check("IP/VPN不误算独有", "IpAddress" not in cap["asset_categories"]["_taxonomy"]["netparse_only"])

    os.makedirs(os.path.join(ROOT, "diff-report"), exist_ok=True)
    secret_path = os.path.join(ROOT, "diff-report", "test-secret.json")
    with open(secret_path, "w", encoding="utf-8") as f:
        f.write("old")
    try:
        d.safe_write(secret_path, json.dumps({"sample": "%^%#abc%^%#"}))
        FAILURES.append("密文扫描未中止")
    except RuntimeError:
        check("危险报告已删除", not os.path.exists(secret_path))
    check("长接口描述不截段误报", not d.scan_secret_values("TO-ZB-HongShuLeShiPingKeJiYouXianGongSi02-zhuanxian"))
    check("扫描与引擎规则一致", all(bool(d.scan_secret_values(value)) == d.is_secret_shape(value)
           for value in ("plain", "A1b2C3d4E5f6G7h8I9j0K1l2", "didianZCWanYaoBaoCaiYou35KVBian")))
    check("展示样本复用引擎遮蔽", d.report_value("A1b2C3d4E5f6G7h8I9j0K1l2") == d.engine.MASK_VALUE)
    check("展示样本不改普通值", d.report_value("Eth-Trunk1") == "Eth-Trunk1")
    check("完整高熵值仍需拦截", bool(d.scan_secret_values("A1b2C3d4E5f6G7h8I9j0K1l2")))
    # 证据里的真实命令行也必须脱敏后才进报告。
    check("证据命令样本脱敏", d.report_value("peer 1.1.1.1 password cipher %^%#abc%^%#") ==
          "peer 1.1.1.1 password cipher " + d.engine.MASK_VALUE)
    check("证据原始行保留供规则复核",
          d.trace_records([(1, "qos car cir 100 inbound", "Interfaces", ["__root__"])],
                          "接口入流限速(M)")[0]["raw_text"] == "qos car cir 100 inbound")
    benign_path = os.path.join(ROOT, "diff-report", "test-benign.json")
    d.safe_write(benign_path, json.dumps({"Password": "******"}))
    check("掩码可安全写盘", os.path.isfile(benign_path))
    os.unlink(benign_path)
    print("diff 单元验收：%d 项通过 / %d 项失败" % (PASSED, len(FAILURES)))
    if FAILURES:
        for failure in FAILURES:
            print("  -", failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
