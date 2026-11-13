import csv
import math
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = PROJECT_ROOT / "servers" / "client_log.csv"
OUTPUT_FILE = PROJECT_ROOT / "results" / "performance_summary.txt"


def calculate_metrics():
    if not LOG_FILE.exists():
        print("Client log not found:", LOG_FILE)
        print("Run the Mininet experiments first to generate client data.")
        return

    response_times = []
    total_bytes = 0
    timestamps = []

    with LOG_FILE.open("r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        for row in reader:
            try:
                response = float(row["response_time_ms"])
                size = int(row["bytes"])

                response_times.append(response)
                total_bytes += size
                timestamps.append(row["timestamp"])

            except (ValueError, KeyError):
                continue

    if not response_times:
        print("No valid download records found.")
        return

    average = sum(response_times) / len(response_times)

    sorted_times = sorted(response_times)
    index = math.ceil(0.95 * len(sorted_times)) - 1
    percentile_95 = sorted_times[index]

    duration = 0

    if len(timestamps) > 1:
        from datetime import datetime

        start = datetime.fromisoformat(timestamps[0])
        end = datetime.fromisoformat(timestamps[-1])
        duration = (end - start).total_seconds()

    throughput = (
        (total_bytes * 8) / duration / 1_000_000
        if duration > 0 else 0
    )

    report = f"""
SMART CDN PERFORMANCE SUMMARY

Total successful downloads: {len(response_times)}

Average response time: {average:.3f} ms

95th percentile response time: {percentile_95:.3f} ms

Total downloaded data: {total_bytes} bytes

Average throughput: {throughput:.3f} Mbps

Recovery time: Not available
Reason: Exact failure and recovery timestamps
were not recorded in the existing experiment logs.
"""

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(report, encoding="utf-8")

    print(report)
    print("Report saved to:", OUTPUT_FILE)


if __name__ == "__main__":
    calculate_metrics()
