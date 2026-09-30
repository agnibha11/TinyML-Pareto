// host_hal.cpp - PC implementation of hal.h for the bench simulator.
// The "serial port" is a pseudo-terminal: tools/dut.py opens its slave path exactly like /dev/ttyACM0.
// D2 edges are written to stderr as "SYNC <0|1> <us>" so tests can check the window rules.
#include "hal.h"

#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

int g_pty_master = -1;  // set by main.cpp

static uint64_t now_ns(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static uint64_t s_t0_ns;

void bhal_init(void) { s_t0_ns = now_ns(); }

uint32_t bhal_micros(void) { return (uint32_t)((now_ns() - s_t0_ns) / 1000ull); }

uint32_t bhal_cycles(void) { return (uint32_t)((now_ns() - s_t0_ns) * 64ull / 1000ull); }  // 64 MHz

void bhal_sync(int high) {
  fprintf(stderr, "SYNC %d %lu\n", high ? 1 : 0, (unsigned long)bhal_micros());
  fflush(stderr);
}

void bhal_wait_for_interrupt(void) { usleep(100); }

void bhal_sleep_ms(uint32_t ms) { usleep((useconds_t)ms * 1000u); }

int bhal_read_byte(void) {
  struct pollfd p = {g_pty_master, POLLIN, 0};
  if (poll(&p, 1, 0) <= 0 || !(p.revents & POLLIN)) return -1;
  unsigned char c;
  return read(g_pty_master, &c, 1) == 1 ? c : -1;
}

size_t bhal_read_bytes(uint8_t *buf, size_t n, uint32_t timeout_ms) {
  size_t got = 0;
  const uint64_t deadline = now_ns() + (uint64_t)timeout_ms * 1000000ull;
  while (got < n && now_ns() < deadline) {
    struct pollfd p = {g_pty_master, POLLIN, 0};
    if (poll(&p, 1, 10) > 0 && (p.revents & POLLIN)) {
      ssize_t r = read(g_pty_master, buf + got, n - got);
      if (r > 0) got += (size_t)r;
    }
  }
  return got;
}

void bhal_write(const char *s) {
  size_t len = strlen(s);
  while (len) {
    ssize_t w = write(g_pty_master, s, len);
    if (w <= 0) return;
    s += w;
    len -= (size_t)w;
  }
}
