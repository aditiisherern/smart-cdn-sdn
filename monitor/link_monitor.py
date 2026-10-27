"""Link monitor (Ryu app): every 2 s, asks each switch for port stats and
turns the byte counters into link_load[(dpid, port)] = 0..1.

Runs in the same ryu-manager as the controller:
    ryu-manager --observe-links controller/cdn_controller.py monitor/link_monitor.py
"""

import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import DEAD_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib import hub
from ryu.ofproto import ofproto_v1_3

POLL_INTERVAL = 2
# Switch-to-switch links are 10 Mbps (topology/cdn_topo.py). Host links are
# 100 Mbps, so their numbers read 10x too high; only switch links matter here.
LINK_CAPACITY_BPS = 10 * 1000 * 1000


class LinkMonitor(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(LinkMonitor, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.last_tx = {}     # (dpid, port) -> (tx_bytes, time)
        self.link_load = {}   # (dpid, port) -> fraction of capacity used, outgoing
        hub.spawn(self._poll)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(datapath.id, None)

    def _poll(self):
        while True:
            for datapath in list(self.datapaths.values()):
                parser = datapath.ofproto_parser
                datapath.send_msg(parser.OFPPortStatsRequest(datapath, 0,
                                                             datapath.ofproto.OFPP_ANY))
            hub.sleep(POLL_INTERVAL)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply(self, ev):
        dpid = ev.msg.datapath.id
        now = time.time()
        parts = []

        for stat in sorted(ev.msg.body, key=lambda s: s.port_no):
            if stat.port_no > ofproto_v1_3.OFPP_MAX:
                continue
            key = (dpid, stat.port_no)
            previous = self.last_tx.get(key)
            self.last_tx[key] = (stat.tx_bytes, now)
            if previous is None:
                continue
            bits_per_s = (stat.tx_bytes - previous[0]) * 8 / max(now - previous[1], 1e-6)
            self.link_load[key] = bits_per_s / LINK_CAPACITY_BPS
            parts.append(f"p{stat.port_no}={self.link_load[key]:.2f}")

        if parts:
            self.logger.info("s%d link load: %s", dpid, " ".join(parts))
