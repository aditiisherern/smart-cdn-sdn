import os
import sys
from pathlib import Path

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.topo import Topo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "experiments"))

import config  # noqa: E402
import failures  # noqa: E402

CONTENT_DIR = REPO / "servers" / "content"
TEST_FILES = {"test_100k.bin": 100 * 1024,
              "test_1m.bin": 1024 * 1024,
              "test_10m.bin": 10 * 1024 * 1024}

# Core links are the bottleneck, so congestion and slowdowns show up there.
CORE_LINK = {"bw": 10, "delay": "5ms"}
HOST_LINK = {"bw": 100}


class CDNTopology(Topo):
    def build(self):
        # Four OpenFlow switches in a square: s1-s2-s4 and s1-s3-s4.
        s1 = self.addSwitch("s1", protocols="OpenFlow13")
        s2 = self.addSwitch("s2", protocols="OpenFlow13")
        s3 = self.addSwitch("s3", protocols="OpenFlow13")
        s4 = self.addSwitch("s4", protocols="OpenFlow13")

        # Six clients h11..h16 = 10.0.0.11..16, MAC ends in the same digits.
        for i in range(11, 17):
            host = self.addHost(f"h{i}", ip=f"10.0.0.{i}/24",
                                mac=f"00:00:00:00:00:{i:02d}")
            self.addLink(host, s1, **HOST_LINK)

        # Three servers h1..h3; IPs and MACs must match config.SERVERS,
        # because the controller rewrites eth_dst to these MACs.
        for i, (ip, mac) in enumerate(config.SERVERS.items(), start=1):
            host = self.addHost(f"h{i}", ip=f"{ip}/24", mac=mac)
            self.addLink(host, s4, **HOST_LINK)

        self.addLink(s1, s2, **CORE_LINK)
        self.addLink(s2, s4, **CORE_LINK)
        self.addLink(s1, s3, **CORE_LINK)
        self.addLink(s3, s4, **CORE_LINK)


topos = {"cdn": CDNTopology}


def make_test_files():
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    for name, size in TEST_FILES.items():
        path = CONTENT_DIR / name
        if not path.exists() or path.stat().st_size != size:
            path.write_bytes(os.urandom(size))


def main():
    setLogLevel("info")
    make_test_files()

    net = Mininet(topo=CDNTopology(), link=TCLink, controller=None,
                  autoSetMacs=False)
    net.addController(RemoteController("c0", ip="127.0.0.1", port=6653))
    net.start()

    try:
        for name in ("h1", "h2", "h3"):
            failures.start_server(net, name)
        failures.CDNCLI(net)
    finally:
        for name in ("h1", "h2", "h3"):
            failures.kill_server(net, name)
        net.stop()


if __name__ == "__main__":
    main()
