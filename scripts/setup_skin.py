"""Copy the CC0 Tiny Swords sprites (Pixel Frog, via ejaj-boj) into viewer/skins/tinyswords/.
Sprites are not committed to this repo (D23); the placeholder skin always works without them.
Usage: python scripts/setup_skin.py [path/to/ejaj-boj/client/assets]"""
import shutil
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
src = Path(sys.argv[1]) if len(sys.argv) > 1 else root.parent / "ejaj-boj" / "client" / "assets"
if src.name == "assets":
    src = src / "sprites"
dst = root / "viewer" / "skins" / "tinyswords"
dst.mkdir(parents=True, exist_ok=True)
n = 0
for color, side in (("yellow", "p0"), ("black", "p1")):
    for name in ("castle", "barracks", "pawn", "warrior"):
        f = src / color / f"{name}.png"
        if f.exists():
            shutil.copy(f, dst / f"{side}_{name}.png")
            n += 1
print(f"copied {n} sprite files to {dst}")
