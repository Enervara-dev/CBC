"""
<PANEL> reference-range seed data.

Return the reference-range rows for this panel from ``reference_range_rows``.
Each row is a dict matching ``db.models.ReferenceRange`` columns. Use
``domains/cbc/reference_ranges.py`` as the reference (it shows the ``_rr`` helper,
age-band convention, and sex-specific vs unisex rows).
"""

from __future__ import annotations

from typing import Any, Dict, List


def reference_range_rows() -> List[Dict[str, Any]]:
    """The full set of seed reference-range rows for this panel."""
    return []
