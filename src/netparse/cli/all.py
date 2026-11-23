"""netparse -m all <入口模板>：全量自测（递归子模板的 [__main__] 段）。"""
import os
import sys

from ..engine import parse_template, TemplateFileError


def main(args):
    entry = os.path.abspath(args.template)
    if not os.path.isfile(entry):
        print("[FAIL] 入口模板不存在: %s" % entry)
        return 2
    tpl_dir = os.path.dirname(entry)
    nps = sorted(fn for fn in os.listdir(tpl_dir) if fn.endswith((".np", ".cmdb")))
    passed, failed, results = 0, 0, []
    for fn in nps:
        path = os.path.join(tpl_dir, fn)
        try:
            parse_template(path)   # [__main__] 段在加载时执行；异常 = FAIL
            passed += 1
            results.append("[PASS] %s" % fn)
        except TemplateFileError as e:
            failed += 1
            results.append("[FAIL] %s: %s" % (fn, str(e).split(": ", 1)[-1][:120]))
        except Exception as e:
            failed += 1
            results.append("[FAIL] %s: %s: %s" % (fn, type(e).__name__, e))
    for line in results:
        print(line)
    print("---")
    print("全量自测: %d PASS / %d FAIL（共 %d）" % (passed, failed, passed + failed))
    return 1 if failed else 0
