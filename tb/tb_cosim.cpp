// Verilator co-simulation harness for rv64-ooo-core.
//
// Loads an ELF into ooo_mem, runs ooo_top, and writes one CSV row per retired
// instruction from the core's RVFI port (riscv-dv trace CSV columns plus a `mem`
// column). The run ends when the core commits a store to the ELF's `tohost`
// symbol, which is the same event that terminates Spike.
//
// usage: Vooo_top +elf=<test.elf> +trace=<out.csv> [+max_cycles=N] [+stall_limit=N] [+vcd=<f>]
// exit:  0 = reached tohost, 1 = setup error, 2 = cycle limit, 3 = commit stall (hang)

#include <cinttypes>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>

#include "Vooo_top.h"
#include "Vooo_top___024root.h"
#include "Vooo_top_ooo_mem__D100000.h"
#include "Vooo_top_ooo_top.h"
#include "elf_loader.h"
#include "verilated.h"
#if VM_TRACE
#include "verilated_vcd_c.h"
#endif

#ifndef MEM_BYTES
#define MEM_BYTES (1u << 20)
#endif

static const char* const ABI[32] = {
    "zero", "ra", "sp", "gp", "tp", "t0", "t1", "t2", "s0", "s1", "a0",
    "a1",   "a2", "a3", "a4", "a5", "a6", "a7", "s2", "s3", "s4", "s5",
    "s6",   "s7", "s8", "s9", "s10", "s11", "t3", "t4", "t5", "t6"};

static std::string plusarg(const char* name, const std::string& dflt = "") {
    const char* v = Verilated::commandArgsPlusMatch(name);
    if (!v || !*v) return dflt;
    const char* eq = std::strchr(v, '=');
    return eq ? std::string(eq + 1) : dflt;
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);

    const std::string elf_path   = plusarg("elf");
    const std::string trace_path = plusarg("trace", "rtl_trace.csv");
    const uint64_t max_cycles    = std::strtoull(plusarg("max_cycles", "2000000").c_str(), nullptr, 0);
    const uint64_t stall_limit   = std::strtoull(plusarg("stall_limit", "5000").c_str(), nullptr, 0);
    if (elf_path.empty()) {
        std::fprintf(stderr, "usage: %s +elf=<test.elf> +trace=<out.csv> [+max_cycles=N]\n", argv[0]);
        return 1;
    }

    auto top = std::make_unique<Vooo_top>();
    auto& mem = top->rootp->ooo_top->u_mem->bytes;

    ElfImage img;
    try {
        img = load_elf(elf_path, [&](uint64_t addr, uint8_t b) { mem[addr & (MEM_BYTES - 1)] = b; });
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        return 1;
    }
    auto it = img.symbols.find("tohost");
    if (it == img.symbols.end()) {
        std::fprintf(stderr, "error: %s has no tohost symbol\n", elf_path.c_str());
        return 1;
    }
    const uint64_t tohost = it->second;

    FILE* csv = std::fopen(trace_path.c_str(), "w");
    if (!csv) {
        std::fprintf(stderr, "error: cannot write %s\n", trace_path.c_str());
        return 1;
    }
    std::fprintf(csv, "pc,instr,gpr,csr,binary,mode,instr_str,operand,pad,mem\n");

#if VM_TRACE
    std::unique_ptr<VerilatedVcdC> vcd;
    const std::string vcd_path = plusarg("vcd");
    if (!vcd_path.empty()) {
        Verilated::traceEverOn(true);
        vcd = std::make_unique<VerilatedVcdC>();
        top->trace(vcd.get(), 99);
        vcd->open(vcd_path.c_str());
    }
#endif

    uint64_t cycle = 0, retired = 0, last_retire_cycle = 0;
    auto tick = [&]() {
        top->clk = 1;
        top->eval();
#if VM_TRACE
        if (vcd) vcd->dump(cycle * 10 + 5);
#endif
        top->clk = 0;
        top->eval();
#if VM_TRACE
        if (vcd) vcd->dump(cycle * 10 + 10);
#endif
        cycle++;
    };

    top->rst = 1;
    top->dbg_arch_reg = 0;
    top->clk = 0;
    top->eval();
    tick();
    tick();
    top->rst = 0;
    top->eval();

    int rc = 2;
    while (cycle < max_cycles) {
        // RVFI is combinational from the ROB head: sample with clk low, before the
        // edge on which the instruction actually retires.
        if (top->rvfi_valid) {
            char gpr[64] = "";
            if (top->rvfi_rd_addr != 0)
                std::snprintf(gpr, sizeof gpr, "%s:%016" PRIx64, ABI[top->rvfi_rd_addr],
                              (uint64_t)top->rvfi_rd_wdata);
            char memf[80] = "";
            if (top->rvfi_mem_wmask)
                std::snprintf(memf, sizeof memf, "st:%016" PRIx64 ":%016" PRIx64,
                              (uint64_t)top->rvfi_mem_addr, (uint64_t)top->rvfi_mem_wdata);
            else if (top->rvfi_mem_rmask)
                std::snprintf(memf, sizeof memf, "ld:%016" PRIx64, (uint64_t)top->rvfi_mem_addr);
            std::fprintf(csv, "%016" PRIx64 ",,%s,,%08x,3,,,,%s\n", (uint64_t)top->rvfi_pc_rdata, gpr,
                         (unsigned)top->rvfi_insn, memf);
            retired++;
            last_retire_cycle = cycle;
            if (top->rvfi_mem_wmask && top->rvfi_mem_addr == tohost) {
                rc = 0;
                break;
            }
        } else if (cycle - last_retire_cycle > stall_limit) {
            std::fprintf(stderr, "HANG: no instruction retired for %" PRIu64 " cycles (last pc %016" PRIx64 ")\n",
                         stall_limit, (uint64_t)top->dbg_last_commit_pc);
            rc = 3;
            break;
        }
        tick();
    }

    std::fclose(csv);
#if VM_TRACE
    if (vcd) vcd->close();
#endif
    if (rc == 2) std::fprintf(stderr, "TIMEOUT after %" PRIu64 " cycles\n", cycle);
    std::printf("rtl: %s cycles=%" PRIu64 " retired=%" PRIu64 " ipc=%.3f\n",
                rc == 0 ? "tohost" : (rc == 3 ? "hang" : "timeout"), cycle, retired,
                cycle ? (double)retired / (double)cycle : 0.0);
    top->final();
    return rc;
}
