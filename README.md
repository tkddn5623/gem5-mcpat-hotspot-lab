# gem5-mcpat-hotspot-lab

2026 Fall Multicore Computing (SWE3021_41) Assignment

```
gem5 (stats.txt, config.json)
  ↓ activity factor
McPAT (power, area)
  ↓ power trace
HotSpot (steady-state temperature)
```

## Simulator versions

All three are imported with `git subtree --squash`.

| Simulator                                                    | Directory  | Version   |
| ------------------------------------------------------------ | ---------- | --------- |
| [gem5](https://github.com/gem5/gem5/tree/v26.0.0.0)          | `gem5/`    | v26.0.0.0 |
| [McPAT](https://github.com/HewlettPackard/mcpat/tree/v1.3.0) | `mcpat/`   | v1.3.0    |
| [HotSpot](https://github.com/uvahotspot/HotSpot/tree/v7.0)   | `HotSpot/` | v7.0      |

## How to Run Without Docker

### Install Dependencies

Install gem5's dependencies by following the official guide:
<https://www.gem5.org/documentation/general_docs/building>

### Make targets

The Makefile knows nothing about Docker, so it also works natively once all dependencies are installed.

| Command                                        | What it does                                            |
| ---------------------------------------------- | ------------------------------------------------------- |
| `make build`                                   | Build gem5 / McPAT / HotSpot                            |
| `make run`                                     | Every config × workload, all three stages               |
| `make run -j4`                                 | Same, 4 runs in parallel (each gem5 is single-threaded) |
| `make results/1c_4w_blackscholes/steady.temps` | Just one run                                            |
| `make collect`                                 | `results/summary.csv` for plotting                      |

If the linker gets killed while building gem5 (out of memory), lower the parallelism: `make build NPROC=4`.

## How to Run With Docker (Ubuntu 24.04 host)

### One-time setup

Create `.env` **without** `sudo`.

```bash
printf '%s\n' \
  "MULTICORE_LAB_HOST_UID=$(id -u)" \
  "MULTICORE_LAB_HOST_GID=$(id -g)" \
  "MULTICORE_LAB_REPO_DIR=$(pwd)" \
  "MULTICORE_LAB_KVM_GID=$(getent group kvm | cut -d: -f3)" \
  > .env
# The purpose of this is to ensure that you own the file, rather than having root have it.
```

Then build the image:

```bash
sudo docker compose build
```

### Services

| Service | `/dev/kvm` | Use for                                             |
| ------- | ---------- | --------------------------------------------------- |
| `dev`   | no         | Building, debugging, anything that doesn't need KVM |
| `kvm`   | yes        | gem5 runs that boot Linux with the KVM CPU          |

Booting Linux in a fully simulated CPU is very slow, and the assignment only measures the ROI,
so gem5 can boot with the KVM CPU and switch to the O3 CPU before the ROI.
Whether the KVM CPU is used is decided by the gem5 config script; the service only makes `/dev/kvm` available.

### Common commands

It is simply the make commands at the top with `sudo docker compose run --rm` appended.

```bash
# Build all three tools
sudo docker compose run --rm dev make build

# Interactive shell (gdb works)
sudo docker compose run --rm dev

# All simulations, or just one
sudo docker compose run --rm kvm make run -j4
sudo docker compose run --rm kvm make results/1c_4w_blackscholes/steady.temps

# Summarize results into results/summary.csv (also runs any missing simulations)
sudo docker compose run --rm kvm make collect
```

### Smoke tests

```bash
# gem5: SE-mode hello world (seconds, no download)
sudo docker compose run --rm kvm \
  gem5/build/X86/gem5.opt -d results/test-hello \
  gem5/configs/learning_gem5/part1/simple.py

# gem5: boot Ubuntu with KVM (first run downloads a multi-GB disk image into resources/)
sudo docker compose run --rm kvm \
  gem5/build/X86/gem5.opt -d results/test-kvm \
  gem5/configs/example/gem5_library/x86-ubuntu-run-with-kvm-no-perf.py

# McPAT: bundled ARM Cortex-A9 example (seconds);
# numbers should match mcpat/ExampleResults/ARM_A9_2GHz
sudo docker compose run --rm dev \
  mcpat/mcpat -infile mcpat/ProcessorDescriptionFiles/ARM_A9_2GHz.xml -print_level 1

# HotSpot: bundled Alpha EV6 example (instant);
# writes per-block steady-state temperatures (Kelvin) to results/test-hotspot/gcc.steady
sudo docker compose run --rm dev sh -c '
  E=HotSpot/examples/example1
  mkdir -p results/test-hotspot
  HotSpot/hotspot -c $E/example.config -f $E/ev6.flp -p $E/gcc.ptrace \
    -materials_file $E/example.materials -model_type block \
    -steady_file results/test-hotspot/gcc.steady'
```

````

### Windows / macOS / virtual machines

If the host has no `/dev/kvm`, delete the `kvm` service from `docker-compose.yml` and use `dev` for everything.

## Optional: perf counters for KVM

Our configs set `usePerf = False` on KVM cores, so no host change is needed.
gem5's own examples default to `usePerf = True` and may fail with a perf permission error.
To run them, allow perf counters on the host:

```bash
sudo sysctl kernel.perf_event_paranoid=1
````

This is a host-wide setting and is reset on reboot.
