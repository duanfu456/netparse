"""真机批量回归：模块化 main.np vs 单文件版输出一致性 + 守恒/覆盖率统计。

用法：python tools/batch_real.py <配置目录> [--limit N]
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from netparse import parse_template, run

ROOT = os.path.join(os.path.dirname(__file__), '..')

def collect(cfg_dir):
    files = []
    for root, _dirs, fs in os.walk(cfg_dir):
        for f in fs:
            if f.lower().endswith(('.txt', '.cfg', '.conf')):
                files.append(os.path.join(root, f))
    return sorted(files)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cfgdir')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    files = collect(args.cfgdir)
    if args.limit:
        files = files[:args.limit]
    print(f"样本：{len(files)} 份")

    tpl_mod = os.path.join(ROOT, 'templates', 'huawei', 'main.np')
    tpl_single = os.path.join(ROOT, 'templates', 'huawei-vrp.np')
    c_mod = parse_template(tpl_mod)
    c_single = parse_template(tpl_single)

    ok = fail = 0
    stats = {"interfaces": 0, "routes": 0, "vpn": 0, "static_users": 0,
             "qos": 0, "conserved_lines": 0}
    failures = []
    for i, f in enumerate(files, 1):
        try:
            with open(f, encoding='utf-8', errors='replace') as fh:
                lines = fh.readlines()
            r_mod = run(c_mod, lines)
            r_single = run(c_single, lines)
            a = json.dumps(r_mod, sort_keys=True, ensure_ascii=False)
            b = json.dumps(r_single, sort_keys=True, ensure_ascii=False)
            if a != b:
                fail += 1
                failures.append((f, "模块化与单文件输出不一致"))
                continue
            ok += 1
            stats["interfaces"] += len(r_mod.get('Interfaces', []))
            stats["routes"] += len(r_mod.get('RouteStatics', []))
            stats["vpn"] += len(r_mod.get('VpnInstances', []))
            stats["static_users"] += len(r_mod.get('StaticUsers', []))
            stats["qos"] += len(r_mod.get('QosProfiles', []))
            if i % 50 == 0:
                print(f"  进度 {i}/{len(files)}（累计一致 {ok}）")
        except Exception as e:
            fail += 1
            failures.append((f, "%s: %s" % (type(e).__name__, e)))

    print()
    print("═══ 真机批量回归结果 ═══")
    print(f"一致: {ok} / 不一致或失败: {fail}")
    print(f"累计解析: 接口 {stats['interfaces']} | 静态路由 {stats['routes']} | "
          f"VPN {stats['vpn']} | 静态用户 {stats['static_users']} | QoS {stats['qos']}")
    if failures:
        print("\n失败明细（前 10）:")
        for f, err in failures[:10]:
            print(" ", os.path.basename(f), "→", err)
    return 0 if not failures else 1

if __name__ == '__main__':
    sys.exit(main())
