#!/usr/bin/env python3
"""Markdown summary of a regress.json (used for the GitHub Actions job summary)."""
import collections
import json
import sys


def main(path):
    d = json.load(open(path))
    by_test = collections.OrderedDict()
    for r in d["results"]:
        t = r.get("test", r["name"].rsplit(".", 1)[0])
        s = by_test.setdefault(t, {"runs": 0, "pass": 0, "instrs": 0, "cycles": 0, "fails": []})
        s["runs"] += 1
        s["instrs"] += r.get("instrs", 0)
        s["cycles"] += r.get("cycles", 0)
        if r["status"] == "PASS":
            s["pass"] += 1
        else:
            s["fails"].append(r)
    print("## Co-simulation regression\n")
    print("**{} / {} passed**, {:,} instructions compared against Spike\n".format(
        d["passed"], d["runs"], d["instructions"]))
    print("| test | runs | pass | instructions | IPC |")
    print("|---|---:|---:|---:|---:|")
    for t, s in by_test.items():
        ipc = s["instrs"] / s["cycles"] if s["cycles"] else 0
        print("| {} | {} | {} | {:,} | {:.2f} |".format(t, s["runs"], s["pass"], s["instrs"], ipc))
    fails = [r for s in by_test.values() for r in s["fails"]]
    if fails:
        print("\n### Failures\n")
        for r in fails:
            print("- `{}` {}: {}".format(r["name"], r["status"], r.get("detail", "")))


if __name__ == "__main__":
    main(sys.argv[1])
