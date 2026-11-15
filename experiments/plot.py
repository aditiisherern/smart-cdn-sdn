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


def create_comparison_graphs(df):
    # Average response time comparison
    for exp in sorted(df["experiment"].unique()):
        data = df[df["experiment"] == exp]

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
        data = df[df["experiment"] == exp]

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

        for _, row in data.iterrows():
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

            start = clients["timestamp"].min()

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

                if not failures.empty:
                    failure_time = pd.to_datetime(
                        failures.iloc[0]["timestamp"], utc=True
                    )
                    failure_elapsed = (
                        failure_time - start
                    ).total_seconds()

                    plt.axvline(
                        failure_elapsed,
                        linestyle="--",
                        label=f"{row['mode']} failure"
                    )

        plt.xlabel("Time Since First Download (seconds)")
        plt.ylabel("Response Time (ms)")
        plt.title(f"Experiment {exp}: Response Time Over Time")
        plt.legend()
        plt.grid(True)
        plt.tight_layout()

        output = GRAPHS / f"exp{exp}_response_over_time.png"
        plt.savefig(output)
        plt.close()
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

    print("Plotting completed.")
    print("Graphs saved in:", GRAPHS)


if __name__ == "__main__":
    main()
