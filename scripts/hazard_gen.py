#!/usr/bin/env python3
"""Constrained-random hazard program generator for an out-of-order core.

riscv-dv's streams exercise the ISA broadly; this generator concentrates on the
cases that break OoO machinery. Each program is a sequence of scenarios drawn
with weights from SCENARIOS, all built from a deliberately small register pool
and a small memory window so dependences and address aliasing are dense:

  raw_chain      back-to-back true dependences (dependency distance 1..3)
  waw_mixed      a slow load and a fast ALU op writing the same register
  load_use       load immediately consumed, including by a store's data
  st_ld_fwd      store then load to an overlapping address, mixed sizes
                 (full, partial and non-forwardable overlaps)
  st_st_ld       two stores to the same bytes then a load (youngest must win)
  ld_branch      branch whose operand comes from a pending load
  ld_jalr        indirect jump through a loaded address
  rob_fill       one load-dependent chain plus many independent ops behind it
  sq_fill        more stores than the store queue holds, then loads
  x0_dest        loads and ALU ops targeting x0
  csr_mix        CSR read/write between memory traffic (serializing ops)
  loop           short counted loop around a random hazard body

Output is a directed test using tests/directed/common.h.
"""
import argparse
import random
import sys

POOL_ALL = ["a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7", "t0", "t1", "t2", "t3", "t4",
            "s1", "s2", "s3", "s4", "s5", "s6", "s7"]
BASE = "s0"      # points at the memory window
LOOPCNT = "s8"   # loop counter, never in the pool
JREG = "s9"      # scratch for computed jump targets

ALU_RR = ["add", "sub", "sll", "slt", "sltu", "xor", "srl", "sra", "or", "and",
          "addw", "subw", "sllw", "srlw", "sraw"]
ALU_RI = ["addi", "slti", "sltiu", "xori", "ori", "andi", "addiw"]
SHIFT_I = ["slli", "srli", "srai"]
SHIFT_IW = ["slliw", "srliw", "sraiw"]
LOADS = {"lb": 1, "lbu": 1, "lh": 2, "lhu": 2, "lw": 4, "lwu": 4, "ld": 8}
STORES = {"sb": 1, "sh": 2, "sw": 4, "sd": 8}
BRANCHES = ["beq", "bne", "blt", "bge", "bltu", "bgeu"]


class Gen:
    def __init__(self, seed, window, pool_size, aligned_only, extra_alu=None, extra_long=None):
        self.r = random.Random(seed)
        self.window = window
        self.pool = self.r.sample(POOL_ALL, pool_size)
        self.aligned_only = aligned_only
        self.alu_rr = ALU_RR + (extra_alu or [])
        self.long_ops = extra_long or []   # e.g. mul/div once the M extension is in
        self.lines = []
        self.label_n = 0
        self.stats = {}

    # ---- helpers ----
    def emit(self, s):
        self.lines.append("    " + s)

    def label(self):
        self.label_n += 1
        return "L{}".format(self.label_n)

    def reg(self, avoid=()):
        return self.r.choice([x for x in self.pool if x not in avoid] or self.pool)

    def offset(self, size):
        hi = self.window - size
        if self.aligned_only or self.r.random() < 0.7:
            return self.r.randrange(0, hi + 1, size)
        return self.r.randint(0, hi)

    def alu(self, rd=None, srcs=None):
        rd = rd or self.reg()
        srcs = srcs or [self.reg(), self.reg()]
        kind = self.r.random()
        if self.long_ops and kind < 0.15:
            self.emit("{} {}, {}, {}".format(self.r.choice(self.long_ops), rd, srcs[0], srcs[1]))
        elif kind < 0.6:
            self.emit("{} {}, {}, {}".format(self.r.choice(self.alu_rr), rd, srcs[0], srcs[1]))
        elif kind < 0.85:
            self.emit("{} {}, {}, {}".format(self.r.choice(ALU_RI), rd, srcs[0],
                                             self.r.randint(-2048, 2047)))
        elif kind < 0.95:
            self.emit("{} {}, {}, {}".format(self.r.choice(SHIFT_I), rd, srcs[0], self.r.randint(0, 63)))
        else:
            self.emit("{} {}, {}, {}".format(self.r.choice(SHIFT_IW), rd, srcs[0], self.r.randint(0, 31)))
        return rd

    def load(self, rd=None, op=None, off=None):
        op = op or self.r.choice(list(LOADS))
        rd = rd or self.reg()
        off = self.offset(LOADS[op]) if off is None else off
        self.emit("{} {}, {}({})".format(op, rd, off, BASE))
        return rd

    def store(self, rs=None, op=None, off=None):
        op = op or self.r.choice(list(STORES))
        rs = rs or self.reg()
        off = self.offset(STORES[op]) if off is None else off
        self.emit("{} {}, {}({})".format(op, rs, off, BASE))
        return off, STORES[op]

    # ---- scenarios ----
    def raw_chain(self):
        prev = self.reg()
        for _ in range(self.r.randint(4, 12)):
            dist_srcs = [prev, self.reg()]
            self.r.shuffle(dist_srcs)
            prev = self.alu(srcs=dist_srcs)
            if self.r.random() < 0.3:
                self.alu()  # distance-2 dependence

    def waw_mixed(self):
        rd = self.reg()
        self.load(rd=rd)
        if self.r.random() < 0.5:
            self.alu()
        self.alu(rd=rd)                 # younger, faster write to the same arch reg
        self.alu(srcs=[rd, self.reg()])  # must see the ALU value, not the load

    def load_use(self):
        rd = self.load()
        c = self.r.random()
        if c < 0.4:
            self.alu(srcs=[rd, self.reg()])
        elif c < 0.7:
            self.store(rs=rd)           # loaded value feeds store data
        else:
            self.emit("addi {}, {}, 0".format(self.reg(), rd))
            self.load()

    def st_ld_fwd(self):
        sop = self.r.choice(list(STORES))
        soff, ssz = self.store(op=sop)
        lop = self.r.choice(list(LOADS))
        lsz = LOADS[lop]
        mode = self.r.random()
        if mode < 0.4:   # contained: forwardable in principle
            lo, hi = soff, soff + ssz - lsz
        else:            # partial overlap, either side
            lo, hi = soff - lsz + 1, soff + ssz - 1
        lo, hi = max(lo, 0), min(hi, self.window - lsz)
        if lo > hi:
            lo, hi = 0, self.window - lsz
        off = self.r.randint(lo, hi)
        if self.aligned_only:
            off -= off % lsz
        for _ in range(self.r.randint(0, 3)):
            self.alu()
        self.load(op=lop, off=off)

    def st_st_ld(self):
        op = self.r.choice(list(STORES))
        off = self.offset(STORES[op])
        self.store(op=op, off=off)
        if self.r.random() < 0.5:
            self.alu()
        self.store(op=self.r.choice(list(STORES)), off=off)
        lop = self.r.choice(list(LOADS))
        self.load(op=lop, off=min(off, self.window - LOADS[lop]))

    def ld_branch(self):
        a = self.load()
        b = self.reg() if self.r.random() < 0.7 else self.load()
        skip = self.label()
        self.emit("{} {}, {}, {}".format(self.r.choice(BRANCHES), a, b, skip))
        for _ in range(self.r.randint(1, 4)):
            self.alu() if self.r.random() < 0.7 else self.store()
        self.lines.append(skip + ":")

    def ld_jalr(self):
        tgt = self.label()
        slot = self.r.randrange(0, self.window - 8 + 1, 8)
        self.emit("la {}, {}".format(JREG, tgt))
        self.emit("sd {}, {}({})".format(JREG, slot, BASE))
        for _ in range(self.r.randint(0, 3)):
            self.alu()
        self.emit("ld {}, {}({})".format(JREG, slot, BASE))
        self.emit("jalr {}, 0({})".format(self.r.choice(["ra", "zero"]), JREG))
        self.alu()   # skipped
        self.lines.append(tgt + ":")

    def rob_fill(self):
        head = self.load()
        self.alu(srcs=[head, head])
        for _ in range(self.r.randint(24, 40)):
            self.alu(srcs=[self.reg(avoid=[head]), self.reg(avoid=[head])])

    def sq_fill(self):
        for _ in range(self.r.randint(8, 14)):
            self.store()
        for _ in range(self.r.randint(2, 6)):
            self.load()

    def x0_dest(self):
        self.load(rd="zero")
        self.alu(rd="zero")
        self.alu(srcs=["zero", self.reg()])

    def csr_mix(self):
        rd = self.reg()
        self.store()
        self.emit("csrrw {}, mscratch, {}".format(rd, self.reg()))
        self.load()
        self.emit("csrr {}, mscratch".format(self.reg()))
        self.emit("csrrsi {}, mscratch, {}".format(self.reg(), self.r.randint(0, 31)))

    def loop(self):
        top = self.label()
        self.emit("li {}, {}".format(LOOPCNT, self.r.randint(2, 6)))
        self.lines.append(top + ":")
        for _ in range(self.r.randint(2, 4)):
            self.pick(allow_loop=False)()
        self.emit("addi {0}, {0}, -1".format(LOOPCNT))
        self.emit("bnez {}, {}".format(LOOPCNT, top))

    SCENARIOS = {"raw_chain": 10, "waw_mixed": 8, "load_use": 10, "st_ld_fwd": 16, "st_st_ld": 8,
                 "ld_branch": 8, "ld_jalr": 4, "rob_fill": 4, "sq_fill": 5, "x0_dest": 3,
                 "csr_mix": 3, "loop": 5}

    def pick(self, allow_loop=True):
        names = [n for n in self.SCENARIOS if allow_loop or n != "loop"]
        name = self.r.choices(names, weights=[self.SCENARIOS[n] for n in names])[0]
        self.stats[name] = self.stats.get(name, 0) + 1
        return getattr(self, name)

    def program(self, n_scenarios):
        self.lines = ['#include "common.h"', "", "TEST_BEGIN",
                      "    la {}, window".format(BASE)]
        # random but reproducible initial register values
        for x in POOL_ALL:
            self.emit("li {}, {:#x}".format(x, self.r.choice([
                0, 1, -1 & (2**64 - 1), 0x8000000000000000, self.r.getrandbits(64), self.r.getrandbits(12)])))
        for i in range(0, self.window, 8):
            self.emit("sd {}, {}({})".format(self.r.choice(POOL_ALL), i, BASE))
        for _ in range(n_scenarios):
            self.pick()()
        self.lines += ["TEST_END", "", "    .data", "    .align 6",
                       "window: .zero {}".format(self.window)]
        return "\n".join(self.lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--scenarios", type=int, default=400)
    ap.add_argument("--window", type=int, default=64, help="bytes of memory all accesses fall in")
    ap.add_argument("--pool", type=int, default=6, help="registers the scenarios draw from")
    ap.add_argument("--aligned-only", action="store_true")
    ap.add_argument("--m-ext", action="store_true", help="mix in mul/div (long-latency ops)")
    a = ap.parse_args()
    extra_alu, extra_long = None, None
    if a.m_ext:
        extra_alu = ["mul", "mulw"]
        extra_long = ["mul", "mulh", "mulhsu", "mulhu", "div", "divu", "rem", "remu",
                      "mulw", "divw", "divuw", "remw", "remuw"]
    g = Gen(a.seed, a.window, a.pool, a.aligned_only, extra_alu, extra_long)
    with open(a.out, "w") as f:
        f.write("// generated: hazard_gen.py --seed {} --scenarios {} --window {} --pool {}{}\n".format(
            a.seed, a.scenarios, a.window, a.pool, " --aligned-only" if a.aligned_only else ""))
        f.write(g.program(a.scenarios))
    print(" ".join("{}={}".format(k, v) for k, v in sorted(g.stats.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
