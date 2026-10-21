
from mininet.topo import Topo


class CDNTopology(Topo):
    def build(self):
        # Four OpenFlow switches.
        s1 = self.addSwitch("s1", protocols="OpenFlow13")
        s2 = self.addSwitch("s2", protocols="OpenFlow13")
        s3 = self.addSwitch("s3", protocols="OpenFlow13")
        s4 = self.addSwitch("s4", protocols="OpenFlow13")

        # Six clients: 10.0.0.11 through 10.0.0.16.
        for i in range(11, 17):
            host = self.addHost(
                f"c{i - 10}",
                ip=f"10.0.0.{i}/24",
                mac=f"00:00:00:00:00:{i:02x}",
            )
            self.addLink(host, s1)

        # Three content servers: 10.0.0.1 through 10.0.0.3.
        for i in range(1, 4):
            host = self.addHost(
                f"srv{i}",
                ip=f"10.0.0.{i}/24",
                mac=f"00:00:00:00:01:{i:02x}",
            )
            self.addLink(host, s4)

        # Two paths between client-side and server-side switches.
        self.addLink(s1, s2)
        self.addLink(s2, s4)
        self.addLink(s1, s3)
        self.addLink(s3, s4)


topos = {"cdn": CDNTopology}
