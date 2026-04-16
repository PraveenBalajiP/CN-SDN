"""
ARP Handling SDN Controller (Ryu)
==================================
Topic  : ARP Request & Reply Handling in SDN Networks
Course : SDN Mininet Simulation Project

What this controller does
--------------------------
1. Intercepts every ARP packet via packet_in events.
2. Builds an ARP table  {IP -> (MAC, datapath_id, port)}  from ARP Requests.
3. If the target IP is already known  -> generates a synthetic ARP Reply
   directly from the controller (proxy ARP) — the request never floods.
4. If the target IP is unknown        -> floods the request out all ports
   so the real host can answer, then learns from the reply.
5. Installs proactive OpenFlow rules for unicast IP traffic once both
   endpoints are known, pushing forwarding decisions into the data plane.
6. Logs every ARP event so Wireshark / terminal output gives clear proof
   of execution.

OpenFlow version: 1.3
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, arp, ipv4
from ryu.lib.packet import ether_types
import logging

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  [%(levelname)s]  %(message)s',
    datefmt='%H:%M:%S'
)
LOG = logging.getLogger('arp_controller')


class ARPController(app_manager.RyuApp):
    """
    Proxy-ARP SDN controller.

    Data structures
    ---------------
    arp_table  : { ip_str -> (mac_str, dpid, port_no) }
    mac_to_port: { dpid   -> { mac_str -> port_no   } }
    """

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ARPController, self).__init__(*args, **kwargs)

        # ARP cache: IP -> (MAC, datapath_id, in_port)
        self.arp_table   = {}

        # MAC learning table: dpid -> { mac -> port }
        self.mac_to_port = {}

        LOG.info("=" * 60)
        LOG.info("  ARP Handling SDN Controller  –  started")
        LOG.info("=" * 60)

    # ── Switch handshake ─────────────────────────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """
        Install a table-miss flow entry so all unmatched packets
        are sent to the controller (packet_in).
        """
        dp      = ev.msg.datapath
        ofproto = dp.ofproto
        parser  = dp.ofproto_parser

        # Match: everything (empty match)
        match  = parser.OFPMatch()
        # Action: send to controller, max 65535 bytes
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self._add_flow(dp, priority=0, match=match, actions=actions)

        LOG.info("[SWITCH %016x]  connected — table-miss rule installed", dp.id)

    # ── Packet-in handler ────────────────────────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg     = ev.msg
        dp      = msg.datapath
        ofproto = dp.ofproto
        parser  = dp.ofproto_parser
        in_port = msg.match['in_port']

        pkt  = packet.Packet(msg.data)
        eth  = pkt.get_protocol(ethernet.ethernet)

        if eth is None:
            return

        # ── Ignore LLDP / spanning-tree frames ───────────────────────────
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst_mac = eth.dst
        src_mac = eth.src
        dpid    = dp.id

        # Initialise MAC table for this switch
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src_mac] = in_port

        # ── ARP handling ──────────────────────────────────────────────────
        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt:
            self._handle_arp(dp, in_port, eth, arp_pkt, msg)
            return

        # ── IPv4 forwarding (after ARP has populated the table) ───────────
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if ip_pkt:
            self._handle_ipv4(dp, in_port, eth, ip_pkt, msg)
            return

        # ── Unknown ethertype: flood ──────────────────────────────────────
        self._flood(dp, msg, in_port)

    # ── ARP logic ────────────────────────────────────────────────────────────

    def _handle_arp(self, dp, in_port, eth_pkt, arp_pkt, msg):
        """
        Core ARP handler.

        ARP Request  →  learn sender, reply from cache or flood.
        ARP Reply    →  learn sender, notify waiting request if any.
        """
        opcode     = arp_pkt.opcode
        src_ip     = arp_pkt.src_ip
        src_mac    = arp_pkt.src_mac
        dst_ip     = arp_pkt.dst_ip
        dpid       = dp.id

        # ── Always learn the sender ───────────────────────────────────────
        if src_ip not in self.arp_table:
            self.arp_table[src_ip] = (src_mac, dpid, in_port)
            LOG.info("[ARP LEARN]  %-15s  ->  %s  (dpid=%016x port=%d)",
                     src_ip, src_mac, dpid, in_port)
        else:
            # Update port in case host moved
            old_mac, old_dpid, old_port = self.arp_table[src_ip]
            if old_port != in_port or old_dpid != dpid:
                self.arp_table[src_ip] = (src_mac, dpid, in_port)
                LOG.info("[ARP UPDATE] %-15s  port %d -> %d", src_ip, old_port, in_port)

        if opcode == arp.ARP_REQUEST:
            LOG.info("[ARP REQ]    Who has %-15s?  Tell %s (%s)  port=%d",
                     dst_ip, src_ip, src_mac, in_port)

            if dst_ip in self.arp_table:
                # ── Proxy ARP: answer from the controller ─────────────────
                target_mac, _, _ = self.arp_table[dst_ip]
                LOG.info("[PROXY ARP]  Answering: %-15s is at %s", dst_ip, target_mac)
                self._send_arp_reply(dp, in_port,
                                     target_mac, dst_ip,
                                     src_mac,    src_ip)
            else:
                # ── Unknown target: flood the request ─────────────────────
                LOG.info("[ARP FLOOD]  %-15s unknown — flooding request", dst_ip)
                self._flood(dp, msg, in_port)

        elif opcode == arp.ARP_REPLY:
            LOG.info("[ARP REPLY]  %-15s is at %s  (port=%d)", src_ip, src_mac, in_port)
            # Forward reply to the host that originally asked
            if dst_ip in self.arp_table:
                _, _, out_port = self.arp_table[dst_ip]
                LOG.info("[ARP FWD]    Forwarding reply to %s via port %d", dst_ip, out_port)
                self._send_packet(dp, out_port, msg.data)
            else:
                self._flood(dp, msg, in_port)

    def _send_arp_reply(self, dp, out_port,
                        src_mac, src_ip,
                        dst_mac, dst_ip):
        """
        Craft and send a synthetic ARP Reply from the controller.
        """
        ofproto = dp.ofproto
        parser  = dp.ofproto_parser

        # Build the reply packet
        pkt = packet.Packet()
        pkt.add_protocol(ethernet.ethernet(
            ethertype=ether_types.ETH_TYPE_ARP,
            dst=dst_mac,
            src=src_mac))
        pkt.add_protocol(arp.arp(
            opcode=arp.ARP_REPLY,
            src_mac=src_mac,
            src_ip=src_ip,
            dst_mac=dst_mac,
            dst_ip=dst_ip))
        pkt.serialize()

        actions = [parser.OFPActionOutput(out_port)]
        out = parser.OFPPacketOut(
            datapath=dp,
            buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=ofproto.OFPP_CONTROLLER,
            actions=actions,
            data=pkt.data)
        dp.send_msg(out)

    # ── IPv4 forwarding ──────────────────────────────────────────────────────

    def _handle_ipv4(self, dp, in_port, eth_pkt, ip_pkt, msg):
        """
        Forward IPv4 packets using the MAC table.
        Install a flow rule once we know the egress port so future
        packets bypass the controller.
        """
        ofproto = dp.ofproto
        parser  = dp.ofproto_parser
        dpid    = dp.id
        dst_mac = eth_pkt.dst

        if dst_mac in self.mac_to_port.get(dpid, {}):
            out_port = self.mac_to_port[dpid][dst_mac]

            # Install flow rule: match dst_mac → output out_port
            match = parser.OFPMatch(eth_dst=dst_mac)
            actions = [parser.OFPActionOutput(out_port)]
            self._add_flow(dp, priority=1, match=match, actions=actions,
                           idle_timeout=30, hard_timeout=120)

            LOG.info("[IP FWD]     %s -> %s  via port %d  (flow installed)",
                     ip_pkt.src, ip_pkt.dst, out_port)
            self._send_packet(dp, out_port, msg.data, msg.buffer_id)
        else:
            LOG.info("[IP FLOOD]   %s -> %s  dst MAC unknown, flooding",
                     ip_pkt.src, ip_pkt.dst)
            self._flood(dp, msg, in_port)

    # ── OpenFlow helpers ─────────────────────────────────────────────────────

    def _add_flow(self, dp, priority, match, actions,
                  idle_timeout=0, hard_timeout=0):
        """Install an OpenFlow flow rule."""
        ofproto = dp.ofproto
        parser  = dp.ofproto_parser

        inst = [parser.OFPInstructionActions(
                    ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=dp,
            priority=priority,
            match=match,
            instructions=inst,
            idle_timeout=idle_timeout,
            hard_timeout=hard_timeout)
        dp.send_msg(mod)

    def _send_packet(self, dp, out_port, data, buffer_id=None):
        """Send a raw packet out a specific port."""
        ofproto = dp.ofproto
        parser  = dp.ofproto_parser

        if buffer_id is None:
            buffer_id = ofproto.OFP_NO_BUFFER

        actions = [parser.OFPActionOutput(out_port)]
        out = parser.OFPPacketOut(
            datapath=dp,
            buffer_id=buffer_id,
            in_port=ofproto.OFPP_CONTROLLER,
            actions=actions,
            data=data)
        dp.send_msg(out)

    def _flood(self, dp, msg, in_port):
        """Flood a packet out all ports except the one it arrived on."""
        ofproto = dp.ofproto
        parser  = dp.ofproto_parser

        actions = [parser.OFPActionOutput(ofproto.OFPP_FLOOD)]
        out = parser.OFPPacketOut(
            datapath=dp,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None)
        dp.send_msg(out)

    # ── REST-like status dump (called via ryu-manager --observe-links) ───────

    def get_arp_table(self):
        """Return current ARP table snapshot (useful for testing)."""
        return dict(self.arp_table)
