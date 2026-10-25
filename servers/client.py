import argparse
import csv
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

LOG_FILE = Path(__file__).resolve().parent / "client_log.csv"
TIMEOUT = 5


class ServerError(Exception):
    """The server answered with ERR ...; retrying won't help."""


def fetch(server_ip, filename, port):
    """Download one file. Returns (client_ip, data)."""
    with socket.create_connection((server_ip, port), timeout=TIMEOUT) as sock:
        # This host's real IP. gethostbyname(gethostname()) gives 127.0.1.1
        # inside Mininet, because every host shares the same hostname.
        client_ip = sock.getsockname()[0]
        sock.sendall(f"GET {filename}\n".encode("utf-8"))

        # Read the response header one byte at a time so that
        # we don't accidentally consume the beginning of the file.
        header = bytearray()

        while not header.endswith(b"\n"):
            chunk = sock.recv(1)

            if not chunk:
                raise ConnectionError("Server closed before sending a header")

            header.extend(chunk)

            if len(header) > 128:
                raise ValueError("Response header is too long")

        header_text = header.decode("ascii").strip()

        if header_text.startswith("ERR "):
            raise ServerError(header_text)

        parts = header_text.split()

        if len(parts) != 2 or parts[0] != "OK":
            raise ValueError(f"Invalid response header: {header_text!r}")

        size = int(parts[1])

        if size < 0:
            raise ValueError("Invalid file size")

        output = bytearray()

        while len(output) < size:
            chunk = sock.recv(min(65536, size - len(output)))

            if not chunk:
                raise ConnectionError("Connection ended before the complete file arrived")

            output.extend(chunk)

        return client_ip, output


def write_log(client_ip, filename, size, elapsed_ms):
    log_exists = LOG_FILE.exists()

    with LOG_FILE.open("a", newline="", encoding="utf-8") as log:
        writer = csv.writer(log)

        if not log_exists:
            writer.writerow([
                "timestamp",
                "client_ip",
                "file",
                "bytes",
                "response_time_ms",
            ])

        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            client_ip,
            filename,
            size,
            round(elapsed_ms, 3),
        ])


def request_file(server_ip, filename, port=config.CONTENT_PORT, retries=1):
    # Timed from the first attempt, so a failed try + retry shows up as a slow request.
    start = time.perf_counter()

    for attempt in range(retries + 1):
        try:
            client_ip, data = fetch(server_ip, filename, port)
            break
        except ServerError as exc:
            print(f"Server error: {exc}")
            return False
        except (OSError, ValueError) as exc:
            print(f"Request failed (attempt {attempt + 1}): {exc}")
    else:
        return False

    elapsed_ms = (time.perf_counter() - start) * 1000

    # Save received files outside the content directory. Prefix with the
    # client IP so several clients downloading the same file don't clash.
    download_dir = Path("downloads")
    download_dir.mkdir(exist_ok=True)
    output_path = download_dir / f"{client_ip}_{Path(filename).name}"
    output_path.write_bytes(data)

    write_log(client_ip, filename, len(data), elapsed_ms)

    print(f"Downloaded {filename}: {len(data)} bytes in {elapsed_ms:.3f} ms -> {output_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Download files from the CDN.")
    parser.add_argument("--server", default=config.VIP,
                        help=f"server IP (default: virtual IP {config.VIP})")
    parser.add_argument("--file", default="test_1m.bin", help="file to request")
    parser.add_argument("--port", type=int, default=config.CONTENT_PORT)
    parser.add_argument("--loop", type=int, default=1, metavar="N",
                        help="number of requests to make")
    parser.add_argument("--interval", type=float, default=1.0, metavar="X",
                        help="seconds between requests")
    args = parser.parse_args()

    successes = 0

    for i in range(args.loop):
        if i:
            time.sleep(args.interval)
        successes += request_file(args.server, args.file, args.port)

    print(f"{successes}/{args.loop} requests succeeded")
    sys.exit(0 if successes == args.loop else 1)


if __name__ == "__main__":
    main()
