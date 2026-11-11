import socket
import time
import subprocess
import csv
import os

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import (
    CONFIG_DISPATCHER,
    MAIN_DISPATCHER,
    DEAD_DISPATCHER,
    set_ev_cls,
)
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib import hub
from os_ken.lib.packet import packet, ethernet


class SmartCDNController(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    CHECK_INTERVAL = 2
    LINK_SPEED_BPS = 10_000_000  # Assumed 10 Mbps per port

    SERVERS = {
        "Server 1": ("10.0.0.1", 8080),
        "Server 2": ("10.0.0.2", 8080),
        "Server 3": ("10.0.0.3", 8080),
    }
        
    def select_best_server(self):
        """Choose the healthy server with the lowest measured latency."""
        healthy_servers = [
            (name, address, self.server_status[name]["latency_ms"])
            for name, address in self.SERVERS.items()
            if self.server_status[name]["alive"]
            and self.server_status[name]["latency_ms"] is not None
        ]

        if not healthy_servers:
            return None, None

        best_server = min(healthy_servers, key=lambda server: server[2])
        return best_server[0], best_server[1]

    def __init__(self, *args, **kwargs):
        super(SmartCDNController, self).__init__(*args, **kwargs)

        self.mac_to_port = {}
        self.datapaths = {}

        self.previous_port_stats = {}
        self.link_load = {}

        self.server_status = {
            name: {
                "alive": False,
                "latency_ms": None,
                "consecutive_failures": 0,
            }
            for name in self.SERVERS
        }

        self.monitor_thread = hub.spawn(self._monitor)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        self.datapaths[datapath.id] = datapath

        match = parser.OFPMatch()
        actions = [
            parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER,
                ofproto.OFPCML_NO_BUFFER,
            )
        ]
        instructions = [
            parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS, actions
            )
        ]

        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=0,
            match=match,
            instructions=instructions,
        )
        datapath.send_msg(mod)

        self.logger.info("Switch connected: %s", datapath.id)

    @set_ev_cls(
        ofp_event.EventOFPStateChange,
        [MAIN_DISPATCHER, DEAD_DISPATCHER],
    )
    def switch_state_handler(self, ev):
        datapath = ev.datapath

        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(datapath.id, None)

    def _monitor(self):
        while True:
            # Request port statistics from every connected switch.
            for datapath in list(self.datapaths.values()):
                parser = datapath.ofproto_parser
                ofproto = datapath.ofproto

                request = parser.OFPPortStatsRequest(
                    datapath, 0, ofproto.OFPP_ANY
                )
                datapath.send_msg(request)
                self.logger.info("Requested port stats from switch %s", datapath.id)

            # Check each server from its Mininet host namespace.
            for name, address in self.SERVERS.items():
                status = self.server_status[name]
                start = time.monotonic()
                host_map = {
                  "Server 1": "h1",
                  "Server 2": "h2",
                  "Server 3": "h3",
                }
                host = host_map[name]

                try:
                    pid_result = subprocess.run(
                        [
                            "pgrep", "-f",
                            rf"^bash --norc --noediting -is mininet:{host}$"
                        ],
                        capture_output=True,
                        text=True,
                        timeout=2,
                        check=True,
                    )
                    host_pid = pid_result.stdout.strip().splitlines()[0]

                    probe_code = (
                        f"import socket; "
                        f"s=socket.create_connection({address!r}, timeout=1); "
                        f"s.close()"
                    )

                    subprocess.run(
                        [
                            "mnexec", "-a", host_pid,
                            "python3", "-c", probe_code
                        ],
                        capture_output=True,
                        text=True,
                        timeout=3,
                        check=True,
                    )

                    latency = (time.monotonic() - start) * 1000
                    status["alive"] = True
                    status["latency_ms"] = round(latency, 2)
                    status["consecutive_failures"] = 0

                except (OSError, subprocess.SubprocessError, IndexError):
                    status["consecutive_failures"] += 1

                    if status["consecutive_failures"] >= 2:
                        status["alive"] = False
                        status["latency_ms"] = None

            self.logger.info("Server health: %s", self.server_status)
            self.logger.info("Link utilization: %s", self.link_load)

            hub.sleep(self.CHECK_INTERVAL)

    def log_link_utilization(self, switch_id, port_no, utilization):
        """Append a link-utilization measurement to CSV."""
        results_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "results")
        os.makedirs(results_dir, exist_ok=True)
        csv_path = os.path.join(results_dir, "link_utilization.csv")
        file_exists = os.path.exists(csv_path) and os.path.getsize(csv_path) > 0

        with open(csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["timestamp", "switch_id", "port_no", "utilization_percent"])
            writer.writerow([
                time.strftime("%Y-%m-%dT%H:%M:%S"),
                switch_id,
                port_no,
                round(utilization * 100, 2),
            ])

    
    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        self.logger.info("Received port stats reply from switch %s", ev.msg.datapath.id)
        datapath = ev.msg.datapath
        now = time.monotonic()

        for stat in ev.msg.body:
            port_no = stat.port_no

            if port_no >= datapath.ofproto.OFPP_MAX:
                continue

            key = (datapath.id, port_no)
            current_bytes = stat.rx_bytes + stat.tx_bytes

            if key in self.previous_port_stats:
                old_bytes, old_time = self.previous_port_stats[key]
                interval = now - old_time
                delta = max(0, current_bytes - old_bytes)

                if interval > 0:
                    bps = (delta * 8) / interval
                    utilization = bps / self.LINK_SPEED_BPS
                    self.link_load[key] = round(
                        min(max(utilization, 0.0), 1.0), 4
                    )
                    self.log_link_utilization(
                        datapath.id, port_no, self.link_load[key]
                    )

            self.previous_port_stats[key] = (current_bytes, now)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        in_port = msg.match["in_port"]
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth is None or eth.ethertype == 0x88CC:
            return

        dst = eth.dst
        src = eth.src
        dpid = datapath.id

        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        out_port = self.mac_to_port[dpid].get(
            dst, ofproto.OFPP_FLOOD
        )

        actions = [parser.OFPActionOutput(out_port)]

        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(
                in_port=in_port,
                eth_dst=dst,
            )
            instructions = [
                parser.OFPInstructionActions(
                    ofproto.OFPIT_APPLY_ACTIONS, actions
                )
            ]
            mod = parser.OFPFlowMod(
                datapath=datapath,
                priority=10,
                match=match,
                instructions=instructions,
            )
            datapath.send_msg(mod)

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data,
        )
        datapath.send_msg(out)
