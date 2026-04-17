"""
arp_handler.py  —  POX SDN Controller for ARP Handling
========================================================
Topic  : ARP Request & Reply Handling in SDN Networks
Course : SDN Mininet Simulation Project (Orange Problem)

How to run
----------
  cd ~/pox
  python3 pox.py log.level --DEBUG arp_handler

What this controller does
--------------------------
1.  Installs a table-miss rule on every switch that connects, so ALL
    packets are forwarded to the controller via packet_in.

2.  On every packet_in it checks the EtherType:
      • ARP Request  → learn sender's IP→MAC→port, then:
            - If target IP already in ARP table → send Proxy ARP reply
              (controller crafts and sends the reply itself, no flood)
            - Else → flood the request out all ports
      • ARP Reply    → learn sender, forward reply to the correct port.
      • IPv4         → forward using the MAC table; install a flow rule
                       so future packets bypass the controller.
      • Other        → flood.

3.  Logs every event clearly so Wireshark / terminal output gives
    unambiguous proof of execution for the demo.

POX API notes (vs Ryu)
-----------------------
  • Component entry point : launch()  function at bottom of file
  • Event registration    : core.openflow.addListeners(self)
  • packet_in event       : ConnectionUp  /  PacketIn
  • Packet parsing        : pox.lib.packet  (ethernet, arp, ipv4 objects)
  • Flow install          : msg.ofp_flow_mod  via connection.send()
  • Packet out            : ofp_packet_out   via connection.send()
  • MAC/port table        : plain dict  { dpid -> { mac -> port } }
"""

import logging
from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.packet import ethernet, arp, ipv4
from pox.lib.packet.ethernet import ethernet as eth_type
from pox.lib.addresses import IPAddr, EthAddr
from pox.lib.util import dpid_to_str

log = core.getLogger()

# ─────────────────────────────────────────────────────────────────────────────
# Helper: pretty-print a datapath id
# ─────────────────────────────────────────────────────────────────────────────
def _sw(dpid):
    return dpid_to_str(dpid)


# ─────────────────────────────────────────────────────────────────────────────

class ARPHandler(object):
    """
    POX component that handles ARP in the SDN control plane.

    Data structures
    ───────────────
    arp_table   : { IPAddr  -> (EthAddr, dpid, port) }
    mac_to_port : { dpid    -> { EthAddr -> port    } }
    """

    def __init__(self):
        self.arp_table   = {}   # IP  -> (MAC, dpid, port)
        self.mac_to_port = {}   # dpid -> { mac -> port }

        # Register for OpenFlow events on all connected switches
        core.openflow.addListeners(self)

        log.info("=" * 60)
        log.info("  ARP Handling SDN Controller (POX)  —  started")
        log.info("=" * 60)

    # ── Switch connects ───────────────────────────────────────────────────────

    def _handle_ConnectionUp(self, event):
        """
        Called when a switch connects to the controller.
        Installs a table-miss flow: send ALL packets to controller.
        """
        dpid = event.dpid
        log.info("[SWITCH %s]  connected — installing table-miss rule", _sw(dpid))

        msg = of.ofp_flow_mod()
        msg.priority  = 0          # lowest priority = match everything
        msg.match     = of.ofp_match()   # empty match = wildcard all fields
        msg.actions.append(of.ofp_action_output(port=of.OFPP_CONTROLLER))
        event.connection.send(msg)

        self.mac_to_port.setdefault(dpid, {})

    # ── Packet-in handler ─────────────────────────────────────────────────────

    def _handle_PacketIn(self, event):
        """
        Main entry point.  Called for every packet that reaches the controller.
        """
        packet  = event.parsed          # parsed Ethernet frame
        if not packet.parsed:
            log.warning("Incomplete packet — ignoring")
            return

        dpid    = event.dpid
        in_port = event.port
        src_mac = packet.src            # EthAddr

        # ── Learn source MAC -> port ──────────────────────────────────────
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src_mac] = in_port

        # ── Dispatch by EtherType ─────────────────────────────────────────
        if packet.type == ethernet.ARP_TYPE:
            self._handle_arp(event, packet)

        elif packet.type == ethernet.IP_TYPE:
            self._handle_ipv4(event, packet)

        else:
            # Unknown EtherType — flood
            self._flood(event)

    # ── ARP handling ──────────────────────────────────────────────────────────

    def _handle_arp(self, event, eth_pkt):
        """
        Core ARP logic.

        ARP Request  → learn sender; proxy-reply if target known, else flood.
        ARP Reply    → learn sender; forward to the requesting host.
        """
        arp_pkt = eth_pkt.payload      # pox.lib.packet.arp object
        dpid    = event.dpid
        in_port = event.port

        src_ip  = arp_pkt.protosrc     # IPAddr
        src_mac = arp_pkt.hwsrc        # EthAddr
        dst_ip  = arp_pkt.protodst

        # ── Always learn the sender ───────────────────────────────────────
        if src_ip not in self.arp_table:
            self.arp_table[src_ip] = (src_mac, dpid, in_port)
            log.info("[ARP LEARN]  %-15s  ->  %s  (sw=%s port=%d)",
                     src_ip, src_mac, _sw(dpid), in_port)
        else:
            old_mac, old_dpid, old_port = self.arp_table[src_ip]
            if old_port != in_port or old_dpid != dpid:
                self.arp_table[src_ip] = (src_mac, dpid, in_port)
                log.info("[ARP UPDATE] %-15s  port %d -> %d", src_ip, old_port, in_port)

        # ── ARP Request ───────────────────────────────────────────────────
        if arp_pkt.opcode == arp.REQUEST:
            log.info("[ARP REQ]    Who has %-15s?  Tell %s (%s)  port=%d",
                     dst_ip, src_ip, src_mac, in_port)

            if dst_ip in self.arp_table:
                # Proxy ARP: controller answers directly
                target_mac, _, _ = self.arp_table[dst_ip]
                log.info("[PROXY ARP]  Answering: %-15s is at %s", dst_ip, target_mac)
                self._send_arp_reply(event, src_mac, src_ip, target_mac, dst_ip)
            else:
                log.info("[ARP FLOOD]  %-15s unknown — flooding request", dst_ip)
                self._flood(event)

        # ── ARP Reply ─────────────────────────────────────────────────────
        elif arp_pkt.opcode == arp.REPLY:
            log.info("[ARP REPLY]  %-15s is at %s  (port=%d)", src_ip, src_mac, in_port)

            if dst_ip in self.arp_table:
                _, _, out_port = self.arp_table[dst_ip]
                log.info("[ARP FWD]    Forwarding reply to %s via port %d", dst_ip, out_port)
                self._send_packet(event.connection, out_port, event.ofp.data)
            else:
                log.info("[ARP FWD]    dst %s unknown — flooding reply", dst_ip)
                self._flood(event)

    def _send_arp_reply(self, event, req_mac, req_ip, reply_mac, reply_ip):
        """
        Craft a synthetic ARP Reply and send it back out the in_port.

        Parameters
        ----------
        req_mac   : EthAddr  — requester's MAC  (packet goes to them)
        req_ip    : IPAddr   — requester's IP
        reply_mac : EthAddr  — MAC that owns reply_ip (from ARP table)
        reply_ip  : IPAddr   — the IP that was asked about
        """
        # Build ARP reply payload
        arp_reply             = arp()
        arp_reply.opcode      = arp.REPLY
        arp_reply.hwsrc       = reply_mac   # "I am reply_ip"
        arp_reply.protosrc    = reply_ip
        arp_reply.hwdst       = req_mac     # "to the requester"
        arp_reply.protodst    = req_ip

        # Wrap in Ethernet frame
        eth_reply             = ethernet()
        eth_reply.type        = ethernet.ARP_TYPE
        eth_reply.src         = reply_mac
        eth_reply.dst         = req_mac
        eth_reply.payload     = arp_reply

        # Send back out the port the request came in on
        msg                   = of.ofp_packet_out()
        msg.data              = eth_reply.pack()
        msg.actions.append(of.ofp_action_output(port=event.port))
        msg.in_port           = of.OFPP_NONE
        event.connection.send(msg)

    # ── IPv4 forwarding ───────────────────────────────────────────────────────

    def _handle_ipv4(self, event, eth_pkt):
        """
        Forward IPv4 using the MAC table.
        Install a flow rule so future same-dst packets skip the controller.
        """
        dpid    = event.dpid
        dst_mac = eth_pkt.dst

        if dst_mac in self.mac_to_port.get(dpid, {}):
            out_port = self.mac_to_port[dpid][dst_mac]

            # Install flow rule: match dst MAC → output to port
            msg             = of.ofp_flow_mod()
            msg.match       = of.ofp_match()
            msg.match.dl_dst = dst_mac
            msg.priority    = 1
            msg.idle_timeout = 30
            msg.hard_timeout = 120
            msg.actions.append(of.ofp_action_output(port=out_port))
            # Also send the current buffered packet
            msg.buffer_id   = event.ofp.buffer_id
            msg.in_port     = event.port
            event.connection.send(msg)

            ip_pkt = eth_pkt.payload
            log.info("[IP FWD]     %s -> %s  via port %d  (flow installed)",
                     ip_pkt.srcip if hasattr(ip_pkt, 'srcip') else '?',
                     ip_pkt.dstip if hasattr(ip_pkt, 'dstip') else '?',
                     out_port)
        else:
            log.info("[IP FLOOD]   dst MAC %s unknown — flooding", dst_mac)
            self._flood(event)

    # ── OpenFlow helpers ──────────────────────────────────────────────────────

    def _send_packet(self, connection, out_port, data):
        """Send raw packet data out a specific port."""
        msg         = of.ofp_packet_out()
        msg.data    = data
        msg.actions.append(of.ofp_action_output(port=out_port))
        msg.in_port = of.OFPP_NONE
        connection.send(msg)

    def _flood(self, event):
        """Flood a packet out all ports except the one it arrived on."""
        msg            = of.ofp_packet_out()
        msg.data       = event.ofp.data
        msg.in_port    = event.port
        msg.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
        event.connection.send(msg)

    # ── Status helpers ────────────────────────────────────────────────────────

    def get_arp_table(self):
        """Return a snapshot copy of the ARP table (used by tests)."""
        return dict(self.arp_table)

    def dump_arp_table(self):
        """Print the full ARP table to the log."""
        log.info("=" * 50)
        log.info("  Current ARP Table")
        log.info("  %-15s  %-20s  %s  %s", "IP", "MAC", "Switch", "Port")
        log.info("  " + "-" * 46)
        for ip, (mac, dpid, port) in sorted(self.arp_table.items(),
                                             key=lambda x: str(x[0])):
            log.info("  %-15s  %-20s  %s  %d", ip, mac, _sw(dpid), port)
        log.info("=" * 50)


# ─────────────────────────────────────────────────────────────────────────────
# POX entry point — called by:  python3 pox.py arp_handler
# ─────────────────────────────────────────────────────────────────────────────

def launch():
    """Register the ARPHandler component with the POX core."""
    core.registerNew(ARPHandler)
    log.info("ARPHandler component registered.")
