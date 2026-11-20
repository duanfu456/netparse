"""兼容 shim：脱敏策略已迁入 `netparse.masking`。

新代码请用 `from netparse import masking` 或 `from netparse import mask_value`。
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "src"))

from netparse.masking import *          # noqa: F401,F403
from netparse.masking import (          # noqa: F401
    CONFIG, MASK_DEFAULT, SECRET_SHAPE_PATTERNS, STATS, MaskingConfig,
    MaskingStats, is_secret_shape, mask_value, reset_stats, should_mask_by_shape,
)
