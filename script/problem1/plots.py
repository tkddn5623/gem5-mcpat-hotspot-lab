"""Standard static plots; loaded only when complete experiment data exist."""

def plot(rows, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from .model import WORKLOADS
    for sweep, axis in (("cores", "cores"), ("width", "width")):
        fig, axes = plt.subplots(1, 3, figsize=(13, 3.8), layout="constrained")
        for ax, metric, label in zip(axes, ("speedup", "power_w", "peak_temperature_c"),
                                     ("Normalized speedup", "Whole-chip average power (W)", "Peak steady-state temperature (°C)")):
            for w in WORKLOADS:
                data = [r for r in rows if r["sweep"] == sweep and r["workload"] == w]
                ax.plot([r[axis] for r in data], [r[metric] for r in data], marker="o", label=w)
            ax.set(xlabel="Core count" if sweep == "cores" else "Fetch / issue / commit width", ylabel=label)
            ax.set_xticks((1, 4, 8) if sweep == "cores" else (2, 4, 8))
            ax.grid(alpha=0.25)
        axes[0].legend(fontsize=8)
        fig.savefig(output / f"{sweep}.png", dpi=180)
        fig.savefig(output / f"{sweep}.pdf")
        plt.close(fig)
