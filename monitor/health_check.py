import csv
import os
import socket
import time
from datetime import datetime

# Three CDN servers
SERVERS = {
    "Server 1": ("10.0.0.1", 8080),
    "Server 2": ("10.0.0.2", 8080),
    "Server 3": ("10.0.0.3", 8080),
}

CHECK_INTERVAL = 2
FAILURES_TO_MARK_DEAD = 2

LOG_DIR = "results"
LOG_FILE = os.path.join(LOG_DIR, "server_health.csv")

os.makedirs(LOG_DIR, exist_ok=True)

# Shared server status
server_status = {}

for name, address in SERVERS.items():
    server_status[address[0]] = {
        "alive": None,
        "load": 0.0,
        "latency_ms": None,
        "consecutive_failures": 0,
    }

# Create CSV header
if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
    with open(LOG_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp",
            "server",
            "alive",
            "latency_ms",
            "load",
            "consecutive_failures"
        ])


def check_server(address):
    start = time.perf_counter()

    try:
        with socket.create_connection(address, timeout=2):
            latency = (time.perf_counter() - start) * 1000
            return True, round(latency, 2)

    except OSError:
        return False, None


def update_server_status(name, address):
    ip = address[0]

    alive, latency = check_server(address)

    status = server_status[ip]

    if alive:
        status["alive"] = True
        status["latency_ms"] = latency
        status["consecutive_failures"] = 0

    else:
        status["consecutive_failures"] += 1

        if status["consecutive_failures"] >= FAILURES_TO_MARK_DEAD:
            status["alive"] = False
            status["latency_ms"] = None

    return status


def main():
    print("====================================")
    print(" SMART CDN SERVER HEALTH MONITOR")
    print("====================================")

    print("Monitoring started. Press Ctrl+C to stop.")

    try:
        while True:

            timestamp = datetime.now().isoformat(timespec="seconds")

            with open(LOG_FILE, "a", newline="") as f:

                writer = csv.writer(f)

                for name, address in SERVERS.items():

                    status = update_server_status(name, address)

                    writer.writerow([
                        timestamp,
                        name,
                        status["alive"],
                        status["latency_ms"],
                        status["load"],
                        status["consecutive_failures"]
                    ])

                    if status["alive"] is True:
                        print(
                            f"{name}: ALIVE | "
                            f"Latency: {status['latency_ms']} ms"
                        )

                    elif status["alive"] is False:
                        print(f"{name}: DEAD")

                    else:
                        print(
                            f"{name}: UNKNOWN | "
                            "Waiting for second failure check"
                        )

                    print(
                        f"Failures: "
                        f"{status['consecutive_failures']}"
                    )

            print("\nCurrent Server Status:")
            print(server_status)
            print("-" * 60)

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("\nMonitoring stopped.")


if __name__ == "__main__":
    main()
