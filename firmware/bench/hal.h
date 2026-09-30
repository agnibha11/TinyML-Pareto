// hal.h - the only platform-specific surface of the bench firmware.
// Board implementation: hal_nano33ble.cpp (compiled only under ARDUINO).
// Host implementation:  firmware/tests/host_sim/host_hal.cpp (a PC simulator used by the host-side tests).
#pragma once
#include <stddef.h>
#include <stdint.h>

void     bhal_init(void);                 // power rails off, D2 LOW, DWT on, serial up
uint32_t bhal_micros(void);               // wraps every ~71 min; use unsigned differences
uint32_t bhal_cycles(void);               // DWT->CYCCNT at 64 MHz; wraps every ~67 s
void     bhal_sync(int high);             // D2: 1 = HIGH, 0 = LOW
void     bhal_wait_for_interrupt(void);   // __WFI()
void     bhal_sleep_ms(uint32_t ms);      // RTOS thread sleep (lets Mbed enter its sleep state)
int      bhal_read_byte(void);            // next received byte, or -1 if none
size_t   bhal_read_bytes(uint8_t *buf, size_t n, uint32_t timeout_ms);  // blocking with timeout
void     bhal_write(const char *s);       // raw write, no newline added
