// startup.c - minimal Cortex-M4F start-up for QEMU's mps2-an386 machine (virtual MCU).
//
// Vector table at 0x0, FPU enabled (CP10/CP11 full access) before any C code that may touch S registers, then the
// newlib/rdimon C runtime (_start: zero .bss, init libc, semihosting handles, main(), exit()).
#include <stdint.h>

extern uint32_t __stack_top;
extern void _start(void);

void Reset_Handler(void);
void Default_Handler(void);

__attribute__((naked, noreturn)) void Reset_Handler(void) {
  __asm volatile(
      "ldr r0, =0xE000ED88    \n"  // SCB->CPACR
      "ldr r1, [r0]           \n"
      "orr r1, r1, #(0xF << 20)\n"  // CP10, CP11 full access
      "str r1, [r0]           \n"
      "dsb                    \n"
      "isb                    \n"
      "ldr r0, =__stack_top   \n"
      "mov sp, r0             \n"
      "b _start               \n");
}

void Default_Handler(void) {
  for (;;) {
  }
}

__attribute__((section(".isr_vector"), used)) const void *const g_vectors[16] = {
    &__stack_top, (void *)Reset_Handler, (void *)Default_Handler, (void *)Default_Handler,
    (void *)Default_Handler, (void *)Default_Handler, (void *)Default_Handler, 0, 0, 0, 0,
    (void *)Default_Handler, (void *)Default_Handler, 0, (void *)Default_Handler, (void *)Default_Handler,
};

// Marker functions bracketing every measured region. The QEMU plugin (tools/vmcu/plugin/vmcount.c) counts the
// instructions executed between a call to vm_mark_begin() and the next call to vm_mark_end(). Their bodies are
// empty; noinline + the asm barrier keep the compiler from merging or removing them.
__attribute__((noinline)) void vm_mark_begin(void) { __asm volatile("" ::: "memory"); }
__attribute__((noinline)) void vm_mark_end(void) { __asm volatile("nop" ::: "memory"); }
