"""gem5 v26 -> McPAT, with per-core activity and explicit model assumptions.

The upstream XML is a schema/physical-model seed, never a source of activity.
All seed statistics are reset before measured or documented derived values
are inserted. See MODEL.md for the abstraction boundaries.
"""
from copy import deepcopy
import math
from pathlib import Path
import xml.etree.ElementTree as ET
from .stats import Stats, objects

TEMPLATE = Path(__file__).with_name("mcpat-template.xml")


def put(node, kind, name, value):
    field = node.find(f"{kind}[@name='{name}']")
    if field is None:
        field = ET.SubElement(node, kind, name=name)
    field.set("value", str(value))


def fields(node, kind, values):
    for name, value in values.items():
        put(node, kind, name, value)


def child(node, suffix):
    return next(c for c in node.findall("component") if c.get("id").endswith("." + suffix))


def convert(config, manifest, stats_text):
    cfg = manifest["configuration"]
    stats = Stats.roi(stats_text, manifest["roi"])
    obj = objects(config)
    seconds = stats.get("simTicks") / stats.get("simFreq")
    cycles = seconds * cfg["clock_mhz"] * 1e6
    if cycles <= 0:
        raise ValueError("Empty ROI")
    if len(manifest["cpu_paths"]) != cfg["cores"]:
        raise ValueError("Core count disagrees with ROI manifest")
    tree = ET.parse(TEMPLATE)
    system = tree.find(".//component[@id='system']")
    seed = deepcopy(child(system, "core0"))
    for c in list(system.findall("component")):
        if c.get("id").startswith("system.core"):
            system.remove(c)
    # No ARM/example workload counters survive, including disabled devices.
    for stat in tree.iter("stat"):
        stat.set("value", "0")
    fields(system, "param", {
        "number_of_cores": cfg["cores"], "homogeneous_cores": 0,
        "number_of_L1Directories": 0, "number_of_L2Directories": 0,
        "number_of_L2s": 1, "Private_L2": 0, "homogeneous_L2s": 0,
        "number_of_L3s": 0, "number_of_NoCs": 2, "homogeneous_NoCs": 0,
        "number_cache_levels": 2, "core_tech_node": cfg["technology_nm"],
        "target_core_clockrate": cfg["clock_mhz"], "temperature": cfg["leakage_temperature_k"],
        "longer_channel_device": 0, "power_gating": 0, "physical_address_width": 48,
    })
    fields(system, "stat", {"total_cycles": cycles, "busy_cycles": cycles, "idle_cycles": 0})

    def cache_map(xml, path, field):
        c = obj[path]
        # The classic hierarchy uses the board clock and 64-byte lines.
        fields(xml, "param", {field: f"{c['size']},64,{c['assoc']},1,1,{int(c['tag_latency']) + int(c['data_latency'])},64,1",
                              "buffer_sizes": f"{c['mshrs']},{c['mshrs']},4,{c['write_buffers']}",
                              "clockrate": cfg["clock_mhz"], "vdd": cfg["voltage"]})
        # Command groups in BaseCache::CacheCmdStats. ReadExReq obtains a line
        # for a CPU store and is a read of the next cache; WritebackDirty writes.
        writes = ("WriteReq", "WritebackDirty", "WritebackClean", "WriteClean")
        reads = ("ReadReq", "ReadExReq", "ReadSharedReq", "ReadCleanReq",
                 "SoftPFReq", "HardPFReq", "LoadLockedReq", "SwapReq")
        for output, commands, event in (
            ("read_accesses", reads, "accesses"), ("write_accesses", writes, "accesses"),
            ("read_misses", reads, "misses"), ("write_misses", writes, "misses"),
        ):
            value = sum(stats.vector(f"{path}.{cmd}.{event}", suppressed=True) for cmd in commands)
            put(xml, "stat", output, value)
        # Require cache-level schema evidence, while allowing zero-activity cores.
        if not any(k.startswith(path + ".") for k in stats.values):
            raise ValueError(f"No cache statistics for {path}")
        put(xml, "stat", "duty_cycle", 1)

    for index, path in enumerate(manifest["cpu_paths"]):
        cpu = obj[path]
        for name in ("fetchWidth", "decodeWidth", "renameWidth", "dispatchWidth", "issueWidth", "wbWidth", "commitWidth"):
            if int(cpu[name]) != cfg["width"]:
                raise ValueError(f"{path}.{name} does not match experiment")
        core = deepcopy(seed)
        for node in core.iter("component"):
            node.set("id", node.get("id").replace("system.core0", f"system.core{index}"))
        core.set("name", f"core{index}")
        for stat in core.iter("stat"):
            stat.set("value", "0")
        system.insert(index, core)
        get = lambda name, suppressed=False: stats.get(f"{path}.{name}", suppressed=suppressed)
        commit = lambda name, suppressed=False: get("commitStats0." + name, suppressed)
        ops = commit("numOps")
        active = get("numCycles")
        if active > cycles * 1.001:
            raise ValueError("CPU cycles exceed ROI clock cycles")
        bp = cpu["branchPred"]
        pred = bp["conditionalBranchPred"]
        iq = cpu["instQueues"]
        if len(iq) != 1 or pred["type"] != "TournamentBP":
            raise ValueError("Converter requires one IQ and TournamentBP")
        fu = iq[0]["fuPool"]["FUList"]
        # IntALU/FP_ALU Python subclasses serialize as type=FUDesc. Identify
        # capabilities from opList instead of relying on Python class names.
        capabilities = {"IntALU": {"IntAlu"}, "IntMultDiv": {"IntMult"},
                        "FP_ALU": {"FloatAdd"}, "FP_MultDiv": {"FloatMult"},
                        "SIMD_Unit": {"SimdAdd"}}
        def fu_count(kind):
            return sum(int(f["count"]) for f in fu
                       if capabilities[kind] & {o["opClass"] for o in f["opList"]})
        memory_ports = sum(int(f["count"]) for f in fu
                           if {"MemRead", "MemWrite"} & {o["opClass"] for o in f["opList"]})
        fields(core, "param", {
            "clock_rate": cfg["clock_mhz"], "vdd": cfg["voltage"], "x86": 1,
            "machine_type": 0, "number_hardware_threads": 1,
            "fetch_width": cpu["fetchWidth"], "decode_width": cpu["decodeWidth"],
            "issue_width": cpu["issueWidth"], "peak_issue_width": cpu["issueWidth"],
            "commit_width": cpu["commitWidth"], "fp_issue_width": min(cfg["width"], fu_count("FP_ALU") + fu_count("FP_MultDiv")),
            "ROB_size": cpu["numROBEntries"], "instruction_window_size": iq[0]["numEntries"],
            "fp_instruction_window_size": iq[0]["numEntries"],
            "phy_Regs_IRF_size": cpu["numPhysIntRegs"], "phy_Regs_FRF_size": cpu["numPhysFloatRegs"],
            "load_buffer_size": cpu["LQEntries"], "store_buffer_size": cpu["SQEntries"],
            "ALU_per_core": fu_count("IntALU"), "MUL_per_core": fu_count("IntMultDiv"),
            "FPU_per_core": fu_count("FP_ALU") + fu_count("FP_MultDiv") + fu_count("SIMD_Unit"),
            "memory_ports": memory_ports,
            "RAS_size": bp["ras"]["numEntries"], "number_of_BPT": 1, "number_of_BTB": 1,
            "pipeline_depth": "14,14", "instruction_buffer_size": max(1, int(cpu["fetchBufferSize"]) // 4),
            "decoded_stream_buffer_size": cpu["fetchQueueSize"],
        })
        fields(child(core, "predictor"), "param", {
            "local_predictor_size": f"{int(math.log2(int(pred['localPredictorSize'])))},{pred['localCtrBits']}",
            "local_predictor_entries": pred["localHistoryTableSize"],
            "global_predictor_entries": pred["globalPredictorSize"], "global_predictor_bits": pred["globalCtrBits"],
            "chooser_predictor_entries": pred["choicePredictorSize"], "chooser_predictor_bits": pred["choiceCtrBits"],
        })
        fp = commit("numFpInsts") + commit("numVecInsts")
        fields(core, "stat", {
            "total_cycles": cycles, "busy_cycles": min(active, cycles), "idle_cycles": max(0, cycles-active),
            "total_instructions": ops, "committed_instructions": ops,
            "int_instructions": commit("numIntInsts"), "committed_int_instructions": commit("numIntInsts"),
            "fp_instructions": fp, "committed_fp_instructions": fp,
            "branch_instructions": stats.vector(path + ".commitStats0.committedControl::IsControl", suppressed=True),
            "branch_mispredictions": get("commit.branchMispredicts", True),
            "load_instructions": commit("numLoadInsts"), "store_instructions": commit("numStoreInsts"),
            "function_calls": commit("functionCalls"),
            "pipeline_duty_cycle": min(1, ops / (cycles * cfg["width"])),
            "ROB_reads": get("rob.reads"), "ROB_writes": get("rob.writes"),
            # RenameStats marks these with prereq(self): zero values are absent.
            "rename_reads": get("rename.intLookups", True),
            "fp_rename_reads": get("rename.fpLookups", True) + get("rename.vecLookups", True),
            # gem5 reports lookups but not per-register-class destination writes.
            "rename_writes": commit("numIntInsts"), "fp_rename_writes": fp,
            "inst_window_reads": get("intInstQueueReads"), "inst_window_writes": get("intInstQueueWrites"),
            "inst_window_wakeup_accesses": get("intInstQueueWakeupAccesses"),
            "fp_inst_window_reads": get("fpInstQueueReads") + get("vecInstQueueReads"),
            "fp_inst_window_writes": get("fpInstQueueWrites") + get("vecInstQueueWrites"),
            "fp_inst_window_wakeup_accesses": get("fpInstQueueWakeupAccesses") + get("vecInstQueueWakeupAccesses"),
            "int_regfile_reads": get("executeStats0.numIntRegReads", True) + get("executeStats0.numCCRegReads", True),
            "int_regfile_writes": get("executeStats0.numIntRegWrites", True) + get("executeStats0.numCCRegWrites", True),
            "float_regfile_reads": get("executeStats0.numFpRegReads", True) + get("executeStats0.numVecRegReads", True),
            "float_regfile_writes": get("executeStats0.numFpRegWrites", True) + get("executeStats0.numVecRegWrites", True),
            "ialu_accesses": get("intAluAccesses"),
            "fpu_accesses": get("fpAluAccesses") + get("vecAluAccesses"),
            "mul_accesses": sum(stats.vector(path + ".issuedInstType_0::" + op, suppressed=True) for op in ("IntMult", "IntDiv")),
            "cdb_alu_accesses": get("intAluAccesses"),
            "cdb_fpu_accesses": get("fpAluAccesses") + get("vecAluAccesses"),
        })
        mul = float(core.find("stat[@name='mul_accesses']").get("value"))
        for name in ("ialu_accesses", "cdb_alu_accesses"):
            put(core, "stat", name, max(0, get("intAluAccesses") - mul))
        put(core, "stat", "cdb_mul_accesses", core.find("stat[@name='mul_accesses']").get("value"))
        for stat in core.findall("stat"):
            if stat.get("name").endswith("_duty_cycle") and stat.get("name") != "pipeline_duty_cycle":
                stat.set("value", "1")  # peak model only; runtime uses measured accesses
        for name, gem5_name in (("itlb", "itb"), ("dtlb", "dtb")):
            tlb = cpu["mmu"][gem5_name]
            xml = child(core, name)
            put(xml, "param", "number_entries", tlb["size"])
            for target, event in (("total_accesses", "Accesses"), ("total_misses", "Misses")):
                put(xml, "stat", target, sum(stats.get(tlb["path"] + "." + op + event) for op in ("rd", "wr", "ex")))
        btb = child(core, "BTB")
        put(btb, "param", "BTB_config", f"{int(bp['btb']['numEntries']) * 8},8,{bp['btb']['associativity']},1,1,1")
        put(btb, "stat", "read_accesses", get("branchPred.BTBLookups"))
        put(btb, "stat", "write_accesses", get("branchPred.BTBUpdates"))
        cache_map(child(core, "icache"), manifest["l1i_paths"][index], "icache_config")
        cache_map(child(core, "dcache"), manifest["l1d_paths"][index], "dcache_config")

    cache_map(child(system, "L20"), manifest["l2_path"], "L2_config")
    noc_seed = deepcopy(child(system, "NoC0"))
    system.remove(child(system, "NoC0"))
    for index, path in enumerate(manifest["bus_paths"]):
        noc = deepcopy(noc_seed)
        noc.set("id", f"system.NoC{index}")
        noc.set("name", f"noc{index}")
        fields(noc, "param", {"clockrate": cfg["clock_mhz"], "vdd": cfg["voltage"],
                              "flit_bits": int(obj[path]["width"]) * 8, "type": 0,
                              "chip_coverage": 1, "link_routing_over_percentage": 0.5})
        fields(noc, "stat", {"total_accesses": stats.vector(path + ".pktCount", suppressed=True), "duty_cycle": 1})
        system.append(noc)
    mc = child(system, "mc")
    fields(mc, "param", {"number_mcs": 1, "memory_channels_per_mc": len(manifest["memory_paths"]),
                          "mc_clock": 1200, "peak_transfer_rate": 19200, "databus_width": 64,
                          "number_ranks": 1, "withPHY": 1, "vdd": cfg["voltage"]})
    reads = sum(stats.get(p + ".readReqs") for p in manifest["memory_paths"])
    writes = sum(stats.get(p + ".writeReqs") for p in manifest["memory_paths"])
    fields(mc, "stat", {"memory_reads": reads, "memory_writes": writes, "memory_accesses": reads+writes})
    # McPAT's parser consumes components positionally, including disabled
    # homogeneous directory/L3 placeholders. Keep its required schema order.
    order = ([f"core{i}" for i in range(cfg["cores"])] +
             ["L1Directory0", "L2Directory0", "L20", "L30", "NoC0", "NoC1",
              "mc", "niu", "pcie", "flashc"])
    components = {c.get("id").split(".")[-1]: c for c in system.findall("component")}
    for c in components.values():
        system.remove(c)
    for name in order:
        system.append(components[name])
    ET.indent(tree, space="  ")
    audit = {"roi_seconds": seconds, "roi_cycles": cycles,
             "instructions": sum(stats.get(p + ".commitStats0.numInsts", suppressed=True) for p in manifest["cpu_paths"]),
             "zero_suppressed_stats": sorted(stats.zero_suppressed),
             "model_notes": "See MODEL.md; estimates, not calibrated silicon measurements."}
    return tree, audit
