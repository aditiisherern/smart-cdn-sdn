"""Run one D2 experiment end to end, through the controller and 10.0.0.100.

Starts its own controller in the chosen mode (so don't run one yourself),
builds the network, runs clients for the experiment's duration, injects the
failure, and saves everything to experiments/results/exp<N>_<mode>/:

    clients.csv     one line per successful download (same columns as servers/client_log.csv)
    events.csv      timestamp,event,detail: start, the failure, end (for recovery time)
    summary.json    settings plus per-client succeeded/failed request counts
    controller.log  the controller's output, including every DECISION
    client_output.txt  what each client printed

Usage (from the repository root):
    sudo python3 experiments/run_scenario.py --mode smart --exp 2
    sudo python3 experiments/run_scenario.py --all          # 5 experiments x 3 modes, ~40 min

Experiments (failure at --fail-at seconds, default 60 of 120):
    1  normal, nothing breaks                       h11-h13
    2  server h1 killed                             h11-h13
    3  the s1 link on the path in use is cut        h11-h13
    4  the s4 link on the path in use slowed to 1 Mbps   h11-h13
    5  six clients at once                          h11-h16
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from mininet.clean import cleanup
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import RemoteController

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "topology"))

import cdn_topo  # noqa: E402
from cdn_topo import failures  # noqa: E402

CLIENT = str(REPO / "servers" / "client.py")
CLIENT_LOG = REPO / "servers" / "client_log.csv"
RESULTS = REPO / "experiments" / "results"
MODES = ("smart", "roundrobin", "static")
EXPERIMENTS = {
    1: "normal",
    2: "server h1 killed",
    3: "link on the path cut",
    4: "link on the path slowed to 1 Mbps",
    5: "six clients",
}


def now():
    # Same format as the timestamps client.py writes.
    return datetime.now(timezone.utc).isoformat()


def flows(switch, grep=None):
    out = subprocess.run(["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", switch],
                         capture_output=True, text=True).stdout
    lines = [line.strip() for line in out.splitlines() if "priority" in line]
    return [line for line in lines if grep is None or grep in line]


def path_in_use():
    """s2 or s3, whichever currently carries the VIP connections."""
    return "s2" if flows("s2", "priority=10") else "s3"


def start_controller(mode, log_path):
    ryu = REPO / ".venv-ryu" / "bin" / "ryu-manager"
    ryu = str(ryu) if ryu.exists() else shutil.which("ryu-manager")
    if ryu is None:
        sys.exit("ryu-manager not found (looked in .venv-ryu/bin and PATH)")

    log = open(log_path, "w")
    proc = subprocess.Popen([ryu, "--observe-links", "controller/cdn_controller.py"],
                            cwd=str(REPO), env={**os.environ, "CDN_MODE": mode},
                            stdout=log, stderr=subprocess.STDOUT)
    proc.log = log
    for _ in range(30):
        try:
            socket.create_connection(("127.0.0.1", 6653), timeout=1).close()
            return proc
        except OSError:
            time.sleep(0.5)
    proc.kill()
    sys.exit(f"Controller did not start; see {log_path}")


def run(mode, exp, duration, fail_at, filename, interval):
    out_dir = RESULTS / f"exp{exp}_{mode}"
    out_dir.mkdir(parents=True, exist_ok=True)
    events = [("timestamp", "event", "detail")]
    print(f"\n=== Experiment {exp} ({EXPERIMENTS[exp]}), mode {mode} ===", flush=True)

    # Cleanup kills any ryu-manager, so it has to run before ours starts.
    cleanup()
    controller = start_controller(mode, out_dir / "controller.log")
    cdn_topo.make_test_files()
    log_offset = CLIENT_LOG.stat().st_size if CLIENT_LOG.exists() else 0

    net = Mininet(topo=cdn_topo.CDNTopology(), link=TCLink, controller=None,
                  autoSetMacs=False)
    net.addController(RemoteController("c0", ip="127.0.0.1", port=6653))
    net.start()
    clients = {}
    try:
        if not net.waitConnected(timeout=15):
            sys.exit("Switches did not connect to the controller")
        for name in ("h1", "h2", "h3"):
            failures.start_server(net, name)
        time.sleep(8)  # link discovery + first load reports

        hosts = ["h11", "h12", "h13", "h14", "h15", "h16"] if exp == 5 else ["h11", "h12", "h13"]
        loops = str(int(duration / interval) + 100)  # more than enough; stopped at the end
        events.append((now(), "start", f"clients {','.join(hosts)}"))
        start = time.time()
        for host in hosts:
            clients[host] = net.get(host).popen(
                ["python3", "-u", CLIENT, "--file", filename, "--loop", loops,
                 "--interval", str(interval)],
                cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        if exp in (2, 3, 4):
            time.sleep(max(0, fail_at - (time.time() - start)))
            if exp == 2:
                failures.kill_server(net, "h1")
                events.append((now(), "server_killed", "h1"))
            elif exp == 3:
                via = path_in_use()
                failures.link_down(net, "s1", via)
                events.append((now(), "link_down", f"s1-{via}"))
            else:
                via = path_in_use()
                failures.slow_link(net, via, "s4", bw=1)
                events.append((now(), "link_slowed", f"{via}-s4 to 1 Mbps"))

        time.sleep(max(0, duration - (time.time() - start)))
        events.append((now(), "end", ""))
    finally:
        outputs = {}
        for host, proc in clients.items():
            proc.terminate()
            outputs[host] = proc.communicate(timeout=10)[0]
        for name in ("h1", "h2", "h3"):
            failures.kill_server(net, name)
        net.stop()
        controller.terminate()
        controller.wait(timeout=10)
        controller.log.close()

    # Only this run's lines from the shared client log.
    with CLIENT_LOG.open() as log:
        log.seek(log_offset)
        new_lines = [line for line in log.read().splitlines()
                     if line and not line.startswith("timestamp")]
    (out_dir / "clients.csv").write_text(
        "timestamp,client_ip,file,bytes,response_time_ms\n" + "".join(l + "\n" for l in new_lines))
    (out_dir / "events.csv").write_text("".join(",".join(e) + "\n" for e in events))
    (out_dir / "client_output.txt").write_text(
        "".join(f"===== {h} =====\n{o}\n" for h, o in outputs.items()))

    # A request that still failed after its retry prints "attempt 2".
    per_client = {h: {"succeeded": o.count("Downloaded "),
                      "failed": o.count("(attempt 2)") + o.count("Server error")}
                  for h, o in outputs.items()}
    summary = {"experiment": exp, "description": EXPERIMENTS[exp], "mode": mode,
               "duration_s": duration, "fail_at_s": fail_at if exp in (2, 3, 4) else None,
               "file": filename, "interval_s": interval, "clients": per_client}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    ok = sum(c["succeeded"] for c in per_client.values())
    bad = sum(c["failed"] for c in per_client.values())
    give_back(out_dir)
    print(f"Done: {ok} downloads succeeded, {bad} failed. Saved to {out_dir}", flush=True)


def give_back(path):
    """Files made under sudo belong to root; hand them to the user who ran sudo."""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid is None:
        return
    for p in [path, *path.rglob("*")]:
        os.chown(p, int(uid), int(gid))


def main():
    parser = argparse.ArgumentParser(description="Run D2 experiments through the controller.")
    parser.add_argument("--mode", choices=MODES)
    parser.add_argument("--exp", type=int, choices=sorted(EXPERIMENTS))
    parser.add_argument("--all", action="store_true", help="all 5 experiments in all 3 modes")
    parser.add_argument("--duration", type=float, default=120, help="seconds per run")
    parser.add_argument("--fail-at", type=float, default=60, help="seconds before the failure")
    parser.add_argument("--file", default="test_100k.bin", help="file each client requests")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between requests")
    args = parser.parse_args()

    if os.geteuid() != 0:
        sys.exit("Run with sudo (Mininet needs root).")
    if not args.all and (args.mode is None or args.exp is None):
        parser.error("give --mode and --exp, or --all")

    setLogLevel("warning")
    runs = ([(m, e) for e in sorted(EXPERIMENTS) for m in MODES] if args.all
            else [(args.mode, args.exp)])
    for mode, exp in runs:
        run(mode, exp, args.duration, args.fail_at, args.file, args.interval)


if __name__ == "__main__":
    main()
