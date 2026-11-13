"""Smart CDN controller (Ryu, OpenFlow 1.3). Run with --observe-links.

- Answers ARP itself (including for the virtual IP), so broadcasts never
  circle the square topology.
- Routes ordinary traffic along shortest paths on a networkx graph built
  from LLDP topology discovery.
- Redirects each TCP connection to VIP:8080 to a real server, rewriting
  addresses both ways so the client only ever sees the VIP.
- Reads the servers' UDP load reports (VIP:9999) to keep server_status live,
  and drops a dead server's connections so new ones go elsewhere.
"""

import json
import sys
import time
from pathlib import Path

import networkx as nx

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import arp, ether_types, ethernet, ipv4, packet, tcp, udp
from ryu.ofproto import ofproto_v1_3
from ryu.topology import event as topo_event

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

SERVER_IPS = list(config.SERVERS)
REPORT_TIMEOUT = 5     # seconds without a load report before a server counts as dead
MAX_CLIENTS = 10       # active_clients that counts as load 1.0
CONN_IDLE = 10         # idle_timeout of per-connection redirect flows
L2_IDLE = 30           # idle_timeout of ordinary host-to-host flows
CONN_FORGET = 300      # seconds before an idle connection is forgotten

COOKIE_APP = 0x1000    # set on every flow we install except table-miss
COOKIE_CONN = 0x1100   # | server index, on per-connection redirect flows


class CDNController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(CDNController, self).__init__(*args, **kwargs)
        self.datapaths = {}         # dpid -> datapath
        self.switch_ports = {}      # dpid -> set of port numbers
        self.graph = nx.DiGraph()   # switches; edge attribute "port" = output port
        self.link_ports = set()     # (dpid, port) facing another switch
        self.hosts = {}             # mac -> (dpid, port)
        self.ip_to_mac = {}
        self.connections = {}       # (client_ip, client_port) -> connection dict
        self.static_hops = {}       # static mode: paths are computed once, never change
        self.rr_next = 0
        self.recent_arp = {}        # (src_ip, dst_ip) -> time of last flood

        # Day 0 handoff format. Filled from the servers' UDP load reports.
        self.server_status = {ip: {"alive": False, "load": 0.0, "latency_ms": None}
                              for ip in SERVER_IPS}
        self.last_report = {}
        self.assigned = {ip: 0 for ip in SERVER_IPS}  # connections given out since last report

        self.logger.info("CDN controller starting in %s mode", config.MODE)
        hub.spawn(self._watch_servers)

    # ---------------------------------------------------------------- switches

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        self.datapaths[datapath.id] = datapath

        # Table-miss: send everything we have no rule for to the controller.
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions, cookie=0, idle_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        datapath.send_msg(parser.OFPFlowMod(datapath=datapath, cookie=cookie,
                                            priority=priority, match=match,
                                            instructions=inst,
                                            idle_timeout=idle_timeout))

    def delete_flows(self, cookie, cookie_mask):
        for datapath in self.datapaths.values():
            ofproto = datapath.ofproto
            datapath.send_msg(datapath.ofproto_parser.OFPFlowMod(
                datapath=datapath, cookie=cookie, cookie_mask=cookie_mask,
                table_id=ofproto.OFPTT_ALL, command=ofproto.OFPFC_DELETE,
                out_port=ofproto.OFPP_ANY, out_group=ofproto.OFPG_ANY))

    def packet_out(self, datapath, in_port, actions, data):
        ofproto = datapath.ofproto
        datapath.send_msg(datapath.ofproto_parser.OFPPacketOut(
            datapath=datapath, buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=in_port, actions=actions, data=data))

    # ---------------------------------------------------------------- topology

    @set_ev_cls(topo_event.EventSwitchEnter)
    def _switch_enter(self, ev):
        dpid = ev.switch.dp.id
        self.graph.add_node(dpid)
        self.switch_ports[dpid] = {p.port_no for p in ev.switch.ports
                                   if p.port_no <= ofproto_v1_3.OFPP_MAX}

    @set_ev_cls(topo_event.EventSwitchLeave)
    def _switch_leave(self, ev):
        dpid = ev.switch.dp.id
        if self.graph.has_node(dpid):
            self.graph.remove_node(dpid)
        self.switch_ports.pop(dpid, None)

    @set_ev_cls(topo_event.EventPortAdd)
    def _port_add(self, ev):
        port = ev.port
        if port.port_no <= ofproto_v1_3.OFPP_MAX:
            self.switch_ports.setdefault(port.dpid, set()).add(port.port_no)

    @set_ev_cls(topo_event.EventLinkAdd)
    def _link_add(self, ev):
        src, dst = ev.link.src, ev.link.dst
        if not self.graph.has_edge(src.dpid, dst.dpid):
            self.logger.info("link up: s%d port %d -> s%d", src.dpid, src.port_no, dst.dpid)
        self.graph.add_edge(src.dpid, dst.dpid, port=src.port_no)
        # These ports stay marked as switch-facing even if the link goes down.
        self.link_ports.add((src.dpid, src.port_no))
        self.link_ports.add((dst.dpid, dst.port_no))

    @set_ev_cls(topo_event.EventLinkDelete)
    def _link_delete(self, ev):
        src, dst = ev.link.src, ev.link.dst
        if not self.graph.has_edge(src.dpid, dst.dpid):
            return
        self.graph.remove_edge(src.dpid, dst.dpid)
        if config.MODE == "static":
            self.logger.info("link down: s%d-s%d (static mode: not rerouting)",
                             src.dpid, dst.dpid)
            return
        # Wipe our flows. Traffic comes back to the controller and is
        # re-installed on paths that avoid the dead link; existing
        # connections keep their server because self.connections remembers it.
        self.logger.info("link down: s%d-s%d, rerouting", src.dpid, dst.dpid)
        self.delete_flows(COOKIE_APP, COOKIE_APP)

    def _hops(self, src, dst):
        """[(dpid, out_port), ...] from switch src to switch dst.
        The last hop's port is None: the caller fills in the host port."""
        if config.MODE == "static" and (src, dst) in self.static_hops:
            return self.static_hops[(src, dst)]
        try:
            path = nx.shortest_path(self.graph, src, dst)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None
        hops = [(a, self.graph[a][b]["port"]) for a, b in zip(path, path[1:])]
        hops.append((dst, None))
        if config.MODE == "static":
            self.static_hops[(src, dst)] = hops
        return hops

    # ---------------------------------------------------------------- hosts

    def _learn(self, mac, ip, dpid, port):
        # Only learn from host-facing ports, and never the virtual MAC.
        if mac == config.VMAC or (dpid, port) in self.link_ports:
            return
        if mac not in self.hosts:
            self.hosts[mac] = (dpid, port)
            self.logger.info("host %s %s at s%d port %d", ip, mac, dpid, port)
        if ip and ip != "0.0.0.0":
            self.ip_to_mac[ip] = mac

    def _locate(self, ip):
        mac = config.SERVERS.get(ip) or self.ip_to_mac.get(ip)
        return self.hosts.get(mac)

    # ---------------------------------------------------------------- packet-in

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt:
            self._learn(eth.src, arp_pkt.src_ip, datapath.id, in_port)
            self._handle_arp(datapath, in_port, arp_pkt, msg.data)
            return

        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if ip_pkt is None:
            return  # IPv6 and others: not used here
        self._learn(eth.src, ip_pkt.src, datapath.id, in_port)

        udp_pkt = pkt.get_protocol(udp.udp)
        if (udp_pkt and ip_pkt.dst == config.VIP
                and udp_pkt.dst_port == config.REPORT_PORT):
            self._handle_report(ip_pkt.src, pkt.protocols[-1])
            return

        tcp_pkt = pkt.get_protocol(tcp.tcp)
        if tcp_pkt and self._handle_cdn_tcp(datapath, in_port, ip_pkt, tcp_pkt, msg.data):
            return

        if ip_pkt.dst == config.VIP:
            return  # only TCP to VIP:8080 is served
        self._route(datapath, in_port, eth.dst, msg.data)

    # ---------------------------------------------------------------- ARP

    def _handle_arp(self, datapath, in_port, arp_pkt, data):
        if (datapath.id, in_port) in self.link_ports:
            return  # a copy of a flood we already handled

        if arp_pkt.opcode == arp.ARP_REQUEST:
            if arp_pkt.dst_ip == config.VIP:
                mac = config.VMAC
            else:
                mac = self.ip_to_mac.get(arp_pkt.dst_ip)
            if mac:
                self._arp_reply(datapath, in_port, arp_pkt, mac)
            else:
                self._flood_to_hosts(datapath.id, in_port, arp_pkt, data)

        elif arp_pkt.opcode == arp.ARP_REPLY:
            location = self.hosts.get(arp_pkt.dst_mac)
            if location:
                target = self.datapaths[location[0]]
                self.packet_out(target, target.ofproto.OFPP_CONTROLLER,
                                [target.ofproto_parser.OFPActionOutput(location[1])], data)

    def _arp_reply(self, datapath, in_port, request, mac):
        reply = packet.Packet()
        reply.add_protocol(ethernet.ethernet(ethertype=ether_types.ETH_TYPE_ARP,
                                             dst=request.src_mac, src=mac))
        reply.add_protocol(arp.arp(opcode=arp.ARP_REPLY,
                                   src_mac=mac, src_ip=request.dst_ip,
                                   dst_mac=request.src_mac, dst_ip=request.src_ip))
        reply.serialize()
        self.packet_out(datapath, datapath.ofproto.OFPP_CONTROLLER,
                        [datapath.ofproto_parser.OFPActionOutput(in_port)], reply.data)

    def _flood_to_hosts(self, dpid, in_port, arp_pkt, data):
        """Send an ARP request out of every host-facing port, never between
        switches, so it can't loop."""
        key = (arp_pkt.src_ip, arp_pkt.dst_ip)
        now = time.time()
        if now - self.recent_arp.get(key, 0) < 1:
            return
        self.recent_arp[key] = now

        for other, ports in self.switch_ports.items():
            datapath = self.datapaths.get(other)
            if datapath is None:
                continue
            parser = datapath.ofproto_parser
            actions = [parser.OFPActionOutput(port) for port in sorted(ports)
                       if (other, port) not in self.link_ports
                       and not (other == dpid and port == in_port)]
            if actions:
                self.packet_out(datapath, datapath.ofproto.OFPP_CONTROLLER, actions, data)

    # ---------------------------------------------------------------- ordinary routing

    def _route(self, datapath, in_port, dst_mac, data):
        location = self.hosts.get(dst_mac)
        if location is None:
            return
        hops = self._hops(datapath.id, location[0])
        if hops is None:
            return
        for dpid, port in hops:
            hop_dp = self.datapaths[dpid]
            parser = hop_dp.ofproto_parser
            out = port if port is not None else location[1]
            # IPv4 only: ARP must keep reaching the controller, which is
            # where hosts get learned.
            match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, eth_dst=dst_mac)
            self.add_flow(hop_dp, 1, match,
                          [parser.OFPActionOutput(out)], COOKIE_APP, L2_IDLE)
        out = hops[0][1] if hops[0][1] is not None else location[1]
        self.packet_out(datapath, in_port, [datapath.ofproto_parser.OFPActionOutput(out)], data)

    # ---------------------------------------------------------------- CDN redirect

    def choose_server(self):
        if config.MODE == "static":
            return SERVER_IPS[0], "static"

        if config.MODE == "roundrobin":
            server = SERVER_IPS[self.rr_next % len(SERVER_IPS)]
            self.rr_next += 1
            return server, "round-robin"

        alive = [ip for ip in SERVER_IPS
                 if self.server_status[ip]["alive"] and self._locate(ip)]
        if not alive:
            return None, "no live server"

        def score(ip):
            status = self.server_status[ip]
            # assigned covers connections handed out since the last report.
            load = status["load"] + self.assigned[ip] / MAX_CLIENTS
            return load + (status["latency_ms"] or 0) / 100

        best = min(alive, key=score)
        return best, f"smart: lowest score {score(best):.2f} of {len(alive)} live"

    def _handle_cdn_tcp(self, datapath, in_port, ip_pkt, tcp_pkt, data):
        """Handle a packet belonging to a VIP connection. Returns False if it isn't one."""
        port = config.CONTENT_PORT

        if ip_pkt.dst == config.VIP and tcp_pkt.dst_port == port:
            # Client -> VIP, not rewritten yet. A new connection picks a server.
            forward, rewritten = True, False
            key = (ip_pkt.src, tcp_pkt.src_port)
            conn = self.connections.get(key) or self._new_connection(key)
            if conn is None:
                return True
        elif tcp_pkt.dst_port == port and ip_pkt.dst in config.SERVERS:
            # Client -> server, already rewritten (we're past the first switch).
            forward, rewritten = True, True
            conn = self.connections.get((ip_pkt.src, tcp_pkt.src_port))
            if conn is None or conn["server"] != ip_pkt.dst:
                return False
        elif tcp_pkt.src_port == port and (ip_pkt.src == config.VIP
                                           or ip_pkt.src in config.SERVERS):
            # Server -> client, either side of the source rewrite.
            forward, rewritten = False, ip_pkt.src == config.VIP
            conn = self.connections.get((ip_pkt.dst, tcp_pkt.dst_port))
            if conn is None or (not rewritten and conn["server"] != ip_pkt.src):
                return False
        else:
            return False

        conn["time"] = time.time()
        if not self._install_connection(conn):
            return True

        # Send this packet on its way from wherever it is now.
        target = self._locate(conn["server"] if forward else conn["client"])
        hops = self._hops(datapath.id, target[0])
        if hops is None:
            return True
        parser = datapath.ofproto_parser
        out = hops[0][1] if hops[0][1] is not None else target[1]
        actions = [] if rewritten else self._rewrite_actions(parser, conn, forward)
        self.packet_out(datapath, in_port, actions + [parser.OFPActionOutput(out)], data)
        return True

    def _new_connection(self, key):
        server, reason = self.choose_server()
        if server is None:
            self.logger.warning("DECISION %s:%d -> none (%s)", key[0], key[1], reason)
            return None
        conn = {"client": key[0], "port": key[1], "server": server, "time": time.time(),
                "cookie": COOKIE_CONN | SERVER_IPS.index(server)}
        self.connections[key] = conn
        self.assigned[server] += 1
        self.logger.info("DECISION %s:%d -> %s (%s)", key[0], key[1], server, reason)
        return conn

    def _rewrite_actions(self, parser, conn, forward):
        if forward:
            return [parser.OFPActionSetField(eth_dst=config.SERVERS[conn["server"]]),
                    parser.OFPActionSetField(ipv4_dst=conn["server"])]
        return [parser.OFPActionSetField(eth_src=config.VMAC),
                parser.OFPActionSetField(ipv4_src=config.VIP)]

    def _conn_match(self, parser, conn, forward, rewritten):
        if forward:
            dst = conn["server"] if rewritten else config.VIP
            return parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ip_proto=6,
                                   ipv4_src=conn["client"], ipv4_dst=dst,
                                   tcp_src=conn["port"], tcp_dst=config.CONTENT_PORT)
        src = config.VIP if rewritten else conn["server"]
        return parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ip_proto=6,
                               ipv4_src=src, ipv4_dst=conn["client"],
                               tcp_src=config.CONTENT_PORT, tcp_dst=conn["port"])

    def _install_connection(self, conn):
        """Install both directions of a connection. The first switch in each
        direction rewrites the addresses; the rest just forward."""
        client = self._locate(conn["client"])
        server = self._locate(conn["server"])
        if client is None or server is None:
            return False

        for forward, src, dst in ((True, client, server), (False, server, client)):
            hops = self._hops(src[0], dst[0])
            if hops is None:
                return False
            for i, (dpid, port) in enumerate(hops):
                hop_dp = self.datapaths[dpid]
                parser = hop_dp.ofproto_parser
                out = port if port is not None else dst[1]
                actions = self._rewrite_actions(parser, conn, forward) if i == 0 else []
                self.add_flow(hop_dp, 10, self._conn_match(parser, conn, forward, i > 0),
                              actions + [parser.OFPActionOutput(out)],
                              conn["cookie"], CONN_IDLE)
        return True

    # ---------------------------------------------------------------- server status

    def _handle_report(self, server, payload):
        if server not in self.server_status or not isinstance(payload, bytes):
            return
        try:
            active = int(json.loads(payload.decode("utf-8"))["active_clients"])
        except (ValueError, KeyError, TypeError):
            return

        status = self.server_status[server]
        if not status["alive"]:
            self.logger.info("server %s is UP", server)
        status["alive"] = True
        status["load"] = active / MAX_CLIENTS
        self.last_report[server] = time.time()
        self.assigned[server] = 0

    def _watch_servers(self):
        while True:
            hub.sleep(2)
            now = time.time()
            for server, status in self.server_status.items():
                if status["alive"] and now - self.last_report.get(server, 0) > REPORT_TIMEOUT:
                    status["alive"] = False
                    status["load"] = 0.0
                    self.logger.warning("server %s is DOWN (no load report for %ds)",
                                        server, REPORT_TIMEOUT)
                    if config.MODE == "smart":
                        self._drop_connections(server)

            self.connections = {key: conn for key, conn in self.connections.items()
                                if now - conn["time"] < CONN_FORGET}
            self.recent_arp = {key: t for key, t in self.recent_arp.items() if now - t < 5}

    def _drop_connections(self, server):
        """Forget a dead server's connections so client retries pick another one."""
        self.delete_flows(COOKIE_CONN | SERVER_IPS.index(server), 0xFFFF)
        self.connections = {key: conn for key, conn in self.connections.items()
                            if conn["server"] != server}
