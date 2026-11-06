"""Build TruckSimEnv's replay Space (PLAN.md M11): a static Space holding one collect_runs.py site, as is.

The site (results/site/NAME/, written by collect_runs.py) is already static: the runs app (index.html), each run's
replay (runs/*.json), the 3D player and the trailer with their vendored libraries and trucks. The Space adds its card,
hub/space/README.md with its {{PLACEHOLDERS}} filled.

Usage: .venv/bin/python scripts/build_space.py SITE_NAME OUT_DIR --github OWNER/NAME --hf OWNER/NAME --version vX.Y.Z"""

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "hub" / "space" / "README.md"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("site_name")
    ap.add_argument("out_dir", type=Path)
    for flag in ("--github", "--hf", "--version"):
        ap.add_argument(flag, required=True)
    args = ap.parse_args()
    site = ROOT / "results" / "site" / args.site_name
    if not (site / "index.html").is_file() or not (site / "site.json").is_file():
        raise SystemExit(f"{site} is not a collect_runs.py site (no index.html and site.json)")
    if args.out_dir.exists():
        shutil.rmtree(args.out_dir)
    shutil.copytree(site, args.out_dir)
    card = CARD.read_text()
    for key, value in {"GITHUB": args.github, "HF_REPO": args.hf, "VERSION": args.version,
                       "HF_LINK": f"https://huggingface.co/datasets/{args.hf}"}.items():
        card = card.replace("{{" + key + "}}", value)
    left = sorted(set(re.findall(r"\{\{([A-Z_]+)\}\}", card)))
    if left:
        raise SystemExit(f"the Space card's placeholders {left} have no value")
    (args.out_dir / "README.md").write_text(card)
    runs = len(list((args.out_dir / "runs").glob("*.json")))
    size = sum(f.stat().st_size for f in args.out_dir.rglob("*") if f.is_file()) / 1e6
    print(f"wrote {args.out_dir}: {runs} runs, {size:.1f} MB")


if __name__ == "__main__":
    main()
