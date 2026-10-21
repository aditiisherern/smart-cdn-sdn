
import csv
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent / "client_log.csv"


def request_file(server_ip, filename, port=8080):
    start = time.perf_counter()

    try:
        with socket.create_connection((server_ip, port), timeout=5) as sock:
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
                print(f"Server error: {header_text}")
                return False

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

            elapsed_ms = (time.perf_counter() - start) * 1000

            # Save received files outside the content directory.
            download_dir = Path("downloads")
            download_dir.mkdir(exist_ok=True)
            output_path = download_dir / Path(filename).name
            output_path.write_bytes(output)

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
                    socket.gethostbyname(socket.gethostname()),
                    filename,
                    size,
                    round(elapsed_ms, 3),
                ])

            print(f"Downloaded {filename}: {size} bytes")
            print(f"Saved to: {output_path.resolve()}")
            print(f"Response time: {elapsed_ms:.3f} ms")
            return True

    except (OSError, ValueError, ConnectionError) as exc:
        print(f"Request failed: {exc}")
        return False


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: python3 {Path(sys.argv[0]).name} SERVER_IP FILENAME")
        sys.exit(1)

    success = request_file(sys.argv[1], sys.argv[2])
    sys.exit(0 if success else 1)
