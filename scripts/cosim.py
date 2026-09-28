#!/usr/bin/env python3
"""Spike co-simulation flow for rv64-ooo-core.

    cosim.py build                         build the Verilator model
    cosim.py run  tests/directed/smoke.S   compile + Spike + RTL + compare one program
    cosim.py gen  riscv_arithmetic_basic_test --seed 7   generate with riscv-dv, then run
    cosim.py regress [--iterations N] [--jobs J] [--budget MIN]   testlist regression

Every test produces <out>/<name>/{test.elf, spike.log, spike.csv, rtl.csv, compare.log}.
"""
import argparse
import concurrent.futures as cf
import csv
import itertools
import json
import os
import random
import shlex
import shutil
import subprocess
import sys
import time

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import compare as cmp  # noqa: E402
import spike_trace  # noqa: E402

RDV = os.environ.get("RISCV_DV", os.path.join(ROOT, "riscv-dv"))
CORE = os.environ.get("CORE", os.path.join(ROOT, "core"))
BUILD = os.environ.get("BUILD", os.path.join(ROOT, "build"))
RTL_BIN = os.path.join(BUILD, "rtl", "Vooo_top")
GCC = os.environ.get("RISCV_GCC", "riscv64-unknown-elf-gcc")
NM = GCC.replace("gcc", "nm")
SPIKE = os.environ.get("SPIKE", "spike")

TARGET = "rv64i_ooo"
MARCH = "rv64i_zicsr_zifencei"
MABI = "lp64"
SPIKE_ISA = "rv64i_zicsr_zifencei"
RESET_PC = 0x80000000

# Options every generated test needs on this core: no traps or privilege changes
# exist yet, so programs run bare in M-mode and avoid ecall/ebreak/wfi/dret.
BASE_GEN_OPTS = ["--bare_program_mode=1", "--boot_mode=m", "--disable_compressed_instr=1",
                 "--no_csr_instr=1", "--no_ebreak=1", "--no_wfi=1", "--no_dret=1",
                 "--illegal_instr_ratio=0"]


def sh(cmd, log=None, timeout=None, cwd=None):
    """Run cmd (list), tee combined output into log. Returns (rc, output)."""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, cwd=cwd, text=True, errors="replace")
        rc, out = p.returncode, p.stdout
    except subprocess.TimeoutExpired as e:
        rc, out = -1, (e.stdout or "") + "\nTIMEOUT after {}s\n".format(timeout)
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
    if log:
        with open(log, "a") as f:
            f.write("$ " + " ".join(shlex.quote(c) for c in cmd) + "\n" + out + "\n")
    return rc, out


def build(verbose=True):
    rc, out = sh(["make", "-C", os.path.join(ROOT, "tb"), "CORE=" + CORE,
                  "BUILD=" + os.path.join(BUILD, "rtl")])
    if rc != 0 or verbose:
        print(out[-4000:])
    return rc


def compile_elf(src, elf, log):
    cmd = [GCC, "-march=" + MARCH, "-mabi=" + MABI, "-static", "-mcmodel=medany",
           "-fvisibility=hidden", "-nostdlib", "-nostartfiles",
           "-T", os.path.join(ROOT, "dv", "link.ld"),
           "-I", os.path.join(RDV, "user_extension"), "-I", os.path.join(ROOT, "tests", "directed"),
           src, "-o", elf]
    return sh(cmd, log)[0]


def symbol(elf, name):
    rc, out = sh([NM, elf])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == name:
            return int(parts[0], 16)
    return None


def run_program(src, outdir, timeout=300):
    """Compile (if needed), run Spike and RTL, compare. Returns a result dict."""
    os.makedirs(outdir, exist_ok=True)
    log = os.path.join(outdir, "compare.log")
    open(log, "w").close()
    res = {"name": os.path.basename(outdir), "src": src, "status": "PASS", "detail": "",
           "instrs": 0, "cycles": 0}
    t0 = time.time()

    elf = src if src.endswith(".elf") else os.path.join(outdir, "test.elf")
    if elf is not src and compile_elf(src, elf, log) != 0:
        return dict(res, status="COMPILE_FAIL", detail="gcc failed")
    tohost = symbol(elf, "tohost")
    if tohost is None:
        return dict(res, status="COMPILE_FAIL", detail="no tohost symbol")

    spike_log = os.path.join(outdir, "spike.log")
    rc, _ = sh([SPIKE, "--isa=" + SPIKE_ISA, "--priv=m", "--misaligned",
                "-l", "--log-commits", "--log=" + spike_log, elf], log, timeout=timeout)
    if rc != 0:
        return dict(res, status="SPIKE_FAIL", detail="spike rc={}".format(rc))
    spike_csv = os.path.join(outdir, "spike.csv")
    rows = spike_trace.parse(spike_log, RESET_PC, tohost)
    with open(spike_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=spike_trace.FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    # the ISS log is large and fully reproducible from the ELF
    os.remove(spike_log)

    rtl_csv = os.path.join(outdir, "rtl.csv")
    rc, out = sh([RTL_BIN, "+elf=" + elf, "+trace=" + rtl_csv,
                  "+max_cycles={}".format(max(200000, 50 * len(rows)))], log, timeout=timeout)
    for tok in out.split():
        if tok.startswith("cycles="):
            res["cycles"] = int(tok.split("=")[1])
    rtl_status = {0: None, 2: "TIMEOUT", 3: "HANG", -1: "TIMEOUT"}.get(rc, "RTL_ERROR")

    ref, dut = cmp.load(spike_csv), cmp.load(rtl_csv)
    res["instrs"] = len(ref)
    mismatches, lines = cmp.compare(ref, dut)
    with open(log, "a") as f:
        f.write("\n".join(lines) + "\n")
    if mismatches:
        idx, fields = mismatches[0]
        res["status"] = "MISMATCH"
        res["detail"] = "#{} {}: {}".format(idx, ",".join(fields), lines[0] if lines else "")
        if idx < len(ref):
            res["detail"] = "#{} pc={} {} [{}]".format(idx, ref[idx]["pc"], ref[idx]["instr_str"],
                                                      ",".join(fields))
    if rtl_status and res["status"] == "PASS":
        res["status"] = rtl_status
    elif rtl_status:
        res["detail"] += " (rtl {})".format(rtl_status.lower())
    res["seconds"] = round(time.time() - t0, 1)
    return res


def load_testlist(path):
    with open(path) as f:
        return [t for t in yaml.safe_load(f) if t.get("iterations", 1) > 0]


def install_target():
    """riscv-dv pygen imports targets from its own tree; link ours in."""
    dst = os.path.join(RDV, "pygen", "pygen_src", "target", TARGET)
    src = os.path.join(ROOT, "dv", "target", TARGET)
    if os.path.islink(dst) or os.path.exists(dst):
        if os.path.realpath(dst) == os.path.realpath(src):
            return
        if os.path.islink(dst):
            os.remove(dst)
        else:
            shutil.rmtree(dst)
    os.symlink(src, dst)


def gen_opts_list(test):
    opts = []
    for tok in (test.get("gen_opts") or "").split():
        opts.append("--" + tok.lstrip("+"))
    return BASE_GEN_OPTS + opts


def generate(test, seed, outdir):
    os.makedirs(outdir, exist_ok=True)
    install_target()
    asm_base = os.path.join(outdir, "test")
    gen_test = test.get("gen_test", "riscv_instr_base_test")
    cmd = [sys.executable, os.path.join(RDV, "pygen", "pygen_src", "test", gen_test + ".py"),
           "--target=" + TARGET, "--asm_file_name=" + asm_base, "--seed={}".format(seed),
           "--start_idx=0", "--num_of_tests=1"] + gen_opts_list(test)
    rc, _ = sh(cmd, os.path.join(outdir, "gen.log"), timeout=1200, cwd=RDV)
    asm = asm_base + "_0.S"
    return asm if rc == 0 and os.path.exists(asm) else None


def gen_and_run(test, seed, outroot):
    name = "{}.{}".format(test["test"], seed)
    outdir = os.path.join(outroot, name)
    asm = generate(test, seed, outdir)
    if not asm:
        return {"name": name, "status": "GEN_FAIL", "detail": "see gen.log", "instrs": 0, "cycles": 0}
    res = run_program(asm, outdir)
    res.update(name=name, test=test["test"], seed=seed)
    return res


def print_result(r):
    print("{:<48} {:<12} {:>7} instr {:>8} cyc  {}".format(
        r["name"], r["status"], r.get("instrs", 0), r.get("cycles", 0), r.get("detail", "")), flush=True)


def cmd_regress(a):
    tests = load_testlist(a.testlist)
    if a.test:
        tests = [t for t in tests if t["test"] in a.test]
    rng = random.Random(a.start_seed)
    jobs = []
    for t in tests:
        for _ in range(a.iterations or t.get("iterations", 1)):
            jobs.append((t, rng.randrange(1 << 31)))
    # round-robin across tests so a time budget still covers every test type
    by_test = [[j for j in jobs if j[0] is t] for t in tests]
    jobs = [j for group in itertools.zip_longest(*by_test) for j in group if j]
    print("regress: {} runs across {} tests, {} jobs".format(len(jobs), len(tests), a.jobs), flush=True)
    deadline = time.time() + a.budget * 60 if a.budget else None
    results = []
    with cf.ProcessPoolExecutor(max_workers=a.jobs) as ex:
        pending = {}
        it = iter(jobs)
        def submit():
            for t, seed in it:
                if deadline and time.time() > deadline:
                    return
                pending[ex.submit(gen_and_run, t, seed, a.out)] = (t, seed)
                return
        for _ in range(a.jobs * 2):
            submit()
        while pending:
            done, _ = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
            for fut in done:
                t, seed = pending.pop(fut)
                try:
                    r = fut.result()
                except Exception as e:  # keep the regression going
                    r = {"name": "{}.{}".format(t["test"], seed), "status": "INFRA_ERROR",
                         "detail": repr(e)}
                results.append(r)
                print_result(r)
                submit()
    passed = sum(1 for r in results if r["status"] == "PASS")
    summary = {"runs": len(results), "passed": passed, "failed": len(results) - passed,
               "instructions": sum(r.get("instrs", 0) for r in results), "results": results}
    with open(os.path.join(a.out, "regress.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print("\n{} / {} passed, {} instructions co-simulated".format(
        passed, len(results), summary["instructions"]))
    return 0 if passed == len(results) and results else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "out"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    p = sub.add_parser("run")
    p.add_argument("program", nargs="+", help=".S or .elf")
    p = sub.add_parser("gen")
    p.add_argument("test")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--testlist", default=os.path.join(ROOT, "dv", "testlist.yaml"))
    p = sub.add_parser("regress")
    p.add_argument("--testlist", default=os.path.join(ROOT, "dv", "testlist.yaml"))
    p.add_argument("--test", action="append", help="only these tests (repeatable)")
    p.add_argument("--iterations", type=int, default=0, help="override per-test iterations")
    p.add_argument("--start-seed", type=int, default=int(time.time()))
    p.add_argument("--jobs", type=int, default=os.cpu_count())
    p.add_argument("--budget", type=float, default=0, help="stop launching new runs after N minutes")
    a = ap.parse_args()
    a.out = os.path.abspath(a.out)
    os.makedirs(a.out, exist_ok=True)

    if a.cmd == "build":
        return build()
    if not os.path.exists(RTL_BIN) and build(verbose=False) != 0:
        return 1
    if a.cmd == "run":
        rc = 0
        for prog in a.program:
            name = os.path.splitext(os.path.basename(prog))[0]
            r = run_program(os.path.abspath(prog), os.path.join(a.out, name))
            print_result(r)
            rc |= r["status"] != "PASS"
        return rc
    if a.cmd == "gen":
        test = next(t for t in load_testlist(a.testlist) if t["test"] == a.test)
        r = gen_and_run(test, a.seed, a.out)
        print_result(r)
        return r["status"] != "PASS"
    if a.cmd == "regress":
        return cmd_regress(a)


if __name__ == "__main__":
    sys.exit(main())
