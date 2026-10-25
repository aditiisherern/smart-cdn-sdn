"""Ways to break the network, and to fix it again.

Each function takes the running Mininet `net`, so P3's experiment scripts can
call them directly. CDNCLI exposes the same functions as Mininet CLI commands.
"""

from pathlib import Path

from mininet.cli import CLI

REPO = Path(__file__).resolve().parents[1]
SERVER_SCRIPT = REPO / "servers" / "server.py"

# host name -> PID of its server process
server_pids = {}


def start_server(net, name):
    if name in server_pids:
        print(f"{name}: server already running (pid {server_pids[name]})")
        return
    host = net.get(name)
    out = host.cmd(f"python3 -u {SERVER_SCRIPT} > /tmp/{name}_server.log 2>&1 & echo $!")
    server_pids[name] = int(out.strip().split()[-1])
    print(f"{name}: server started (pid {server_pids[name]}), log in /tmp/{name}_server.log")


def kill_server(net, name):
    # Kill by PID. All Mininet hosts share one process table, so
    # `pkill -f server.py` would kill every server, not just this one.
    pid = server_pids.pop(name, None)
    if pid is None:
        print(f"{name}: no server running")
        return
    net.get(name).cmd(f"kill {pid}")
    print(f"{name}: server killed (pid {pid})")


def link_down(net, a, b):
    net.configLinkStatus(a, b, "down")
    print(f"link {a}-{b}: down")


def link_up(net, a, b):
    net.configLinkStatus(a, b, "up")
    print(f"link {a}-{b}: up")


def slow_link(net, a, b, bw=1, delay="5ms"):
    for link in net.linksBetween(net.get(a), net.get(b)):
        link.intf1.config(bw=bw, delay=delay)
        link.intf2.config(bw=bw, delay=delay)
    print(f"link {a}-{b}: slowed to {bw} Mbps, {delay}")


def fix_link(net, a, b):
    # intf.params holds the bw/delay the link was created with.
    for link in net.linksBetween(net.get(a), net.get(b)):
        link.intf1.config(**link.intf1.params)
        link.intf2.config(**link.intf2.params)
    print(f"link {a}-{b}: restored")


class CDNCLI(CLI):
    """Mininet CLI plus: kill_server, start_server, link_down, link_up,
    slow_link, fix_link, servers."""

    def _args(self, line, count, usage):
        args = line.split()
        if len(args) < count:
            print(f"usage: {usage}")
            return None
        return args

    def do_kill_server(self, line):
        """kill_server h1"""
        args = self._args(line, 1, "kill_server h1")
        if args:
            kill_server(self.mn, args[0])

    def do_start_server(self, line):
        """start_server h1"""
        args = self._args(line, 1, "start_server h1")
        if args:
            start_server(self.mn, args[0])

    def do_link_down(self, line):
        """link_down s1 s2"""
        args = self._args(line, 2, "link_down s1 s2")
        if args:
            link_down(self.mn, args[0], args[1])

    def do_link_up(self, line):
        """link_up s1 s2"""
        args = self._args(line, 2, "link_up s1 s2")
        if args:
            link_up(self.mn, args[0], args[1])

    def do_slow_link(self, line):
        """slow_link s2 s4 [bw_mbps] [delay], e.g. slow_link s2 s4 1 50ms"""
        args = self._args(line, 2, "slow_link s2 s4 [bw_mbps] [delay]")
        if args:
            bw = float(args[2]) if len(args) > 2 else 1
            delay = args[3] if len(args) > 3 else "5ms"
            slow_link(self.mn, args[0], args[1], bw, delay)

    def do_fix_link(self, line):
        """fix_link s2 s4"""
        args = self._args(line, 2, "fix_link s2 s4")
        if args:
            fix_link(self.mn, args[0], args[1])

    def do_servers(self, line):
        """List running content servers."""
        for name, pid in sorted(server_pids.items()):
            print(f"{name}: pid {pid}")
