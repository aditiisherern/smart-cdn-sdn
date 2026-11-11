import csv
import os
import time
from datetime import datetime

link_load = {}
previous_stats = {}

LINK_SPEED_BPS = 10_000_000  # 10 Mbps default
LOG_DIR = "/home/seema/cdn-project/results"
LOG_FILE = os.path.join(LOG_DIR, "link_utilization.csv")

os.makedirs(LOG_DIR, exist_ok=True)

if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, "w", newline="") as f:
        csv.writer(f).writerow([
            "timestamp",
            "switch_id",
            "port_no",
            "utilization_percent",
        ])


def calculate_utilization(bytes_delta, interval, link_speed_bps):
    if interval <= 0 or link_speed_bps <= 0:
        return 0.0

    bits_per_second = (bytes_delta * 8) / interval
    utilization = bits_per_second / link_speed_bps
    return min(max(utilization, 0.0), 1.0)


def update_port_stats(switch_id, port_no, rx_bytes, tx_bytes, timestamp=None):
    if timestamp is None:
        timestamp = time.monotonic()

    key = (switch_id, port_no)
    current_bytes = rx_bytes + tx_bytes

    if key in previous_stats:
        old_bytes, old_time = previous_stats[key]
        interval = timestamp - old_time
        byte_delta = max(0, current_bytes - old_bytes)

        utilization = calculate_utilization(
            byte_delta, interval, LINK_SPEED_BPS
        )
        link_load[key] = utilization

        with open(LOG_FILE, "a", newline="") as f:
            csv.writer(f).writerow([
                datetime.now().isoformat(timespec="seconds"),
                switch_id,
                port_no,
                round(utilization * 100, 2),
            ])

    previous_stats[key] = (current_bytes, timestamp)


def main():
    print("Link monitor helper ready.", flush=True)
    print("Waiting for OpenFlow port statistics integration.", flush=True)

    try:
        while True:
            if link_load:
                print("Current link loads:", flush=True)
                for key, utilization in link_load.items():
                    print(
                        f"Switch {key[0]}, port {key[1]}: "
                        f"{utilization * 100:.2f}% utilized",
                        flush=True,
                    )
            else:
                print(
                    "No port statistics received yet; "
                    "OpenFlow integration is still required.",
                    flush=True,
                )

            time.sleep(2)

    except KeyboardInterrupt:
        print("\nLink monitor stopped.", flush=True)


if __name__ == "__main__":
    main()
