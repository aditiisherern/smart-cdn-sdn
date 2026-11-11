import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

base = Path("/home/seema/cdn-project")
data = pd.read_csv(base / "results/client_performance.csv")

plt.figure(figsize=(8, 5))
plt.plot(data["test"], data["server1_response_ms"], marker="o", label="Server 1")
plt.plot(data["test"], data["server2_response_ms"], marker="o", label="Server 2")
plt.xlabel("Test Number")
plt.ylabel("Response Time (ms)")
plt.title("Smart CDN: Server Response Time")
plt.xticks(data["test"])
plt.legend()
plt.grid(True)
plt.tight_layout()

output = base / "report/client_performance.png"
plt.savefig(output, dpi=150)
plt.close()

print(f"Graph saved to: {output}")
