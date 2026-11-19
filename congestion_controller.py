"""Congestion detection and adaptive rerouting controller (Ryu, OpenFlow 1.3).

Path A (primary, 5 Mbps):   h1-s1-s2-s4-h2
Path B (alternate, 20 Mbps): h1-s1-s3-s4-h2

Every POLL_INTERVAL seconds the controller reads s1's port counters:
  - s1 port 2 tx  -> Path A load (used to detect congestion)
  - s1 port 3 tx  -> Path B load (logged; warns if Path B is also overloaded)
  - s1 port 1 rx  -> demand from h1 (used to decide when to switch back)

A -> B: Path A utilization >= CONGESTION_THRESHOLD for CONGESTION_SAMPLES readings.
B -> A: demand <= SWITCH_BACK_THRESHOLD of Path A for SWITCH_BACK_SAMPLES readings.
Both directions also wait MIN_HOLD_TIME on the current path (anti-flapping).

After each reroute the controller sends a barrier request to every switch.
When all switches confirm, the reroute is complete; the controller then reads
the flow tables of s1 and s4 back to verify the forward and reverse rules.
All events and every reading are written to CSV files in RESULTS_DIR.
"""

import csv
import os
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (CONFIG_DISPATCHER, MAIN_DISPATCHER,
                                    DEAD_DISPATCHER, set_ev_cls)
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub

import controller_config as cfg

# OpenFlow reports an unavailable counter as all ones (2**64 - 1)
COUNTER_UNAVAILABLE = 0xFFFFFFFFFFFFFFFF
# No port here can carry more than 10x the fastest configured link
MAX_VALID_RATE = 10 * max(cfg.PATH_A_CAPACITY, cfg.PATH_B_CAPACITY)


def clock(t):
    """Readable timestamp with milliseconds."""
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t)) + \
        '.%03d' % int((t % 1) * 1000)


class CongestionController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(CongestionController, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.start_time = time.time()

        # path state
        self.active_path = 'A'
        self.path_since = 0.0          # no hold time before the first switch
        self.congested_samples = 0
        self.calm_samples = 0
        self.onset_time = None         # start of the first congested interval
        self.switch_count = 0

        # reroute tracking
        self.pending_barriers = set()
        self.reroute = None            # details of the reroute in progress

        # counter history: (dpid, port, field) -> (bytes, time)
        self.previous = {}

        self.open_result_files()
        self.log_event('CONTROLLER_START', detail='Path A active')
        self.monitor_thread = hub.spawn(self.monitor)

    # ================= result files =================

    def open_result_files(self):
        os.makedirs(cfg.RESULTS_DIR, exist_ok=True)
        stamp = time.strftime('%Y%m%d_%H%M%S', time.localtime(self.start_time))
        events_path = os.path.join(cfg.RESULTS_DIR,
                                   'controller_events_%s.csv' % stamp)
        stats_path = os.path.join(cfg.RESULTS_DIR,
                                  'controller_stats_%s.csv' % stamp)
        self.events_file = open(events_path, 'w', newline='')
        self.event_writer = csv.writer(self.events_file)
        self.event_writer.writerow(['time', 'elapsed_s', 'event', 'active_path',
                              'detail'])
        self.stats_file = open(stats_path, 'w', newline='')
        self.stats_writer = csv.writer(self.stats_file)
        self.stats_writer.writerow(['time', 'elapsed_s', 'active_path',
                             'path_a_mbps', 'path_a_util_pct',
                             'path_b_mbps', 'path_b_util_pct', 'demand_mbps'])
        self.logger.info('Writing results to %s and %s',
                         events_path, stats_path)

    def log_event(self, event, detail='', when=None):
        when = time.time() if when is None else when
        self.event_writer.writerow([clock(when),
                              '%.3f' % (when - self.start_time),
                              event, self.active_path, detail])
        self.events_file.flush()

    # ================= switch connection =================

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
        self.log_event('SWITCH_CONNECTED', 's%s' % datapath.id)
        self.install_routes()

    @set_ev_cls(ofp_event.EventOFPStateChange, DEAD_DISPATCHER)
    def state_change_handler(self, ev):
        datapath = ev.datapath
        if datapath.id in self.datapaths:
            del self.datapaths[datapath.id]
            self.logger.info('Switch s%s disconnected', datapath.id)
            self.log_event('SWITCH_DISCONNECTED', 's%s' % datapath.id)
        # a switch that left can no longer confirm a reroute
        if datapath.id in self.pending_barriers:
            self.pending_barriers.discard(datapath.id)
            self.check_reroute_complete()

    # ================= forwarding =================

    def expected_ports(self, dpid, path):
        """(forward h1->h2 port, reverse h2->h1 port) for a switch on a path."""
        if dpid == cfg.S1:
            forward = cfg.S1_PATH_A_PORT if path == 'A' else cfg.S1_PATH_B_PORT
            return forward, cfg.S1_HOST_PORT
        if dpid in (cfg.S2, cfg.S3):
            return cfg.MID_TO_S4_PORT, cfg.MID_TO_S1_PORT
        if dpid == cfg.S4:
            reverse = cfg.S4_PATH_A_PORT if path == 'A' else cfg.S4_PATH_B_PORT
            return cfg.S4_HOST_PORT, reverse
        return None

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

    def install_routes(self, confirm=False):
        """Install both directions on every switch; optionally ask each
        switch to confirm (barrier) once the rules are applied."""
        for dpid, datapath in self.datapaths.items():
            ports = self.expected_ports(dpid, self.active_path)
            if ports is None:
                continue
            parser = datapath.ofproto_parser
            forward_match = parser.OFPMatch(
                eth_type=0x0800, ipv4_src=cfg.H1_IP, ipv4_dst=cfg.H2_IP)
            reverse_match = parser.OFPMatch(
                eth_type=0x0800, ipv4_src=cfg.H2_IP, ipv4_dst=cfg.H1_IP)
            self.add_flow(datapath, 100, forward_match, ports[0])
            self.add_flow(datapath, 100, reverse_match, ports[1])
            if confirm:
                self.pending_barriers.add(dpid)
                datapath.send_msg(parser.OFPBarrierRequest(datapath))

        self.logger.info('Forwarding rules installed for Path %s on %d switch(es)',
                         self.active_path, len(self.datapaths))

    # ================= rerouting =================

    def switch_path(self, new_path, event, detail, onset, now):
        old_path = self.active_path
        self.active_path = new_path
        self.path_since = now
        self.congested_samples = 0
        self.calm_samples = 0
        self.onset_time = None
        self.switch_count += 1
        self.reroute = {'from': old_path, 'to': new_path, 'event': event,
                        'onset': onset, 'detected': now}

        self.logger.warning('%s at %s: switching Path %s -> Path %s (%s)',
                            event, clock(now)[11:], old_path, new_path, detail)
        self.log_event(event, '%s; condition began %s' % (detail, clock(onset)),
                       when=now)
        self.install_routes(confirm=True)
        self.log_event('REROUTE_SENT', 'Path %s -> Path %s' % (old_path, new_path))
        if not self.pending_barriers:      # no switches to confirm
            self.check_reroute_complete()

    @set_ev_cls(ofp_event.EventOFPBarrierReply, MAIN_DISPATCHER)
    def barrier_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        if dpid in self.pending_barriers:
            self.pending_barriers.discard(dpid)
            self.check_reroute_complete()

    def check_reroute_complete(self):
        if self.pending_barriers or self.reroute is None:
            return
        done = time.time()
        r = self.reroute
        self.reroute = None
        detection_delay = r['detected'] - r['onset']
        install_time = done - r['detected']
        recovery_time = done - r['onset']
        self.logger.warning(
            'REROUTE COMPLETE at %s: Path %s -> Path %s | detection %.2f s, '
            'rule install %.3f s, total recovery %.2f s',
            clock(done)[11:], r['from'], r['to'],
            detection_delay, install_time, recovery_time)
        self.log_event('REROUTE_COMPLETE',
                       'Path %s -> Path %s; detection_delay_s=%.2f; '
                       'rule_install_s=%.3f; recovery_time_s=%.2f'
                       % (r['from'], r['to'], detection_delay,
                          install_time, recovery_time), when=done)
        self.request_flow_check()

    # ================= rule verification =================

    def request_flow_check(self):
        for dpid in (cfg.S1, cfg.S4):
            datapath = self.datapaths.get(dpid)
            if datapath is None:
                continue
            parser = datapath.ofproto_parser
            datapath.send_msg(parser.OFPFlowStatsRequest(
                datapath, match=parser.OFPMatch(eth_type=0x0800)))

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        expected = self.expected_ports(dpid, self.active_path)
        if expected is None:
            return
        for stat in ev.msg.body:
            if stat.priority != 100:
                continue
            src = stat.match.get('ipv4_src')
            dst = stat.match.get('ipv4_dst')
            out_ports = [a.port for inst in stat.instructions
                         for a in getattr(inst, 'actions', [])
                         if hasattr(a, 'port')]
            want = expected[0] if src == cfg.H1_IP else expected[1]
            ok = out_ports == [want]
            direction = 'forward' if src == cfg.H1_IP else 'reverse'
            self.logger.info('Verify s%s %s rule %s -> %s: output %s (expected %s) %s',
                             dpid, direction, src, dst, out_ports, want,
                             'OK' if ok else 'MISMATCH')
            self.log_event('RULE_VERIFIED' if ok else 'RULE_MISMATCH',
                           's%s %s %s->%s output=%s expected=%s'
                           % (dpid, direction, src, dst, out_ports, want))

    # ================= monitoring =================

    def monitor(self):
        while True:
            datapath = self.datapaths.get(cfg.S1)
            if datapath is not None:
                parser = datapath.ofproto_parser
                datapath.send_msg(parser.OFPPortStatsRequest(
                    datapath, 0, datapath.ofproto.OFPP_ANY))
            hub.sleep(cfg.POLL_INTERVAL)

    def rate(self, port_stats, port, field, now):
        """Bits per second on a port since the previous reading.
        Returns (rate, interval_start) or (None, None) when no valid rate
        exists: first reading, counter reset, or a bogus counter value."""
        key = (cfg.S1, port, field)
        stat = port_stats.get(port)
        if stat is None:
            return None, None
        value = stat.tx_bytes if field == 'tx' else stat.rx_bytes
        if value == COUNTER_UNAVAILABLE:   # OpenFlow "not available" marker
            self.previous.pop(key, None)
            return None, None
        previous = self.previous.get(key)
        self.previous[key] = (value, now)
        if previous is None:
            return None, None
        elapsed = now - previous[1]
        delta = value - previous[0]
        if elapsed <= 0 or delta < 0:      # counter reset after reconnect
            return None, None
        bps = delta * 8 / elapsed
        if bps > MAX_VALID_RATE:           # impossible jump: ignore, start over
            self.logger.info('Ignoring impossible reading on s1 port %s (%.0f Mbps)',
                             port, bps / 1e6)
            return None, None
        return bps, previous[1]

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        if ev.msg.datapath.id != cfg.S1:
            return
        now = time.time()
        port_stats = {stat.port_no: stat for stat in ev.msg.body}

        rate_a, interval_start = self.rate(port_stats, cfg.S1_PATH_A_PORT, 'tx', now)
        rate_b, _ = self.rate(port_stats, cfg.S1_PATH_B_PORT, 'tx', now)
        demand, _ = self.rate(port_stats, cfg.S1_HOST_PORT, 'rx', now)
        if rate_a is None or rate_b is None or demand is None:
            return

        util_a = rate_a / cfg.PATH_A_CAPACITY
        util_b = rate_b / cfg.PATH_B_CAPACITY
        demand_share = demand / cfg.PATH_A_CAPACITY

        self.logger.info(
            'Path %s | A: %.2f Mbps (%.1f%%) | B: %.2f Mbps (%.1f%%) | demand: %.2f Mbps',
            self.active_path, rate_a / 1e6, util_a * 100,
            rate_b / 1e6, util_b * 100, demand / 1e6)
        self.stats_writer.writerow([clock(now), '%.3f' % (now - self.start_time),
                             self.active_path,
                             '%.3f' % (rate_a / 1e6), '%.1f' % (util_a * 100),
                             '%.3f' % (rate_b / 1e6), '%.1f' % (util_b * 100),
                             '%.3f' % (demand / 1e6)])
        self.stats_file.flush()

        if self.reroute is not None:       # wait until the last reroute is confirmed
            return
        held = now - self.path_since

        if self.active_path == 'A':
            if util_a >= cfg.CONGESTION_THRESHOLD:
                if self.congested_samples == 0:
                    self.onset_time = interval_start
                self.congested_samples += 1
            else:
                self.congested_samples = 0
                self.onset_time = None

            if self.congested_samples >= cfg.CONGESTION_SAMPLES:
                if held >= cfg.MIN_HOLD_TIME:
                    self.switch_path(
                        'B', 'CONGESTION_DETECTED',
                        'Path A at %.1f%% for %d readings'
                        % (util_a * 100, self.congested_samples),
                        self.onset_time, now)
                else:
                    self.logger.info('Congestion confirmed; holding Path A for '
                                     '%.0f s more (anti-flapping)',
                                     cfg.MIN_HOLD_TIME - held)

        else:  # on Path B
            if util_b >= cfg.CONGESTION_THRESHOLD:
                self.logger.warning('Path B is also above %.0f%% (%.1f%%); '
                                    'no better path available',
                                    cfg.CONGESTION_THRESHOLD * 100, util_b * 100)

            if demand_share <= cfg.SWITCH_BACK_THRESHOLD:
                if self.calm_samples == 0:
                    self.onset_time = interval_start
                self.calm_samples += 1
            else:
                self.calm_samples = 0
                self.onset_time = None

            if self.calm_samples >= cfg.SWITCH_BACK_SAMPLES:
                if held >= cfg.MIN_HOLD_TIME:
                    self.switch_path(
                        'A', 'LOAD_DROPPED',
                        'demand %.2f Mbps (%.0f%% of Path A) for %d readings'
                        % (demand / 1e6, demand_share * 100, self.calm_samples),
                        self.onset_time, now)
                else:
                    self.logger.info('Load is low; holding Path B for %.0f s '
                                     'more (anti-flapping)',
                                     cfg.MIN_HOLD_TIME - held)
