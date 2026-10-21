from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (CONFIG_DISPATCHER, MAIN_DISPATCHER,
                                    DEAD_DISPATCHER, set_ev_cls)
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
import time

# Port map (set by link order in topology.py; verify with `net` in Mininet):
#   s1: 1=h1, 2=s2, 3=s3     s2: 1=s1, 2=s4
#   s3: 1=s1, 2=s4           s4: 1=s2, 2=s3, 3=h2
# Path A (primary, 5 Mbps):   h1-s1-s2-s4-h2
# Path B (alternate, 20 Mbps): h1-s1-s3-s4-h2

H1_IP = '10.0.0.1'
H2_IP = '10.0.0.2'
MONITOR_DPID = 1      # s1
MONITOR_PORT = 2      # s1 -> s2 (primary path)


class CongestionController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(CongestionController, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.active_path = 'A'
        self.previous_bytes = {}
        self.previous_time = {}
        self.congested_samples = 0
        self.threshold = 0.80                # 80% utilization
        self.primary_capacity = 5_000_000    # 5 Mbps (wrt topology.py)
        self.poll_interval = 2               # seconds
        self.reroute_time = None
        self.monitor_thread = hub.spawn(self.monitor)

    # switch connection

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        parser = datapath.ofproto_parser

        # Table-miss: drop unmatched packets (ARP is static, so nothing floods)
        datapath.send_msg(parser.OFPFlowMod(
            datapath=datapath, priority=0,
            match=parser.OFPMatch(), instructions=[]))

        self.datapaths[datapath.id] = datapath
        self.logger.info('Switch s%s connected', datapath.id)
        self.install_routes()

    @set_ev_cls(ofp_event.EventOFPStateChange, DEAD_DISPATCHER)
    def state_change_handler(self, ev):
        # Forget switches that disconnect so we stop sending to them
        datapath = ev.datapath
        if datapath.id in self.datapaths:
            del self.datapaths[datapath.id]
            self.logger.info('Switch s%s disconnected', datapath.id)

    # forwarding

    def add_flow(self, datapath, priority, match, out_port):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        actions = [parser.OFPActionOutput(out_port)]
        instructions = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS, actions)]
        # OFPFC_ADD with the same match+priority overwrites the old rule
        datapath.send_msg(parser.OFPFlowMod(
            datapath=datapath, priority=priority,
            match=match, instructions=instructions))

    def install_routes(self):
        for dpid, datapath in self.datapaths.items():
            parser = datapath.ofproto_parser

            if dpid == 1:
                forward_port = 2 if self.active_path == 'A' else 3
                reverse_port = 1
            elif dpid == 2:
                forward_port, reverse_port = 2, 1
            elif dpid == 3:
                forward_port, reverse_port = 2, 1
            elif dpid == 4:
                forward_port = 3
                reverse_port = 1 if self.active_path == 'A' else 2
            else:
                continue

            forward_match = parser.OFPMatch(
                eth_type=0x0800, ipv4_src=H1_IP, ipv4_dst=H2_IP)
            reverse_match = parser.OFPMatch(
                eth_type=0x0800, ipv4_src=H2_IP, ipv4_dst=H1_IP)

            self.add_flow(datapath, 100, forward_match, forward_port)
            self.add_flow(datapath, 100, reverse_match, reverse_port)

        self.logger.info('Forwarding rules installed for Path %s on %d switch(es)',
                         self.active_path, len(self.datapaths))

    #monitoring

    def monitor(self):
        while True:
            datapath = self.datapaths.get(MONITOR_DPID)
            if datapath is not None:
                parser = datapath.ofproto_parser
                datapath.send_msg(parser.OFPPortStatsRequest(
                    datapath, 0, datapath.ofproto.OFPP_ANY))
            hub.sleep(self.poll_interval)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        datapath = ev.msg.datapath
        if datapath.id != MONITOR_DPID:
            return
        now = time.time()

        for stat in ev.msg.body:
            if stat.port_no != MONITOR_PORT:
                continue

            key = (datapath.id, stat.port_no)
            current_bytes = stat.tx_bytes

            if key in self.previous_bytes:
                elapsed = now - self.previous_time[key]
                byte_delta = current_bytes - self.previous_bytes[key]

                if elapsed > 0 and byte_delta >= 0:
                    rate = byte_delta * 8 / elapsed
                    utilization = rate / self.primary_capacity
                    self.logger.info(
                        'Path %s | s1 port 2 rate: %.2f Mbps, utilization: %.1f%%',
                        self.active_path, rate / 1_000_000, utilization * 100)

                    if utilization >= self.threshold:
                        self.congested_samples += 1
                    else:
                        self.congested_samples = 0

                    if self.active_path == 'A' and self.congested_samples >= 2:
                        self.active_path = 'B'
                        self.congested_samples = 0
                        self.reroute_time = now
                        self.logger.warning(
                            'CONGESTION DETECTED at %s: switching to Path B',
                            time.strftime('%H:%M:%S', time.localtime(now)))
                        self.install_routes()

            self.previous_bytes[key] = current_bytes
            self.previous_time[key] = now
