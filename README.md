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

## Results

Local run of `dv/testlist_ci.yaml` (seed 3000, 14 jobs, about 20 minutes):

| test | runs | pass | instructions | IPC |
|---|---:|---:|---:|---:|
| ooo_hazard_tight_test | 200 | 200 | 944,146 | 0.39 |
| ooo_hazard_test | 200 | 200 | 928,015 | 0.38 |
| ooo_hazard_aligned_test | 100 | 100 | 478,460 | 0.38 |
| riscv_jump_stress_test | 20 | 20 | 20,396 | 0.24 |
| riscv_rand_instr_test | 20 | 20 | 17,255 | 0.19 |
| riscv_arithmetic_basic_test | 20 | 20 | 31,235 | 0.16 |
| **total** | **560** | **560** | **2,419,507** | |

IPC is low because the core stalls fetch on every branch and `jalr` until the
branch resolves (it has no speculation) and runs one mul/div at a time on an
iterative divider.

### Checker qualification

A regression that has never failed proves little. `scripts/mutation_check.py`
injects realistic OoO bugs into a copy of the core and reruns a fixed set of
programs (smoke, muldiv, and 8 hazard programs):

| mutation | programs failing (of 10) |
|---|---:|
| forward from a store that only partially covers the load | 8 |
| forward from the oldest overlapping store, not the youngest | 8 |
| loads bypass older stores with unknown addresses | 9 |
| load destinations never marked not-ready | 8 |
| SRAW/SRAIW with a 6-bit shift amount | 6 |
| LHU sign-extends | 9 |
| commit frees the new physical register instead of the old one | 10 |
| ALU op issues on the cycle a mul/div result takes writeback port 0 | 9 |
| BLTU compares signed | 3 |

All 9 are caught. The "oldest store" mutation was caught by only 2 of 9
programs before the hazard programs included mul/div. The added latency is
what separates forwarding order from program order. BLTU is the weakest at
3/10, so unsigned-compare branch operands are a stimulus gap worth widening.

Removing the LSQ's same-cycle store address/commit bypass is an *equivalent*
mutant. The ROB commits from a registered `done_q`, so a store can never issue
and commit in the same cycle, and the bypass is unreachable in this
integration.

### Bugs and gaps found

In the core (fixed in rv64-ooo-core):

1. **M-extension instructions executed as ADD.** `decode` flagged
   `MUL/DIV/REM*` but `ooo_core` ignored `is_muldiv`, so every M instruction
   silently produced `rs1 + rs2`. Fixed by integrating the iterative `muldiv`
   unit (4468eab).
2. **All of SYSTEM decoded as a NOP.** `csrr x5, mhartid`, riscv-dv's first
   instruction, retired without writing `x5`. Added M-mode Zicsr with
   serialized CSR issue (652568c).
3. **A testbench halt convention built into the RTL.** The core stopped
   dispatching whenever `x31 == 1`, and random programs use `x31` freely.
   It's now a parameter (3af56e9).
4. **CSR field packing** in the new Zicsr code (the CSR address was read from
   `instr[26:15]`). Caught by the first co-sim run (3af56e9).

In riscv-dv's Python generator (pygen), each seen as Spike traps or invalid
assembly and worked around in the testlists:

- The call-stack path raises, and `insert_jump_instr`/`riscv_jump_instr` are
  TODO stubs, so sub-programs are never called.
- `riscv_loop_instr` emits `sret`, `none` immediates and `x0` loop counters.
- In the load/store streams `rs1_reg` is unconstrained, so the `la` that sets
  up the base and the register the accesses use disagree (`la sp, region_1+3641`
  followed by `sh zero, -142(s5)`).
- The main program length is drawn uniformly from `[10, instr_cnt]`.

In the flow itself:

- **Reset-state mismatch between Spike and RTL.** CI's first run on fresh
  seeds hit 2 mismatches in 560 runs (`divu` returning the divide-by-zero
  value, `xori` reading 0). Root cause: Spike's boot ROM leaves `a1 = 0x1020`
  (the DTB pointer), the RTL resets every GPR to 0, and riscv-dv's `init_gpr`
  skips the two registers it picked as `sp`/`tp`, so a program could read `a1`
  before writing it. `dv/user_extension/user_init.s` now zeroes `x1`–`x31` at
  `_start`.

## Limitations and next steps

- No traps or interrupts yet, so tests run in bare M-mode with no
  `ecall`/`ebreak`/illegal instructions. Adding M-mode traps would let
  riscv-dv's privileged and illegal-instruction tests run.
- The core has no branch speculation, so there is no flush/squash path to
  stress yet. When speculation lands, the same flow checks it unchanged.
- riscv-dv's memory streams need the SystemVerilog generator (VCS or Xcelium).
  `cosim.py` takes any `.S`, so SV-generated programs drop in directly.
