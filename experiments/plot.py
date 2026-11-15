import csv
from pathlib import Path

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = Path(__file__).resolve().parent
RESULTS = BASE / "results"
GRAPHS = RESULTS / "graphs"

GRAPHS.mkdir(parents=True, exist_ok=True)

SUMMARY = RESULTS / "summary.csv"
MODES = ["smart", "roundrobin", "static"]


def in_mode_order(data):
    return data.set_index("mode").reindex(
        [m for m in MODES if m in set(data["mode"])]
    ).reset_index()


def create_comparison_graphs(df):
    # Average response time comparison
    for exp in sorted(df["experiment"].unique()):
        data = in_mode_order(df[df["experiment"] == exp])

        plt.figure(figsize=(8, 5))
        plt.bar(data["mode"], data["average_response_ms"])
        plt.xlabel("CDN Mode")
        plt.ylabel("Average Response Time (ms)")
        plt.title(f"Experiment {exp}: Average Response Time")
        plt.grid(axis="y")
        plt.tight_layout()

        output = GRAPHS / f"exp{exp}_response_comparison.png"
        plt.savefig(output)
        plt.close()
        print("Created:", output)

    # Failed requests comparison
    for exp in sorted(df["experiment"].unique()):
        data = in_mode_order(df[df["experiment"] == exp])

        plt.figure(figsize=(8, 5))
        plt.bar(data["mode"], data["failed_requests"])
        plt.xlabel("CDN Mode")
        plt.ylabel("Failed Requests")
        plt.title(f"Experiment {exp}: Failed Requests")
        plt.grid(axis="y")
        plt.tight_layout()

        output = GRAPHS / f"exp{exp}_failed_requests.png"
        plt.savefig(output)
        plt.close()
        print("Created:", output)


def create_timeline_graphs(df):
    for exp in (2, 3, 4):
        data = df[df["experiment"] == exp]

        if data.empty:
            continue

        plt.figure(figsize=(9, 5))
        failure_drawn = False

        for _, row in in_mode_order(data).iterrows():
            folder = RESULTS / f"exp{exp}_{row['mode']}"
            client_file = folder / "clients.csv"
            events_file = folder / "events.csv"

            if not client_file.exists():
                continue

            clients = pd.read_csv(client_file)

            if clients.empty:
                continue

            clients["timestamp"] = pd.to_datetime(
                clients["timestamp"], utc=True
            )

            # Time zero is the run's start event, so every mode lines up.
            start = clients["timestamp"].min()
            if events_file.exists():
                run_events = pd.read_csv(events_file)
                started = run_events[run_events["event"] == "start"]
                if not started.empty:
                    start = pd.to_datetime(started.iloc[0]["timestamp"], utc=True)

            elapsed = (
                clients["timestamp"] - start
            ).dt.total_seconds()

            plt.scatter(
                elapsed,
                clients["response_time_ms"],
                label=row["mode"],
                alpha=0.7
            )

            if events_file.exists():
                events = pd.read_csv(events_file)

                failures = events[
                    events["event"].isin(
                        ["server_killed", "link_down", "link_slowed"]
                    )
                ]

                if not failures.empty and not failure_drawn:
                    failure_time = pd.to_datetime(
                        failures.iloc[0]["timestamp"], utc=True
                    )
                    failure_elapsed = (
                        failure_time - start
                    ).total_seconds()

                    plt.axvline(
                        failure_elapsed,
                        color="red",
                        linestyle="--",
                        label="failure"
                    )
                    failure_drawn = True

        plt.xlabel("Time Since Start of Run (seconds)")
        plt.ylabel("Response Time (ms)")
        plt.title(f"Experiment {exp}: Response Time Over Time")
        plt.legend()
        plt.grid(True)
        plt.tight_layout()

        output = GRAPHS / f"exp{exp}_response_over_time.png"
        plt.savefig(output)
        plt.close()
        print("Created:", output)


def create_recovery_graph(df):
    """Longest wait for a successful download after the failure, per mode."""
    data = df[df["experiment"].isin([2, 3, 4])]

    if data.empty or "recovery_time_s" not in data.columns:
        return

    fig, axes = plt.subplots(1, 3, figsize=(13, 4.5), sharey=True)

    for ax, exp in zip(axes, (2, 3, 4)):
        rows = in_mode_order(data[data["experiment"] == exp])
        ax.set_title(f"Experiment {exp}")
        ax.set_xlabel("CDN Mode")
        ax.grid(axis="y")

        for i, row in rows.iterrows():
            value = str(row["recovery_time_s"])
            if value in ("never", "nan", ""):
                ax.bar(row["mode"], 0)
                ax.text(i, 0, "never\nrecovered", ha="center", va="bottom")
            else:
                ax.bar(row["mode"], float(value))

    axes[0].set_ylabel("Longest wait after failure (s)")
    fig.suptitle("Recovery time: longest wait for a successful download after the failure")
    fig.tight_layout()

    output = GRAPHS / "recovery_comparison.png"
    fig.savefig(output)
    plt.close(fig)
    print("Created:", output)


def main():
    if not SUMMARY.exists():
        print("Summary CSV not found. Run parse_logs.py first.")
        return

    df = pd.read_csv(SUMMARY)

    if df.empty:
        print("Summary CSV is empty. Run experiments first.")
        return

    required = {
        "experiment",
        "mode",
        "average_response_ms",
        "failed_requests",
    }

    if not required.issubset(df.columns):
        print("Summary CSV is missing required columns.")
        return

    create_comparison_graphs(df)
    create_timeline_graphs(df)
    create_recovery_graph(df)

    print("Plotting completed.")
    print("Graphs saved in:", GRAPHS)


if __name__ == "__main__":
    main()
