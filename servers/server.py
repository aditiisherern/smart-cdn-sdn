
import os
import socket
import threading
from pathlib import Path

HOST = "0.0.0.0"
PORT = 8080
CONTENT_DIR = Path(__file__).resolve().parent / "content"


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
        data = file_path.read_bytes()
        conn.sendall(f"OK {len(data)}\n".encode("ascii"))
        conn.sendall(data)
    except OSError as exc:
        print(f"Could not read {filename}: {exc}")
        try:
            conn.sendall(b"ERR NOT_FOUND\n")
        except OSError:
            pass


def handle_client(conn, address):
    print(f"Connected: {address}")

    try:
        conn.settimeout(5)
        with conn:
            reader = conn.makefile("rb")
            try:
                request = reader.readline(4097)

                if not request or len(request) > 4096 or not request.endswith(b"\n"):
                    conn.sendall(b"ERR NOT_FOUND\n")
                    return

                try:
                    command, filename = request.decode("utf-8").strip().split(" ", 1)
                except ValueError:
                    conn.sendall(b"ERR NOT_FOUND\n")
                    return

                if command != "GET":
                    conn.sendall(b"ERR NOT_FOUND\n")
                    return

                send_response(conn, filename.strip())
            finally:
                reader.close()

    except (OSError, socket.timeout) as exc:
        print(f"Connection error from {address}: {exc}")
    finally:
        print(f"Disconnected: {address}")


def main():
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen(20)

        print(f"Content server listening on {HOST}:{PORT}")
        print(f"Content directory: {CONTENT_DIR}")

        while True:
            conn, address = server.accept()
            threading.Thread(
                target=handle_client,
                args=(conn, address),
                daemon=True,
            ).start()


if __name__ == "__main__":
    main()

