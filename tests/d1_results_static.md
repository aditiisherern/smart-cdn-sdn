# D1 test results

Run: 2026-11-15 12:20, mode `static`, 6/6 passed.

| # | Test | Result |
|---|---|---|
| 1 | pingall | PASS |
| 2 | Download via the virtual IP 10.0.0.100 | PASS |
| 3 | Error handling | PASS |
| 4 | Server h1 killed while a client is downloading | PASS |
| 5 | Link s1-s3 cut while a client is downloading | PASS |
| 6 | Six clients downloading 1 MB files at once | PASS |

## 1. pingall: PASS

```
Packet loss: 0%
```

## 2. Download via the virtual IP 10.0.0.100: PASS

```
Client: Downloaded test_1m.bin: 1048576 bytes in 935.320 ms -> downloads/10.0.0.11_test_1m.bin
1/1 requests succeeded
Served by: ['h1']
CSV line: 2026-11-15T06:47:03.932772+00:00,10.0.0.11,test_1m.bin,1048576,935.32
s1 VIP flow: cookie=0x1100, duration=0.942s, table=0, n_packets=171, n_bytes=11302, idle_timeout=10, priority=10,tcp,nw_src=10.0.0.11,nw_dst=10.0.0.100,tp_src=55290,tp_dst=8080 actions=set_field:00:00:00:00:00:01->eth_dst,set_field:10.0.0.1->ip_dst,output:8
s4 VIP flow: cookie=0x1100, duration=0.947s, table=0, n_packets=228, n_bytes=711555, idle_timeout=10, priority=10,tcp,nw_src=10.0.0.1,nw_dst=10.0.0.11,tp_src=8080,tp_dst=55290 actions=set_field:00:00:00:00:01:00->eth_src,set_field:10.0.0.100->ip_src,output:5
Direct download from 10.0.0.1: 1/1 requests succeeded
```

## 3. Error handling: PASS

```
Missing file: Server error: ERR NOT_FOUND
0/1 requests succeeded
Bad request: ERR BAD_REQUEST
Unreachable server: Request failed (attempt 1): [Errno 113] No route to host
Request failed (attempt 2): [Errno 113] No route to host
0/1 requests succeeded
```

## 4. Server h1 killed while a client is downloading: PASS

```
Expected in static mode: static never fails over, so all requests to the dead h1 fail
0/15 requests succeeded over 15 s
Connections served by h2/h3 during the test: h2=0, h3=0
Client output (last 3 lines):
Request failed (attempt 1): [Errno 111] Connection refused
Request failed (attempt 2): [Errno 111] Connection refused
0/15 requests succeeded
```

## 5. Link s1-s3 cut while a client is downloading: PASS

```
Expected in static mode: static never reroutes, so requests after the cut fail
Traffic was on s3; after the cut, connection flows on s2: False
3/15 requests succeeded
Client output (last 2 lines):
Request failed (attempt 2): timed out
3/15 requests succeeded
```

## 6. Six clients downloading 1 MB files at once: PASS

```
Expected in static mode: all succeed, all served by h1
Per client: {'h11': '10/10', 'h12': '10/10', 'h13': '10/10', 'h14': '10/10', 'h15': '10/10', 'h16': '10/10'}
Connections per server: {'h1': 60, 'h2': 0, 'h3': 0}
```
