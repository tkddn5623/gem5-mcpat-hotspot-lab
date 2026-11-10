"""Shared experiment contract; no gem5 imports, so planning works on the host."""
from dataclasses import asdict, dataclass

WORKLOADS = ("blackscholes", "canneal", "freqmine", "streamcluster")


@dataclass(frozen=True)
class Configuration:
    cores: int
    width: int
    clock_mhz: int = 2000
    technology_nm: int = 45
    voltage: float = 1.0
    leakage_temperature_k: int = 350

    def __post_init__(self):
        if self.cores not in (1, 4, 8) or self.width not in (2, 4, 8):
            raise ValueError("Supported cores: 1/4/8; widths: 2/4/8")

    @property
    def key(self):
        return f"c{self.cores}-w{self.width}"

    def to_dict(self):
        return asdict(self)


def sweeps():
    return {"cores": [Configuration(c, 4) for c in (1, 4, 8)],
            "width": [Configuration(4, w) for w in (2, 4, 8)]}


def configurations():
    return list({c.key: c for group in sweeps().values() for c in group}.values())


def guest_command(workload, cores):
    if workload not in WORKLOADS or cores not in (1, 4, 8):
        raise ValueError("Invalid workload or thread count")
    # gcc-hooks in the official x86-parsec image emits global workbegin/end.
    # freqmine uses OpenMP; match both OpenMP and parsecmgmt thread counts.
    return f"""#!/bin/bash
set -e
cd /home/gem5/parsec-benchmark
source env.sh
export OMP_NUM_THREADS={cores}
export OMP_DYNAMIC=FALSE
parsecmgmt -a run -p {workload} -c gcc-hooks -i simsmall -n {cores}
m5 exit
"""
