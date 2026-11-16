"""Load the rules table (data/rules.toml). Rules are data, not code (D13)."""
import hashlib
import tomllib
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "rules.toml"


class Rules:
    def __init__(self, path=DEFAULT_PATH):
        raw = Path(path).read_bytes()
        self.hash = hashlib.sha256(raw).hexdigest()[:12]  # goes into every replay header
        d = tomllib.loads(raw.decode())
        self.version = d["version"]
        self.game = d["game"]
        self.units = d["units"]
        self.buildings = d["buildings"]
        self.counters = d["counters"]
        self.unit_names = sorted(self.units)
        self.military = [u for u in self.unit_names if self.units[u]["damage"] > 0]

    def damage(self, attacker_kind, defender_kind):
        base = self.units[attacker_kind]["damage"]
        pct = self.counters.get(f"{attacker_kind}>{defender_kind}", 100)
        return max(1, base * pct // 100)  # integer math only
