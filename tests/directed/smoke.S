// Smoke test: one of everything the RV64I+Zicsr core implements.
#include "common.h"

TEST_BEGIN
    csrr    x5, mhartid
    li      a0, 0x123456789abcdef0
    li      a1, -7
    add     a2, a0, a1
    sub     a3, a0, a1
    sll     a4, a0, a1
    sra     a5, a1, 1
    srl     a6, a1, 60
    slt     a7, a1, a0
    sltu    s2, a1, a0
    xor     s3, a0, a1
    or      s4, a0, a1
    and     s5, a0, a1
    addiw   s6, a0, -1
    addw    s7, a0, a1
    subw    s8, a1, a0
    sllw    s9, a0, a1
    srlw    s10, a0, a1
    sraw    s11, a1, a0
    lui     t3, 0xfffff
    auipc   t4, 0x10

    la      s0, buf
    sd      a0, 0(s0)
    sw      a1, 8(s0)
    sh      a0, 12(s0)
    sb      a1, 14(s0)
    ld      t0, 0(s0)          // forwarded from the store queue or memory
    lw      t1, 8(s0)
    lwu     t2, 8(s0)
    lh      t0, 12(s0)
    lhu     t1, 12(s0)
    lb      t2, 14(s0)
    lbu     t0, 14(s0)
    lw      t1, 3(s0)          // misaligned, spans two stored values

    li      t0, 10
    li      t1, 0
loop:
    addi    t1, t1, 3
    addi    t0, t0, -1
    bnez    t0, loop
    blt     a1, a0, 1f
    li      t1, 99
1:  bgeu    a1, a0, 2f
    li      t1, 100
2:  jal     ra, func
    la      t2, func
    jalr    ra, 0(t2)

    csrw    mscratch, a0
    csrrs   t0, mscratch, a1
    csrrci  t1, mscratch, 0x1f
    csrr    t2, mscratch
    csrr    t0, misa
    j       done

func:
    addi    a0, a0, 1
    ret

done:
TEST_END

    .data
    .align 3
buf: .zero 64
