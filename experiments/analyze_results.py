import os
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = "/home/seema/cdn-project"
RESULTS = os.path.join(BASE, "results")
REPORT = os.path.join(BASE, "report")

os.makedirs(REPORT, exist_ok=True)

health_file = os.path.join(RESULTS, "server_health.csv")
link_file = os.path.join(RESULTS, "link_utilization.csv")

if os.path.exists(health_file):
    df = pd.read_csv(health_file)

    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["alive"] = df["alive"].astype(str).str.lower().eq("true")

        summary = df.groupby("server").agg(
            checks=("alive", "count"),
            successful_checks=("alive", "sum"),
            average_latency_ms=("latency_ms", "mean"),
        )
        summary["availability_percent"] = (
            summary["successful_checks"] / summary["checks"] * 100
        )
        summary.to_csv(os.path.join(REPORT, "server_health_summary.csv"))

        plt.figure(figsize=(10, 5))
        for server, group in df[df["alive"]].groupby("server"):
            plt.plot(
                group["timestamp"],
                group["latency_ms"],
                marker=".",
                label=server,
            )
        plt.xlabel("Time")
        plt.ylabel("TCP connection latency (ms)")
        plt.title("Server Health Check Latency")
        plt.legend()
        plt.xticks(rotation=30)
        plt.tight_layout()
        plt.savefig(os.path.join(REPORT, "server_latency.png"))
        plt.close()

        print("Created server_health_summary.csv and server_latency.png")
    else:
        print("Server health CSV is empty.")
else:
    print("No server_health.csv yet. Run health monitoring first.")

if os.path.exists(link_file):
    links = pd.read_csv(link_file)

    if not links.empty:
        links["timestamp"] = pd.to_datetime(links["timestamp"])
        plt.figure(figsize=(10, 5))

        for key, group in links.groupby(["switch_id", "port_no"]):
            plt.plot(
                group["timestamp"],
                group["utilization_percent"],
                label=f"Switch {key[0]} port {key[1]}",
            )

        plt.xlabel("Time")
        plt.ylabel("Utilization (%)")
        plt.title("Link Utilization Over Time")
        plt.legend()
        plt.xticks(rotation=30)
        plt.tight_layout()
        plt.savefig(os.path.join(REPORT, "link_utilization.png"))
        plt.close()
        print("Created link_utilization.png")
    else:
        print("Link utilization CSV is empty; no link graph generated.")
else:
    print("No link_utilization.csv measurements yet.")

print("Analysis finished.")
