// main.cpp - runs bench_core on a PC behind a pseudo-terminal.
// Usage: ./bench_sim            -> prints "PTY /dev/pts/N" on stdout, then serves commands until killed.
#define _XOPEN_SOURCE 600
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <termios.h>
#include <unistd.h>

#include "bench_core.h"

extern int g_pty_master;

int main(void) {
  int m = posix_openpt(O_RDWR | O_NOCTTY);
  if (m < 0 || grantpt(m) != 0 || unlockpt(m) != 0) {
    perror("pty");
    return 1;
  }
  // Raw mode on the slave side so bytes pass unchanged (VERIFY sends binary floats).
  int s = open(ptsname(m), O_RDWR | O_NOCTTY);
  struct termios t;
  tcgetattr(s, &t);
  cfmakeraw(&t);
  tcsetattr(s, TCSANOW, &t);
  g_pty_master = m;
  printf("PTY %s\n", ptsname(m));
  fflush(stdout);

  bench_setup();
  for (;;) {
    bench_poll();
    usleep(200);
  }
  close(s);  // unreachable; kept so the slave stays open while serving
}
