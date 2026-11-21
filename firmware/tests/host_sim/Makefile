# Builds the bench firmware core for a PC, behind a pseudo-terminal, for host-side protocol tests.
#   make                 -> ./bench_sim          (stub model; cascade commands answer ERR NOTIMPL)
#   make bench_sim_cascade CASC_DATA=<path to cascade_data.h>
#                        -> ./bench_sim_cascade  (stub model + the cascade commands of CONTRACT.md 7.6 on real gate
#                           data; tools/bundle.py --cascade-data writes the header). Needs Arduino_CMSIS-DSP (the
#                           board's copy) for fx_r1 and the fixed-point FFT front ends: CMSIS_DSP=<its src dir>.
BENCH := ../../bench
CXXFLAGS := -std=c++17 -O2 -Wall -Wextra -Werror -Wno-unused-parameter -I$(BENCH)
SRC := $(BENCH)/bench_core.cpp $(BENCH)/bench_cascade.cpp $(BENCH)/models_stub.cpp $(BENCH)/stored_inputs.cpp \
       host_hal.cpp main.cpp

bench_sim: $(SRC) $(BENCH)/bench_core.h $(BENCH)/bench_internal.h $(BENCH)/hal.h $(BENCH)/stored_inputs.h
	$(CXX) $(CXXFLAGS) -DBENCH_GIT_HASH='"sim"' $(SRC) -o $@

CMSIS_DSP ?= $(HOME)/Arduino/libraries/Arduino_CMSIS-DSP/src
CASC_DATA ?= $(BENCH)/models/cascade_data.h
# Paderborn (gate 6): make bench_sim_cascade CASC_DATA=... CASC_DEFS='-DCASC_NG_MAX=23 -DCASC_CAL_MAX=128 -DCASC_HAS_G6_STUB'
CASC_DEFS ?=
DSP_FILES := TransformFunctions/arm_rfft_fast_f32 TransformFunctions/arm_rfft_fast_init_f32 \
  TransformFunctions/arm_cfft_f32 TransformFunctions/arm_cfft_init_f32 TransformFunctions/arm_cfft_radix8_f32 \
  TransformFunctions/arm_bitreversal CommonTables/arm_common_tables CommonTables/arm_const_structs \
  ComplexMathFunctions/arm_cmplx_mag_f32
DSP_SRC := $(addprefix $(CMSIS_DSP)/Source/,$(addsuffix .c,$(DSP_FILES)))
DSP_INC := -I$(CURDIR)/inc -I$(CMSIS_DSP) -I$(CMSIS_DSP)/Include -I$(CMSIS_DSP)/PrivateInclude
# -ffp-contract=off keeps the PC float order equal to the host reference (ml/cascade_export.py). The board build may
# fuse multiply-adds; RQ1 compares alarms, and no exported vector lies within 1e-5 of a threshold.
CFLAGS_C := $(CASC_DEFS) -std=c11 -O2 -ffp-contract=off -ffunction-sections -fdata-sections -w $(DSP_INC) \
  -I$(abspath $(BENCH)) -I$(abspath ../../../handoff)
CASC_SRC := $(BENCH)/cascade.c $(BENCH)/frontend_fx.c dsp_shim.c
OUT ?= bench_sim_cascade

bench_sim_cascade bench_sim_cascade_pu: $(SRC) $(CASC_SRC) $(CASC_DATA) $(BENCH)/cascade.h $(BENCH)/frontend_fx.h
	@rm -rf obj_casc && mkdir -p obj_casc
	cd obj_casc && $(CC) $(CFLAGS_C) -c $(abspath $(DSP_SRC) $(CASC_SRC))
	$(CXX) $(CXXFLAGS) $(CASC_DEFS) -ffp-contract=off -DBENCH_GIT_HASH='"sim-cascade"' -DBENCH_CASCADE=1 \
	  -DCASC_DATA_H='"$(abspath $(CASC_DATA))"' -I$(BENCH) $(SRC) obj_casc/*.o -Wl,--gc-sections -lm -o $@

clean:
	rm -rf bench_sim bench_sim_cascade bench_sim_cascade_pu obj_casc

.PHONY: clean
