// Shared prologue/epilogue for directed co-sim tests. A test ends by storing
// gp=1 to tohost; Spike exits on that write and the RTL harness stops on the
// same retired store.
#define TEST_BEGIN          \
    .section .text.init;    \
    .globl _start;          \
_start:

#define TEST_END            \
    li gp, 1;               \
1:  sw gp, tohost, t5;      \
    j 1b;                   \
    .section .tohost, "aw", @progbits; \
    .align 6;               \
    .globl tohost;          \
tohost: .dword 0;           \
    .align 6;               \
    .globl fromhost;        \
fromhost: .dword 0;
