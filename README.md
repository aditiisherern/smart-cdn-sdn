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

## Repository layout

| Path | What it is | Owner |
|---|---|---|
| `topology/cdn_topo.py` | Mininet topology; starts servers and the CLI | P1 |
| `servers/server.py`, `servers/client.py` | TCP content server and client | P1 |
| `experiments/failures.py` | Kill/start servers, cut/restore/slow links (CLI commands) | P1 |
| `controller/cdn_controller.py` | ARP, routing, server choice, address rewriting, failover | P2 |
| `monitor/link_monitor.py` | Port stats every 2 s → `link_load` | P3 |
| `tests/test_cases.md` | Test cases with steps and expected results | P3 |
| `config.py` | Shared addresses and mode | All |

## Setup

**Ubuntu 22.04 (Python 3.10), with Ryu:**

```bash
sudo apt install mininet openvswitch-switch iperf python3-pip
pip install ryu eventlet==0.30.2 networkx
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
```
Pin `ryu==4.34`: left unpinned, uv picks an ancient 2.x whose `ryu-manager` is broken.
Then use `.venv-ryu/bin/ryu-manager` wherever this README says `ryu-manager`.

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

Full steps are in [`tests/test_cases.md`](tests/test_cases.md).

| # | Test | Expected result | Actual result |
|---|---|---|---|
| 1 | `pingall` | 0% dropped (72/72) | _fill in after running_ |
| 2 | h11 downloads `test_1m.bin` from `10.0.0.100` | `OK`, 1048576 bytes, CSV line written, controller logs a DECISION | |
| 3 | Missing file / bad request | `ERR NOT_FOUND` / `ERR BAD_REQUEST`, client exits cleanly | |
| 4 | `kill_server` on the server in use, client looping | Controller logs DOWN within ~5 s; later requests succeed from another server | |
| 5 | `link_down` on the path in use, client looping | Controller logs reroute; requests keep succeeding via the other path | |
| 6 | 6 clients looping at once | All succeed; DECISION lines spread across h1–h3 | |

## Troubleshooting

| Problem | Fix |
|---|---|
| `pingall` fails right after start | Link discovery takes a few seconds. Wait and run it again |
| `pingall` fails every time | Controller not running, or started after Mininet. `sudo mn -c`, start the controller first |
| Client hangs on `10.0.0.100` | Controller not running, or `MODE=smart` with no live servers (check for `server ... is UP`) |
| "File exists" starting Mininet | `sudo mn -c`. It also kills the controller, so start the controller again afterwards |
| See exactly what a switch does | `sh ovs-ofctl -O OpenFlow13 dump-flows s1` in the Mininet CLI |

## Still to do for D2

- `monitor/health_check.py`: fill in `latency_ms` (currently `None`).
- Use `link_load` from the link monitor to avoid congested paths.
- `experiments/run_scenario.py`, `parse_logs.py`, `plot.py`: the 5 experiments and graphs.
