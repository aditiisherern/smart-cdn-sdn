import csv
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
RESULTS = BASE / "results"
OUTPUT = RESULTS / "summary.csv"

FAILURE_EVENTS = {"server_killed", "link_down", "link_slowed"}


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_time(value):
    return datetime.fromisoformat(value)


def percentile_95(values):
    values = sorted(values)
    if not values:
        return 0
    return values[math.ceil(0.95 * len(values)) - 1]


def client_throughputs(records):
    grouped = defaultdict(list)

    for row in records:
        grouped[row["client_ip"]].append(row)

    results = {}

    for client, rows in grouped.items():
        rows.sort(key=lambda r: parse_time(r["timestamp"]))
        total_bytes = sum(r["bytes"] for r in rows)
        first = parse_time(rows[0]["timestamp"])
        last = parse_time(rows[-1]["timestamp"])
        last_response = rows[-1]["response_time_ms"] / 1000

        elapsed = max(
            last_response,
            (last - first).total_seconds() + last_response
        )

        results[client] = round(
            total_bytes / 1_000_000 / elapsed, 4
        )

    return results


def longest_wait(records, start, end):
    """Longest time any one client waited between successful downloads in
    [start, end]. Counted from start, so a client stuck right after a failure
    shows up. Returns "never" if some client got nothing in the window."""
    grouped = defaultdict(list)

    for row in records:
        grouped[row["client_ip"]].append(row["parsed_time"])

    longest = 0.0

    for times in grouped.values():
        previous = start
        later = sorted(t for t in times if start <= t <= end)

        if not later:
            return "never"

        for t in later:
            longest = max(longest, (t - previous).total_seconds())
            previous = t

    return round(longest, 3)


def process_experiment(folder):
    summary_file = folder / "summary.json"
    clients_file = folder / "clients.csv"
    events_file = folder / "events.csv"

    if not all(p.exists() for p in (
        summary_file, clients_file, events_file
    )):
        print("Skipping incomplete folder:", folder.name)
        return None

    with summary_file.open(encoding="utf-8") as f:
        summary = json.load(f)

    downloads = read_csv(clients_file)
    events = read_csv(events_file)

    valid = []

    for row in downloads:
        try:
            row["response_time_ms"] = float(row["response_time_ms"])
            row["bytes"] = int(row["bytes"])
            row["parsed_time"] = parse_time(row["timestamp"])
            valid.append(row)
        except (ValueError, KeyError, TypeError):
            continue

    response_times = [r["response_time_ms"] for r in valid]
    total_bytes = sum(r["bytes"] for r in valid)

    clients = summary.get("clients", {})
    succeeded = sum(c.get("succeeded", 0) for c in clients.values())
    failed = sum(c.get("failed", 0) for c in clients.values())

    duration = float(summary.get("duration_s", 0))
    average_response = (
        sum(response_times) / len(response_times)
        if response_times else 0
    )

    average_throughput = (
        total_bytes / 1_000_000 / duration if duration > 0 else 0
    )

    per_client = client_throughputs(valid)

    times = {e.get("event"): parse_time(e["timestamp"]) for e in events}
    failure_time = next(
        (parse_time(e["timestamp"]) for e in events
         if e.get("event") in FAILURE_EVENTS),
        None,
    )

    # Recovery time: the longest a client went without a successful download
    # after the failure. normal_wait_s is the same measure before the failure,
    # so the two can be compared (about interval + response time when healthy).
    recovery_time = ""
    normal_wait = ""

    if failure_time is not None and "start" in times and "end" in times:
        recovery_time = longest_wait(valid, failure_time, times["end"])
        normal_wait = longest_wait(valid, times["start"], failure_time)

    return {
        "experiment": summary.get("experiment"),
        "description": summary.get("description", ""),
        "mode": summary.get("mode", ""),
        "duration_s": duration,
        "successful_requests": succeeded,
        "failed_requests": failed,
        "average_response_ms": round(average_response, 3),
        "p95_response_ms": round(percentile_95(response_times), 3),
        "total_downloaded_bytes": total_bytes,
        "average_throughput_MBps": round(average_throughput, 4),
        "client_throughput_MBps": json.dumps(per_client, sort_keys=True),
        "normal_wait_s": normal_wait,
        "recovery_time_s": recovery_time,
    }


def main():
    rows = []

    for folder in sorted(RESULTS.glob("exp*_*/")):
        if folder.is_dir():
            result = process_experiment(folder)
            if result is not None:
                rows.append(result)

    if not rows:
        print("No completed experiment results found.")
        print("Run an experiment first.")
        return

    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print("Performance summary created:", OUTPUT)
    print("Experiments processed:", len(rows))


if __name__ == "__main__":
    main()
