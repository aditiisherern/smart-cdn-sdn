"""Run the D1 test cases end to end on the real Mininet network.

Starts its own controller in the chosen mode (any controller already running is
stopped), so from the repository root just run:
    sudo python3 tests/run_d1_tests.py                    # smart mode
    sudo python3 tests/run_d1_tests.py --mode roundrobin
    sudo python3 tests/run_d1_tests.py --mode static

Prints PASS/FAIL per test and writes the evidence to tests/d1_results.md
(smart) or tests/d1_results_<mode>.md. In static mode, tests 4 and 5 pass when
the system does NOT recover: static is the baseline and never fails over.
"""

import argparse
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
sys.path.insert(0, str(REPO / "experiments"))

import cdn_topo  # noqa: E402
from cdn_topo import failures  # noqa: E402
from run_scenario import give_back, start_controller  # noqa: E402

CLIENT = str(REPO / "servers" / "client.py")
CSV = REPO / "servers" / "client_log.csv"
LOGS = REPO / "tests" / "d1_logs"
MODE = "smart"  # set from --mode in main()
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
    out, _ = proc.communicate(timeout=400)
    after = server_connections()
    failures.start_server(net, "h1")
    elapsed = time.time() - killed

    lines = [line for line in out.splitlines() if line.startswith(("Downloaded", "Request failed"))]
    downloads = [line for line in lines if line.startswith("Downloaded")]
    if MODE == "static":
        # Baseline: everything goes to h1, so nothing should succeed.
        expected = "static never fails over, so all requests to the dead h1 fail"
        ok = len(downloads) == 0
    else:
        # Once the controller notices (~5 s), every request must succeed.
        expected = "requests keep succeeding on h2/h3"
        ok = len(downloads) >= 12 and bool(lines) and "Downloaded" in lines[-1]
    record(4, "Server h1 killed while a client is downloading", ok,
           f"Expected in {MODE} mode: {expected}\n"
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
    out, _ = proc.communicate(timeout=400)
    failures.link_up(net, "s1", via)

    downloads = out.count("Downloaded")
    if MODE == "static":
        # Baseline: the path is fixed, so requests after the cut fail.
        expected = "static never reroutes, so requests after the cut fail"
        ok = downloads < 14 and not moved
    else:
        expected = "traffic moves to the other path and keeps succeeding"
        ok = downloads >= 14 and moved
    record(5, f"Link s1-{via} cut while a client is downloading", ok,
           f"Expected in {MODE} mode: {expected}\n"
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
    used = sum(1 for v in spread.values() if v)
    if MODE == "static":
        expected = "all succeed, all served by h1"
        ok = all(v == "10/10" for v in summary.values()) and used == 1 and spread["h1"] > 0
    else:
        expected = "all succeed, spread over all 3 servers"
        ok = all(v == "10/10" for v in summary.values()) and used == 3
    record(6, "Six clients downloading 1 MB files at once", ok,
           f"Expected in {MODE} mode: {expected}\n"
           f"Per client: {summary}\nConnections per server: {spread}")


def write_report():
    path = REPO / "tests" / ("d1_results.md" if MODE == "smart" else f"d1_results_{MODE}.md")
    passed = sum(1 for r in results if r[2])
    lines = [f"# D1 test results\n",
             f"Run: {datetime.now():%Y-%m-%d %H:%M}, mode `{MODE}`, "
             f"{passed}/{len(results)} passed.\n",
             "| # | Test | Result |", "|---|---|---|"]
    lines += [f"| {n} | {name} | {'PASS' if ok else 'FAIL'} |" for n, name, ok, _ in results]
    for n, name, ok, evidence in results:
        lines += [f"\n## {n}. {name}: {'PASS' if ok else 'FAIL'}\n", "```", evidence, "```"]
    path.write_text("\n".join(lines) + "\n")
    give_back(path)
    print(f"{passed}/{len(results)} passed. Evidence written to {path}")


def main():
    global MODE
    parser = argparse.ArgumentParser(description="Run the D1 tests in one controller mode.")
    parser.add_argument("--mode", choices=("smart", "roundrobin", "static"), default="smart")
    MODE = parser.parse_args().mode

    setLogLevel("warning")
    # Cleanup kills any ryu-manager, so it has to run before ours starts.
    cleanup()
    LOGS.mkdir(exist_ok=True)
    controller = start_controller(MODE, LOGS / f"controller_{MODE}.log")
    cdn_topo.make_test_files()

    net = Mininet(topo=cdn_topo.CDNTopology(), link=TCLink, controller=None,
                  autoSetMacs=False)
    net.addController(RemoteController("c0", ip="127.0.0.1", port=6653))
    net.start()
    try:
        if not net.waitConnected(timeout=15):
            sys.exit("Switches did not connect to the controller")
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
        controller.terminate()
        controller.wait(timeout=10)
        controller.log.close()
        give_back(LOGS)


if __name__ == "__main__":
    main()
