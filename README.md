# Smart Content Delivery System (SDN + Sockets)

Clients download files from one virtual address, `10.0.0.100`. No real server has that
address. An SDN controller (Ryu) picks the best live server for each new TCP connection,
rewrites the packet addresses so traffic reaches that server, and rewrites the replies so
the client only ever sees `10.0.0.100`. If a server dies or a link breaks, the controller
notices and sends new traffic somewhere that works.

## Objectives

- Serve files over our own TCP protocol to many clients at once (threaded server).
- Hide 3 servers behind one virtual IP using OpenFlow address rewriting.
- Let the application influence the network: servers report their load to the
  controller over UDP, and the controller uses it to choose servers.
- Survive a server crash and a link failure without clients reconfiguring anything.
- Compare smart selection against round-robin and a static baseline (D2).

## Architecture

```
                         ┌────────────────────────────────────────┐
                         │ Ryu controller (127.0.0.1:6653)        │
                         │  cdn_controller.py                     │
                         │   - ARP for 10.0.0.100 + all hosts     │
                         │   - topology graph (LLDP, networkx)    │
                         │   - choose_server() from server_status │
                         │   - installs rewrite + path flows      │
                         │  link_monitor.py: port stats every 2 s │
                         └──────────────────┬─────────────────────┘
                                 OpenFlow 1.3 (packet-in / flow-mod)
                  ┌─────────────────────────┼────────────────────────┐
                  │                         │                        │
 h11 10.0.0.11 ─┐ │       ┌──── s2 ────┐    │                        │
 h12 10.0.0.12 ─┤ │       │  10 Mbps   │    │      ┌─ h1 10.0.0.1:8080
 h13 10.0.0.13 ─┼─┴── s1 ─┤   5 ms     ├─ s4 ┴──────┼─ h2 10.0.0.2:8080
 h14 10.0.0.14 ─┤         │            │            └─ h3 10.0.0.3:8080
 h15 10.0.0.15 ─┤         └──── s3 ────┘
 h16 10.0.0.16 ─┘   clients               two paths      content servers
                    (servers/client.py)   s1-s2-s4       (servers/server.py)
                                          s1-s3-s4
```

Switch-to-switch links are 10 Mbps with 5 ms delay (`TCLink`), so they are the
bottleneck where congestion and failures show. Host links are 100 Mbps.

| Thing | Address | Notes |
|---|---|---|
| Virtual IP / MAC | `10.0.0.100` / `00:00:00:00:01:00` | Clients only ever talk to this |
| Servers h1, h2, h3 | `10.0.0.1`–`10.0.0.3`, MACs `00:00:00:00:00:01`–`03` | TCP port 8080 |
| Clients h11–h16 | `10.0.0.11`–`10.0.0.16`, MACs `00:00:00:00:00:11`–`16` | |
| Load reports | UDP to `10.0.0.100:9999` | Caught by the controller |

All addresses live in `config.py`.

## Message flow

**1. Application protocol (TCP, port 8080)**

```
client → server   GET <filename>\n
server → client   OK <size>\n<size bytes of file>      (success)
                  ERR NOT_FOUND\n                        (no such file)
                  ERR BAD_REQUEST\n                      (malformed request)
```
The client times out after 5 s and retries once. Every completed request appends one
line to `servers/client_log.csv`: `timestamp, client_ip, file, bytes, response_time_ms`.

**2. One download via the virtual IP**

```
h11                     s1 / s2 / s4                 controller               h2
 │ ARP who-has .100 ───────► packet-in ─────────────────► │
 │ ◄─────────────── ARP reply 00:..:01:00 (packet-out) ───│
 │ TCP SYN → 10.0.0.100:8080 ─► packet-in ──────────────► │ choose_server() → h2
 │                          ◄── flow-mods on s1,s2,s4 ────│ (both directions)
 │ SYN, dst rewritten at s1 to 10.0.0.2 / h2's MAC ──────────────────────────────► │
 │ ◄────────── reply, src rewritten at s4 back to 10.0.0.100 / virtual MAC ────────│
 │   (later packets match the flows directly, no controller involved)
```

**3. Servers → controller (socket–SDN integration)**

Every 2 s each server sends `{"server": "10.0.0.2", "active_clients": 3}` over UDP to
`10.0.0.100:9999`. That packet has no flow rule, so it reaches the controller as a
packet-in. The controller updates `server_status`:

```python
server_status = {"10.0.0.1": {"alive": True, "load": 0.3, "latency_ms": None}, ...}
```

`load = active_clients / 10`. A server with no report for 5 s is marked dead.
`choose_server()` skips dead servers and picks the lowest load. So the application's own
state directly changes where the network sends traffic.

**4. Reacting to failures**

| Event | What the controller does |
|---|---|
| Server stops reporting (crash) | Marks it dead, deletes its connection flows; client retries go to a live server |
| Link goes down (`EventLinkDelete`) | Deletes its flows; traffic comes back to the controller and is re-installed on a path avoiding the link. Open connections keep their server |
| ARP broadcast | Answered by the controller, or sent only out of host-facing ports, so it can't loop around the square |

## Modes (`MODE` in `config.py`)

| Mode | Server choice | Path |
|---|---|---|
| `smart` | Lowest load among live servers | Shortest path, rerouted on failure |
| `roundrobin` | h1, h2, h3, h1, ... regardless of health | Shortest path, rerouted on failure |
| `static` | Always h1 | First path found, never rerouted (baseline) |

## Controller (`controller/cdn_controller.py`)

A single Ryu app (OpenFlow 1.3). Start it with `--observe-links`, so Ryu's LLDP discovery
reports the switches and links.

**What it listens to**

| Event | What it does |
|---|---|
| Switch connects | Installs the table-miss rule: anything unmatched goes to the controller |
| `EventSwitchEnter`, `EventLinkAdd` | Adds the switch/link to a `networkx` graph; remembers which ports face other switches |
| `EventLinkDelete` | Removes the link and deletes all its own flows, so traffic is re-installed on paths avoiding it (not in `static` mode) |
| Packet-in: ARP | Answers itself: `00:00:00:00:01:00` for `10.0.0.100`, the learned MAC for any known host. Unknown targets are sent only out of host-facing ports, so broadcasts can't loop around the square |
| Packet-in: UDP to `10.0.0.100:9999` | A server's load report: updates `server_status` |
| Packet-in: TCP to `10.0.0.100:8080` | New connection: `choose_server()`, then installs the redirect flows below |
| Packet-in: other IPv4 | Ordinary routing: `eth_dst` flows along the shortest path |

**Flow rules it installs**

| Priority | Match | Actions | Timeout | Cookie |
|---|---|---|---|---|
| 10 | client IP:port → `10.0.0.100:8080`, on the client's switch | set `eth_dst`/`ipv4_dst` to the server, output | idle 10 s | `0x1100` + server index |
| 10 | server → client IP:port, on the server's switch | set `eth_src`/`ipv4_src` back to the VIP, output | idle 10 s | same |
| 10 | the rewritten packets, on the switches in between | output | idle 10 s | same |
| 1 | IPv4 to a host's MAC | output along the shortest path | idle 30 s | `0x1000` |
| 0 | anything | send to controller | none | 0 |

Rules are per TCP connection, so each new download is balanced separately. The cookies let it
delete exactly one server's flows (server died) or all of its own flows (link died) without
touching the table-miss rule.

**Choosing a server** (`choose_server()`)

- `smart`: skip servers marked dead, then pick the lowest
  `load + assigned / 10 + latency_ms / 100`, where `load = active_clients / 10` from the last
  report and `assigned` counts connections handed out since that report (so a burst of new
  connections doesn't all land on one server before the next report arrives).
- `roundrobin`: h1, h2, h3, h1, ... ignoring health.
- `static`: always h1.

**Failure handling**

- A server with no load report for 5 s is marked dead; in `smart` mode its flows are deleted and
  its connections forgotten, so the client's retry picks a live server.
- The controller remembers which server each connection uses, so after a link failure an open
  connection is re-installed on the new path with the same server.
- Every decision and state change is logged:
  `DECISION 10.0.0.11:43122 -> 10.0.0.2 (smart: lowest score 0.10 of 3 live)`,
  `server 10.0.0.1 is DOWN (no load report for 5s)`, `link down: s1-s2, rerouting`.

The mode comes from `MODE` in `config.py`, or from the `CDN_MODE` environment variable if set
(the experiment runner uses this).

## Repository layout

| Path | What it is | Owner |
|---|---|---|
| `topology/cdn_topo.py` | Mininet topology; starts servers and the CLI | P1 |
| `servers/server.py`, `servers/client.py` | TCP content server and client | P1 |
| `experiments/failures.py` | Kill/start servers, cut/restore/slow links (CLI commands) | P1 |
| `controller/cdn_controller.py` | ARP, routing, server choice, address rewriting, failover | P2 |
| `experiments/run_scenario.py` | Runs one experiment end to end and saves its results | P2 |
| `monitor/link_monitor.py` | Ryu app: port stats every 2 s → `link_load` and a CSV log | P3 |
| `monitor/health_check.py` | Standalone TCP health checker (not used by the controller) | P3 |
| `experiments/parse_logs.py`, `experiments/plot.py` | Turn experiment results into `summary.csv` and graphs | P3 |
| `tests/run_d1_tests.py`, `tests/test_cases.md` | Automated D1 tests and the test case list | P1/P3 |
| `experiments/results/` | One folder per experiment run, plus `summary.csv` and `graphs/` | P3 |
| `config.py` | Shared addresses and mode | All |

## Setup

**Ubuntu 22.04 (Python 3.10), with Ryu:**

```bash
sudo apt install mininet openvswitch-switch iperf python3-pip
pip install ryu eventlet==0.30.2 networkx pandas matplotlib
```
If Ryu won't start, it's the eventlet version: use `eventlet==0.30.2`, or Python 3.8.

**Ubuntu 24.04 / Pop!_OS 24.04 (Python 3.12):** Ryu doesn't run on Python 3.12. Use
[uv](https://docs.astral.sh/uv/) to get Python 3.9 without sudo and put Ryu in a
`.venv-ryu` folder inside the repo:

```bash
sudo apt install mininet openvswitch-switch iperf netcat-openbsd
curl -LsSf https://astral.sh/uv/install.sh | sh     # installs uv (or: pipx install uv)
uv venv --python 3.9 .venv-ryu
VIRTUAL_ENV=.venv-ryu uv pip install "setuptools<58" wheel
VIRTUAL_ENV=.venv-ryu uv pip install --no-build-isolation "ryu==4.34" eventlet==0.30.2 networkx
VIRTUAL_ENV=.venv-ryu uv pip install pandas matplotlib      # for the graphs
```
Pin `ryu==4.34`: left unpinned, uv picks an ancient 2.x whose `ryu-manager` is broken.
Then use `.venv-ryu/bin/ryu-manager` wherever this README says `ryu-manager`, and
`.venv-ryu/bin/python` for `parse_logs.py` and `plot.py`.

## Running

Run everything from the repository root, each in its own terminal, in this order:

```bash
sudo mn -c                                                    # clear old runs
ryu-manager --observe-links controller/cdn_controller.py monitor/link_monitor.py
sudo python3 topology/cdn_topo.py                             # network + 3 servers + CLI
```

The topology creates `test_100k.bin`, `test_1m.bin` and `test_10m.bin` in `servers/content/`
and starts a server on h1, h2 and h3 (logs in `/tmp/h1_server.log` etc.). Wait about
5 seconds for link discovery. Then, in the Mininet CLI:

```
mininet> pingall
mininet> h11 python3 servers/client.py --file test_1m.bin
mininet> h11 python3 servers/client.py --loop 50 --interval 1 &
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1
```

Client options: `--server IP` (default `10.0.0.100`), `--file NAME` (default `test_1m.bin`),
`--loop N`, `--interval SECONDS`.

Extra CLI commands for breaking things:

| Command | Effect |
|---|---|
| `kill_server h1` / `start_server h1` | Stop / restart the server process on h1 |
| `link_down s1 s2` / `link_up s1 s2` | Cut / restore a link |
| `slow_link s2 s4 1 50ms` / `fix_link s2 s4` | Throttle a link to 1 Mbps, 50 ms / restore it |
| `servers` | List running servers |

The controller terminal shows each decision, e.g.
`DECISION 10.0.0.11:43122 -> 10.0.0.2 (smart: lowest score 0.10 of 3 live)`, plus
`server ... is UP/DOWN` and `link down ... rerouting` messages.

## Test cases (D1)

Full steps are in [`tests/test_cases.md`](tests/test_cases.md). The test script starts its own
controller, so you don't need one running:

```bash
sudo python3 tests/run_d1_tests.py                     # smart → tests/d1_results.md
sudo python3 tests/run_d1_tests.py --mode roundrobin   # → tests/d1_results_roundrobin.md
sudo python3 tests/run_d1_tests.py --mode static       # → tests/d1_results_static.md
```

In `static` mode, tests 4 and 5 pass when the system does **not** recover, because static is
the no-failover baseline. The table below is the smart-mode run from 2026-10-27.

| # | Test | Expected result | Actual result |
|---|---|---|---|
| 1 | `pingall` | 0% dropped (72/72) | PASS: 0% loss |
| 2 | h11 downloads `test_1m.bin` from `10.0.0.100` | `OK`, 1048576 bytes, CSV line written, controller logs a DECISION | PASS: 1 MB in 948 ms, served by h1; rewrite flows seen on s1 and s4 |
| 3 | Missing file / bad request | `ERR NOT_FOUND` / `ERR BAD_REQUEST`, client exits cleanly | PASS: `ERR NOT_FOUND`, `ERR BAD_REQUEST`; unreachable server failed twice cleanly |
| 4 | `kill_server` on the server in use, client looping | Controller logs DOWN within ~5 s; later requests succeed from another server | PASS: 15/15 succeeded after killing h1 (h2 served 9, h3 served 6) |
| 5 | `link_down` on the path in use, client looping | Controller logs reroute; requests keep succeeding via the other path | PASS: traffic moved from s3 to s2, 15/15 succeeded |
| 6 | 6 clients looping at once | All succeed; DECISION lines spread across h1–h3 | PASS: 60/60 succeeded, spread h1 22 / h2 22 / h3 16 |

## Experiments (D2)

`experiments/run_scenario.py` runs one experiment through the real system (clients talk to
`10.0.0.100`, the controller makes every decision). It starts its own controller in the chosen
mode, so stop any controller you have running first.

```bash
sudo python3 experiments/run_scenario.py --mode smart --exp 2    # one run, ~2.5 min
sudo python3 experiments/run_scenario.py --all                   # 5 experiments x 3 modes, ~40 min
```

Each run lasts 120 s; the failure happens at 60 s (`--duration`, `--fail-at` to change).
Clients request `test_100k.bin` every second (`--file`, `--interval`).

| # | Experiment | Clients |
|---|---|---|
| 1 | Normal, nothing breaks | h11–h13 |
| 2 | Server h1 killed at 60 s | h11–h13 |
| 3 | The s1 link on the path in use cut at 60 s | h11–h13 |
| 4 | The s4 link on the path in use slowed to 1 Mbps at 60 s | h11–h13 |
| 5 | Six clients at once | h11–h16 |

Results go to `experiments/results/exp<N>_<mode>/`:

| File | Contents |
|---|---|
| `clients.csv` | One line per successful download: `timestamp,client_ip,file,bytes,response_time_ms` |
| `events.csv` | Exact times of `start`, the failure (`server_killed`, `link_down`, `link_slowed`) and `end`, in the same timestamp format, for recovery time |
| `summary.json` | Settings and per-client succeeded/failed request counts (a request counts as failed if its retry also failed) |
| `controller.log` | Controller output, including every DECISION |
| `client_output.txt` | What each client printed |
| `link_utilization.csv` | Link monitor: % use of every switch port, every 2 s |

Then turn all the runs into one table and the graphs:

```bash
python3 experiments/parse_logs.py    # → experiments/results/summary.csv
python3 experiments/plot.py          # → experiments/results/graphs/*.png
```
(On Ubuntu 24.04 use `.venv-ryu/bin/python` instead of `python3`.)

**Recovery time** in `summary.csv` is the longest any client went without a successful download
after the failure (`never` if a client got nothing more). `normal_wait_s` is the same measure
before the failure, for comparison: about 1–2 s when everything is healthy.

## Results

_To be filled in from the full run (`run_scenario.py --all`, 120 s per run, failure at 60 s)._

## Troubleshooting

| Problem | Fix |
|---|---|
| `pingall` fails right after start | Link discovery takes a few seconds. Wait and run it again |
| `pingall` fails every time | Controller not running, or started after Mininet. `sudo mn -c`, start the controller first |
| Client hangs on `10.0.0.100` | Controller not running, or `MODE=smart` with no live servers (check for `server ... is UP`) |
| "File exists" starting Mininet | `sudo mn -c`. It also kills the controller, so start the controller again afterwards |
| See exactly what a switch does | `sh ovs-ofctl -O OpenFlow13 dump-flows s1` in the Mininet CLI |

## Known limitations

- **Server choice ignores latency and link load.** `latency_ms` is always `None` in the
  controller, and the port statistics aren't used. A slow or congested link is not avoided
  (experiment 4); only a link that goes down is.
- **One path at a time.** All connections between the same two switches take the same shortest
  path; traffic isn't split across s2 and s3.
- **Detecting a dead server takes up to ~5–7 s** (reports every 2 s, 5 s timeout). Requests in
  that window can fail and rely on the client's single retry.
- **Connections to a server that dies are broken**, not moved: TCP state lives on that server.
  The client's retry opens a new connection to a live server.
- **Load is the number of open connections** a server reports, not bytes or CPU.
- **The first packet of every connection goes through the controller**, adding a little setup
  delay to each download.
- **`static` mode never recovers** from a server or link failure. That is intentional: it is the
  baseline.
