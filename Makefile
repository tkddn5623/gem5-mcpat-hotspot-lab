# gem5 -> McPAT -> HotSpot pipeline
#
# Plain native Makefile: it knows nothing about Docker. Run it either
# directly on a prepared Linux host, or inside the container:
#   docker compose run --rm dev make build
#
#   make build                 # build gem5 / McPAT / HotSpot
#   make run                   # every config x workload, all three stages
#   make run -j8               # 8 runs in parallel (gem5 is single-threaded)
#   make results/1c_4w_blackscholes/steady.temps   # just one run
#
# Each stage only re-runs when its inputs change: editing
# scripts/gem5_to_mcpat.py re-runs McPAT + HotSpot, never gem5.

GEM5    := gem5/build/X86/gem5.opt
MCPAT   := mcpat/mcpat
HOTSPOT := HotSpot/hotspot

# Parallel jobs for the gem5 build; lower it if the linker gets OOM-killed
NPROC ?= $(shell nproc)

WORKLOADS := blackscholes canneal freqmine streamcluster
# Sweep A: 1/4/8 cores at 4-wide; Sweep B: 2/4/8-wide at 4 cores (4c_4w shared)
CONFIGS   := 1c_4w 4c_4w 8c_4w 4c_2w 4c_8w
RUNS      := $(foreach c,$(CONFIGS),$(foreach w,$(WORKLOADS),$(c)_$(w)))

.PHONY: build run collect
# Keep intermediate files (stats.txt, mcpat.out) instead of auto-deleting them
.SECONDARY:
# Never leave a half-written output behind when a step fails
.DELETE_ON_ERROR:

build: $(GEM5) $(MCPAT) $(HOTSPOT)

$(GEM5):
	scons -C gem5 build/X86/gem5.opt -j$(NPROC)
$(MCPAT):
	$(MAKE) -C mcpat CXX=g++ CC=gcc
$(HOTSPOT):
	$(MAKE) -C HotSpot

run: $(RUNS:%=results/%/steady.temps)

# Stage 1: gem5 (hours). The binary is order-only (after |) on purpose:
# rebuilding gem5 must not silently trigger days of re-simulation.
results/%/m5out/stats.txt: | $(GEM5)
	scripts/run_gem5.sh $* $(@D)

# Stage 2: gem5 stats -> McPAT XML -> power report (seconds)
results/%/mcpat.out: results/%/m5out/stats.txt scripts/gem5_to_mcpat.py templates/mcpat.xml | $(MCPAT)
	python3 scripts/gem5_to_mcpat.py -o results/$*/mcpat.xml results/$*/m5out
	$(MCPAT) -infile results/$*/mcpat.xml -print_level 5 > $@

# Stage 3: McPAT report -> floorplan + power trace -> steady-state temps (seconds)
results/%/steady.temps: results/%/mcpat.out scripts/mcpat_to_hotspot.py templates/hotspot.config | $(HOTSPOT)
	python3 scripts/mcpat_to_hotspot.py results/$*
	$(HOTSPOT) -c templates/hotspot.config \
		-f results/$*/chip.flp -p results/$*/chip.ptrace -steady_file $@

# Gather speedup / power / temperature of every run into one CSV for plotting
collect: run
	python3 scripts/collect.py results > results/summary.csv