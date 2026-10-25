VIP = "10.0.0.100"
VMAC = "00:00:00:00:01:00"
SERVERS = {"10.0.0.1": "00:00:00:00:00:01",
           "10.0.0.2": "00:00:00:00:00:02",
           "10.0.0.3": "00:00:00:00:00:03"}
CONTENT_PORT = 8080
REPORT_IP = VIP   # servers send UDP load reports here; the controller catches them as packet-ins
REPORT_PORT = 9999
MODE = "smart"   # smart | static | roundrobin
