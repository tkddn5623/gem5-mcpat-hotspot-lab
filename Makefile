GEM5    := gem5/build/X86/gem5.opt
MCPAT   := mcpat/mcpat
HOTSPOT := HotSpot/hotspot

# NPROC: Parallel jobs for the gem5 build lower it if the linker gets OOM-killed
NPROC ?= $(shell nproc)

# On page 4 of the assignment PDF
WORKLOADS := blackscholes canneal freqmine streamcluster
CONFIGS   := 1c_4w 4c_4w 8c_4w 4c_2w 4c_8w
RUNS      := $(foreach c,$(CONFIGS),$(foreach w,$(WORKLOADS),$(c)_$(w)))
TEMPS     := $(RUNS:%=results/%/steady.temps)

# .SECONDARY: Keep intermediate files (stats.txt, mcpat.out) instead of auto-deleting them
.PHONY: build gem5 run collect
.SECONDARY:
.DELETE_ON_ERROR:

build: gem5 $(MCPAT) $(HOTSPOT)

gem5:
	cd gem5 && scons build/X86/gem5.opt -j$(NPROC)

# $(GEM5):  Used only when gem5 is not built at all (Since there is no prerequisite for make)
# $(MCPAT): mcpat.mk hardcodes "g++ -m32"; override to build 64-bit without multilib
$(GEM5):
	cd gem5 && scons build/X86/gem5.opt -j$(NPROC)

$(MCPAT):
	$(MAKE) -C mcpat CXX=g++ CC=gcc

$(HOTSPOT):
	$(MAKE) -C HotSpot

run: $(TEMPS)

# Stage 1: gem5 (hours). The binary is order-only (after |) on purpose:
# rebuilding gem5 must not silently trigger days of re-simulation.
# run_gem5.sh must exit non-zero when gem5 fails, so .DELETE_ON_ERROR
# removes a partial stats.txt.
results/%/m5out/stats.txt: | $(GEM5)
	scripts/run_gem5.sh $* $(@D)

# Stage 2: gem5 stats -> McPAT XML -> power report (seconds)
results/%/mcpat.out: results/%/m5out/stats.txt scripts/gem5_to_mcpat.py templates/mcpat.xml | $(MCPAT)
	python3 scripts/gem5_to_mcpat.py -t templates/mcpat.xml -o results/$*/mcpat.xml results/$*/m5out
	$(MCPAT) -infile results/$*/mcpat.xml -print_level 5 > $@

# Stage 3: McPAT report -> floorplan + power trace -> steady-state temps (seconds)
results/%/steady.temps: results/%/mcpat.out scripts/mcpat_to_hotspot.py templates/hotspot.config | $(HOTSPOT)
	python3 scripts/mcpat_to_hotspot.py results/$*
	$(HOTSPOT) -c templates/hotspot.config \
		-f results/$*/chip.flp -p results/$*/chip.ptrace -steady_file $@

# Gather speedup / power / temperature of every run into one CSV for plotting.
collect: results/summary.csv

results/summary.csv: $(TEMPS) scripts/collect.py
	python3 scripts/collect.py results > $@