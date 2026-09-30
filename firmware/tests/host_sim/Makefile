# Builds the bench firmware core for a PC, behind a pseudo-terminal, for host-side protocol tests.
#   make            -> ./bench_sim
BENCH := ../../bench
CXXFLAGS := -std=c++17 -O2 -Wall -Wextra -Werror -Wno-unused-parameter -I$(BENCH)
SRC := $(BENCH)/bench_core.cpp $(BENCH)/models_stub.cpp $(BENCH)/stored_inputs.cpp host_hal.cpp main.cpp

bench_sim: $(SRC) $(BENCH)/bench_core.h $(BENCH)/hal.h $(BENCH)/stored_inputs.h
	$(CXX) $(CXXFLAGS) -DBENCH_GIT_HASH='"sim"' $(SRC) -o $@

clean:
	rm -f bench_sim

.PHONY: clean
