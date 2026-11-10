"""McPAT report parsing, conservative power accounting, and HotSpot inputs."""
import math
import re

METRICS = {"Area": "area_mm2", "Runtime Dynamic": "dynamic_w",
           "Subthreshold Leakage": "subthreshold_w", "Gate Leakage": "gate_w"}


def parse_report(text, cores):
    sections = []
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        match = re.match(r"(Area|Runtime Dynamic|Subthreshold Leakage|Gate Leakage)\s*=\s*(\S+)\s+(mm\^2|W)$", stripped)
        if match and current is not None:
            name, number, unit = match.groups()
            if unit != ("mm^2" if name == "Area" else "W"):
                raise ValueError("McPAT report unit mismatch")
            value = float(number)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid McPAT metric: {line}")
            current[METRICS[name]] = value
        elif stripped and "=" not in stripped:
            current = {"label": stripped.rstrip(":"), "indent": len(line)-len(line.lstrip())}
            sections.append(current)

    def complete(s):
        return all(k in s for k in METRICS.values())

    def select(label, indent=None):
        return [s for s in sections if s["label"] == label and complete(s)
                and (indent is None or s["indent"] == indent)]

    def block(s, name, group):
        result = {k: s[k] for k in METRICS.values()}
        result.update(name=name, group=group)
        result["static_w"] = result["subthreshold_w"] + result["gate_w"]
        result["power_w"] = result["static_w"] + result["dynamic_w"]
        if result["area_mm2"] <= 0:
            raise ValueError(f"Nonpositive area for {name}")
        return result

    processor = select("Processor", 0)
    core_reports = select("Core", 0)
    icaches = select("Instruction Cache")
    dcaches = select("Data Cache")
    if len(processor) != 1 or any(len(s) != cores for s in (core_reports, icaches, dcaches)):
        raise ValueError("Expected per-core + L1 detail; run McPAT with -print_level 5 and homogeneous_cores=0")
    blocks = []
    for i, (c, ic, dc) in enumerate(zip(core_reports, icaches, dcaches)):
        # L1s are inside Core in McPAT. Subtract them before separate placement.
        logic = {k: c[k]-ic[k]-dc[k] for k in METRICS.values()}
        if any(v < -1e-4 for v in logic.values()):
            raise ValueError("L1 power/area exceeds parent core")
        logic = {k: max(0, v) for k, v in logic.items()}
        blocks.extend([block(logic, f"core{i}", "core"), block(ic, f"l1i{i}", "cache"),
                       block(dc, f"l1d{i}", "cache")])
    for label, count, prefix, group in (("L2", 1, "l2", "cache"),
                                        ("BUSES", 2, "bus", "interconnect"),
                                        ("Memory Controller", 1, "mc", "memory_controller")):
        found = select(label, 0)
        if len(found) != count:
            raise ValueError(f"Expected {count} {label} components; got {len(found)}")
        blocks.extend(block(s, f"{prefix}{i}", group) for i, s in enumerate(found))
    total = block(processor[0], "processor", "total")
    for metric in ("area_mm2", "power_w", "dynamic_w", "static_w"):
        actual = sum(b[metric] for b in blocks)
        if not math.isclose(actual, total[metric], rel_tol=0.002, abs_tol=1e-4):
            raise ValueError(f"McPAT component sum mismatch for {metric}: {actual} vs {total[metric]}")
    return {"processor": total, "blocks": blocks}


def floorplan(power):
    """Area-preserving compact rectangle: one equal-height strip per block.

    This is a deterministic architectural floorplan, not a physical placement.
    Every block's full McPAT power is included exactly once.
    """
    blocks = power["blocks"]
    side_m = math.sqrt(sum(b["area_mm2"] for b in blocks)) * 1e-3
    x = 0.0
    lines = []
    for b in blocks:
        width = b["area_mm2"] * 1e-6 / side_m
        lines.append(f"{b['name']}\t{width:.12g}\t{side_m:.12g}\t{x:.12g}\t0")
        x += width
    trace = "\t".join(b["name"] for b in blocks) + "\n"
    trace += "\t".join(f"{b['power_w']:.12g}" for b in blocks) + "\n"
    return "\n".join(lines) + "\n", trace


def temperatures(text, power):
    values = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                values[parts[0]] = float(parts[1])
            except ValueError:
                continue
    result = {}
    for b in power["blocks"]:
        k = values.get(b["name"])
        if k is None or not math.isfinite(k) or k <= 0:
            raise ValueError(f"Missing or invalid HotSpot temperature: {b['name']}")
        result[b["name"]] = k - 273.15
    # Ignore package/spreader nodes in HotSpot output.
    area = sum(b["area_mm2"] for b in power["blocks"])
    return {"blocks_c": result, "peak_c": max(result.values()),
            "area_weighted_c": sum(result[b["name"]]*b["area_mm2"] for b in power["blocks"])/area}
