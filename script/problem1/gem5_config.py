"""gem5 v26 full-system config. Run through gem5.opt, not host Python."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from problem1.model import Configuration, WORKLOADS, guest_command


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cores", type=int, choices=(1, 4, 8), required=True)
    parser.add_argument("--width", type=int, choices=(2, 4, 8), required=True)
    parser.add_argument("--workload", choices=WORKLOADS)
    parser.add_argument("--smoke-directory", type=Path, help="Short SE validation guests; no PARSEC")
    parser.add_argument("--kernel", type=Path)
    parser.add_argument("--disk", type=Path)
    parser.add_argument("--root-partition", default="1")
    parser.add_argument("--resource-directory", default="resources")
    parser.add_argument("--max-ticks", type=int, default=10**16,
                        help="Whole-run watchdog, including Linux boot (default 10000 s simulated)")
    args = parser.parse_args()
    if not args.smoke_directory and not args.workload:
        parser.error("--workload is required for PARSEC")
    if bool(args.kernel) != bool(args.disk):
        parser.error("Provide both --kernel and --disk, or neither")
    if args.max_ticks <= 0:
        parser.error("--max-ticks must be positive")

    import m5
    if not m5.core.gem5Version.startswith("26."):
        raise RuntimeError("This configuration and statistic mapping require gem5 v26")
    from m5.objects import BranchPredictor, TournamentBP, IQUnit
    from gem5.components.boards.x86_board import X86Board
    from gem5.components.boards.simple_board import SimpleBoard
    from gem5.components.cachehierarchies.classic.private_l1_shared_l2_cache_hierarchy import PrivateL1SharedL2CacheHierarchy
    from gem5.components.memory import DualChannelDDR4_2400
    from gem5.components.processors.cpu_types import CPUTypes
    from gem5.components.processors.simple_processor import SimpleProcessor
    from gem5.isas import ISA
    from gem5.resources.resource import KernelResource, DiskImageResource, BinaryResource, obtain_resource
    from gem5.simulate.exit_event import ExitEvent
    from gem5.simulate.simulator import Simulator

    cfg = Configuration(args.cores, args.width)
    processor = SimpleProcessor(cpu_type=CPUTypes.O3, isa=ISA.X86,
                                num_cores=cfg.cores, clk_freq=f"{cfg.clock_mhz}MHz")
    for core in processor.get_cores():
        cpu = core.get_simobject()
        for field in ("fetchWidth", "decodeWidth", "renameWidth", "dispatchWidth",
                      "issueWidth", "wbWidth", "commitWidth"):
            setattr(cpu, field, cfg.width)
        cpu.numROBEntries = 192
        cpu.instQueues = [IQUnit(numEntries=64)]
        cpu.numPhysIntRegs = 256
        cpu.numPhysFloatRegs = 256
        cpu.LQEntries = 32
        cpu.SQEntries = 32
        cpu.branchPred = BranchPredictor(conditionalBranchPred=TournamentBP())

    cache = PrivateL1SharedL2CacheHierarchy(l1i_size="32KiB", l1d_size="32KiB",
                                          l2_size="2MiB", l1i_assoc=8,
                                          l1d_assoc=8, l2_assoc=16)
    board_type = SimpleBoard if args.smoke_directory else X86Board
    board = board_type(clk_freq=f"{cfg.clock_mhz}MHz", processor=processor,
                     memory=DualChannelDDR4_2400(size="3GiB"), cache_hierarchy=cache)
    if args.smoke_directory:
        binaries = [BinaryResource(local_path=str((args.smoke_directory / ("leader" if i == 0 else "worker")).resolve()))
                    for i in range(cfg.cores)]
        board.set_se_multi_binary_workload(binaries=binaries)
    elif args.kernel:
        for path in (args.kernel, args.disk):
            if not path.is_file():
                raise FileNotFoundError(path)
        kernel = KernelResource(local_path=str(args.kernel.resolve()))
        disk = DiskImageResource(local_path=str(args.disk.resolve()), root_partition=args.root_partition)
    else:
        kernel = obtain_resource("x86-linux-kernel-4.19.83", resource_version="1.0.0", gem5_version=None,
                                 resource_directory=args.resource_directory)
        disk = obtain_resource("x86-parsec", resource_version="1.0.0", gem5_version=None,
                               resource_directory=args.resource_directory)
    if not args.smoke_directory:
        board.set_kernel_disk_workload(kernel=kernel, disk_image=disk,
                                       readfile_contents=guest_command(args.workload, cfg.cores))
    # Stay on O3 throughout boot and ROI: portable without KVM, no CPU-switch
    # counter attribution or cold-cache reset at workbegin. Boot is slower.
    out = Path(m5.options.outdir)
    state = {"begin_tick": None, "end_tick": None}

    def begin():
        while True:
            if state["begin_tick"] is not None:
                raise RuntimeError("Multiple ROI begins: expected one global PARSEC ROI")
            state["begin_tick"] = int(m5.curTick())
            m5.stats.reset()
            yield False

    def end():
        if state["begin_tick"] is None:
            raise RuntimeError("ROI ended before workbegin")
        state["end_tick"] = int(m5.curTick())
        m5.stats.dump()
        yield True

    sim = Simulator(board=board, on_exit_event={ExitEvent.WORKBEGIN: begin(),
                                               ExitEvent.WORKEND: end()},
                    max_ticks=args.max_ticks)
    sim.run()
    if state["end_tick"] is None or state["end_tick"] <= state["begin_tick"]:
        raise RuntimeError("No complete PARSEC ROI; inspect gem5.log and guest serial output")
    # Explicit paths remove any dependency on guessed SimObject names.
    manifest = {"schema": 1, "configuration": cfg.to_dict(), "workload": "__smoke__" if args.smoke_directory else args.workload,
                "kind": "smoke" if args.smoke_directory else "parsec",
                "input": "selftest" if args.smoke_directory else "simsmall", "roi": state,
                "cpu_paths": [c.get_simobject().path() for c in processor.get_cores()],
                "l1i_paths": [c.path() for c in cache.l1icaches],
                "l1d_paths": [c.path() for c in cache.l1dcaches],
                "l2_path": cache.l2cache.path(),
                "bus_paths": [cache.l2bus.path(), cache.membus.path()],
                "memory_paths": [c.path() for c in board.get_memory().get_memory_controllers()]}
    (out / "roi.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__m5_main__":
    main()
