# Tasks · netparse-v3-diff-and-mask

> 配套：`proposal.md` · `design.md` · `specs/secret-mask/spec.md` · `specs/incumbent-diff/spec.md`
> 纪律：**本清单是实现阶段的施工单**，实施者按编号顺序执行；每条任务的「验证」列给出**可执行的命令**或**可判定的检查点**，验证不通过不得勾选。
> 总量估算：**能力 A 2-3 人日 + 能力 B 3-4 人日 = 5-7 人日**（不含文档 0.5 人日）

**编号约定**：`0.x` 前置 · `1.x` 能力 A（脱敏）· `2.x` 能力 B（diff）· `3.x` 文档与结论
**硬约束提醒**：① `E:\配置文件\` 下的一切**只读**；② 实现代码只在 netparse 仓库内；③ 除脱敏外不改变解析语义。

---

## 0. 前置（0.5 人日）

- [x] **0.1 初始化 git 仓库，建立基线提交**
  - 改动：在 `netparse/` 目录执行 `git init`、写 `.gitignore`（至少忽略 `__pycache__/`、`*.pyc`、`diff-report/` 原始大文件）、`git add -A && git commit -m "baseline: netparse 原型（评估前状态）"`
  - **不改任何现有文件的内容**
  - 验证：`git log --oneline` 显示 1 个基线提交；`git status` 干净
  - ⚠️ 对应 design.md **Q2**：若 team-lead 否决建 git，则本任务取消，但**后续任务必须改为"每步手工备份 + 记录文件哈希"**，并在 `tasks.md` 追加哈希清单

- [x] **0.2 记录只读基线（供 2.7 的只读自检比对）**
  - 改动：新建 `tools/readonly_guard.py`（本仓库内），功能：对给定目录树计算「路径 → (size, mtime_ns, sha1)」清单，存到 `.devforge/spec/changes/netparse-v3-diff-and-mask/readonly-baseline.json`
  - 只读目录清单：① `E:\配置文件\BRAS&SR\原始配置\`（⚠️ **不是** `E:\配置文件\原始配置\` —— **那个路径不存在**，本条为 2026-11-19 实现期修正；真实路径下有 26 个月目录 / 10054 个文件 / 3.3GB）② `E:\配置文件\BRAS&SR\解析后\` ③ `E:\配置文件\配置解析\华为\`
  - ⚠️ ②③ 目录树大（8440 个 JSON + 解析后 1.2GB）→ **只记录 size + mtime_ns，不算 sha1**（性能）；① 只对 `原始配置` 算 sha1（用于强校验，可按文件数分片）
  - 验证：`python tools/readonly_guard.py --capture` 退出码 0，输出基线文件行数统计

- [x] **0.3 确认依赖可用性**
  - 改动：无
  - 验证：`python -c "import openpyxl; print(openpyxl.__version__)"` → 期望 `3.1.5`；`python -c "import textfsm"` → 期望 ImportError（**不需要 textfsm**，若已装也不影响）
  - ⚠️ 若 `openpyxl` 缺失：diff 工具必须给出明确依赖提示（spec: R-B2 场景 2），**不得**自行 `pip install`

---

## 1. 能力 A · 引擎层脱敏（2-3 人日）

### 1.1 引擎：字段类型 `secret`（0.3 人日）

- [x] **1.1.1 `Field` 支持 `type(secret)`**
  - 改动：`tools/probe_stack.py` 的 `Field.__init__`（第 91-102 行）—— `self.type` 取值允许 `"secret"`；新增方法 `Field.is_secret()` 返回 `self.type == "secret"`
  - 注意：`self.type` 默认 `"str"`（第 95 行 `attrs.get("type", "str")`）—— **默认值不变**
  - 验证：写一段 3 行的 Python 内联脚本，`from tools.probe_stack import Field` 构造 `Field("X", {"type":"secret"}, set())`，断言 `f.is_secret() is True` 且 `f.type == "secret"`

- [x] **1.1.2 `convert()` 对 secret 字段不做类型转换**
  - 改动：`tools/probe_stack.py` 的 `convert()`（第 325-348 行）—— 入口加 `if fld is not None and fld.is_secret(): return value`（原样返回字符串，避免 int/float 转换把掩码串改掉或回退原值）
  - 验证：单元级 —— `convert("******", Field("P", {"type":"secret"}, set()))` 返回 `"******"`；`convert("******", Field("P", {"type":"int"}, set()))` 返回 `"******"`（int 转换失败回退原值，行为不变）

- [x] **1.1.3 一个字段只能声明一种 type（`secret` 与其它类型互斥）**
  - 改动：`tools/probe_stack.py` 的 `parse_modifiers()` —— 遇到第二个 `type(...)` 时**保留第一个** type 并打冲突标记（`TYPE_CONFLICT_KEY`）；`Field` 增 `type_conflict` 属性；`apply_writes()` 两处分支遇到冲突字段**跳过不写**（该字段按缺字段处理）
  - ⚠️ **为什么跳过而不是报错**：spec 的 Requirement「secret 与其它类型互斥」要求"引擎不产出该字段（该记录按缺字段处理），而不是抛未捕获异常" —— 抛异常会让整个文件解析失败，代价过大
  - ⚠️ **值仍照常遮蔽**：即使字段因冲突被跳过，捕获到的值在 `_apply` 阶段**已经遮蔽**（D6 遮蔽点在捕获处）⇒ **不会泄露明文**
  - 验证：① `Password type(secret) type(int)` → lint 报「错误」；② 该字段**不出现在**输出记录里；③ 引擎**不抛异常**，其余字段正常产出；④ 输出全文无明文

### 1.2 引擎：遮蔽发生在写入时（0.5 人日）

- [x] **1.2.1 新增 `tools/masking.py`**
  - 改动：新建文件，实现
    - `MASK_DEFAULT = "******"`
    - `SECRET_SHAPE_PATTERNS`：密文形态正则列表。**必须包含**华为形态 `%^%#...%^%#`；高熵长串启发要求**同时**满足「长度 ≥ 24」且「字符集 ⊆ base64/十六进制」且「至少含 3 类字符」——**不得**包含纯数字、纯字母、常见单词
    - `is_secret_shape(text) -> bool`：L2 形态判定；**必须带结果缓存**（按 text 缓存，避免 8440 文件 × 百万捕获组的性能损失）
    - `mask_value(value, fld, mask=MASK_DEFAULT, heuristic=True) -> str`：L1（`fld.is_secret()`）→ L2（`is_secret_shape`）→ 遮蔽
  - 验证：内联脚本断言 —— `is_secret_shape("%^%#;}4v<YDuLCwX%^%#") is True`；`is_secret_shape("aes256_gcm") is False`；`is_secret_shape("TO-[SNANK-PC-CMNET-RT01-NE5000E]-Eth-Trunk6=6*10GE") is False`；`is_secret_shape("111.19.27.122") is False`；`is_secret_shape("car5000m") is False`

- [x] **1.2.2 在 `apply_writes` 中插入遮蔽**
  - 改动：`tools/probe_stack.py` 的 `apply_writes()`（第 370-397 行）—— 在 `convert(value, ...)` **之前**对每个值调用 `mask_value`
  - ⚠️ **两处都要改**：第 388 行（list 字段分支）与第 397 行（普通字段分支）
  - ⚠️ **不动 `last` 缓存的写入语义**：第 636 行 `last[(coll.name, path)] = v` 写入的是**规则捕获的原值** → **必须改成写入遮蔽后的值**，否则 `inherit=` 会把明文复制出去
  - 验证：构造一份最小配置 + 模板（`peer X password cipher %^%#abc%^%#`），跑引擎后断言：
    - `result["BgpPeers"][0]["Password"] == "******"`
    - `"%^%#" not in json.dumps(result, ensure_ascii=False)`

- [x] **1.2.3 引擎内 `last` 缓存不含明文**
  - 改动：同 1.2.2，`last` 写入遮蔽后的值
  - ⚠️ **排查动作要按"所有写入点"做，不能只改我列的那一处行号**（行号会随改动漂移）：
    ```bash
    grep -n "last\[" tools/probe_stack.py
    ```
    逐个确认**每一处** `last[...] = ...` 写入的都是**遮蔽后**的值；`last` 还有一处读取点（`inherit` 补齐，第 644-646 行）也要确认读到的是遮蔽后的值
  - 验证：构造带 `inherit=` 的最小用例 —— 一个 secret 字段 + 一个 `inherit` 指向它的字段；断言继承到的值是 `"******"` 而非明文
  - ⚠️ **为什么这条最危险**：`apply_writes` 改对了、表面上完全看不到问题，但 `inherit` 会把明文复制到一个**普通字段**里 → 明文从"被遮蔽的字段"流到"没被遮蔽的字段"，**肉眼扫描输出看不出来**（因为原字段确实是掩码），只能靠这个 grep 逐点确认

### 1.3 引擎：CLI 开关（0.2 人日）

- [x] **1.3.1 `--mask-value` 与 `--no-mask-heuristic`**
  - 改动：`tools/probe_stack.py` 的 `main()`（第 753-795 行）—— 解析两个新开关；`--mask-value` 覆盖 `MASK_DEFAULT`；`--no-mask-heuristic` 关闭 L2
  - ⚠️ `run()` 的签名**不变**（`run(colls, lines, trace=None)`）→ 遮蔽参数通过模块级/引擎对象传入。**若必须改 `run()` 签名，13 个探针会受影响 → 优先用模块级配置或 `Engine` 对象**
  - 验证：`python tools/probe_stack.py <tpl> <cfg> --json --mask-value "REDACTED"` 输出中密码字段为 `"REDACTED"`；加 `--no-mask-heuristic` 且模板漏标时输出为明文（**这条是"关闭开关生效"的验证，不是缺陷**）

### 1.4 lint：两条错误 + 一条警告（0.4 人日）

- [x] **1.4.1 `option(Key)` + `type(secret)` → 错误**
  - 改动：`tools/probe_stack.py` 的 `lint()`（第 703-749 行）
  - 验证：构造模板（`Password type(secret) reg(\S+) option(Key)`）跑 `--lint`，输出含「错误」级别且提到该集合与字段名

- [x] **1.4.2 `inherit=` 指向 secret 字段 → 错误**
  - 改动：同上。注意 `fld.inherit` 的格式是 `<集合>.<字段>`（第 99 行、第 644 行 `src_coll, _, src_field = fld.inherit.partition(".")`）→ 需要在**跨集合**查找目标集合的字段声明
  - 验证：构造模板（`Other` 集合有 `Secret type(secret)`，本集合有 `Copied type(str) inherit=Other.Secret`）跑 `--lint`，输出含「错误」

- [x] **1.4.3 疑似漏标 → 警告**
  - 改动：同上。规则文本含凭据关键字（`password`/`cipher`/`secret`/`shared-key`/`md5-password`/`irreversible-cipher`/`key-string`/`authentication-key`）**且**该规则引用的字段（`${X}` 引用 + `-> 目标{}` 里的字段）未被声明为 `type(secret)` → 警告
  - ⚠️⚠️ **关键字匹配不能用 `\b` 词边界**（本条为 2026-11-19 实现期修正）：**连字符是非单词字符** ⇒ `\bpassword\b` 对 `default-password-template` **照样命中**（实测 19317 次误报）。
    **正确做法：按"整词切分（保留连字符）"后再比对** —— 即 `re.split(r"[^A-Za-z0-9-]+", pattern)`，`default-password-template` 切成**一个整词**，不等于 `password` → 不命中 ✓
    - 字段**名**的判定仍可用 `\b`（camelCase 下 `Password` 命中、`PasswordType`/`DefaultPasswordTemplate` 不命中 ✓）
    - ⚠️ 残留（可接受，级别是警告不是错误）：`password-policy` 仍会被命中（`startswith(kw + "-")`）—— 华为的 `password-policy` 是口令复杂度策略、不是凭据
    - **参考实现**：`tools/probe_stack.py` 的 `_rule_tokens()` / `_rule_has_credential_keyword()` / `_name_is_credential()`（能力 A 已实现并验证）
  - 验证：三条子用例 —— ① `peer X password cipher ${Password}` + `Password type(str)` → 警告；② 同上但 `type(secret)` → 无警告；③ `default-password-template ${X}` → **无警告**

- [x] **1.4.4 一个字段声明了多个 type → 错误**
  - 改动：`tools/probe_stack.py` 的 `lint()` —— `fld.type_conflict` 为真时报「错误」：*"字段 X 声明了多个 type：一个字段只能声明一种 type（secret 与其它类型互斥）"*
  - 验证：构造 `Password type(secret) type(int)` 跑 `--lint`，输出含「错误」级别并提到该集合与字段名

### 1.5 模板：存量字段标注（0.2 人日）

- [x] **1.5.1 `templates/huawei-vrp.cmdb` 标注 secret 字段**
  - 改动：第 167-168 行的 `Password type(str) reg(\S+) desc(密码)` → `Password type(secret) reg(\S+) desc(密码)`
  - ⚠️ 检索该模板**全部**含 `Password`/`cipher`/`secret` 的字段声明（当前只有 `BgpPeers.Password` 与 `PasswordType`）
  - ⚠️ `PasswordType type(str)`（第 167 行）**不是**凭据（值是 `"cipher"`），**不要**标 secret
  - 验证：`grep -n "type(secret)" templates/huawei-vrp.cmdb` 至少 1 处；`python tools/probe_stack.py templates/huawei-vrp.cmdb <真实文件> --lint` 无「错误」级

### 1.6 回归：基线更新 + 脱敏断言（0.3 人日）

- [x] **1.6.1 更新 `tests/fixtures/sample-vrp.expected.json`**
  - 改动：运行 `python tests/run_regression.py --update`，然后**人工 diff 基线**
  - 验证：`git diff tests/fixtures/sample-vrp.expected.json` 中**只有**密码相关字段的值变为掩码串；其余行不变

- [x] **1.6.2 在 `tests/run_regression.py` 加明文扫描断言**
  - 改动：在现有 diff 比对之后加一步：对 `json.dumps(res, ensure_ascii=False)` 做密文形态扫描，命中则 `problems.append(...)` 并让退出码非 0
  - 验证：临时把模板里的 `type(secret)` 改回 `type(str)` → 回归**失败**且输出命中位置；改回 → 回归通过

- [x] **1.6.3 ⚠️ 新增脱敏专用回归 fixture（现有 fixture 覆盖不到）**
  - **为什么必须做**：实测 `tests/fixtures/sample-vrp.txt`（69 行）里 **没有任何 password / cipher 行**（`grep -i "password\|cipher"` → 0 命中）→ 1.6.2 的明文扫描断言会是**空断言**（永远通过，等于没测）。同理 `sample-vrp.txt` 只有一个 `ip address`，**没有 `sub` 地址**（`grep "ip address"` → 第 22 行一条，无 sub）→ 2.2.2 的 `AddressType` 回归也是空断言。
  - 改动：
    1. 新建 `tests/fixtures/sample-vrp-secret.txt`，内容至少覆盖：
       - 一条**带密码**的 peer：` peer 218.200.1.10 password cipher %^%#;}4v<YDuLCwX%^%#`
       - 一条**不带密码**的 peer（用于断言"键不存在"）：` peer 218.200.31.228 as-number 64650`
       - 一条**带 `sub` 的地址**：` ip address 111.19.27.122 255.255.255.252 sub`
       - 一条**不带 `sub` 的地址**（断言 `AddressType` 键不存在）
       - 一条**长的非凭据描述**（断言 L2 形态误报检查）：` description TO-[SNANK-PC-CMNET-RT01-NE5000E]-Eth-Trunk6=6*10GEUPLINKCR01`
       - 一条**形似凭据但不是凭据**的命令（断言 L2/L1 都不误报）：` default-password-template iptv`（需在模板里加对应字段，或至少断言它不出现在输出里）
    2. 生成 `tests/fixtures/sample-vrp-secret.expected.json`
    3. 改 `tests/run_regression.py`：把比对逻辑抽成函数，**对两个 fixture 各跑一遍**（`sample-vrp.txt` + `sample-vrp-secret.txt`），并让明文扫描断言**至少在第二个 fixture 上有实际覆盖**
  - 验证：
    - `python tests/run_regression.py` 退出码 0，且输出显示跑了 **2 个** fixture
    - 临时把模板的 `type(secret)` 改回 `type(str)` → 回归**失败**（证明断言非空）
    - 临时把 fixture 里的 `sub` 删掉 → `AddressType` 相关断言失败（证明该断言非空）
  - ✅ **实施记录（能力 A）**：fixture 已建（`tests/fixtures/sample-vrp-secret.txt` + `.expected.json`），回归跑 2 个 fixture。
    - ⚠️ **`AddressType` 断言随 2.2.1（D3）落地**：模板还没有 `AddressType` 字段，断言无从写起；
      fixture 里**已含**一条带 `sub`（`ip address 111.19.27.126 255.255.255.252 sub`）与一条不带 `sub` 的地址，
      2.2.1 改完模板即可补断言。**"删掉 sub → 断言失败"这条验证属于 2.2.2，不在能力 A 范围。**
    - ⚠️ **"形似凭据但不是凭据"的断言放在 `tests/test_masking.py`**（用最小模板真捕获 `default-password-template iptv`
      → 断言值仍是 `iptv`、未计入兜底命中）。生产模板**没有**这条命令 → 在回归里只能断言"它不出现在输出里"（空断言，价值低）。
    - ⚠️ **回归脚本额外加了一步 L1-only 扫描**（关掉 L2 形态兜底再扫一遍）：否则把 `type(secret)` 改回 `type(str)`
      会被 L2 兜住 → 回归仍然通过 → **明文扫描断言就成了永远通过的空断言**。
      L1（模板显式标注）才是"标注齐全 ⇒ 零泄露"这条可审计保证，L1-only 扫描把"漏标"变成硬失败。

### 1.7 能力 A 验收（0.2 人日）

- [x] **1.7.1 全量真实文件明文扫描**
  - 验证（对**至少 3 个**真实文件，覆盖不同平台）：
    ```bash
    python tools/probe_stack.py templates/huawei-vrp.cmdb "<真实文件>" --json > /tmp/np_out.json
    grep -c "%\^%#" /tmp/np_out.json        # 期望 0
    python -c "import json;d=json.load(open('/tmp/np_out.json',encoding='utf-8'));p=[x for x in d.get('BgpPeers',[]) if 'Password' in x];print(len(p), all(x['Password']=='******' for x in p))"
    # 期望：N True（N>0 时）
    ```
  - **AND** `python tools/probe_stack.py templates/huawei-vrp.cmdb "<真实文件>" --lint` 无「错误」级

- [x] **1.7.2 13 个探针冒烟**
  - 改动：无
  - 验证：依次运行 `tools/probe_*.py`（每个脚本按其 docstring 的用法传参），**全部不因脱敏改动而崩溃**；输出与改动前语义一致（字段值可能变掩码）
  - ⚠️ **原注"probe_coverage2.py 本身是坏的（trace 4 元组 vs 3 元组解包）"已作废**（2026-11-19 复核）：该问题在基线提交 `d369293` 之前就已修好（`git show d369293:tools/probe_coverage2.py` 第 48 行是 `for no, line, hit, _stack in trace:`），**实测该脚本现在 exit=0** ⇒ **它不再享有豁免，失败即算本任务失败**

- [x] **1.7.3 ⛔ 能力 A 门禁**
  - 检查点：1.7.1 与 1.7.2 全通过 → **能力 B 才可开工**（否则 diff 报告会带明文）
  - ✅ **实施记录**：1.7.1（4 个真实文件 / 4 种型号，明文 0 命中，配密码的 peer 100% 被遮蔽、无密码的 peer 无 Password 键、
    lint 无「错误」级）+ 1.7.2（14 个探针全部 exit 0，其中 11 个与基线**逐字节输出相同**）→ **门禁通过，能力 B 可开工**。

---

### 1.8 实施记录 · 能力 A 的偏离与边界（devforge-developer）

> 下面几条是**实现阶段发现、规格没写到**的事实，供后续能力 B 与文档回写（3.1/3.2）参考。

1. **`^` 在 Python 正则里永远是锚点**：`%^%#` 这种华为密文包裹形态，写成 `r"%^%#"` **永远匹配不上**
   （`^` 被当成行首断言）。必须转义成 `r"%\^%#"`。踩过这个坑。
2. **`\bpassword\b` 挡不住 `default-password-template`**：连字符是"非单词字符"→ `password` 两侧都有词边界 →
   照样命中。**必须按"整词（空白/非字母数字连字符分隔）"切分后再比对**，否则 19317 次误报。
3. **lint 报告原本打在 stdout** → `probe_stack.py ... --json > out.json` 出来的文件**无法 `json.load`**
   （tasks.md 1.7.1 的验证命令正是这么写的）。已改：**`--json` 时 lint 报告走 stderr**，stdout 保持纯 JSON。
4. **L2 会兜住"漏标"** → 单靠 L2，明文扫描断言对"模板漏标"是空断言。回归因此加了 **L1-only 扫描**（见 1.6.3）。
5. **规格里 `E:\配置文件\原始配置\` 不存在**，真实路径是 `E:\配置文件\BRAS&SR\原始配置\`
   （26 个月目录 / 10054 个文件 / 3.3GB）。`tools/readonly_guard.py` 写成"候选路径列表"，谁存在用谁，并把实际用的路径记进基线。
6. **`type(secret)` 与其它 type 同时声明**（spec 的"互斥"场景）：lint 报「错误」，且**引擎不产出该字段**
   （按缺字段处理，不抛未捕获异常）。实现方式：重复 type 保留**第一个**并打冲突标记，值仍照常遮蔽（不泄露）。
7. **"配了多少条凭据"的计数**：脱敏统计 `masking.STATS.l1_hits / l2_hits` 在 `run()` 开头复位 → 读完即"本次运行"的统计；
   CLI 每次运行把统计打到 **stderr**（不污染 stdout 的 JSON）。

---

## 2. 能力 B · 逐字段 diff（3-4 人日）

### 2.1 适配器（1.2 人日）

- [ ] **2.1.1 定义映射表 `MAPPING_SPEC`**
  - 改动：新建 `src/netparse/adapters/incumbent.py`，定义映射表，每个条目含 `path`（incumbent 目标路径）、`source`（netparse 来源路径或 `None`）、`root_cause_if_empty`、`note`
  - **必须覆盖**的映射（依据实测）：

    | incumbent 路径 | netparse 来源 | 备注 |
    |---|---|---|
    | `device_name` | `Device[0].SysName` | |
    | `device_ip` | `Device[0].RouterId` | |
    | `interfaces[].name` | `Interfaces[].Name` | |
    | `interfaces[].description` | `Interfaces[].Description` | |
    | `interfaces[].IP.IPV4[].ip` | `Interfaces[].IpAddress[].Address`（Family=ipv4） | |
    | `interfaces[].IP.IPV4[].mask` | 同上 `.Mask` | |
    | `interfaces[].IP.IPV4[].type` | `Interfaces[].IpAddress[].AddressType` | **依赖 D3 的模板改动** |
    | `interfaces[].IP.IPV6[].ip` | `Interfaces[].IpAddress[].Address`（Family=ipv6） | 实测键名是 `ip`，不是 `addr` |
    | `interfaces[].IP.IPV6[].mask` | 同上 `.Mask` | |
    | `interfaces[].vpn.name` | `Interfaces[].IpBindingVpn` | 无值则不产生 `vpn` 键 |
    | `route-static.IPV4[].dest` | `RouteStatics[].Prefix` | |
    | `route-static.IPV4[].mask` | `RouteStatics[].Mask` | |
    | `route-static.IPV4[].nexthop` | `RouteStatics[].NextHop` | ⚠️ 「只有出接口」形态下 incumbent 是**空串 `""`**、netparse 是**键缺失** → 适配器映射成 `""` |
    | `route-static.IPV4[].interface` | `RouteStatics[].NextHopIface` | ⚠️ 根因 **`整条记录丢失`**（实测 96% 的"出接口+下一跳"路由整条没进记录），不是单纯的 `字段丢失` |
    | `route-static.IPV4[].vpn` | `RouteStatics[].VpnInstance` | |
    | `interfaces[].VLAN.user-vlan[].user-vlan` | `Interfaces[].UserVlan` | **纯值搬运**：`UserVlan` 存在则输出 `{"VLAN": {"user-vlan": [{"user-vlan": [值]}]}}`；**不合成 `type`/`QinQ`/`dot1q`**（无证据的推导一律禁止） |
    | `interfaces[].VLAN.QinQ` / `.dot1q` | **无** | 根因 `模板未覆盖该命令`（实测 PVLAN 外层在真机上是 `user-vlan 1000 1999 qinq 2000` 同行，无独立 `qinq` 命令；`vlan-type dot1q` 模板无对应规则） |
    | `interfaces[].bas.damian` | **无** | 根因 `模板未覆盖该命令` |
    | `interfaces[].multicast.type` | **无** | 根因 `模板未覆盖该命令` |
    | `interfaces[].traffic-policy.in/out` | **无** | 根因 `模板未覆盖该命令` |
    | `interfaces[].QOS.in/out.car` | **无** | 根因 `模板未覆盖该命令` |
    | `interfaces[].qos-profile.in/out` | **无** | 根因 `模板未覆盖该命令` |
    | `route-static.IPV6[]` | **无** | 根因 `模板未覆盖该命令` |
    | 顶层 `qos-profile[]`（`{name, in:{cir}, out:{cir}}`） | **无** | ⚠️ 根因 `模板未覆盖该命令`；**限速列的第 1/3 层（命名空间定义）** |
    | `interfaces[].qos-profile.in/out`（**引用名**字符串） | **无** | ⚠️ 根因 `模板未覆盖该命令`；**限速列的第 2/3 层（接口级引用）** |
    | `interfaces[].QOS.in/out.car`（**直给值**） | **无** | ⚠️ 根因 `模板未覆盖该命令`；**限速列的第 3/3 层（接口级直给）** |

  - ⚠️ **限速三层的缺一不可**（实测）：顶层 list（40/40 文件都有）+ 接口级引用（1770 个接口）+ 接口级直给（2304 个接口）；`get_speed` 的规则是"**两者都存在取小**"。⇒ 模板要补**三处**规则，不是三选一
  - ⚠️⚠️ **映射表不得添加 peer / Password / Bgp 相关行**（2026-11-19 复核修正）：**incumbent 产物里根本没有 peer/BGP 段** —— 其顶层键**恒为 5 个**（`device_name`/`device_ip`/`interfaces`/`route-static`/`qos-profile`，401 个产物抽样一致）；实测一个配置原文含 58 行 `password cipher` 的设备，产物里 `%^%#`/`password`/`peer` 命中**均为 False**。⇒ 加了会写出**恒为空的映射 = 假缺口**。`Bgp`/`BgpPeers` 应作为 **netparse 独有的资产类别**出现在段二的 `only_netparse` 里，不是映射行
  - 验证：映射表条目数 ≥ **26**；每条都有 `path` 与 `root_cause_if_empty`；`grep -c "source" src/netparse/adapters/incumbent.py` > 0；**`grep -iE "peer|password|bgp" src/netparse/adapters/incumbent.py` 的 MAPPING_SPEC 段无命中**（docstring 提到不算）

- [ ] **2.1.2 实现 `to_incumbent_shape()`**
  - 改动：同文件。**纯结构映射**（design.md D9）：键改名 + 结构重排 + 值搬运；**禁止**推导/默认值/合成
  - ⚠️ `route-static` 在 incumbent 侧是 **dict**（`{"IPV4":[...], "IPV6":[...]}`），不是 list —— 必须重排成 dict
  - ⚠️ `IP` 是 dict（`{"IPV4":[...], "IPV6":[...]}`）；**接口无 IP 时不产生 `IP` 键**
  - ⚠️ IPv6 的键名是 `ip`、前缀键是 `mask`（incumbent 实测：`{"ip": "...", "mask": "127"}`；早期文档误写为 `addr`，已按真实产物更正）
  - 验证：2.1.3 的样例回归全绿

- [ ] **2.1.3 映射表最小样例回归**
  - 改动：新建 `tests/test_adapter.py` —— 每条映射一个最小样例（netparse 形状输入 → 期望 incumbent 形状输出）；**缺样例则失败**
  - 验证：`python tests/test_adapter.py` 退出码 0；人为改错一条映射的 source → 该样例失败

### 2.2 模板：`AddressType`（D3）（0.3 人日）

- [x] **2.2.1 `IpAddress` 增加 `AddressType` 字段并捕获 `sub`**
  - 改动：`templates/huawei-vrp.cmdb` 的 `IpAddress` 集合 —— 在 `[value]` 段加 `AddressType type(str) reg(sub) desc(地址类型)`；规则第 96 行改为
    `^\s*ip address\s+${Address}\s+${Mask}(?:\s+(?P<AddressType>sub))?\s*$   -> Family="ipv4"`
  - ⚠️ **裸命名组 `AddressType` 必须先在字段表声明**（否则 lint「命名组未声明」会报错）→ **先加字段声明，再改规则**
  - ⚠️ **保持规则与原来一样能匹配非 sub 形式**（`(?:\s+...)?` 可选）
  - 验证：`python tools/probe_stack.py templates/huawei-vrp.cmdb <含 sub 的真实文件> --json` → 对应 IP 记录含 `AddressType: "sub"`；不含 sub 的接口**无**该键

- [x] **2.2.2 回归基线同步**
  - 改动：确认 1.6.3 新建的 `sample-vrp-secret.txt` 里**已包含**一条 `sub` 地址与一条非 `sub` 地址；运行 `python tests/run_regression.py --update` 后**人工 diff**
  - ⚠️ 原 `sample-vrp.txt` **没有 `sub` 地址**（实测只有第 22 行一条 `ip address`）→ 它的基线**不变**；`AddressType` 的断言由 `sample-vrp-secret.txt` 承担
  - 验证：`git diff tests/fixtures/` 中只出现 `AddressType` 相关键（且只在 `sample-vrp-secret.expected.json` 里）；`sample-vrp.expected.json` **无变化**

- [x] **2.2b.0 ⭐ 改动前先存修前模板快照**
  - 改动：把**改动前**的 `templates/huawei-vrp.cmdb` 原样复制到 `.devforge/spec/changes/netparse-v3-diff-and-mask/baseline-template.cmdb`
  - ⚠️ **必须在 2.2b.1 之前做**，否则"修前"基线就丢了
  - 验证：`diff <(cat templates/huawei-vrp.cmdb) <(cat .devforge/.../baseline-template.cmdb)` → 无差异（改动前应完全相同）

### 2.2b 模板：静态路由多形态（修 96% 的整条丢失）（0.5 人日）

> **背景**：实测 60 台 / 1651 条 `ip route-static`：纯下一跳形态丢失 0.3%，**"出接口 + 下一跳"形态丢失 96%（72/75）** —— 因为 `NextHop` 的 reg `\S+` 捕获到接口名，随后的下一跳 IP 使规则末尾 `\s*$` 失配 ⇒ **整条路由根本没进记录**。
> **⇒ 对台账第 10 列的影响 ≈ 100%**：incumbent 的第 10 列只收带出接口的路由（`build_route_index` 跳过无 `interface` 的），而这些恰好是 netparse 丢的那批。
> **最终 4 条规则的写法、判别式设计、两个引擎坑、逐字段验证结果，全部见 design.md `## Interfaces` 的「静态路由多形态」小节（已实测，与 incumbent 212/212 完全一致）。**

- [x] **2.2b.1 `RouteStatics` 的字段声明：`NextHopIface` 新增 + `NextHop` 的 reg 收窄**
  - 改动：`templates/huawei-vrp.cmdb` 的 `RouteStatics` 集合 `[value]` 段
    - 新增 `NextHopIface type(str) reg((?!NULL0)[A-Za-z][\w./-]*) desc(出接口)`（**判别式**：以字母开头且不是 NULL0 = 接口名）
    - `NextHop` 的 reg 从 `\S+` 收窄为 `\d{1,3}(?:\.\d{1,3}){3}|NULL0`（**IP 或黑洞下一跳**）
    - ⚠️ **先加字段声明，再改规则**（lint 的「命名组名必须在字段表」要求 `NextHopIface` 先声明）
  - 验证：`python tools/probe_stack.py templates/huawei-vrp.cmdb <真实文件> --lint` 无「错误」级

- [x] **2.2b.2 静态路由规则改成 4 条（vpn-instance 变体在前，plain 变体在后）**
  - 改动：把现有 2 条 `ip route-static` 规则换成 design.md 的 4 条（① vpn·纯下一跳 ② vpn·出接口+下一跳 ③ plain·纯下一跳 ④ plain·出接口）
  - ⚠️ **坑 1 · 每条规则内 `${NextHop}` 只出现一次** —— 重复出现会编译成回溯引用 `(?P=NextHop)`（语义变成"接口名必须等于下一跳"）。**已实测：拆成多条规则即可规避，不需要换字段名**（`compile_rule` 的 `seen` 是函数内局部变量，per-rule）
  - ⚠️ **坑 2 · 不要用 alternation 做形态分流** —— 用 `reg()` 判别（`NextHop`=IP|NULL0 / `NextHopIface`=以字母开头且非 NULL0）
  - ⚠️ **坑 3 · `vpn-instance` 变体必须在 plain 变体之前** —— 否则 plain 规则会**错位捕获** `ip route-static vpn-instance ...`（`Prefix="vpn-instance"`），是静默错值，比丢数据更糟
  - ⚠️ **`->` 目标里必须显式带上 `NextHopIface`** —— 目标只写花括号里列出的字段（`probe_stack.py:656-668`），漏了就进不了记录
  - ⚠️ **plain·纯下一跳规则尾部要放宽**（`(?:\s+.*)?`）—— 与 incumbent 一致：首 token 是 IP 而后面还有 token 时，incumbent 把多余 token 丢弃
  - ⚠️ **已知边界**：下一跳为**主机名**（非 IP）的路由匹配不到；实测语料 0 条，须在模板注释里写明
  - 验证：见 2.2b.4

- [x] **2.2b.3 ⭐ 全量汇总口径验证（不要用单文件断言 —— 单文件无分辨力）**
  - 改动：无（只读验证；比对脚本放 `tools/` 下可重跑）
  - 验证（**至少 60 个含形态2 路由的真实文件**，汇总断言）：
    - 形态 2 的 **75 条**中，netparse **修前抓到 3 条**、**修后应抓到 ≥ 70 条**（允许少量因其它形态差异而失配）
    - **⚠️ 不要用单文件断言** —— 形态 2 分散在 60 个文件里（单文件仅 1-2 条），单文件口径两侧都是 0，无分辨力
    - **⚠️ 判定时 MUST 排除 `NULL0`**（`interface NULL0` 在 60/60 个文件里存在；不排除会把分母从 76 污染到 930、丢失率从 94.7% 算成 5.6%）
  - **补充 · 单文件逐字段比对（作为字段级正确性验证，不作为丢失率口径）**：
    真实文件 `原始配置/cfgfile_20250824/华为/SNBJI-MC-CMNET-BRAS01-MYL-X16A_218.200.1.138.20250824061211`
    对照 `解析后/cfgfile_20250824/华为/同名.json`：
    - ① `NextHopIface` 有值（如 `Eth-Trunk188`）
    - ② `NextHop` 是**下一跳 IP**而不是接口名
    - ③ **带 `vpn-instance` 前缀的出接口路由也能抓到**（4 条规则都覆盖到）
    - ④ **整体逐字段一致**：`(Prefix, Mask, NextHopIface, NextHop, VpnInstance)` 集合与 incumbent 的 `(dest, mask, interface, nexthop, vpn)` **完全相同，差异 0**
    - **基线**：该文件 incumbent 有 **212** 条 IPV4 路由；改后 netparse 也应是 **212** 条且集合一致（design.md 已实测达成 212/212、差异 0）

- [x] **2.2b.4 边界形态回归（7 种，全部要过）**
  - 验证（每种输入的期望值 = incumbent 的判据「首 token 非 IP 且非 NULL0 → 出接口」）：
    ```
    ip route-static P M 10.1.1.1 description pure      → NextHop=10.1.1.1, Description ✓
    ip route-static P M NULL0                          → NextHop=NULL0 ✓
    ip route-static P M Eth-Trunk188 192.168.0.70 desc → NextHopIface=Eth-Trunk188, NextHop=192.168.0.70 ✓
    ip route-static P M Eth-Trunk99                    → NextHopIface=Eth-Trunk99（无 NextHop 键）✓
    ip route-static P M 1.2.3.4 5.6.7.8               → NextHop=1.2.3.4（5.6.7.8 丢弃，与 incumbent 一致）✓
    ip route-static vpn-instance V1 P M Eth-Trunk77 172.16.0.1 → VpnInstance=V1, NextHopIface=Eth-Trunk77, NextHop=172.16.0.1 ✓
    ip route-static vpn-instance V2 P M 10.7.7.7       → VpnInstance=V2, NextHop=10.7.7.7 ✓
    ```
    - ⚠️ 第 5 条是**与 incumbent 对齐的关键**：若 `NextHopIface` 的 reg 用 `\S+` 而不是判别式，这条会变成 `NextHopIface=1.2.3.4, NextHop=5.6.7.8`（**与 incumbent 不一致**）
    - ⚠️ 第 4 条是**「只有出接口」形态**：若 `NextHop` 的 reg 不收窄，这条会变成 `NextHop=Eth-Trunk99`（**接口名被误填进下一跳**）

- [x] **2.2b.5 丢失率复测**
  - 改动：无（只读验证）
  - 验证：在 60 台样本上复算「带显式出接口的路由」条数与 netparse 丢失条数
    - **判定时 MUST 排除 `NULL0`**（`interface NULL0` 在 60/60 个文件里存在）
    - 期望：带出接口的路由 **76** 条 → 丢失 **0** 条（修完）
  - ⚠️ 复测脚本放在 `tools/` 下（可重跑），不要只跑一次
  - ✅ **实施记录**：新增 `tools/check_route_forms.py`（可重跑，一次跑完 2.2b.3/4/5）。
    - **边界形态 7/7 过**；**真实文件逐字段集合完全一致 360/360**（cfgfile_20250824 / cfgfile_20260124 / 配置文件202408 各 120 个）
    - **带显式出接口的路由（排除 NULL0）：修前 0 条（丢 100%）→ 修后 100% 抓到（丢 0%）**（198 / 295 / 96 条三批）
    - 架构师指定的验收文件 `SNBJI-MC-CMNET-BRAS01-MYL-X16A_218.200.1.138.20250824061211`：**incumbent 212 条 vs netparse 212 条，集合完全相同，差异 0**（修前 207 条）
    - ⚠️ **规则写法相对 design.md 的 4 条方案做了 3 处修正（真实语料逼出来的，design.md 的写法在现网匹配不上）**：
      1. **`NextHopIface` 的 reg 从 `(?!NULL0)[A-Za-z][\w./-]*` 改成 `(?!NULL0)(?!\d{1,3}(?:\.\d{1,3}){3}(?:\s|$))[^\s]+`**
         —— 现网接口名**有以数字开头和含 `|` 的**（`50|100GE6/0/0`、`100GE11/0/1`），原写法匹配不上 ⇒ 这批路由整条丢失。
         新写法 = "不是 IP、不是 NULL0 ⇒ 就是接口名"，与 incumbent 的判据**逐字对齐**。
      2. **4 条规则的尾部都放宽成 `(?:\s+.*)?`**
         —— 现网路由行带 `track nqa Ali ...` 尾巴（原写法整条失配）。incumbent 对多余 token 是**忽略**，不是失配。
      3. **②④ 的"下一跳"改成可选 `(?:\s+${NextHop})?`；③④ 加 `(?!vpn-instance\s)` 前瞻**
         —— 现网存在"vpn-instance + 出接口、**无下一跳**"形态（抽样 337 文件里 4 条）；无前瞻时 plain 规则会错位捕获 `Prefix="vpn-instance"`（静默错值，比丢数据更糟）。
      ⇒ **design.md 的"4 条规则 + 判别式"骨架、顺序、坑 1/2/3 全部保留**，只是把两处与现网不符的正则形态修正了。建议架构师回填 design.md。

### 2.3 diff 工具骨架（0.8 人日）

- [ ] **2.3.1 CLI 与参数解析**
  - 改动：新建 `tools/diff_incumbent.py`，参数：`--all` / `--sample N` / `--month YYYYMM` / `--out-dir` / `--json` / `--markdown` / `--dry-run` / **`--template <path>`**（指定用哪份模板跑；缺省用 `templates/huawei-vrp.cmdb`）
  - ⚠️ `--template` 是**跑两遍**的基础（修前用 `baseline-template.cmdb`，修后用当前模板），报告里的 `netparse_before_fix` / `netparse_after_fix` 两栏由它产出
  - 验证：`python tools/diff_incumbent.py --help` 退出码 0 并列出全部参数（含 `--template`）

- [ ] **2.3.2 只读 import `build_excel`**
  - 改动：`sys.path.insert` 到 `E:\配置文件\配置解析\华为`，`import build_excel`；**只调用** `build_excel.build_rows(json_path)` 与 `build_excel.COLUMNS`
  - ⚠️ **不得**修改/复制 `build_excel.py`；**不得**调用 `write_month` / `main`
  - 验证：diff 跑完后 `readonly_guard.py --verify` 对 `配置解析\华为\` 报 OK

- [ ] **2.3.3 路径反推与完整性检查**
  - 改动：按 `解析后/<月>/华为/<stem>.json` → `原始配置/<月>/华为/<stem>`（再试 `<stem>.txt`）
  - 验证：`python tools/diff_incumbent.py --dry-run` 输出「找到对应原始配置 8440 / 8440」且缺失清单为空

### 2.4 diff 主流程（1.2 人日）

- [ ] **2.4.1 逐文件两侧行生成与按名对齐**
  - 改动：netparse 解析 → 适配器 → 写临时 JSON（**文件名保留原 `<stem>`**）→ `build_rows` → 行；incumbent JSON → `build_rows` → 行；**按接口名（小写）join**
  - ⚠️ **临时文件名必须保留时间戳**，否则 `extract_time` 提取不到 → "采集时间"列全线不同（design.md R5）
  - ⚠️ 两侧接口数可能不同 → **按名 join，不按索引**
  - ⚠️ 重名接口 → **报错并跳过该文件**（计入 `files_failed`）
  - 验证：对同一个样本文件，断言 netparse 与 incumbent 的接口数均为 **1044**（实测基线）；断言两侧行能按名配对

- [ ] **2.4.2 19 列填充率统计（段一 · 台账口径 · 逐文件粒度）**
  - 改动：逐文件逐列统计 `{filled, total, rate}`；列名与顺序取自 `build_excel.COLUMNS`
  - ⚠️ **限速列（第 6/7 列）的值只能来自 `build_rows`**（顶层定义 + 接口级引用 + 接口级直给 三层 join 取小）—— diff 工具**不得**自算
  - 验证：报告 JSON 的 `column_fill_rate` 有 19 个键；`接口入流限速(M)` 的键名与 `COLUMNS` 逐字相同；**diff 工具源码里 grep 不到任何自制的限速计算逻辑**

- [ ] **2.4.2b ⭐ 全局汇总口径（段一 · 台账口径 · 全局粒度）—— 必须项，不是可选项**
  - 改动：把 8440 个文件的**所有记录汇总**（不按文件切分），按列统计**值条数**（非空值的个数），产出 `global_column_value_count`
  - ⚠️ **为什么必须有**（实测）：第 10 列在**单文件粒度上没有分辨力** —— `SNANK-MC-CMNET-BAS03-SCEC-X16A`（1558 接口）**incumbent 自己就是 0/1558 = 0.0%**（该文件 79 条路由全无显式出接口），而 netparse 丢掉的 72 条分散在 60 个文件里 ⇒ 逐文件口径显示"两侧都是 0%，无差距"，**把 96% 的丢失完全掩盖掉**
  - **报告规则**：
    1. 每列**同时**给「逐文件填充率」与「全局值条数」
    2. **判据**：全局值条数两侧差 > **20%** → **显式标红**（`flag: "RED"` + `flag_reason`），**即使逐文件填充率都接近 0**
    3. 某列两侧的逐文件填充率**都 < 1%** → 附注「**本列逐文件差异不具分辨力，请以全局值条数为准**」（`per_file_resolvable: false`）
  - **基线（实测 120 文件样本）**：incumbent 第 10 列全局值条数 **178**（分布在 42 个文件）vs netparse 修前 ≈ **6** 条 → **差 96.6%**
  - 验证：
    - 报告 JSON 的 `global_column_value_count` 有 19 个键
    - 「IPv4静态路由明细」列：incumbent 值条数 **远大于** netparse 修前，且 `flag == "RED"`
    - **报告不会显示"该列无差距"**
    - ⚠️ 判定"带显式出接口"时 **MUST 排除 `NULL0`**

- [ ] **2.4.2c 接口数一致性统计（识别层基准）**
  - 改动：逐文件比对两侧接口数，产出 `interface_count_parity: {files_matched, files_mismatched}`；mismatched 的文件列出仅一侧有的接口名
  - 验证：对 `SNANK-MC-CMNET-BAS03-SCEC-X16A_218.200.1.171.20250824063214` 断言两侧均为 **1558**；对 1044 接口的样本断言均为 **1044**

- [ ] **2.4.3 差异样本采集**
  - 改动：每列若存在差异，取至少 1 条 `{device, interface, incumbent, netparse}`
  - 验证：至少有一列（预期"域名名称"）有非空样本；样本能回溯到原始配置的接口行

- [ ] **2.4.4 根因自动分类**
  - 改动：用引擎的 `run(colls, lines, trace=...)`（`probe_stack.py:594/610`）按命令族统计命中数；结合 `MAPPING_SPEC` 的 `root_cause_if_empty` 自动判定 `模板未覆盖该命令` / `字段丢失` / `join缺失`
  - ⚠️ 命令族定义必须与 `probe_coverage3.py:25-34` 一致（骨架归一化 + 前 1~2 词），否则数字对不上
  - 验证：断言"域名名称"列的 `root_cause` 含 `模板未覆盖该命令`，且 `evidence` 里 `bas`/`access-type` 命令族的总行数 > 0、命中行数 = 0；断言"IPv4静态路由明细"列的 `root_cause` 含 **`整条记录丢失`** 且 evidence 指明"带显式出接口的路由整条没进记录"（不是只缺字段）；断言"接口入流限速(M)"列的 `root_cause` 含 `模板未覆盖该命令`（netparse 侧模板**完全没有** qos-profile 规则，三层全缺）
  - ⚠️ **判定"带显式出接口"时必须排除 `NULL0`**（`interface NULL0` 在 60/60 个文件里存在）—— 不排除会把分母从 76 污染到 930、丢失率从 94.7% 算成 5.6%

- [ ] **2.4.5 ⭐ 双向字段级能力覆盖（段二 · 能力口径）**
  - 改动：在 `part2_capability` 里产出 `interface_level_fields: {matched, only_incumbent, only_netparse, coverage}` 与 `collection_level_fields`（`qos_profile_top` / `route_static`）；并写 `extra_value_note`
  - ⚠️ **字段名归一化**：incumbent 用 snake_case（`name`/`description`/`IP`/`vpn`/`QOS`/`VLAN`/`bas`/`multicast`/`qos-profile`/`traffic-policy`），netparse 用 PascalCase（`Name`/`Description`/`IpAddress`/`IpBindingVpn`/…）→ 比较前必须先按**语义别名表**归一化，别用大小写折叠
  - ⚠️ **必须双向**：只报缺口会系统性低估 netparse（netparse 多出的接口级字段 incumbent 一个都没有）
  - 验证：在 1558 接口的样本上断言 `matched` 恰为 **4**（name/description/IP/vpn）、`only_incumbent` 恰为 **6**（QOS/VLAN/bas/multicast/qos-profile/traffic-policy）、`only_netparse` **不少于 10**；断言 `collection_level_fields.qos_profile_top.netparse == null`

- [ ] **2.4.5b ⭐ 资产类别覆盖对比（段二 · 谁覆盖的资产种类多）**
  - 改动：在 `part2_capability.asset_categories` 里产出两侧各自的资产类别清单 + **三分类** + 真正独有类别及其实例数
  - **实测基线（★三分类，不是简单二分）**：
    ```
    netparse 12 类 = 共有(结构同) 3 + 共有(结构不同) 2 + 真正独有 7
      共有(结构同) 3: Device↔device, Interfaces↔interfaces, RouteStatics↔route-static
      共有(结构不同) 2: IpAddress↔interfaces[].IP, VpnInstances↔interfaces[].vpn
      真正独有 7: EthTrunkMember, Bgp, BgpPeers, Acls, AclRules, Nqas, NqaSessions
    incumbent 4 类 = 共有(结构同) 3 + 真正独有 1
      真正独有 1: qos-profile（顶层命名空间定义）
    ```
    - **⚠️ 不能简单说"netparse 独有 9 类"**：实测 incumbent 的 `interfaces[]` 子键只有 **10 种**（`name`/`description`/`VLAN`/`bas`/`IP`/`vpn`/`multicast`/`traffic-policy`/`QOS`/`qos-profile`，120 个产物抽样）⇒ **`IP` 在 120/120 个产物里（26831 次）、`vpn` 在 117/120（21486 次）** ⇒ **IpAddress 与 VpnInstances 的业务内容 incumbent 是有的**（只是嵌在 interfaces 里）→ 算成"独有"会**高估 2 类**，且一旦有人打开 incumbent 的 JSON 就会发现对不上 ⇒ **报告可信度受损**
  - ⚠️ **实例数是"条数"不是"行数比例"** —— 与段一的全局汇总口径一致
  - 验证：① `asset_categories.netparse` 的计数 = `total 12 / shared_same_shape 3 / shared_diff_shape 2 / only 7`；② `asset_categories.incumbent` 的计数 = `total 4 / shared_same_shape 3 / shared_diff_shape 0 / only 1`；③ `netparse_only_instances` **恰 7 项**且**不含** `IpAddress`/`VpnInstances`；④ **结论段两面都报**（"台账列差 N" + "资产类别真正独有 7 类、另有 2 类同业务对象不同层级、接口级字段多 17 个"）

### 2.5 报告产出（0.6 人日）

- [ ] **2.5.1 JSON 报告**
  - 改动：按 design.md `## Interfaces` 节的**两段式**结构写 `--json` 输出（顶层 `part1_ledger` / `part2_capability` / `workload_estimate` / `readonly_check`）；**流式写**（不把 8440 文件的全量数据攒在内存里）
  - ⚠️ `part1_ledger` 里必须有**两个粒度**：`column_fill_rate`（逐文件）与 `global_column_value_count`（全局值条数）；受模板改动影响的列还要有 `incumbent` / `netparse_before_fix` / `netparse_after_fix` 三栏
  - ⚠️ **`meta` 段要带脱敏统计 `l1_hits` / `l2_hits`** —— `masking.STATS.as_dict()` 提供。**`l2_hits` 是 D4 说的「模板标注完整度」度量**：L2 命中数远大于 L1 ⇒ 模板漏标 secret。实测真实文件 **L1=3/4/4/9、L2=0** ⇒ 当前模板对已抓到的凭据字段标注完整、无漏标
  - ⚠️ **`STATS` 是 per-run 的**：`run()` 开头会 `STATS.reset()` ⇒ **diff 工具必须自己累加**每个文件跑完后的 `STATS.as_dict()`，不能只在最后读一次
  - 验证：`python tools/diff_incumbent.py --all --json <path>` 后 `python -c "import json;d=json.load(open(...));print(d['meta']);print(d['part1_ledger'].keys());print(d['part2_capability'].keys())"` 输出 `files_total=8440, files_processed=8440, files_failed=0` 且两段的键都在；`global_column_value_count` 有 19 个键；`meta` 含 `l1_hits` / `l2_hits`

- [ ] **2.5.2 Markdown 报告 + 工作量估算**
  - 改动：人可读版；**两段都要有**——段一：19 列填充率表（**逐文件 + 全局值条数两栏**）+ 根因表 + 差异样本 + 接口数一致性 + **修前/修后对照**；段二：双向字段覆盖（缺什么 / 多什么）+ 集合级结构差异 + **资产类别三分类对比** + `extra_value_note`；外加 `workload_estimate`（逐列 `column`/`gap`/`change`/`effort_days` + 总区间 + 与 FINAL-plan 8.4 的 `68-104` 对照）
  - ⚠️ **标红规则**：全局值条数两侧差 > 20% 的列，在 Markdown 表格里**必须视觉标红**（如加 `🔴` 与加粗）
  - ⚠️ **无分辨力的列**要加注「本列逐文件差异不具分辨力，请以全局值条数为准」
  - ⚠️⭐ **报告必须带「复现锚点」段**（2026-11-19 批准新增）—— **这份报告是能力 B 唯一的决策依据**（决定要不要投 68-104 人日），而语料是**月度快照**、下个月就变 ⇒ 没有锚点则三个月后没人能复现、**决策失去锚点**。报告 SHALL 记录：
    1. **复现命令**（完整命令行，含 `--template` 等全部参数；修前/修后各一条）
    2. **输入语料快照**：用了哪几个 `cfgfile_YYYYMMDD` 月份目录、各多少文件、总文件数
    3. **git commit hash ×3**：工具版本 + **模板当前版本** + **模板修前版本**（后者对应 `baseline-template.cmdb`；报告有「修前/修后」两栏 ⇒ **两个模板 commit 都要记**）
  - 验证：
    - Markdown 里能直接读出"哪几列差、每列差多少、共多少人日" **以及** "netparse 比 incumbent 多了哪些字段/哪些资产类别、这些值不值得用" **以及** "第 10 列修前 vs 修后差多少"
    - **报告里有 `repro` 段**，且含 `command` / `command_baseline` / `corpus_snapshots` / `git_commit_tool` / `git_commit_template_current` / `git_commit_template_baseline` 六个字段，**均非空**
    - **资产类别数字与规格基线一致**：netparse `12/3/2/7`、incumbent `4/3/0/1`（这是最难事后发现的一类错位 —— 两边都自洽、只是对不上）

- [ ] **2.5.3 明文自检 + 临时文件清理 + 只读自检**
  - 改动：报告写盘后扫描密文形态，命中 → 删除报告 + 非 0 退出；finally 块清理临时 JSON；调用 `readonly_guard.py --verify`
  - 验证：① `grep -c "%\^%#" <report>` → 0；② 临时目录运行后为空；③ 输出含 `READ-ONLY OK`

### 2.6 抽样冒烟与全量（0.4 人日）

- [ ] **2.6.1 `--sample 20` 冒烟**
  - 改动：无
  - 验证：产出报告且结构与全量一致；人工核对 3 个文件的差异样本合理

- [ ] **2.6.2 `--all` 全量**
  - 改动：无
  - 验证：产出正式报告；`meta.files_processed == 8440`、`files_failed == 0`；记录耗时

### 2.7 能力 B 验收（0.3 人日）

- [ ] **2.7.1 结论可回答决策问题**
  - 检查点：报告能回答**两个**问题 ——
    1. **台账口径**：`workload_estimate` 段能回答"netparse 距离替代 incumbent 还差哪几列、每列差多少、补齐共需多少人日"
    2. **能力口径**：`part2_capability` 段能回答"netparse 整体解析能力是更强还是更弱、比 incumbent 多了哪些**字段**、多了哪些**资产类别**、这些值不值得用上"
  - **AND** 能对照 `docs/FINAL-plan.md` 8.3 实验 1 的判据给出结论（差距大 → 重新评估路线 2；差距小 → 按 8.1 走）
  - ⚠️ **结论必须两面都报，不得只依据段一**：结论形态应是「**台账列差 N，但资产类别多 M 类、接口级字段多 K 个**」。若段二显示 netparse 多出的字段/类别有 CMDB 价值，则"台账列还差 N 列"这个负面结论要**连同"但能力面并不更弱"一起**陈述
  - ⚠️ **段一无密码列**：incumbent 产物无 peer/BGP 段（顶层键恒为 5 个）⇒ 报告里**不出现**任何与密码相关的列或结论；`Bgp`/`BgpPeers` 属段二的 netparse 独有类别，否则结论会被系统性低估

- [ ] **2.7.2 报告入仓 + 只读终检**
  - 改动：报告写入 `.devforge/spec/changes/netparse-v3-diff-and-mask/diff-report/`；大文件按 design.md **Q7** 处理（压缩或只提交汇总 + 样本）
  - 验证：`python tools/readonly_guard.py --verify` → `READ-ONLY OK`

---

## 3. 文档与结论回写（0.5 人日）

- [ ] **3.1 `docs/TEMPLATE-GUIDE.md` 补 `type(secret)` 语法**
  - 改动：3.2 字段声明表加 `type(secret)` 行；新增一小节讲脱敏（何时用、掩码串可配、Key 不兼容、inherit 禁止、**一个字段只能声明一种 type**）
  - ⚠️ **同时要更新命令表**：说明 **`--json` 时 lint 报告走 stderr**（D14）—— 想同时看 lint 要写 `> out.json 2> lint.txt`（或 `2>&1 | tee`）；非 `--json` 模式不变
  - ⚠️ **同时要注明**「嵌套字段用**点号路径**（`Statistic.Mode`）而不是组名（`Statistic__Mode`）」—— 后者在 `-> 集合名{}` 目标里会被静默丢弃（实现期发现，能力 A 的 `_apply` members 分支以路径为键）
  - 验证：`grep -n "type(secret)" docs/TEMPLATE-GUIDE.md` 命中；`grep -n "stderr" docs/TEMPLATE-GUIDE.md` 命中；文档中的语法示例能被引擎解析（用一个最小模板验证）

- [ ] **3.2 `docs/FINAL-plan.md` 回填 diff 结论**
  - 改动：8.3 实验 1 标注"已完成"，填入实测的 19 列填充率、根因、工作量区间；4.1 的"9/19 覆盖"估算改为实测值
  - 验证：文档中的数字与报告 JSON 一致（逐个核对）

- [ ] **3.3 ~~`docs/FINAL-plan.md` 回填语料口径~~ —— 取消**
  - **team-lead 已自行修正**（口径 = 按管理 IP 去重 **632 台**；华为 560 / 中兴 66 / 诺基亚 6），**不要重复改同一份文档**

- [ ] **3.4 归档准备**
  - 改动：若全部验收通过，在 `docs/FINAL-plan.md` 记录本变更的结论与后续批次建议
  - 验证：勾选 `tasks.md` 全部条目；`git log` 能看到分阶段提交（能力 A / 能力 B / 文档）

---

## 附：实施阶段最容易翻车的 3 个点

> 详细分析见 `design.md` 的 Risks 表（R1/R5/R6）与本清单各任务的 ⚠️ 标注。

### 翻车点 1 · `last` 缓存绕过脱敏（design.md R1 + 1.2.2/1.2.3）

`apply_writes` 改对了，**但 `last[(coll.name, path)] = v`（`probe_stack.py:636`）写的仍是捕获原值**。这个缓存是 `inherit=` 的数据源 → 一旦有字段 `inherit` 一个 secret 字段，明文会被复制到一个**普通字段**里，且**表面上看起来脱敏成功了**（因为原字段是掩码）。

**排查方法**：全局 grep `last\[` 的所有写入点，确认**每一处**写入的都是遮蔽后的值。

### 翻车点 2 · 临时 JSON 文件名丢掉时间戳（design.md R5）

`build_rows(json_path, collect_time=None)` 在 `collect_time` 为空时调 `extract_time(json_path)`（从**文件名**提取时间戳）。diff 工具为调用 `build_rows` 必须把 netparse 结果写成临时文件 —— 如果临时文件名是 `netparse_tmp_1.json` 之类，**"采集时间"列会全线为空**，产生 8440 × N 条假差异，**彻底淹没真差异**，让整个 diff 结论作废。

**排查方法**：diff 报告产出后**先看第 3 列"采集时间"的填充率** —— 若它接近 0，就是这个 bug，而不是能力差距。**这一列是 diff 工具的"自检仪表盘"，必须第一个看。**

### 翻车点 3 · 把"能力差距"误当成"映射写错"（design.md R2）

适配器的映射表是 24+ 条手工映射，**写错一条就污染整列的结论**（例如把 `Description` 映射到 `interfaces[].vpn.name`，第 5 列会显示 0% 而第 12 列显示一个看似合理的值 —— 极难发现）。

**排查方法**：报告里必须有"两侧都有值但不一致"的计数（`diff_samples` 的存在性与数量）。若某列填充率异常低 **且** 差异样本为空 → **映射很可能写错了**，而不是 netparse 抓不到。**先排除映射错误，再下"能力差距"的结论。**
