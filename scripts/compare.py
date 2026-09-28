#!/usr/bin/env python3
"""Lockstep compare of two co-sim trace CSVs, one row per retired instruction.

Unlike riscv-dv's instr_trace_compare.py (which only looks at GPR values that
change), every retired instruction must agree on pc, encoding, destination
register write and memory access. The first divergence is what matters for
debug, so by default the compare stops after --max-mismatch mismatches.
"""
import argparse
import csv
import sys

KEYS = ("pc", "binary", "gpr", "mem")


def load(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def fmt(row):
    s = "pc={} bin={} {}".format(row["pc"], row["binary"], row.get("instr_str") or "")
    if row["gpr"]:
        s += " | " + row["gpr"]
    if row.get("mem"):
        s += " | " + row["mem"]
    return s


def compare(ref, dut, max_mismatch=1, context=3):
    mismatches = []
    n = min(len(ref), len(dut))
    for i in range(n):
        r, d = ref[i], dut[i]
        diff = [k for k in KEYS if (r.get(k) or "") != (d.get(k) or "")]
        if diff:
            mismatches.append((i, diff))
            if len(mismatches) >= max_mismatch:
                break
    lines = []
    for i, diff in mismatches:
        lines.append("MISMATCH at retired instruction #{} (fields: {})".format(i, ", ".join(diff)))
        for j in range(max(0, i - context), i):
            lines.append("    ok   {}".format(fmt(ref[j])))
        lines.append("  spike  {}".format(fmt(ref[i])))
        lines.append("  rtl    {}".format(fmt(dut[i])))
    if not mismatches and len(ref) != len(dut):
        short, name = (dut, "rtl") if len(dut) < len(ref) else (ref, "spike")
        lines.append("LENGTH MISMATCH: spike retired {}, rtl retired {} ({} stopped early)".format(
            len(ref), len(dut), name))
        if len(dut) < len(ref):
            lines.append("  next spike instruction: {}".format(fmt(ref[len(dut)])))
        mismatches.append((n, ["length"]))
    return mismatches, lines


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ref_csv", help="Spike trace CSV")
    ap.add_argument("dut_csv", help="RTL trace CSV")
    ap.add_argument("--max-mismatch", type=int, default=1)
    a = ap.parse_args()
    ref, dut = load(a.ref_csv), load(a.dut_csv)
    mismatches, lines = compare(ref, dut, a.max_mismatch)
    for l in lines:
        print(l)
    if mismatches:
        print("[FAILED] {} of {} instructions compared".format(mismatches[0][0], len(ref)))
        return 1
    print("[PASSED] {} instructions match".format(len(ref)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
