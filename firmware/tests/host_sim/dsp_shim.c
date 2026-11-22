// dsp_shim.c - C versions of the two CMSIS-DSP bit-reversal routines that Arduino_CMSIS-DSP 5.7.0 ships only as ARM
// assembly (arm_bitreversal2.S), so the cascade simulator can link the FFTs on a PC. Same algorithm as the C
// fallback of later CMSIS-DSP releases; checked against numpy by tests/test_cascade.py.
#include <stdint.h>

void arm_bitreversal_32(uint32_t *pSrc, const uint16_t bitRevLen, const uint16_t *pBitRevTab) {
  for (uint32_t i = 0; i < bitRevLen; i += 2) {
    const uint32_t a = pBitRevTab[i] >> 2, b = pBitRevTab[i + 1] >> 2;
    uint32_t t = pSrc[a]; pSrc[a] = pSrc[b]; pSrc[b] = t;
    t = pSrc[a + 1]; pSrc[a + 1] = pSrc[b + 1]; pSrc[b + 1] = t;
  }
}

void arm_bitreversal_16(uint16_t *pSrc, const uint16_t bitRevLen, const uint16_t *pBitRevTab) {
  for (uint32_t i = 0; i < bitRevLen; i += 2) {
    const uint32_t a = pBitRevTab[i] >> 2, b = pBitRevTab[i + 1] >> 2;
    uint16_t t = pSrc[a]; pSrc[a] = pSrc[b]; pSrc[b] = t;
    t = pSrc[a + 1]; pSrc[a + 1] = pSrc[b + 1]; pSrc[b + 1] = t;
  }
}
