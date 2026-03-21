from __future__ import annotations

from typing import TypeAlias

NormalizedMap: TypeAlias = dict[str, "NormalizedValue"]
NormalizedList: TypeAlias = list["NormalizedValue"]
NormalizedScalar: TypeAlias = None | bool | int | float | str
NormalizedValue: TypeAlias = NormalizedScalar | NormalizedList | NormalizedMap
GroupKey: TypeAlias = tuple[str, str, int]
