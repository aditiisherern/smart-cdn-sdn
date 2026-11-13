# D1 test results

Run: 2026-10-27 13:59, mode `smart`, 6/6 passed.

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
Client: Downloaded test_1m.bin: 1048576 bytes in 947.799 ms -> downloads/10.0.0.11_test_1m.bin
1/1 requests succeeded
Served by: ['h1']
CSV line: 2026-10-27T08:27:20.434741+00:00,10.0.0.11,test_1m.bin,1048576,947.799
s1 VIP flow: cookie=0x1100, duration=0.958s, table=0, n_packets=180, n_bytes=11896, idle_timeout=10, priority=10,tcp,nw_src=10.0.0.11,nw_dst=10.0.0.100,tp_src=57444,tp_dst=8080 actions=set_field:00:00:00:00:00:01->eth_dst,set_field:10.0.0.1->ip_dst,output:8
s4 VIP flow: cookie=0x1100, duration=0.961s, table=0, n_packets=222, n_bytes=632231, idle_timeout=10, priority=10,tcp,nw_src=10.0.0.1,nw_dst=10.0.0.11,tp_src=8080,tp_dst=57444 actions=set_field:00:00:00:00:01:00->eth_src,set_field:10.0.0.100->ip_src,output:5
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
15/15 requests succeeded over 16 s
Connections served by h2/h3 during the test: h2=9, h3=6
Client output (last 3 lines):
Downloaded test_100k.bin: 102400 bytes in 136.241 ms -> downloads/10.0.0.11_test_100k.bin
Downloaded test_100k.bin: 102400 bytes in 136.413 ms -> downloads/10.0.0.11_test_100k.bin
15/15 requests succeeded
```

## 5. Link s1-s3 cut while a client is downloading: PASS

```
Traffic was on s3; after the cut, connection flows on s2: True
15/15 requests succeeded
Client output (last 2 lines):
Downloaded test_100k.bin: 102400 bytes in 138.157 ms -> downloads/10.0.0.11_test_100k.bin
15/15 requests succeeded
```

## 6. Six clients downloading 1 MB files at once: PASS

```
Per client: {'h11': '10/10', 'h12': '10/10', 'h13': '10/10', 'h14': '10/10', 'h15': '10/10', 'h16': '10/10'}
Connections per server: {'h1': 22, 'h2': 22, 'h3': 16}
```
