/* Link script for co-sim tests: same layout as riscv-dv's scripts/link.ld, with
 * directed tests' .text.init placed first so _start sits at the reset PC. */
OUTPUT_ARCH( "riscv" )
ENTRY(_start)

SECTIONS
{
  . = 0x80000000;
  .text : { *(.text.init) *(.text) *(.text.*) }
  . = ALIGN(0x1000);
  .tohost : { *(.tohost) }
  . = ALIGN(0x1000);
  .page_table : { *(.page_table) }
  .data : { *(.data) *(.rodata) *(.rodata.*) }
  .user_stack : { *(.user_stack) }
  .kernel_data : { *(.kernel_data) }
  .kernel_stack : { *(.kernel_stack) }
  .bss : { *(.bss) }
  _end = .;
}
