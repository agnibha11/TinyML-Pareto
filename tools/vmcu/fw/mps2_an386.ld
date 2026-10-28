/* mps2_an386.ld - linker script for the virtual MCU (QEMU mps2-an386, Cortex-M4F).
 * Code and read-only data (models, test vectors) live in SSRAM1 at 0x0 (stands in for flash);
 * data, bss, heap and stack live in SSRAM2/3 (4 MB) and PSRAM (16 MB, used for the big VERIFY buffers).
 * QEMU's ELF loader writes every loadable segment, so .data is placed directly at its run address. */
MEMORY
{
  FLASH (rx)  : ORIGIN = 0x00000000, LENGTH = 4M
  RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 4M
  PSRAM (rwx) : ORIGIN = 0x21000000, LENGTH = 16M
}

ENTRY(Reset_Handler)

SECTIONS
{
  .isr_vector : { KEEP(*(.isr_vector)) } > FLASH
  .text : {
    *(.text*)
    KEEP(*(.init)) KEEP(*(.fini))
    *(.rodata*)
    . = ALIGN(16);
  } > FLASH
  .ARM.extab : { *(.ARM.extab* .gnu.linkonce.armextab.*) } > FLASH
  .ARM.exidx : { __exidx_start = .; *(.ARM.exidx* .gnu.linkonce.armexidx.*) __exidx_end = .; } > FLASH
  .preinit_array : { PROVIDE_HIDDEN(__preinit_array_start = .); KEEP(*(.preinit_array*)) PROVIDE_HIDDEN(__preinit_array_end = .); } > FLASH
  .init_array : { PROVIDE_HIDDEN(__init_array_start = .); KEEP(*(SORT(.init_array.*))) KEEP(*(.init_array*)) PROVIDE_HIDDEN(__init_array_end = .); } > FLASH
  .fini_array : { PROVIDE_HIDDEN(__fini_array_start = .); KEEP(*(SORT(.fini_array.*))) KEEP(*(.fini_array*)) PROVIDE_HIDDEN(__fini_array_end = .); } > FLASH

  .data : { . = ALIGN(4); *(.data*) . = ALIGN(4); } > RAM
  .bss (NOLOAD) : { . = ALIGN(4); __bss_start__ = .; *(.bss*) *(COMMON) . = ALIGN(4); __bss_end__ = .; } > RAM
  .psram (NOLOAD) : { . = ALIGN(16); *(.psram*) } > PSRAM

  . = ALIGN(8);
  end = .;
  __end__ = .;
  PROVIDE(__stack_top = ORIGIN(RAM) + LENGTH(RAM));
  __stack_limit = __stack_top - 0x10000;
}
