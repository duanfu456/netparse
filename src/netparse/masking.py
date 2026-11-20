"""脱敏策略（能力 A · secret-mask）

两层机制（设计依据：.devforge/spec/changes/netparse-v3-diff-and-mask/design.md D4/D5/D6）

  L1 显式标注 —— 模板把字段声明成 `type(secret)` → 引擎写入时精确遮蔽。
  L2 形态兜底 —— 值本身长得像密文（华为 `%^%#...%^%#` 包裹 / 高熵长串）→ 遮蔽。
                 即使模板漏标也兜住；可用 `--no-mask-heuristic` 关闭。

**不做关键字猜测**：实测语料里 `default-password-template` 出现 19317 次（模板名，不是密码），
而 `shared-key-cipher` / `community read cipher` / `authentication-mode md5 cipher` 全都不含
"password" 字样（却是真密码）。按关键字猜会同时产生海量误报和漏报，所以只看**值本身的形态**。

掩码串固定（默认 `"******"`），不保留长度、不保留首尾字符 —— 长度和片段本身都是信息。
"""

import re
from functools import lru_cache

MASK_DEFAULT = "******"


class MaskingConfig:
    """一次运行的脱敏配置。

    为什么要有这个对象，而不是两个模块级全局
    ----------------------------------------
    原来 `MASK_VALUE` / `MASK_HEURISTIC` 是 `probe_stack` 模块内的两个全局变量，
    外部靠 `probe_stack.MASK_HEURISTIC = False` 改行为。搬进包以后，如果 `__init__.py` 里
    写 `from .engine import MASK_HEURISTIC`，赋值会落在包命名空间的**副本**上，
    引擎读的仍是 `engine` 模块里那个原变量 —— 表现为"设了没生效"，且不报错。
    所以把配置收进一个对象：只有一份实例，所有读写都经过它，不存在副本问题。

    用法::

        from netparse import masking
        masking.CONFIG.heuristic = False     # 只留 L1 显式标注
        masking.CONFIG.reset()               # 恢复默认
    """

    __slots__ = ("value", "heuristic")

    def __init__(self, value=MASK_DEFAULT, heuristic=True):
        self.value = value
        self.heuristic = heuristic

    def reset(self):
        self.value = MASK_DEFAULT
        self.heuristic = True

    def as_dict(self):
        return {"value": self.value, "heuristic": self.heuristic}

    def __repr__(self):
        return "<MaskingConfig value=%r heuristic=%r>" % (self.value, self.heuristic)


#: 全局唯一配置实例（引擎与 CLI 都读它）
CONFIG = MaskingConfig()

# ── L2 密文形态正则 ────────────────────────────────────
# ① 华为 VRP cipher 包裹形态（实测语料里华为密文 100% 是这个形态）
#    ⚠️ 这里的 ^ 必须转义：Python 的 re 里 ^ 永远是"行首锚点"（即便在模式中间），
#       不转义的话 `%^%#` 会被当成 `%` + 行首断言 + `%#` → 永远匹配不上。
_HUAWEI_CIPHER = re.compile(r"%\^%#.+?%\^%#")

# ② 高熵长串：长度 ≥ 24 **且** 字符集 ⊆ base64/十六进制 **且** 至少含 3 类字符
#    （大写 / 小写 / 数字各算一类；缺一类就放过 —— 小写十六进制校验和是误报重灾区）
# 无分隔符的拼音描述也可能只含大小写与数字，不能仅凭三类字符认定凭据。
_B64 = r"[A-Za-z0-9+/=]"
_HIGH_ENTROPY = re.compile(
    rf"(?={_B64}*[a-z])(?={_B64}*[A-Z])(?={_B64}*[0-9]){_B64}{{24,}}")

#: 密文形态正则列表。命中任一即视为凭据（L2 兜底）。
SECRET_SHAPE_PATTERNS = [_HUAWEI_CIPHER, _HIGH_ENTROPY]


@lru_cache(maxsize=32768)
def is_secret_shape(text):
    """L2 兜底：值本身是不是密文形态。

    带缓存（按 text 缓存）—— 8440 个文件 × 百万级捕获组，不缓存会明显变慢。
    """
    if not isinstance(text, str) or not text:
        return False
    for i, p in enumerate(SECRET_SHAPE_PATTERNS):
        if p.fullmatch(text):
            if i == 1:
                # 至少 6 个数字 / base64 符号：31 位拼音描述只有 2 个数字时不再误报；
                # 华为密文即使外层标记缺失，通常仍含足够多的非字母字符。
                if sum(not ch.isalpha() for ch in text) < 6:
                    continue
            return True
    return False


# design.md 的接口文档里这个函数叫 should_mask_by_shape，这里保留同名别名。
should_mask_by_shape = is_secret_shape


class MaskingStats:
    """一次运行的脱敏统计。

    l1_hits —— 被 L1（显式 type(secret)）遮蔽的值个数
    l2_hits —— 被 L2（形态兜底）遮蔽的值个数
              → 「L2 命中数远大于 L1」= 模板标注不完整，是模板完整度的度量
    """

    def __init__(self):
        self.l1_hits = 0
        self.l2_hits = 0

    def reset(self):
        self.l1_hits = 0
        self.l2_hits = 0

    def as_dict(self):
        return {"l1_hits": self.l1_hits, "l2_hits": self.l2_hits}

    def __repr__(self):
        return f"<MaskingStats l1={self.l1_hits} l2={self.l2_hits}>"


#: 模块级统计（run() 每次开始时复位 → 读完即"本次运行"的统计）
STATS = MaskingStats()


def reset_stats():
    """复位统计（run() 内部会调用；外部测试也可手动调用）。"""
    STATS.reset()


def mask_value(value, fld, mask=MASK_DEFAULT, heuristic=True, count=True):
    """判断并遮蔽单个值。返回遮蔽后的值；非凭据返回原值。

    L1 —— `fld.is_secret()` 为真 → 遮蔽（字段自己声明了"我是凭据"）
    L2 —— `heuristic` 为真且值命中密文形态 → 遮蔽（模板漏标时的兜底）

    幂等：值已经等于掩码串时原样返回、不重复计数。
    """
    if value is None:
        return value
    if fld is not None and getattr(fld, "is_secret", None) is not None and fld.is_secret():
        if value == mask:
            return value
        if count:
            STATS.l1_hits += 1
        return mask
    if heuristic:
        text = value if isinstance(value, str) else str(value)
        if is_secret_shape(text):
            if value == mask:
                return value
            if count:
                STATS.l2_hits += 1
            return mask
    return value
