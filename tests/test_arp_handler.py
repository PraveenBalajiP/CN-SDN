"""
Tests ALL controller logic without needing a live Mininet or POX instance.
Uses lightweight stubs that replicate just enough of the POX API surface.
"""
import sys
import os
import types
import unittest
from unittest.mock import MagicMock, patch, call

def _install_pox_stubs():
    # IPAddr / EthAddr
    # Use real wrapper classes so equality comparisons work correctly.
    class IPAddr(str):
        """Minimal IPAddr — just a string subclass for our tests."""
        pass

    class EthAddr(str):
        """Minimal EthAddr — just a string subclass for our tests."""
        def __new__(cls, v):
            return str.__new__(cls, str(v).lower())
    # pox.lib.addresses
    addresses_mod = types.ModuleType('pox.lib.addresses')
    addresses_mod.IPAddr  = IPAddr
    addresses_mod.EthAddr = EthAddr
    # pox.lib.util
    util_mod = types.ModuleType('pox.lib.util')
    util_mod.dpid_to_str = lambda dpid: '%016x' % (dpid or 0)
    # pox.lib.packet.arp
    arp_mod           = types.ModuleType('pox.lib.packet.arp')
    arp_mod.REQUEST   = 1
    arp_mod.REPLY     = 2

    class FakeArp:
        REQUEST = 1
        REPLY   = 2
        def __init__(self):
            self.opcode   = None
            self.hwsrc    = None
            self.hwdst    = None
            self.protosrc = None
            self.protodst = None
        def pack(self):
            return b'\x00' * 28

    arp_mod.arp     = FakeArp
    # The controller does  'from pox.lib.packet import arp'
    # which brings the MODULE arp_mod into scope as the name 'arp',
    # then uses  arp.REQUEST  and  arp.REPLY  as opcode constants.
    # So the module itself must carry REQUEST/REPLY — it does above.

    # ── pox.lib.packet.ethernet ──────────────────────────────────────────
    eth_mod             = types.ModuleType('pox.lib.packet.ethernet')
    eth_mod.ARP_TYPE    = 0x0806
    eth_mod.IP_TYPE     = 0x0800
    eth_mod.LLDP_TYPE   = 0x88cc

    class FakeEthernet:
        ARP_TYPE  = 0x0806
        IP_TYPE   = 0x0800
        LLDP_TYPE = 0x88cc
        def __init__(self):
            self.src     = None
            self.dst     = None
            self.type    = None
            self.payload = None
            self.parsed  = True   # POX ethernet objects expose this bool
        def pack(self):
            return b'\x00' * 14

    eth_mod.ethernet = FakeEthernet

    # ── pox.lib.packet.ipv4 ──────────────────────────────────────────────
    ip_mod       = types.ModuleType('pox.lib.packet.ipv4')
    class FakeIPv4:
        def __init__(self):
            self.srcip = None
            self.dstip = None
    ip_mod.ipv4 = FakeIPv4

    # ── pox.lib.packet  (parent, must expose submodules as attributes) ───
    packet_mod           = types.ModuleType('pox.lib.packet')
    packet_mod.ethernet  = FakeEthernet   # 'from pox.lib.packet import ethernet'
    packet_mod.arp       = arp_mod        # 'from pox.lib.packet import arp'
    packet_mod.ipv4      = ip_mod         # 'from pox.lib.packet import ipv4'

    # ── pox.openflow.libopenflow_01  (of) ────────────────────────────────
    of_mod = types.ModuleType('pox.openflow.libopenflow_01')
    of_mod.OFPP_CONTROLLER = 0xfffd
    of_mod.OFPP_FLOOD      = 0xfffb
    of_mod.OFPP_NONE       = 0xffff

    class FakeMatch:
        def __init__(self):
            self.dl_dst = None
    class FakeFlowMod:
        def __init__(self):
            self.priority     = 0
            self.match        = FakeMatch()
            self.actions      = []
            self.idle_timeout = 0
            self.hard_timeout = 0
            self.buffer_id    = None
            self.in_port      = None
    class FakePacketOut:
        def __init__(self):
            self.data    = None
            self.actions = []
            self.in_port = None
    class FakeActionOutput:
        def __init__(self, port=0):
            self.port = port

    of_mod.ofp_flow_mod      = FakeFlowMod
    of_mod.ofp_packet_out    = FakePacketOut
    of_mod.ofp_action_output = FakeActionOutput
    of_mod.ofp_match         = FakeMatch

    # ── pox.openflow ──────────────────────────────────────────────────────
    openflow_mod                   = types.ModuleType('pox.openflow')
    openflow_mod.libopenflow_01    = of_mod

    # ── pox.core ──────────────────────────────────────────────────────────
    # core.openflow.addListeners(self)  must not crash.
    # core.registerNew(cls)            must call cls() and return the instance.
    fake_openflow          = MagicMock()
    fake_openflow.addListeners = MagicMock()

    _registered = {}
    def fake_registerNew(cls, *a, **kw):
        inst = cls(*a, **kw)
        _registered[cls.__name__] = inst
        return inst

    fake_core           = MagicMock()
    fake_core.openflow  = fake_openflow
    fake_core.registerNew = fake_registerNew
    fake_core.getLogger = lambda: logging.getLogger('test')

    core_mod      = types.ModuleType('pox.core')
    core_mod.core = fake_core

    # ── pox  (top-level) ──────────────────────────────────────────────────
    pox_mod = types.ModuleType('pox')

    # ── Wire everything into sys.modules ─────────────────────────────────
    sys.modules['pox']                          = pox_mod
    sys.modules['pox.core']                     = core_mod
    sys.modules['pox.openflow']                 = openflow_mod
    sys.modules['pox.openflow.libopenflow_01']  = of_mod
    sys.modules['pox.lib']                      = types.ModuleType('pox.lib')
    sys.modules['pox.lib.packet']               = packet_mod
    sys.modules['pox.lib.packet.ethernet']      = eth_mod
    sys.modules['pox.lib.packet.arp']           = arp_mod
    sys.modules['pox.lib.packet.ipv4']          = ip_mod
    sys.modules['pox.lib.addresses']            = addresses_mod
    sys.modules['pox.lib.util']                 = util_mod

    # Wire parent attributes so 'import pox.openflow.libopenflow_01 as of' works
    pox_mod.openflow                 = openflow_mod
    pox_mod.core                     = core_mod
    openflow_mod.libopenflow_01      = of_mod
    sys.modules['pox.lib'].packet    = packet_mod
    sys.modules['pox.lib'].addresses = addresses_mod
    sys.modules['pox.lib'].util      = util_mod
    packet_mod.ethernet              = FakeEthernet
    packet_mod.arp                   = arp_mod
    packet_mod.ipv4                  = ip_mod

    return IPAddr, EthAddr, FakeArp, FakeEthernet, FakeIPv4, of_mod, fake_core


import logging
logging.basicConfig(level=logging.CRITICAL)   # silence controller logs during tests

IPAddr, EthAddr, FakeArp, FakeEthernet, FakeIPv4, of_mod, fake_core = _install_pox_stubs()

# Now safe to import the controller
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'controller'))
from arp_handler import ARPHandler   # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
#  Helper factories
# ─────────────────────────────────────────────────────────────────────────────

def make_controller():
    """Return a fresh ARPHandler instance."""
    return ARPHandler()


def make_connection(dpid=1):
    conn        = MagicMock()
    conn.dpid   = dpid
    conn.send   = MagicMock()
    return conn


def make_connection_up(dpid=1):
    """Fake ConnectionUp event."""
    conn  = make_connection(dpid)
    event = MagicMock()
    event.dpid       = dpid
    event.connection = conn
    return event


def make_packet_in(dpid=1, in_port=1,
                   eth_type_val=0x0806,
                   src_mac='00:00:00:00:00:01',
                   dst_mac='ff:ff:ff:ff:ff:ff',
                   arp_opcode=1,
                   src_ip='10.0.0.1',
                   dst_ip='10.0.0.2'):
    """
    Build a fake PacketIn event carrying an ARP packet.
    Returns (event, connection).
    """
    conn = make_connection(dpid)

    # ARP payload
    arp_pkt          = FakeArp()
    arp_pkt.opcode   = arp_opcode
    arp_pkt.hwsrc    = EthAddr(src_mac)
    arp_pkt.hwdst    = EthAddr(dst_mac)
    arp_pkt.protosrc = IPAddr(src_ip)
    arp_pkt.protodst = IPAddr(dst_ip)

    # Ethernet frame
    eth         = FakeEthernet()
    eth.src     = EthAddr(src_mac)
    eth.dst     = EthAddr(dst_mac)
    eth.type    = eth_type_val
    eth.payload = arp_pkt

    # ofp raw message (for buffer_id / data)
    ofp_msg           = MagicMock()
    ofp_msg.buffer_id = 0xffffffff
    ofp_msg.data      = b'\x00' * 64

    event             = MagicMock()
    event.dpid        = dpid
    event.port        = in_port
    event.parsed      = eth
    event.ofp         = ofp_msg
    event.connection  = conn

    return event, conn


# ─────────────────────────────────────────────────────────────────────────────
#  Test Cases
# ─────────────────────────────────────────────────────────────────────────────

class TestConnectionUp(unittest.TestCase):
    """Table-miss rule is installed when a switch connects."""

    def test_table_miss_rule_sent(self):
        ctrl  = make_controller()
        event = make_connection_up(dpid=1)
        ctrl._handle_ConnectionUp(event)

        event.connection.send.assert_called_once()
        sent = event.connection.send.call_args[0][0]
        # Should be a flow_mod with priority 0 and FLOOD/CONTROLLER action
        self.assertEqual(sent.priority, 0)
        self.assertTrue(len(sent.actions) > 0)

    def test_mac_table_initialised(self):
        ctrl  = make_controller()
        event = make_connection_up(dpid=42)
        ctrl._handle_ConnectionUp(event)
        self.assertIn(42, ctrl.mac_to_port)


class TestARPLearning(unittest.TestCase):
    """ARP table is populated from ARP Requests and Replies."""

    def setUp(self):
        self.ctrl = make_controller()

    def test_request_populates_arp_table(self):
        event, _ = make_packet_in(
            dpid=1, in_port=1, arp_opcode=1,
            src_mac='00:00:00:00:00:01', src_ip='10.0.0.1',
            dst_ip='10.0.0.2'
        )
        self.ctrl._handle_PacketIn(event)

        key = IPAddr('10.0.0.1')
        self.assertIn(key, self.ctrl.arp_table)
        mac, dpid, port = self.ctrl.arp_table[key]
        self.assertEqual(str(mac),  '00:00:00:00:00:01')
        self.assertEqual(dpid, 1)
        self.assertEqual(port, 1)

    def test_reply_populates_arp_table(self):
        # pre-populate destination so reply is forwarded not flooded
        self.ctrl.arp_table[IPAddr('10.0.0.1')] = (
            EthAddr('00:00:00:00:00:01'), 1, 1)
        self.ctrl.mac_to_port[1] = {EthAddr('00:00:00:00:00:01'): 1}

        event, _ = make_packet_in(
            dpid=1, in_port=2, arp_opcode=2,
            src_mac='00:00:00:00:00:02', src_ip='10.0.0.2',
            dst_mac='00:00:00:00:00:01', dst_ip='10.0.0.1'
        )
        self.ctrl._handle_PacketIn(event)

        key = IPAddr('10.0.0.2')
        self.assertIn(key, self.ctrl.arp_table)
        mac, _, _ = self.ctrl.arp_table[key]
        self.assertEqual(str(mac), '00:00:00:00:00:02')

    def test_port_update_on_host_move(self):
        """If a host appears on a new port, the ARP table must update."""
        self.ctrl.arp_table[IPAddr('10.0.0.1')] = (
            EthAddr('00:00:00:00:00:01'), 1, 1)

        event, _ = make_packet_in(
            dpid=1, in_port=3, arp_opcode=1,
            src_mac='00:00:00:00:00:01', src_ip='10.0.0.1',
            dst_ip='10.0.0.2'
        )
        self.ctrl._handle_PacketIn(event)

        _, _, port = self.ctrl.arp_table[IPAddr('10.0.0.1')]
        self.assertEqual(port, 3)


class TestProxyARP(unittest.TestCase):
    """Controller answers ARP Requests when target IP is known."""

    def setUp(self):
        self.ctrl = make_controller()
        # h2 is already known
        self.ctrl.arp_table[IPAddr('10.0.0.2')] = (
            EthAddr('00:00:00:00:00:02'), 1, 2)
        self.ctrl.mac_to_port[1] = {}

    def test_proxy_reply_is_sent(self):
        """_send_arp_reply must be called; connection.send must fire."""
        event, conn = make_packet_in(
            dpid=1, in_port=1, arp_opcode=1,
            src_mac='00:00:00:00:00:01', src_ip='10.0.0.1',
            dst_mac='ff:ff:ff:ff:ff:ff', dst_ip='10.0.0.2'
        )
        self.ctrl._send_arp_reply = MagicMock()
        self.ctrl._handle_PacketIn(event)

        self.ctrl._send_arp_reply.assert_called_once()
        args = self.ctrl._send_arp_reply.call_args[0]
        # args: (event, src_mac, src_ip, reply_mac, reply_ip)
        self.assertEqual(str(args[3]), '00:00:00:00:00:02')  # target MAC
        self.assertEqual(str(args[4]), '10.0.0.2')           # target IP

    def test_no_flood_when_target_known(self):
        """Proxy ARP must suppress the flood."""
        event, conn = make_packet_in(
            dpid=1, in_port=1, arp_opcode=1,
            src_mac='00:00:00:00:00:01', src_ip='10.0.0.1',
            dst_mac='ff:ff:ff:ff:ff:ff', dst_ip='10.0.0.2'
        )
        self.ctrl._flood          = MagicMock()
        self.ctrl._send_arp_reply = MagicMock()
        self.ctrl._handle_PacketIn(event)

        self.ctrl._flood.assert_not_called()


class TestARPFlood(unittest.TestCase):
    """Unknown target IP → request must be flooded."""

    def setUp(self):
        self.ctrl = make_controller()

    def test_flood_when_target_unknown(self):
        self.ctrl._flood = MagicMock()

        event, _ = make_packet_in(
            dpid=1, in_port=1, arp_opcode=1,
            src_mac='00:00:00:00:00:01', src_ip='10.0.0.1',
            dst_ip='10.0.0.99'   # not in ARP table
        )
        self.ctrl._handle_PacketIn(event)

        self.ctrl._flood.assert_called_once()


class TestARPTableSnapshot(unittest.TestCase):
    """get_arp_table() returns an independent copy."""

    def setUp(self):
        self.ctrl = make_controller()

    def test_empty_on_init(self):
        self.assertEqual(self.ctrl.get_arp_table(), {})

    def test_snapshot_does_not_reflect_later_changes(self):
        key = IPAddr('10.0.0.1')
        self.ctrl.arp_table[key] = (EthAddr('aa:bb:cc:dd:ee:ff'), 1, 1)
        snapshot = self.ctrl.get_arp_table()
        # Mutate live table
        self.ctrl.arp_table[key] = (EthAddr('00:00:00:00:00:00'), 1, 9)
        self.assertEqual(str(snapshot[key][0]), 'aa:bb:cc:dd:ee:ff')


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 60)
    print("  ARP Controller (POX) — Regression Test Suite")
    print("=" * 60)
    unittest.main(verbosity=2)
