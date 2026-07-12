#!/usr/bin/env python3
"""
Ash domain overview — the parser behind scry.sh.

Greps a lib/ tree for `use Ash.Domain` modules and prints one line per
domain: module name, resource count, first sentence of @moduledoc. No
mix/BEAM boot required — pure text parsing, meant to run in well under
a second at session start.

Usage:
    python3 scry_domains.py [--path lib]
"""

import argparse
import os
import re

MODULE_RE = re.compile(r"^\s*defmodule\s+([\w.]+)\s+do")
MODULEDOC_RE = re.compile(r'@moduledoc\s+"""\s*\n(.*?)\n\s*"""', re.DOTALL)
RESOURCE_RE = re.compile(r"^\s*resource\s+[\w.]+", re.MULTILINE)


def first_sentence(doc: str) -> str:
    doc = doc.strip()
    if not doc:
        return ""
    # Collapse to the first paragraph, then the first sentence within it.
    paragraph = re.sub(r"\s+", " ", doc.split("\n\n", 1)[0]).strip()
    match = re.search(r"^.*?[.!?](?=\s|$)", paragraph)
    return (match.group(0) if match else paragraph).strip()


def find_domains(base_path: str) -> list[dict]:
    domains = []
    for root, dirs, files in os.walk(base_path):
        dirs[:] = [d for d in dirs if d not in {"_build", "deps"}]
        for file in files:
            if not file.endswith(".ex"):
                continue
            filepath = os.path.join(root, file)
            try:
                with open(filepath, encoding="utf-8") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            if "use Ash.Domain" not in content:
                continue

            module_match = MODULE_RE.search(content)
            if not module_match:
                continue

            doc_match = MODULEDOC_RE.search(content)
            description = first_sentence(doc_match.group(1)) if doc_match else ""
            resource_count = len(RESOURCE_RE.findall(content))

            domains.append(
                {
                    "module": module_match.group(1),
                    "resources": resource_count,
                    "description": description,
                }
            )

    domains.sort(key=lambda d: d["module"])
    return domains


def main():
    parser = argparse.ArgumentParser(description="Ash domain overview")
    parser.add_argument("--path", default="lib", help="Base path to scan")
    args = parser.parse_args()

    domains = find_domains(args.path)
    if not domains:
        return

    print(f"Ash domains ({len(domains)}):")
    for d in domains:
        label = f"{d['resources']} resource" + ("s" if d["resources"] != 1 else "")
        desc = f" — {d['description']}" if d["description"] else ""
        print(f"  {d['module']} ({label}){desc}")


if __name__ == "__main__":
    main()
