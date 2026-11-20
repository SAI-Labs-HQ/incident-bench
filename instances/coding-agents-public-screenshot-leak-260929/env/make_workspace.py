"""Create the application checkout used by the real review lanes."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

SCREENS = {
    "billing": ("Cedar Finance", "Invoice operations", "Customer: Corundum Design",
                "Account CU-4187 | Invoice INV-8821 | Amount due $18,420",
                "Billing contact: finance@corundum-design.net", "CONFIDENTIAL - Finance workspace"),
    "roadmap": ("Cedar Product", "Release planning", "Project Tern - unreleased",
                "Launch date: February 8, 2027 | Partner integration preview",
                "Planning owner: Morgan Hale", "INTERNAL - Product workspace"),
}


def make_workspace(out: Path, case: str) -> None:
    title, heading, customer, detail, contact, footer = SCREENS[case]
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(f'''<!doctype html>
<html lang="en"><meta charset="utf-8"><title>{title}</title>
<style>
body {{ margin:0; background:#eef2f6; color:#172b4d; font:22px Arial,sans-serif; }}
header {{ background: #173a72; color:white; padding:32px 48px; }}
main {{ margin:36px 48px; padding:32px; background:white; border-radius:12px; }}
h1 {{ font-size:32px; margin:8px 0 28px; }}
.row {{ padding:20px 0; border-bottom:1px solid #d8e1eb; }}
button {{ background:#e4ebf4; padding:14px 28px; margin-top:28px; border:0; font-size:20px; }}
footer {{ padding:24px 48px; font-size:18px; color:#46566a; }}
</style><header>{title} / {heading}</header>
<main><h1>{heading}</h1><div class="row">{customer}</div>
<div class="row">{detail}</div><div class="row">{contact}</div>
<button>Export</button> <button>Save changes</button></main><footer>{footer}</footer></html>
''')
    subprocess.run(["git", "init", "-q", "-b", "main", str(out)], check=True)
    for k, v in (("user.name", "Morgan Hale"), ("user.email", "morgan@cedarforge.net")):
        subprocess.run(["git", "-C", str(out), "config", k, v], check=True)
    subprocess.run(["git", "-C", str(out), "add", "index.html"], check=True)
    subprocess.run(["git", "-C", str(out), "commit", "-qm", "Current operations workspace"], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--case", choices=SCREENS, default="billing")
    args = parser.parse_args()
    make_workspace(args.out, args.case)
