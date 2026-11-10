GEM5    := gem5/build/X86/gem5.opt
MCPAT   := mcpat/mcpat
HOTSPOT := HotSpot/hotspot

# NPROC: Parallel jobs for the gem5 build lower it if the linker gets OOM-killed
NPROC ?= $(shell nproc)

.PHONY: build gem5 plan check run postprocess collect
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

# ---------------------------------------------------------------------------
# Experiments: driven by script/problem1/run.py (python -m problem1.run).
# The workload x configuration matrix lives in script/problem1/model.py;
# the names below only mirror its output layout: results/<workload>/c<cores>-w<width>/
# ---------------------------------------------------------------------------

export PYTHONPATH := $(CURDIR)/script
TOOLS := --gem5 $(CURDIR)/$(GEM5) --mcpat $(CURDIR)/$(MCPAT) --hotspot $(CURDIR)/$(HOTSPOT)
PIPELINE := python3 -m problem1.run

# Must match model.py: 3 + 3 configs with c4-w4 shared, 4 PARSEC workloads (20 runs)
WORKLOADS := blackscholes canneal freqmine streamcluster
CONFIGS   := c1-w4 c4-w4 c8-w4 c4-w2 c4-w8
RESULTS   := $(foreach w,$(WORKLOADS),$(foreach c,$(CONFIGS),results/$(w)/$(c)/result.json))

# results/<workload>/c<cores>-w<width> -> workload, cores, width
run_workload = $(word 2,$(subst /, ,$1))
run_config   = $(word 3,$(subst /, ,$1))
run_cores    = $(patsubst c%,%,$(word 1,$(subst -, ,$(call run_config,$1))))
run_width    = $(patsubst w%,%,$(word 2,$(subst -, ,$(call run_config,$1))))

# Print the 20-run matrix without simulating anything
plan:
	$(PIPELINE) plan

# Short gem5 -> McPAT -> HotSpot check with a tiny guest program (no PARSEC)
check: | $(GEM5) $(MCPAT) $(HOTSPOT)
	$(PIPELINE) check $(TOOLS) --output results/check

# All 20 runs; "make run -j4" simulates 4 at a time (each gem5 is single-threaded)
run: $(RESULTS)

# One run = gem5 + McPAT + HotSpot. run.py writes result.json only on success.
# The tools are order-only: rebuilding gem5 must not re-trigger finished runs.
# run.py refuses to reuse a run directory: delete a failed one before retrying.
results/%/result.json: | $(GEM5) $(MCPAT) $(HOTSPOT)
	$(PIPELINE) run $(TOOLS) --output results \
		--workload $(call run_workload,$@) \
		--cores $(call run_cores,$@) --width $(call run_width,$@)

# Re-run only McPAT + HotSpot on finished gem5 outputs (after changing the glue code)
postprocess: | $(MCPAT) $(HOTSPOT)
	$(PIPELINE) postprocess $(TOOLS) --output results

# summary.csv, components.csv and the plots; needs all 20 runs
collect: results/summary.csv

results/summary.csv: $(RESULTS)
	$(PIPELINE) summarize --output results