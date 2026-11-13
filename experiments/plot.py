import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
RESULTS = BASE / "results"
REPORT = BASE / "report"

REPORT.mkdir(exist_ok=True)

# 1. Response-time graph using existing client performance data

client_file = RESULTS / "client_performance.csv"

if client_file.exists():
    df = pd.read_csv(client_file)

    if not df.empty:
        plt.figure(figsize=(9, 5))

        plt.plot(
            df["test"],
            df["server1_response_ms"],
            marker="o",
            label="Server 1"
        )

        plt.plot(
            df["test"],
            df["server2_response_ms"],
            marker="s",
            label="Server 2"
        )

        plt.xlabel("Test Number")
        plt.ylabel("Response Time (ms)")
        plt.title("CDN Server Response Time")
        plt.legend()
        plt.grid(True)
        plt.tight_layout()

        output = REPORT / "response_time_over_time.png"
        plt.savefig(output)
        plt.close()

        print("Created:", output)
    else:
        print("Client performance CSV is empty.")
else:
    print("Client performance CSV not found.")

# 2. Smart vs Static vs Round Robin comparison

comparison_file = RESULTS / "mode_comparison.csv"

if comparison_file.exists():
    modes = pd.read_csv(comparison_file)

    required = {"mode", "average_response_ms"}

    if required.issubset(modes.columns):
        expected = {"smart", "static", "roundrobin"}
        available = set(modes["mode"].str.lower())

        if expected.issubset(available):
            plt.figure(figsize=(8, 5))

            plt.bar(
                modes["mode"],
                modes["average_response_ms"]
            )

            plt.xlabel("CDN Mode")
            plt.ylabel("Average Response Time (ms)")
            plt.title("Smart vs Static vs Round Robin")
            plt.tight_layout()

            output = REPORT / "mode_comparison.png"
            plt.savefig(output)
            plt.close()

            print("Created:", output)
        else:
            print("Mode comparison requires all three modes.")
    else:
        print("Mode comparison CSV has an incorrect format.")
else:
    print("Mode comparison CSV not available yet.")

print("Plotting completed.")
