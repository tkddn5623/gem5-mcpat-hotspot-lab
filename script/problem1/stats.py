"""Strict gem5 text-stat parsing and config.json SimObject lookup."""
import math
import re


class Stats:
    def __init__(self, values):
        self.values = values
        self.zero_suppressed = set()

    @classmethod
    def roi(cls, text, roi):
        blocks = re.findall(r"Begin Simulation Statistics[^\n]*\n(.*?)End Simulation Statistics", text, re.S)
        expected = roi["end_tick"] - roi["begin_tick"]
        matches = []
        for block in blocks:
            values = {}
            for line in block.splitlines():
                fields = line.split()
                if len(fields) < 2 or fields[0].startswith("#"):
                    continue
                try:
                    values[fields[0]] = float(fields[1])
                except ValueError:
                    continue
            # Ignore boot dumps and any shutdown dump; only the exact ROI.
            if values.get("simTicks") == expected and values.get("finalTick") == roi["end_tick"]:
                matches.append(values)
        if not matches:
            raise ValueError("No statistics block matches the completed ROI ticks")
        if any(v != matches[0] for v in matches[1:]):
            # NaN derived ratios compare unequal; compare only finite counters.
            clean = lambda d: {k: v for k, v in d.items() if math.isfinite(v) and not k.startswith("host")}
            if any(clean(v) != clean(matches[0]) for v in matches[1:]):
                raise ValueError("Conflicting statistics dumps for the same ROI")
        return cls(matches[0])

    def get(self, key, *, suppressed=False):
        if key not in self.values:
            if suppressed:
                self.zero_suppressed.add(key)
                return 0.0
            raise ValueError(f"Required gem5 v26 statistic missing: {key}")
        value = self.values[key]
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid statistic {key}: {value}")
        return value

    def vector(self, prefix, *, suppressed=False):
        if prefix + "::total" in self.values:
            return self.get(prefix + "::total")
        if prefix in self.values:
            return self.get(prefix)
        keys = [k for k in self.values if k.startswith(prefix + "::")
                and not k.endswith(("::total", "::pdf", "::cdf"))]
        if not keys:
            return self.get(prefix + "::total", suppressed=suppressed)
        return sum(self.get(k) for k in keys)


def objects(config):
    result = {}
    def walk(value):
        if isinstance(value, dict):
            if "path" in value and "type" in value:
                result[value["path"]] = value
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(config)
    return result
