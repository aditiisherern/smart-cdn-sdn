# D1 test results

Run: 2026-11-15 12:15, mode `roundrobin`, 6/6 passed.

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
Client: Downloaded test_1m.bin: 1048576 bytes in 935.163 ms -> downloads/10.0.0.11_test_1m.bin
1/1 requests succeeded
Served by: ['h1']
CSV line: 2026-11-15T06:43:41.172060+00:00,10.0.0.11,test_1m.bin,1048576,935.163
s1 VIP flow: cookie=0x1100, duration=0.945s, table=0, n_packets=171, n_bytes=11302, idle_timeout=10, priority=10,tcp,nw_src=10.0.0.11,nw_dst=10.0.0.100,tp_src=50574,tp_dst=8080 actions=set_field:00:00:00:00:00:01->eth_dst,set_field:10.0.0.1->ip_dst,output:8
s4 VIP flow: cookie=0x1100, duration=0.950s, table=0, n_packets=229, n_bytes=711621, idle_timeout=10, priority=10,tcp,nw_src=10.0.0.1,nw_dst=10.0.0.11,tp_src=8080,tp_dst=50574 actions=set_field:00:00:00:00:01:00->eth_src,set_field:10.0.0.100->ip_src,output:5
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
Expected in roundrobin mode: requests keep succeeding on h2/h3
15/15 requests succeeded over 16 s
Connections served by h2/h3 during the test: h2=8, h3=7
Client output (last 3 lines):
Request failed (attempt 1): [Errno 111] Connection refused
Downloaded test_100k.bin: 102400 bytes in 164.365 ms -> downloads/10.0.0.11_test_100k.bin
15/15 requests succeeded
```

## 5. Link s1-s3 cut while a client is downloading: PASS

```
Expected in roundrobin mode: traffic moves to the other path and keeps succeeding
Traffic was on s3; after the cut, connection flows on s2: True
15/15 requests succeeded
Client output (last 2 lines):
Downloaded test_100k.bin: 102400 bytes in 138.530 ms -> downloads/10.0.0.11_test_100k.bin
15/15 requests succeeded
```

## 6. Six clients downloading 1 MB files at once: PASS

```
Expected in roundrobin mode: all succeed, spread over all 3 servers
Per client: {'h11': '10/10', 'h12': '10/10', 'h13': '10/10', 'h14': '10/10', 'h15': '10/10', 'h16': '10/10'}
Connections per server: {'h1': 20, 'h2': 20, 'h3': 20}
```
