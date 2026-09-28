#!/usr/bin/env python3
"""Convert a Spike `-l --log-commits` log into the co-sim trace CSV.

Same columns as riscv-dv's trace CSV (so riscv-dv's instr_trace_compare.py can
read it) plus a `mem` column with the committed memory access:
    st:<addr>:<data>   store (data zero-extended to 64 bits)
    ld:<addr>          load

Only instructions from `--start` onward are kept (skips Spike's boot ROM), and
the trace is cut after the first store to `--tohost`, which is where the RTL
harness stops too.
"""
import argparse
import csv
import re
import sys

ABI = ["zero", "ra", "sp", "gp", "tp", "t0", "t1", "t2", "s0", "s1", "a0", "a1", "a2",
       "a3", "a4", "a5", "a6", "a7", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9",
       "s10", "s11", "t3", "t4", "t5", "t6"]

# core   0: 0x0000000080000000 (0x00000297) auipc   t0, 0x0
INSTR_RE = re.compile(r"core\s+\d+:\s+0x([0-9a-f]+)\s+\(0x([0-9a-f]+)\)\s+(.*)$")
# core   0: 3 0x0000000080000000 (0x00000297) x5  0x0000000080000000 mem 0x... 0x...
COMMIT_RE = re.compile(r"core\s+\d+:\s+(\d)\s+0x([0-9a-f]+)\s+\(0x([0-9a-f]+)\)(.*)$")
REG_RE = re.compile(r"\bx\s*(\d+)\s+0x([0-9a-f]+)")
MEM_RE = re.compile(r"\bmem\s+0x([0-9a-f]+)(?:\s+0x([0-9a-f]+))?")

FIELDS = ["pc", "instr", "gpr", "csr", "binary", "mode", "instr_str", "operand", "pad", "mem"]


EXC_RE = re.compile(r"core\s+\d+:\s+exception\s+(\S+),\s+epc\s+0x([0-9a-f]+)")


def parse(log_path, start, tohost):
    with open(log_path, errors="replace") as f:
        return parse_lines(f, start, tohost)[0]


def parse_lines(lines, start, tohost, max_instrs=None):
    """Parse Spike log lines. Returns (rows, end) where end is one of
    "tohost", "exception <name> @<epc>", "limit" or "eof". Bare-mode tests have
    no trap handler, so any exception after `start` means the program is bad."""
    rows = []
    disasm = {}
    started = False
    end = "eof"
    for line in lines:
        if started:
            e = EXC_RE.match(line)
            if e:
                end = "exception {} @{}".format(e.group(1), e.group(2))
                break
        m = COMMIT_RE.match(line)
        if m:
            mode, pc_s, bin_s, rest = m.groups()
            pc = int(pc_s, 16)
            if not started:
                if pc != start:
                    continue
                started = True
            gpr = ""
            r = REG_RE.search(rest)
            if r and int(r.group(1)) != 0:
                gpr = "{}:{:016x}".format(ABI[int(r.group(1))], int(r.group(2), 16))
            memf = ""
            mm = MEM_RE.search(rest)
            if mm:
                addr = int(mm.group(1), 16)
                if mm.group(2) is not None:
                    memf = "st:{:016x}:{:016x}".format(addr, int(mm.group(2), 16))
                else:
                    memf = "ld:{:016x}".format(addr)
            rows.append({"pc": "{:016x}".format(pc), "instr": "", "gpr": gpr, "csr": "",
                         "binary": "{:08x}".format(int(bin_s, 16)), "mode": mode,
                         "instr_str": disasm.get(pc, ""), "operand": "", "pad": "",
                         "mem": memf})
            if memf.startswith("st:") and tohost is not None and addr == tohost:
                end = "tohost"
                break
            if max_instrs and len(rows) >= max_instrs:
                end = "limit"
                break
            continue
        m = INSTR_RE.match(line)
        if m:
            disasm[int(m.group(1), 16)] = m.group(3).strip()
    return rows, end


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("csv")
    ap.add_argument("--start", type=lambda s: int(s, 0), default=0x80000000)
    ap.add_argument("--tohost", type=lambda s: int(s, 0), default=None)
    a = ap.parse_args()
    rows = parse(a.log, a.start, a.tohost)
    with open(a.csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("spike: {} instructions".format(len(rows)))
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
