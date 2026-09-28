#!/usr/bin/env python3
"""Checker qualification: inject known bugs into a copy of the core and confirm
the co-sim flow catches each one.

A regression that passes proves little unless it is shown to fail on broken
RTL. Each mutation below is a realistic OoO bug; for each, the core is copied,
patched, rebuilt, and run against a fixed set of hazard programs plus the smoke
test. A mutation is "killed" if any program mismatches or hangs.

Removing lsq.sv's same-cycle store address/commit bypass was tried and is an
equivalent mutant in this core: rob.sv commits from the registered done_q, so
a store can never issue and commit in the same cycle and the bypass is
unreachable. It is left out of the list for that reason.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

MUTATIONS = [
    ("fwd_partial", "rtl/lsq/lsq.sv",
     "if (any_overlap && yv_full) begin",
     "if (any_overlap) begin",
     "forward from a store that only partially covers the load"),
    ("fwd_oldest_store", "rtl/lsq/lsq.sv",
     "if (!any_overlap || age_f(sq_rob[sj]) > yv_age) begin",
     "if (!any_overlap || age_f(sq_rob[sj]) < yv_age) begin",
     "forward from the oldest overlapping store instead of the youngest"),
    ("fwd_ignore_unknown", "rtl/lsq/lsq.sv",
     "blocked_unknown = 1'b1;",
     "blocked_unknown = 1'b0;",
     "let loads pass older stores whose address is still unknown"),
    ("load_not_busy", "rtl/ooo/ooo_core.sv",
     "if (dispatch_fire && (prd0 != 7'd0)) ready_q[prd0[5:0]] <= 1'b0;",
     "if (dispatch_fire && (prd0 != 7'd0) && !is_load_d) ready_q[prd0[5:0]] <= 1'b0;",
     "load destinations never marked not-ready, so consumers read a stale value"),
    ("sraw_shamt6", "rtl/alu.sv",
     "4'h7: yw = $signed(a[31:0]) >>> b[4:0];",
     "4'h7: yw = $signed(a[31:0]) >>> b[5:0];",
     "SRAW/SRAIW use a 6-bit shift amount"),
    ("lhu_sign_extends", "rtl/lsq/lsq.sv",
     "2'd1: extend_f = sgn ? {{48{raw[15]}}, raw[15:0]} : {48'd0, raw[15:0]};",
     "2'd1: extend_f = {{48{raw[15]}}, raw[15:0]};",
     "LHU sign-extends"),
    ("free_new_preg", "rtl/ooo/ooo_core.sv",
     ".commit_valid0(rob_commit_valid[0]), .commit_prev_prd0(rob_commit_prev_prd[0]),",
     ".commit_valid0(rob_commit_valid[0]), .commit_prev_prd0(rob_commit_prd[0]),",
     "commit frees the new physical register instead of the previous mapping"),
    ("branch_bltu_signed", "rtl/ooo/ooo_core.sv",
     "3'h6: branch_taken = (rs1_val <  rs2_val);",
     "3'h6: branch_taken = ($signed(rs1_val) < $signed(rs2_val));",
     "BLTU compares signed"),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--core", default=os.environ.get("CORE", os.path.join(ROOT, "core")))
    ap.add_argument("--seeds", type=int, default=6)
    ap.add_argument("--only", action="append")
    a = ap.parse_args()

    work = tempfile.mkdtemp(prefix="mutcheck_")
    progs = [os.path.join(ROOT, "tests", "directed", "smoke.S")]
    for s in range(1, a.seeds + 1):
        for tag, opts in (("mix", []), ("tight", ["--pool", "3", "--window", "16"])):
            p = os.path.join(work, "hazard_{}_{}.S".format(tag, s))
            subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "hazard_gen.py"), p,
                            "--seed", str(s), "--scenarios", "300"] + opts,
                           check=True, stdout=subprocess.DEVNULL)
            progs.append(p)

    killed = 0
    rows = []
    for name, rel, old, new, desc in MUTATIONS:
        if a.only and name not in a.only:
            continue
        core = os.path.join(work, "core_" + name)
        shutil.copytree(os.path.join(a.core, "rtl"), os.path.join(core, "rtl"))
        path = os.path.join(core, rel)
        src = open(path).read()
        if src.count(old) != 1:
            rows.append((name, "STALE", "pattern not found once in " + rel))
            print("{:<20} {:<9} {}".format(*rows[-1]), flush=True)
            continue
        open(path, "w").write(src.replace(old, new))
        build = os.path.join(work, "build_" + name)
        env = dict(os.environ, CORE=core, BUILD=build)
        rc = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "cosim.py"), "build"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        if rc != 0:
            rows.append((name, "BUILD_FAIL", desc))
            print("{:<20} {:<9} {}".format(*rows[-1]), flush=True)
            continue
        out = os.path.join(work, "out_" + name)
        p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "cosim.py"), "-o", out, "run"] + progs,
                           env=env, stdout=subprocess.PIPE, text=True)
        fails = [l for l in p.stdout.splitlines() if l.split() and l.split()[1] != "PASS"]
        if fails:
            killed += 1
            first = fails[0].split(None, 2)
            rows.append((name, "KILLED", "{} ({}/{} programs fail, first: {} {})".format(
                desc, len(fails), len(progs), first[0], first[1])))
        else:
            rows.append((name, "SURVIVED", desc))
        print("{:<20} {:<9} {}".format(*rows[-1]), flush=True)

    print("\n{} / {} mutations killed".format(killed, len(rows)))
    shutil.rmtree(work, ignore_errors=True)
    return 0 if killed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
