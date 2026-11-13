# Test cases

Setup for every test (repository root, three terminals):

```bash
sudo mn -c
ryu-manager --observe-links controller/cdn_controller.py monitor/link_monitor.py
sudo python3 topology/cdn_topo.py
```

Wait ~5 s after the Mininet CLI appears. `MODE = "smart"` in `config.py` unless stated.
Record what you actually see in the **Actual** line, and copy it into the README table.

---

## 1. Connectivity: pingall

**Steps**
```
mininet> pingall
```
**Expected:** `Results: 0% dropped (72/72 received)`. Controller log shows `host ... at s1 port ...`
lines and no repeated flooding. `sh ovs-ofctl -O OpenFlow13 dump-flows s1` shows
`priority=1,ip,dl_dst=...` flows.

**Actual:** PASS. 0% packet loss (run 2026-10-27).

---

## 2. Download via the virtual IP

**Steps**
```
mininet> h11 python3 servers/client.py --file test_1m.bin
mininet> sh tail -1 servers/client_log.csv
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1 | grep 10.0.0.100
```
**Expected**
- Client prints `Downloaded test_1m.bin: 1048576 bytes in ... ms`.
- Controller logs `DECISION 10.0.0.11:<port> -> 10.0.0.x (smart: ...)`.
- CSV line: `<time>,10.0.0.11,test_1m.bin,1048576,<ms>`.
- s1 has a `priority=10` flow matching `nw_dst=10.0.0.100` with `set_field` actions to the
  chosen server's IP/MAC; s4 has the reverse flow setting the source back to `10.0.0.100`.

Also check that direct download from a real server works (Week 1 check):
`h11 python3 servers/client.py --server 10.0.0.1 --file test_100k.bin`

**Actual:** PASS. `Downloaded test_1m.bin: 1048576 bytes in 947.799 ms`, served by h1. s1 flow: `set_field:10.0.0.1->ip_dst`; s4 flow: `set_field:10.0.0.100->ip_src`.

---

## 3. Error handling

**Steps**
```
mininet> h11 python3 servers/client.py --file does_not_exist.bin
mininet> h11 sh -c 'printf "HELLO\n" | nc -q2 10.0.0.1 8080'
mininet> h11 python3 servers/client.py --server 10.0.0.99 --file test_1m.bin
```
**Expected**
- First: `Server error: ERR NOT_FOUND`, `0/1 requests succeeded`.
- Second: `ERR BAD_REQUEST`.
- Third (nobody at that address): two `Request failed (attempt N)` lines a few seconds
  apart (`No route to host` or `timed out`: one try + one retry), then
  `0/1 requests succeeded`. No crash.

**Actual:** PASS. `ERR NOT_FOUND`, `ERR BAD_REQUEST`, and `No route to host` twice then `0/1 requests succeeded`.

---

## 4. Server failure (server_status updates live)

**Steps**
```
mininet> h11 python3 servers/client.py --loop 30 --interval 1 > /tmp/h11.out 2>&1 &
```
Watch the controller's DECISION lines and note which server is being used (say h2 =
10.0.0.2). Then:
```
mininet> kill_server h2
```
Wait 10 s, then:
```
mininet> start_server h2
mininet> sh cat /tmp/h11.out
```
**Expected**
- Within ~5 s of the kill, controller logs `server 10.0.0.2 is DOWN (no load report for 5s)`.
- At most one or two failed or slow requests around the kill; after that every DECISION
  picks 10.0.0.1 or 10.0.0.3, and downloads succeed.
- After `start_server h2`, controller logs `server 10.0.0.2 is UP`.

**Actual:** PASS. h1 killed: 15/15 requests succeeded; h2 served 9, h3 served 6.

---

## 5. Link failure: reroute to the backup path

**Steps**
```
mininet> h11 python3 servers/client.py --loop 30 --interval 1 --file test_100k.bin > /tmp/h11.out 2>&1 &
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s2 | grep -c priority=10
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s3 | grep -c priority=10
```
One of s2/s3 carries the traffic (non-zero count). Cut that link, e.g. if it's s2:
```
mininet> link_down s1 s2
```
Wait 5 s and run the two `dump-flows` counts again, then `link_up s1 s2` and
`sh cat /tmp/h11.out`.

**Expected**
- Controller logs `link down: s1-s2, rerouting`.
- Afterwards the connection flows are on s3 instead of s2.
- Downloads keep succeeding (perhaps one slow request at the moment of the cut).

**Actual:** PASS. Traffic was on s3; after `link_down s1 s3` the flows moved to s2; 15/15 succeeded.

---

## 6. Six clients at once (D2 preview)

**Steps**
```
mininet> h11 python3 servers/client.py --loop 20 &
mininet> h12 python3 servers/client.py --loop 20 &
mininet> h13 python3 servers/client.py --loop 20 &
mininet> h14 python3 servers/client.py --loop 20 &
mininet> h15 python3 servers/client.py --loop 20 &
mininet> h16 python3 servers/client.py --loop 20 &
```
Wait ~30 s.

**Expected:** all clients finish with `20/20 requests succeeded`; DECISION lines use all
three servers; `servers/client_log.csv` has a line for each request, each with the
right client IP.

**Actual:** PASS. All six clients 10/10; connections h1 22, h2 22, h3 16.
