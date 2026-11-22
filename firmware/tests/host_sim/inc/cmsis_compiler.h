// Host (x86, gcc) stand-in for CMSIS cmsis_compiler.h: only what CMSIS-DSP's portable C paths need.
#pragma once
#include <stdint.h>
#define __ASM __asm__
#define __INLINE inline
#define __STATIC_INLINE static inline
#define __STATIC_FORCEINLINE static inline __attribute__((always_inline))
#define __ALIGNED(x) __attribute__((aligned(x)))
#define __PACKED __attribute__((packed))
#define __PACKED_STRUCT struct __attribute__((packed))
#define __WEAK __attribute__((weak))
#define __RESTRICT __restrict
#define __UNALIGNED_UINT32_READ(addr) (*(const uint32_t *)(addr))
static inline uint8_t __CLZ(uint32_t v) { return v ? (uint8_t)__builtin_clz(v) : 32u; }
static inline int32_t __SSAT(int32_t v, uint32_t b) {
  const int32_t mx = (int32_t)((1u << (b - 1)) - 1), mn = -mx - 1;
  return v > mx ? mx : v < mn ? mn : v;
}
static inline uint32_t __USAT(int32_t v, uint32_t b) {
  const int32_t mx = (int32_t)((1u << b) - 1);
  return (uint32_t)(v > mx ? mx : v < 0 ? 0 : v);
}
