# -*- coding: utf-8 -*-
from __future__ import annotations

"""
===================================
股票代码与名称映射
===================================

Shared stock code -> name mapping, used by analyzer, data_provider, and name_to_code_resolver.
"""

# Re-export from the canonical source in data_provider/ to keep existing
# importers working while data_provider/ no longer depends on src/.
from data_provider._stock_names import (  # noqa: F401
    STOCK_NAME_MAP,
    is_meaningful_stock_name,
)
