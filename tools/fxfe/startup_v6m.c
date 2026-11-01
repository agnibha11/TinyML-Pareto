// startup_v6m.c - start-up for the ARMv6-M (Cortex-M0+) build of tools/fxfe, run on QEMU's mps2-an386.
//
// Same as tools/vmcu/fw/startup.c minus the FPU enable (no FPU on an M0+) and with Thumb-1 instructions only.
// ARMv6-M code is a subset of ARMv7-M, so the Cortex-M4 that QEMU models executes it unchanged.
#include <stdint.h>

extern uint32_t __stack_top;
extern void _start(void);

void Reset_Handler(void);
void Default_Handler(void);

__attribute__((naked, noreturn)) void Reset_Handler(void) {
  __asm volatile(
      "ldr r0, =__stack_top \n"
      "mov sp, r0           \n"
      "ldr r0, =_start      \n"
      "bx r0                \n");
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

__attribute__((noinline)) void vm_mark_begin(void) { __asm volatile("" ::: "memory"); }
__attribute__((noinline)) void vm_mark_end(void) { __asm volatile("nop" ::: "memory"); }
