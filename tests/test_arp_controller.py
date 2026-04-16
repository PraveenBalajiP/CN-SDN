"""
Unit / Regression Tests for ARP Controller
===========================================
Tests controller logic WITHOUT a live Mininet instance.
Uses lightweight mock objects that mimic the Ryu datapath API.

Run:
    python3 tests/test_arp_controller.py
"""

import sys
import os
import types
import unittest
from unittest.mock import MagicMock

# ── Ryu stubs ─────────────────────────────────────────────────────────────────
#
# Critical design rule:
#   'from ryu.lib.packet import arp' resolves 'arp' as an ATTRIBUTE of the
#   sys.modules['ryu.lib.packet'] object, NOT from sys.modules['ryu.lib.packet.arp'].
#   If ryu.lib.packet is a plain MagicMock, .arp returns a child Mock each
#   access — so arp.ARP_REQUEST is also a Mock and `opcode == arp.ARP_REQUEST`
#   always evaluates False.
#
#   Fix: create real types.ModuleType objects for every submodule that carries
#   integer constants, and wire them as attributes of their parent modules.

def _install_ryu_stubs():
    # 1. Start with plain MagicMocks for everything
    mock_names = [
        'ryu', 'ryu.base', 'ryu.base.app_manager',
        'ryu.controller', 'ryu.controller.ofp_event',
        'ryu.controller.handler',
        'ryu.ofproto', 'ryu.ofproto.ofproto_v1_3',
        'ryu.lib', 'ryu.lib.packet',
        'ryu.lib.packet.packet', 'ryu.lib.packet.ethernet',
        'ryu.lib.packet.arp', 'ryu.lib.packet.ipv4',
        'ryu.lib.packet.ether_types',
    ]
    for name in mock_names:
        sys.modules[name] = MagicMock()

    # 2. app_manager needs a real class so ARPController inherits properly
    class FakeRyuApp:
        def __init__(self, *a, **kw):
            pass

    real_app_mgr = types.ModuleType('ryu.base.app_manager')
    real_app_mgr.RyuApp = FakeRyuApp
    sys.modules['ryu.base.app_manager'] = real_app_mgr
    sys.modules['ryu.base'].app_manager = real_app_mgr

    # 3. arp module — must be a real ModuleType with integer constants
    #    AND wired as an attribute of ryu.lib.packet so
    #    'from ryu.lib.packet import arp' gives this exact object.
    arp_mod = types.ModuleType('ryu.lib.packet.arp')
    arp_mod.ARP_REQUEST = 1
    arp_mod.ARP_REPLY   = 2
    sys.modules['ryu.lib.packet.arp'] = arp_mod
    sys.modules['ryu.lib.packet'].arp = arp_mod        # <── critical

    # 4. ether_types — same treatment
    et_mod = types.ModuleType('ryu.lib.packet.ether_types')
    et_mod.ETH_TYPE_ARP  = 0x0806
    et_mod.ETH_TYPE_LLDP = 0x88cc
    sys.modules['ryu.lib.packet.ether_types'] = et_mod
    sys.modules['ryu.lib.packet'].ether_types = et_mod  # <── critical

    # 5. ethernet / ipv4 / packet stay as MagicMocks (we only need type identity)
    eth_mod = MagicMock()
    sys.modules['ryu.lib.packet.ethernet'] = eth_mod
    sys.modules['ryu.lib.packet'].ethernet = eth_mod

    ip_mod = MagicMock()
    sys.modules['ryu.lib.packet.ipv4'] = ip_mod
    sys.modules['ryu.lib.packet'].ipv4 = ip_mod

    pkt_mod = MagicMock()
    sys.modules['ryu.lib.packet.packet'] = pkt_mod
    sys.modules['ryu.lib.packet'].packet = pkt_mod

    # 6. OFProto constants
    ofp = sys.modules['ryu.ofproto.ofproto_v1_3']
    ofp.OFP_VERSION         = 4
    ofp.OFP_NO_BUFFER       = 0xffffffff
    ofp.OFPP_CONTROLLER     = 0xfffffffd
    ofp.OFPP_FLOOD          = 0xfffffffb
    ofp.OFPCML_NO_BUFFER    = 0xffff
    ofp.OFPIT_APPLY_ACTIONS = 4

    # 7. set_ev_cls: identity decorator
    sys.modules['ryu.controller.handler'].set_ev_cls = (
        lambda *a, **kw: (lambda f: f)
    )
    sys.modules['ryu.controller.handler'].CONFIG_DISPATCHER = 'config'
    sys.modules['ryu.controller.handler'].MAIN_DISPATCHER   = 'main'


_install_ryu_stubs()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'controller'))
from arp_controller import ARPController   # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_datapath(dpid=1):
    dp = MagicMock()
    dp.id = dpid
    dp.ofproto = sys.modules['ryu.ofproto.ofproto_v1_3']
    dp.ofproto_parser = MagicMock()
    return dp


def make_arp_event(dp, in_port, opcode,
                   src_mac, src_ip, dst_mac, dst_ip):
    """
    Build a fake packet_in event carrying an ARP packet.
    Returns (msg, eth_mock, arp_mock).
    """
    import ryu.lib.packet.ethernet as eth_mod
    import ryu.lib.packet.arp      as arp_mod
    import ryu.lib.packet.ipv4     as ip_mod
    import ryu.lib.packet.ether_types as et_mod

    arp_pkt         = MagicMock()
    arp_pkt.opcode  = opcode
    arp_pkt.src_mac = src_mac
    arp_pkt.src_ip  = src_ip
    arp_pkt.dst_mac = dst_mac
    arp_pkt.dst_ip  = dst_ip

    eth             = MagicMock()
    eth.src         = src_mac
    eth.dst         = dst_mac
    eth.ethertype   = et_mod.ETH_TYPE_ARP

    pkt = MagicMock()
    def get_protocol(cls):
        if cls is eth_mod.ethernet: return eth
        if cls is arp_mod:          return arp_pkt   # arp is the module itself
        if cls is ip_mod.ipv4:      return None
        return None
    pkt.get_protocol.side_effect = get_protocol

    sys.modules['ryu.lib.packet.packet'].Packet.return_value = pkt

    msg           = MagicMock()
    msg.datapath  = dp
    msg.match     = {'in_port': in_port}
    msg.data      = b'\x00' * 64
    msg.buffer_id = dp.ofproto.OFP_NO_BUFFER

    return msg, eth, arp_pkt


# ── Test cases ────────────────────────────────────────────────────────────────

class TestARPLearning(unittest.TestCase):
    """ARP table is populated correctly from ARP Requests and Replies."""

    def setUp(self):
        self.ctrl = ARPController()
        self.dp   = make_datapath(dpid=1)

    def test_arp_request_populates_table(self):
        msg, eth, arp_pkt = make_arp_event(
            self.dp, in_port=1, opcode=1,
            src_mac='00:00:00:00:00:01', src_ip='10.0.0.1',
            dst_mac='ff:ff:ff:ff:ff:ff', dst_ip='10.0.0.2'
        )
        self.ctrl._handle_arp(self.dp, 1, eth, arp_pkt, msg)

        self.assertIn('10.0.0.1', self.ctrl.arp_table)
        mac, dpid, port = self.ctrl.arp_table['10.0.0.1']
        self.assertEqual(mac,  '00:00:00:00:00:01')
        self.assertEqual(dpid, 1)
        self.assertEqual(port, 1)

    def test_arp_reply_populates_table(self):
        self.ctrl.arp_table['10.0.0.1'] = ('00:00:00:00:00:01', 1, 1)

        msg, eth, arp_pkt = make_arp_event(
            self.dp, in_port=2, opcode=2,
            src_mac='00:00:00:00:00:02', src_ip='10.0.0.2',
            dst_mac='00:00:00:00:00:01', dst_ip='10.0.0.1'
        )
        self.ctrl._handle_arp(self.dp, 2, eth, arp_pkt, msg)

        self.assertIn('10.0.0.2', self.ctrl.arp_table)
        mac, _, _ = self.ctrl.arp_table['10.0.0.2']
        self.assertEqual(mac, '00:00:00:00:00:02')

    def test_table_updates_on_port_change(self):
        """If a host is seen on a new port, the table must update."""
        self.ctrl.arp_table['10.0.0.1'] = ('00:00:00:00:00:01', 1, 1)

        msg, eth, arp_pkt = make_arp_event(
            self.dp, in_port=3, opcode=1,
            src_mac='00:00:00:00:00:01', src_ip='10.0.0.1',
            dst_mac='ff:ff:ff:ff:ff:ff', dst_ip='10.0.0.2'
        )
        self.ctrl._handle_arp(self.dp, 3, eth, arp_pkt, msg)

        _, _, port = self.ctrl.arp_table['10.0.0.1']
        self.assertEqual(port, 3)


class TestProxyARP(unittest.TestCase):
    """Controller sends synthetic ARP replies when target IP is already known."""

    def setUp(self):
        self.ctrl = ARPController()
        self.dp   = make_datapath(dpid=1)
        self.ctrl.arp_table['10.0.0.2'] = ('00:00:00:00:00:02', 1, 2)

    def test_proxy_arp_reply_sent(self):
        self.ctrl._send_arp_reply = MagicMock()

        msg, eth, arp_pkt = make_arp_event(
            self.dp, in_port=1, opcode=1,
            src_mac='00:00:00:00:00:01', src_ip='10.0.0.1',
            dst_mac='ff:ff:ff:ff:ff:ff', dst_ip='10.0.0.2'
        )
        self.ctrl._handle_arp(self.dp, 1, eth, arp_pkt, msg)

        self.ctrl._send_arp_reply.assert_called_once_with(
            self.dp, 1,
            '00:00:00:00:00:02', '10.0.0.2',
            '00:00:00:00:00:01', '10.0.0.1'
        )

    def test_no_flood_when_target_known(self):
        """Proxy ARP must suppress the flood."""
        self.ctrl._send_arp_reply = MagicMock()
        self.ctrl._flood          = MagicMock()

        msg, eth, arp_pkt = make_arp_event(
            self.dp, in_port=1, opcode=1,
            src_mac='00:00:00:00:00:01', src_ip='10.0.0.1',
            dst_mac='ff:ff:ff:ff:ff:ff', dst_ip='10.0.0.2'
        )
        self.ctrl._handle_arp(self.dp, 1, eth, arp_pkt, msg)

        self.ctrl._flood.assert_not_called()


class TestARPFlood(unittest.TestCase):
    """Unknown target IP → request must be flooded."""

    def setUp(self):
        self.ctrl = ARPController()
        self.dp   = make_datapath(dpid=1)

    def test_flood_when_target_unknown(self):
        self.ctrl._flood = MagicMock()

        msg, eth, arp_pkt = make_arp_event(
            self.dp, in_port=1, opcode=1,
            src_mac='00:00:00:00:00:01', src_ip='10.0.0.1',
            dst_mac='ff:ff:ff:ff:ff:ff', dst_ip='10.0.0.99'
        )
        self.ctrl._handle_arp(self.dp, 1, eth, arp_pkt, msg)

        self.ctrl._flood.assert_called_once()


class TestARPTable(unittest.TestCase):
    """get_arp_table() returns an independent snapshot."""

    def setUp(self):
        self.ctrl = ARPController()

    def test_empty_on_init(self):
        self.assertEqual(self.ctrl.get_arp_table(), {})

    def test_snapshot_is_independent(self):
        self.ctrl.arp_table['10.0.0.1'] = ('aa:bb:cc:dd:ee:ff', 1, 1)
        snapshot = self.ctrl.get_arp_table()
        self.ctrl.arp_table['10.0.0.1'] = ('00:00:00:00:00:00', 1, 9)
        self.assertEqual(snapshot['10.0.0.1'][0], 'aa:bb:cc:dd:ee:ff')


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 60)
    print("  ARP Controller — Regression Test Suite")
    print("=" * 60)
    unittest.main(verbosity=2)
