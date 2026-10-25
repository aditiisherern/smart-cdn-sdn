
import os
import socket
import sys
import threading
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

HOST = "0.0.0.0"
PORT = config.CONTENT_PORT
LOAD_PORT = config.REPORT_PORT
LOAD_INTERVAL = 2
CHUNK_SIZE = 64 * 1024
CONTENT_DIR = Path(__file__).resolve().parent / "content"

active_clients = 0
clients_lock = threading.Lock()


def get_server_ip():
    """Return this Mininet host's IP address."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect((config.REPORT_IP, LOAD_PORT))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def send_response(conn, filename):
    # Only allow filenames, not arbitrary filesystem paths.
    if not filename or filename != os.path.basename(filename):
        conn.sendall(b"ERR NOT_FOUND\n")
        return

    file_path = CONTENT_DIR / filename

    if not file_path.is_file():
        conn.sendall(b"ERR NOT_FOUND\n")
        return

    try:
        source = file_path.open("rb")
    except OSError as exc:
        print(f"Could not read {filename}: {exc}")
        conn.sendall(b"ERR NOT_FOUND\n")
        return

    with source:
        conn.sendall(f"OK {file_path.stat().st_size}\n".encode("ascii"))
        # Send in chunks: a socket timeout covers a whole sendall() call,
        # so one big sendall() of a 10 MB file would time out on a 10 Mbps link.
        while chunk := source.read(CHUNK_SIZE):
            conn.sendall(chunk)


def handle_client(conn, address):
    global active_clients

    with clients_lock:
        active_clients += 1

    print(f"Connected: {address}")

    try:
        conn.settimeout(5)
        with conn:
            reader = conn.makefile("rb")
            try:
                request = reader.readline(4097)

                if not request or len(request) > 4096 or not request.endswith(b"\n"):
                    conn.sendall(b"ERR BAD_REQUEST\n")
                    return

                try:
                    command, filename = request.decode("utf-8").strip().split(" ", 1)
                except (ValueError, UnicodeDecodeError):
                    conn.sendall(b"ERR BAD_REQUEST\n")
                    return

                if command != "GET":
                    conn.sendall(b"ERR BAD_REQUEST\n")
                    return

                send_response(conn, filename.strip())
            finally:
                reader.close()

    except (OSError, socket.timeout) as exc:
        print(f"Connection error from {address}: {exc}")
    finally:
        with clients_lock:
            active_clients -= 1
        print(f"Disconnected: {address}")


def report_load():
    """Send a UDP load report every two seconds.

    Each Mininet host has its own network namespace, so 127.0.0.1 here is the
    server's own loopback, not the controller. Reports go to config.REPORT_IP
    through the switches instead, and the controller reads them as packet-ins.
    """
    server_ip = get_server_ip()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reporter:
        while True:
            with clients_lock:
                count = active_clients

            message = {
                "server": server_ip,
                "active_clients": count,
            }

            try:
                reporter.sendto(
                    json.dumps(message).encode("utf-8"),
                    (config.REPORT_IP, LOAD_PORT),
                )
            except OSError as exc:
                print(f"Load report error: {exc}")

            time.sleep(LOAD_INTERVAL)


def main():
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)

    threading.Thread(target=report_load, daemon=True).start()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen(20)

        print(f"Content server listening on {HOST}:{PORT}")
        print(f"Content directory: {CONTENT_DIR}")
        print(f"UDP load reports configured for port {LOAD_PORT}")

        while True:
            conn, address = server.accept()
            threading.Thread(
                target=handle_client,
                args=(conn, address),
                daemon=True,
            ).start()


if __name__ == "__main__":
    main()
