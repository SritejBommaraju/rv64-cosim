// M extension corner cases: div-by-zero, signed overflow, W forms, and mul/div
// results racing younger ALU ops and loads through the shared writeback port.
#include "common.h"

#define ALL(rd, a, b)      \
    mul    rd, a, b;       \
    mulh   rd, a, b;       \
    mulhsu rd, a, b;       \
    mulhu  rd, a, b;       \
    div    rd, a, b;       \
    divu   rd, a, b;       \
    rem    rd, a, b;       \
    remu   rd, a, b;       \
    mulw   rd, a, b;       \
    divw   rd, a, b;       \
    divuw  rd, a, b;       \
    remw   rd, a, b;       \
    remuw  rd, a, b;

TEST_BEGIN
    li   s1, 0
    li   s2, 1
    li   s3, -1
    li   s4, 0x8000000000000000
    li   s5, 0xffffffff80000000
    li   s6, 0x123456789abcdef1
    li   s7, 0x00000000ffffffff
    li   s8, 7
    li   s9, -7
    ALL(a0, s6, s1)      // divide by zero
    ALL(a1, s4, s3)      // signed overflow
    ALL(a2, s5, s3)      // W-form overflow
    ALL(a3, s6, s8)
    ALL(a4, s9, s8)
    ALL(a5, s6, s9)
    ALL(a6, s7, s7)
    ALL(a7, s4, s4)

    // a long divide with dependent and independent work behind it
    la   s0, buf
    sd   s6, 0(s0)
    div  t0, s6, s8
    addi t1, s2, 5
    ld   t2, 0(s0)
    add  t3, t0, t2       // waits for the divide
    xor  t4, t1, t2
    mul  t5, t0, t3
    rem  t6, t5, s9
    sd   t6, 8(s0)
    ld   t0, 8(s0)
    divw t1, t0, s9
    mulw t2, t1, t1
TEST_END

    .data
    .align 3
buf: .zero 16
