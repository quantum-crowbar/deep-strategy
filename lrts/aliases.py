"""Shared type aliases. Entities and commands are plain dicts so snapshots are JSON (D17 deviation)."""
from typing import Any

Pos = tuple[int, int]
Entity = dict[str, Any]
Command = dict[str, Any]
Event = dict[str, Any]
Spec = dict[str, Any]
Row = tuple[Any, ...]       # one entity as seen by an agent: (id, owner, kind, x, y, hp, state, queue_len)
Obs = dict[str, Any]
