"""Plan, simulate, postprocess, and summarize Problem 1 (python -m problem1.run)."""
import argparse
import csv
import json
from pathlib import Path
import shutil
import subprocess
from .local import resolve_tools
from .model import WORKLOADS, Configuration, configurations, sweeps
from .mcpat import convert
from .thermal import parse_report, floorplan, temperatures

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def execute(command, log, timeout):
    with log.open("w") as stream:
        try:
            subprocess.run([str(x) for x in command], stdout=stream,
                           stderr=subprocess.STDOUT, check=True, timeout=timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"Command failed or timed out. Inspect {log}") from error


def postprocess(directory, args):
    gem5 = directory / "gem5"
    manifest = read(gem5 / "roi.json")
    tree, audit = convert(read(gem5 / "config.json"), manifest, (gem5 / "stats.txt").read_text())
    tree.write(directory / "mcpat.xml", encoding="utf-8", xml_declaration=True)
    write(directory / "mapping.json", audit)
    execute([args.mcpat, "-infile", directory / "mcpat.xml", "-print_level", "5", "-opt_for_clk", "1"],
            directory / "mcpat.log", args.tool_timeout)
    power = parse_report((directory / "mcpat.log").read_text(), manifest["configuration"]["cores"])
    write(directory / "power.json", power)
    flp, trace = floorplan(power)
    (directory / "chip.flp").write_text(flp)
    (directory / "chip.ptrace").write_text(trace)
    steady = directory / "chip.steady"
    steady.unlink(missing_ok=True)
    execute([args.hotspot, "-c", ROOT / "problem1/hotspot.config", "-model_type", "block",
             "-f", directory / "chip.flp", "-p", directory / "chip.ptrace", "-steady_file", steady],
            directory / "hotspot.log", args.tool_timeout)
    thermal = temperatures(steady.read_text(), power)
    result = {"configuration": manifest["configuration"], "workload": manifest["workload"],
              "input": manifest["input"], "roi_seconds": audit["roi_seconds"],
              "instructions": audit["instructions"], "power": power, "temperature": thermal}
    write(directory / "result.json", result)
    return result


def normalized_rows(results):
    lookup = {}
    expected = {c.key: c for c in configurations()}
    for r in results:
        c = Configuration(**r["configuration"])
        if c != expected.get(c.key):
            raise ValueError("Result configuration differs from the fixed experiment settings")
        key = (r["workload"], c.key)
        if key in lookup:
            raise ValueError(f"Duplicate result: {key}")
        if not r["roi_seconds"] > 0:
            raise ValueError("ROI execution time must be positive")
        lookup[key] = r
    rows = []
    for workload in WORKLOADS:
        for sweep, configs in sweeps().items():
            if not all((workload, c.key) in lookup for c in configs):
                raise ValueError(f"Incomplete {sweep} sweep for {workload}; no partial normalization")
            baseline = lookup[workload, configs[0].key]["roi_seconds"]
            for c in configs:
                r = lookup[workload, c.key]
                rows.append({"sweep": sweep, "workload": workload, "cores": c.cores, "width": c.width,
                             "roi_seconds": r["roi_seconds"], "speedup": baseline/r["roi_seconds"],
                             "power_w": r["power"]["processor"]["power_w"],
                             "dynamic_w": r["power"]["processor"]["dynamic_w"],
                             "static_w": r["power"]["processor"]["static_w"],
                             "peak_temperature_c": r["temperature"]["peak_c"],
                             "area_weighted_temperature_c": r["temperature"]["area_weighted_c"]})
    return rows


def summarize(output):
    results = [read(output / w / c.key / "result.json") for w in WORKLOADS for c in configurations()]
    rows = normalized_rows(results)
    with (output / "summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    # Component breakdown for analysis, without nested aggregate double-counting.
    with (output / "components.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["workload", "configuration", "block", "group", "area_mm2", "dynamic_w", "static_w", "temperature_c"])
        for r in results:
            for b in r["power"]["blocks"]:
                writer.writerow([r["workload"], Configuration(**r["configuration"]).key, b["name"], b["group"],
                                 b["area_mm2"], b["dynamic_w"], b["static_w"], r["temperature"]["blocks_c"][b["name"]]])
    from .plots import plot
    plot(rows, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "check", "run", "postprocess", "summarize"))
    parser.add_argument("--output", type=Path, default=Path("results"))
    parser.add_argument("--workload", choices=WORKLOADS)
    parser.add_argument("--cores", type=int, choices=(1, 4, 8))
    parser.add_argument("--width", type=int, choices=(2, 4, 8))
    parser.add_argument("--gem5", default=None)
    parser.add_argument("--mcpat", default=None)
    parser.add_argument("--hotspot", default=None)
    parser.add_argument("--kernel", type=Path)
    parser.add_argument("--disk", type=Path)
    parser.add_argument("--root-partition", default="1")
    parser.add_argument("--resource-directory", default="resources")
    parser.add_argument("--max-ticks", type=int, default=10**16)
    parser.add_argument("--simulation-timeout", type=int, default=7*24*3600)
    parser.add_argument("--tool-timeout", type=int, default=1800)
    args = parser.parse_args()
    if bool(args.kernel) != bool(args.disk):
        parser.error("Supply --kernel and --disk together")
    if any(x <= 0 for x in (args.max_ticks, args.simulation_timeout, args.tool_timeout)):
        parser.error("Timeouts and max ticks must be positive")
    jobs = [(w, c) for w in WORKLOADS for c in configurations()
            if (args.workload is None or w == args.workload)
            and (args.cores is None or c.cores == args.cores)
            and (args.width is None or c.width == args.width)]
    if not jobs:
        parser.error("No matching configuration in the required sweeps")
    if args.action == "plan":
        print(json.dumps([{"workload": w, "input": "simsmall", **c.to_dict()} for w, c in jobs], indent=2))
        return
    output = args.output.resolve()
    if args.action == "summarize":
        summarize(output)
        return
    resolve_tools(args)
    for binary in (args.mcpat, args.hotspot) + ((args.gem5,) if args.action in ("run", "check") else ()):
        if not shutil.which(binary):
            parser.error(f"Executable not found: {binary}")
    if args.action == "check":
        from .smoke_guest import write_guest
        for c in {c.key: c for _, c in jobs}.values():
            directory = output / "_smoke" / c.key
            directory.mkdir(parents=True, exist_ok=False)
            guests = directory / "guests"
            guests.mkdir()
            write_guest(guests / "leader", True)
            write_guest(guests / "worker", False)
            print(f"Checking native gem5 -> McPAT -> HotSpot: {c.key}", flush=True)
            command = [args.gem5, "-d", directory / "gem5", ROOT / "problem1/gem5_config.py",
                       "--cores", c.cores, "--width", c.width, "--smoke-directory", guests,
                       "--max-ticks", 1000000000]
            write(directory / "command.json", [str(x) for x in command])
            execute(command, directory / "gem5.log", args.simulation_timeout)
            result = postprocess(directory, args)
            if result["workload"] != "__smoke__":
                raise RuntimeError("Smoke result incorrectly labeled as a benchmark")
            if result["instructions"] <= 0:
                raise RuntimeError("Smoke ROI did not commit any instructions")
            print(f"PASS: {c.key}, real gem5 ROI and {len(result['power']['blocks'])} thermal blocks", flush=True)
        return
    for name in ("summary.csv", "components.csv", "cores.png", "cores.pdf", "width.png", "width.pdf"):
        (output / name).unlink(missing_ok=True)
    for w, c in jobs:
        directory = output / w / c.key
        if args.action == "run":
            # Never accidentally combine old counters with a new run.
            directory.mkdir(parents=True, exist_ok=False)
            command = [args.gem5, "-d", directory / "gem5", ROOT / "problem1/gem5_config.py",
                       "--cores", c.cores, "--width", c.width, "--workload", w,
                       "--resource-directory", args.resource_directory, "--max-ticks", args.max_ticks]
            if args.kernel:
                command.extend(["--kernel", args.kernel.resolve(), "--disk", args.disk.resolve(),
                                "--root-partition", args.root_partition])
            write(directory / "command.json", [str(x) for x in command])
            print(f"Simulating {w}/{c.key}", flush=True)
            execute(command, directory / "gem5.log", args.simulation_timeout)
        # Reprocessing must not leave an old success marker after a failure.
        (directory / "result.json").unlink(missing_ok=True)
        print(f"Postprocessing {w}/{c.key}", flush=True)
        postprocess(directory, args)
    if all((output / w / c.key / "result.json").is_file()
           for w in WORKLOADS for c in configurations()):
        summarize(output)


if __name__ == "__main__":
    main()
