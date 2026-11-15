import csv
import time
from pathlib import Path

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import DEAD_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib import hub
from ryu.ofproto import ofproto_v1_3

POLL_INTERVAL = 2
LINK_CAPACITY_BPS = 10 * 1000 * 1000

link_load = {}

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "results"
LOG_FILE = LOG_DIR / "link_utilization.csv"

LOG_DIR.mkdir(exist_ok=True)

if not LOG_FILE.exists():
    with open(LOG_FILE, "w", newline="") as f:
        csv.writer(f).writerow([
            "timestamp", "switch_id", "port_no", "utilization_percent"
        ])


class LinkMonitor(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(LinkMonitor, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.last_tx = {}
        hub.spawn(self._poll)

    @set_ev_cls(
        ofp_event.EventOFPStateChange,
        [MAIN_DISPATCHER, DEAD_DISPATCHER]
    )
    def _state_change(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(dp.id, None)

    def _poll(self):
        while True:
            for dp in list(self.datapaths.values()):
                parser = dp.ofproto_parser
                request = parser.OFPPortStatsRequest(
                    dp, 0, dp.ofproto.OFPP_ANY
                )
                dp.send_msg(request)
            hub.sleep(POLL_INTERVAL)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply(self, ev):
        dp = ev.msg.datapath
        now = time.time()

        for stat in ev.msg.body:
            if stat.port_no > ofproto_v1_3.OFPP_MAX:
                continue

            key = (dp.id, stat.port_no)
            previous = self.last_tx.get(key)
            self.last_tx[key] = (stat.tx_bytes, now)

            if previous is None:
                continue

            elapsed = max(now - previous[1], 0.000001)
            bits_per_second = (
                (stat.tx_bytes - previous[0]) * 8 / elapsed
            )
            utilization = max(
                0.0,
                min(bits_per_second / LINK_CAPACITY_BPS, 1.0)
            )

            link_load[key] = utilization

            with open(LOG_FILE, "a", newline="") as f:
                csv.writer(f).writerow([
                    time.strftime("%Y-%m-%d %H:%M:%S"),
                    dp.id,
                    stat.port_no,
                    round(utilization * 100, 2)
                ])

            self.logger.info(
                "Switch %s port %s utilization: %.2f%%",
                dp.id, stat.port_no, utilization * 100
            )
