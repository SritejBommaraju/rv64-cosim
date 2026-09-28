# Included at _start of every riscv-dv program.
#
# Spike's boot ROM leaves a0/a1/a2/t0 holding the hart id, DTB pointer, etc.
# while the RTL resets every GPR to 0. riscv-dv's init_gpr skips the two
# registers it picked as sp/tp, so a program can read one of them before
# writing it and the two models diverge on reset state alone (seen in CI as
# divu reading a1=0x1020 on Spike and 0 on RTL). Zero x1-x31 so both start
# identical.
    li x1, 0
    li x2, 0
    li x3, 0
    li x4, 0
    li x5, 0
    li x6, 0
    li x7, 0
    li x8, 0
    li x9, 0
    li x10, 0
    li x11, 0
    li x12, 0
    li x13, 0
    li x14, 0
    li x15, 0
    li x16, 0
    li x17, 0
    li x18, 0
    li x19, 0
    li x20, 0
    li x21, 0
    li x22, 0
    li x23, 0
    li x24, 0
    li x25, 0
    li x26, 0
    li x27, 0
    li x28, 0
    li x29, 0
    li x30, 0
    li x31, 0
