import time
import urllib.request

SERVERS = {
    "Server 1": "http://10.0.0.1:8080",
    "Server 2": "http://10.0.0.2:8080",
}

results = []

print("\nSmart CDN: Checking available servers...\n")

for name, url in SERVERS.items():
    try:
        start = time.monotonic()

        with urllib.request.urlopen(url, timeout=3) as response:
            response.read(1)
            latency = (time.monotonic() - start) * 1000

            if response.status == 200:
                results.append((latency, name, url))
                print(f"{name}: Healthy, response time {latency:.2f} ms")

    except Exception as error:
        print(f"{name}: Unavailable ({error})")

if not results:
    print("\nERROR: No healthy CDN servers are available.")
    raise SystemExit(1)

latency, name, url = min(results)

print(f"\nSelected server: {name}")
print(f"Response time: {latency:.2f} ms")
print("Downloading content...")

with urllib.request.urlopen(url, timeout=5) as response:
    content = response.read()

print(f"Download successful: {len(content)} bytes")
print("Smart CDN test completed successfully.")
