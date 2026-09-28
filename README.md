# rv64-cosim

Lockstep co-simulation of [rv64-ooo-core](https://github.com/SritejBommaraju/rv64-ooo-core)
against Spike, driven by riscv-dv random programs and a constrained-random OoO
hazard generator, with a time-budgeted regression in GitHub Actions.

Every retired instruction is compared: PC, encoding, destination register and
value, and memory address and store data. The first divergence stops the test
and prints the surrounding retire history.

```
 riscv-dv pygen ─┐                       ┌─ Spike -l --log-commits ── spike_trace.py ──┐
                 ├─ test.S ── gcc ── ELF ┤                                             ├─ compare.py
 hazard_gen.py ──┘                       └─ Verilator ooo_top ── RVFI ── rtl.csv ──────┘
```

## How it works

**RVFI retire port (in the core).** `ooo_core` exposes a riscv-formal-style RVFI
port (`rvfi_valid`, `insn`, `pc_rdata/pc_wdata`, `rd_addr/rd_wdata`,
`mem_addr/rmask/wmask/rdata/wdata`, ...). A per-ROB-entry record is filled at
dispatch, issue, load response and mul/div completion, and read out at the ROB
head on the commit cycle. The same port is intended for riscv-formal.

**Harness (`tb/`).** Loads the ELF into `ooo_mem`, resets at `0x80000000`, and
writes one CSV row per `rvfi_valid`. The run ends on the committed store to the
ELF's `tohost` symbol, which is also what terminates Spike. If nothing retires
for 5000 cycles, the run is reported as a hang.

**Traces.** Both sides produce riscv-dv's trace CSV columns
(`pc,instr,gpr,csr,binary,mode,instr_str,operand,pad`), so riscv-dv's own
`instr_trace_compare.py` can read them. They also carry a `mem` column
(`st:<addr>:<data>` / `ld:<addr>`).

**Compare (`scripts/compare.py`).** Strict lockstep, row by row. riscv-dv's
compare only looks at GPR writes whose value changes. That misses wrong-PC
execution, lost writes of an unchanged value, and wrong store addresses or data.

**Generators.**
- *riscv-dv (pygen)*, with a custom target `dv/target/rv64im_ooo`. Programs run
  in bare M-mode, since the core has no traps yet.
- *`scripts/hazard_gen.py`*: weighted scenarios aimed at the rename, ROB, LSQ
  and writeback paths, drawn from a small register pool and a small memory
  window so dependences and aliasing are dense. Scenarios: RAW chains; WAW
  between a slow load and a younger ALU op; load-use; store→load forwarding with
  full, partial and non-forwardable overlaps; store-store-load; load-dependent
  branch and `jalr`; ROB fill behind a load; store queue overflow; `x0`
  destinations; CSR serialization; counted loops; mul/div as long-latency ops.

## Running it

Needs Verilator 5, Spike, `riscv64-unknown-elf-gcc`, `dtc`, and Python 3.10
with `requirements.txt`.

```sh
git clone --recursive https://github.com/SritejBommaraju/rv64-cosim && cd rv64-cosim
pip install -r requirements.txt
python3 scripts/cosim.py build
python3 scripts/cosim.py run tests/directed/*.S
python3 scripts/cosim.py gen ooo_hazard_test --seed 7
python3 scripts/cosim.py regress --budget 25 --jobs 8          # dv/testlist.yaml
python3 scripts/mutation_check.py                              # checker qualification
```

Each test leaves `out/<test>.<seed>/{test.S, compare.log, gen.log}`. Failing
tests also keep `test.elf`, `spike.csv` and `rtl.csv`. `CORE=<path>` builds
against a core checkout other than the `core/` submodule.

## CI

`.github/workflows/regress.yml` runs on every push and nightly. It builds Spike
at Chipyard 1.14's pinned revision (cached) and the Verilator model, runs the
directed tests, then runs `dv/testlist_ci.yaml` round-robin for 25 minutes with
the workflow run number as the seed. The job summary shows the per-test
pass/instruction/IPC table. Failing test directories are uploaded as artifacts.
