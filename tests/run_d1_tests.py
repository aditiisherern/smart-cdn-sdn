"""Run the D1 test cases end to end on the real Mininet network.

Start the controller first, then from the repository root:
    sudo python3 tests/run_d1_tests.py

Prints PASS/FAIL per test and writes the evidence to tests/d1_results.md.
"""

import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from mininet.clean import cleanup
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import RemoteController

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "topology"))

import cdn_topo  # noqa: E402
from cdn_topo import config, failures  # noqa: E402

CLIENT = str(REPO / "servers" / "client.py")
CSV = REPO / "servers" / "client_log.csv"
RESULTS = REPO / "tests" / "d1_results.md"
results = []


def record(number, name, passed, evidence):
    results.append((number, name, passed, evidence.strip()))
    print(f"\n{'PASS' if passed else 'FAIL'}  {number}. {name}\n{evidence.strip()}\n", flush=True)


def client(net, host, *args):
    return net.get(host).cmd(f"cd {REPO} && python3 {CLIENT} " + " ".join(args))


def client_bg(net, host, *args):
    return net.get(host).popen(["python3", CLIENT, *args], cwd=str(REPO),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def flows(switch, grep=None):
    out = subprocess.run(["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", switch],
                         capture_output=True, text=True).stdout
    lines = [line.strip() for line in out.splitlines() if "priority" in line]
    return [line for line in lines if grep is None or grep in line]


def server_connections():
    """How many client connections each server's log shows."""
    counts = {}
    for name in ("h1", "h2", "h3"):
        log = Path(f"/tmp/{name}_server.log")
        text = log.read_text() if log.exists() else ""
        counts[name] = len(re.findall(r"Connected: \('10\.0\.0\.1\d'", text))
    return counts


def test_ping(net):
    loss = net.pingAll(timeout=1)
    if loss > 0:  # link discovery may still be settling; one retry
        time.sleep(3)
        loss = net.pingAll(timeout=1)
    record(1, "pingall", loss == 0, f"Packet loss: {loss:.0f}%")


def test_download(net):
    before = server_connections()
    out = client(net, "h11", "--file", "test_1m.bin")
    after = server_connections()
    served_by = [h for h in after if after[h] > before[h]]
    last = CSV.read_text().strip().splitlines()[-1] if CSV.exists() else ""
    s1 = flows("s1", "10.0.0.100")
    s4 = flows("s4", "10.0.0.100")
    ok = ("1/1 requests succeeded" in out and ",10.0.0.11,test_1m.bin,1048576," in last
          and any("set_field" in f for f in s1) and any("set_field" in f for f in s4))
    direct = client(net, "h11", "--server", "10.0.0.1", "--file", "test_100k.bin")
    ok = ok and "1/1 requests succeeded" in direct
    record(2, "Download via the virtual IP 10.0.0.100", ok,
           f"Client: {out.strip()}\nServed by: {served_by}\nCSV line: {last}\n"
           f"s1 VIP flow: {s1[0] if s1 else 'none'}\n"
           f"s4 VIP flow: {s4[0] if s4 else 'none'}\n"
           f"Direct download from 10.0.0.1: {direct.strip().splitlines()[-1]}")


def test_errors(net):
    missing = client(net, "h11", "--file", "does_not_exist.bin")
    bad = net.get("h11").cmd(
        "python3 -c \"import socket; s=socket.create_connection(('10.0.0.100',8080),5); "
        "s.sendall(b'HELLO\\n'); print(s.recv(100).decode().strip())\"")
    unreachable = client(net, "h11", "--server", "10.0.0.99", "--file", "test_1m.bin")
    ok = ("ERR NOT_FOUND" in missing and "ERR BAD_REQUEST" in bad
          and unreachable.count("Request failed (attempt") == 2
          and "0/1 requests succeeded" in unreachable)
    record(3, "Error handling", ok,
           f"Missing file: {missing.strip()}\nBad request: {bad.strip()}\n"
           f"Unreachable server: {unreachable.strip()}")


def test_server_failure(net):
    failures.kill_server(net, "h1")
    killed = time.time()
    proc = client_bg(net, "h11", "--loop", "15", "--interval", "1", "--file", "test_100k.bin")
    before = server_connections()
    out, _ = proc.communicate(timeout=120)
    after = server_connections()
    failures.start_server(net, "h1")
    elapsed = time.time() - killed

    lines = [line for line in out.splitlines() if line.startswith(("Downloaded", "Request failed"))]
    downloads = [line for line in lines if line.startswith("Downloaded")]
    # Once the controller notices (~5 s), every request must succeed.
    ok = len(downloads) >= 12 and "Downloaded" in lines[-1]
    record(4, "Server h1 killed while a client is downloading", ok,
           f"{len(downloads)}/15 requests succeeded over {elapsed:.0f} s\n"
           f"Connections served by h2/h3 during the test: "
           f"h2={after['h2'] - before['h2']}, h3={after['h3'] - before['h3']}\n"
           f"Client output (last 3 lines):\n" + "\n".join(out.strip().splitlines()[-3:]))
    time.sleep(4)  # let h1 report in again


def test_link_failure(net):
    time.sleep(11)  # let flows from earlier tests expire (idle_timeout 10 s)
    proc = client_bg(net, "h11", "--loop", "15", "--interval", "1", "--file", "test_100k.bin")
    time.sleep(3)
    via = "s2" if flows("s2", "priority=10") else "s3"
    other = "s3" if via == "s2" else "s2"
    failures.link_down(net, "s1", via)
    time.sleep(4)
    moved = bool(flows(other, "priority=10"))
    out, _ = proc.communicate(timeout=120)
    failures.link_up(net, "s1", via)

    downloads = out.count("Downloaded")
    ok = downloads >= 14 and moved
    record(5, f"Link s1-{via} cut while a client is downloading", ok,
           f"Traffic was on {via}; after the cut, connection flows on {other}: {moved}\n"
           f"{downloads}/15 requests succeeded\n"
           f"Client output (last 2 lines):\n" + "\n".join(out.strip().splitlines()[-2:]))
    time.sleep(5)  # let LLDP rediscover the link


def test_six_clients(net):
    before = server_connections()
    procs = {h: client_bg(net, h, "--loop", "10", "--interval", "1", "--file", "test_1m.bin")
             for h in ("h11", "h12", "h13", "h14", "h15", "h16")}
    outs = {h: p.communicate(timeout=300)[0] for h, p in procs.items()}
    after = server_connections()

    summary = {h: re.search(r"(\d+/\d+) requests succeeded", o) for h, o in outs.items()}
    summary = {h: m.group(1) if m else "no result" for h, m in summary.items()}
    spread = {h: after[h] - before[h] for h in after}
    ok = all(v == "10/10" for v in summary.values()) and sum(1 for v in spread.values() if v) == 3
    record(6, "Six clients downloading 1 MB files at once", ok,
           f"Per client: {summary}\nConnections per server: {spread}")


def write_report():
    passed = sum(1 for r in results if r[2])
    lines = [f"# D1 test results\n",
             f"Run: {datetime.now():%Y-%m-%d %H:%M}, mode `{config.MODE}`, "
             f"{passed}/{len(results)} passed.\n",
             "| # | Test | Result |", "|---|---|---|"]
    lines += [f"| {n} | {name} | {'PASS' if ok else 'FAIL'} |" for n, name, ok, _ in results]
    for n, name, ok, evidence in results:
        lines += [f"\n## {n}. {name}: {'PASS' if ok else 'FAIL'}\n", "```", evidence, "```"]
    RESULTS.write_text("\n".join(lines) + "\n")
    print(f"{passed}/{len(results)} passed. Evidence written to {RESULTS}")


def main():
    setLogLevel("warning")
    cleanup()
    cdn_topo.make_test_files()

    net = Mininet(topo=cdn_topo.CDNTopology(), link=TCLink, controller=None,
                  autoSetMacs=False)
    net.addController(RemoteController("c0", ip="127.0.0.1", port=6653))
    net.start()
    try:
        if not net.waitConnected(timeout=15):
            sys.exit("Switches did not connect. Is the controller running?")
        for name in ("h1", "h2", "h3"):
            failures.start_server(net, name)
        print("Waiting for link discovery and server load reports...", flush=True)
        time.sleep(8)

        for test in (test_ping, test_download, test_errors, test_server_failure,
                     test_link_failure, test_six_clients):
            try:
                test(net)
            except Exception as exc:  # keep going so one failure doesn't hide the rest
                record(len(results) + 1, test.__name__, False, f"Crashed: {exc!r}")
        write_report()
    finally:
        for name in ("h1", "h2", "h3"):
            failures.kill_server(net, name)
        net.stop()


if __name__ == "__main__":
    main()
