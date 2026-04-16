#!/usr/bin/env python3
"""
Mininet Topology for ARP Handling SDN Project
==============================================

Topology (linear, 1 switch, 4 hosts)
--------------------------------------

    h1 (10.0.0.1) ──┐
    h2 (10.0.0.2) ──┤
                    S1 ───── Ryu Controller (127.0.0.1:6633)
    h3 (10.0.0.3) ──┤
    h4 (10.0.0.4) ──┘

Why this topology?
  • Single switch keeps the OpenFlow interaction easy to observe.
  • 4 hosts produce enough ARP traffic to clearly demonstrate:
      - Proxy ARP (controller answers on behalf of a known host)
      - Flooded ARP (first request before the table is populated)
      - Host discovery (learning from replies)
  • Extending to multi-switch is straightforward.

Usage
-----
  sudo python3 topology.py          # interactive Mininet CLI
  sudo python3 topology.py --test   # run automated test scenarios and exit
"""

import sys
import time
import argparse
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info, error
from mininet.link import TCLink


# ── Topology builder ──────────────────────────────────────────────────────────

def build_topology(controller_ip='127.0.0.1', controller_port=6633):
    """
    Create and return a Mininet network object.
    Does NOT start the network — call net.start() separately.
    """
    net = Mininet(
        controller=RemoteController,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,   # deterministic MACs: 00:00:00:00:00:01 etc.
        autoStaticArp=False # IMPORTANT: let our controller handle ARP
    )

    info("*** Adding Ryu controller\n")
    c0 = net.addController(
        'c0',
        controller=RemoteController,
        ip=controller_ip,
        port=controller_port
    )

    info("*** Adding switch\n")
    s1 = net.addSwitch('s1', protocols='OpenFlow13')

    info("*** Adding hosts\n")
    hosts = []
    for i in range(1, 5):
        h = net.addHost(
            f'h{i}',
            ip=f'10.0.0.{i}/24',
            mac=f'00:00:00:00:00:0{i}'
        )
        hosts.append(h)
        # 10 Mbps link with 5 ms delay — shows iperf & ping results clearly
        net.addLink(h, s1, bw=10, delay='5ms', loss=0)

    return net, hosts


# ── Test scenarios ────────────────────────────────────────────────────────────

def run_test_scenarios(net):
    """
    Scenario 1 – ARP learning + ping (normal communication)
    Scenario 2 – iperf throughput after ARP is established
    Both scenarios print clearly labelled output for the README proof.
    """
    hosts = [net.get(f'h{i}') for i in range(1, 5)]
    h1, h2, h3, h4 = hosts

    print("\n" + "=" * 60)
    print("  SCENARIO 1: ARP Discovery + Ping Reachability")
    print("=" * 60)

    # First ping: triggers ARP Request → controller learns h1, h2
    print("\n[TEST 1a] h1 -> h2  (first ping, ARP will be triggered)")
    result = h1.cmd('ping -c 3 10.0.0.2')
    print(result)

    # Second ping: ARP table already populated → proxy ARP from controller
    print("\n[TEST 1b] h1 -> h2  (repeat ping, controller answers ARP directly)")
    result = h1.cmd('ping -c 3 10.0.0.2')
    print(result)

    print("\n[TEST 1c] h3 -> h4  (different pair)")
    result = h3.cmd('ping -c 3 10.0.0.4')
    print(result)

    print("\n[TEST 1d] h1 -> h4  (cross pair)")
    result = h1.cmd('ping -c 3 10.0.0.4')
    print(result)

    print("\n" + "=" * 60)
    print("  SCENARIO 2: Throughput Measurement with iperf")
    print("=" * 60)

    print("\n[TEST 2a] iperf TCP  h1 (server) <-> h2 (client)  — 10 seconds")
    # Start iperf server on h1
    h1.cmd('iperf -s -D')   # -D = daemon mode
    time.sleep(1)
    result = h2.cmd('iperf -c 10.0.0.1 -t 10')
    print(result)
    h1.cmd('kill %iperf')

    print("\n[TEST 2b] iperf UDP  h3 (server) <-> h4 (client)  — 10 seconds")
    h3.cmd('iperf -s -u -D')
    time.sleep(1)
    result = h4.cmd('iperf -c 10.0.0.3 -u -t 10 -b 5M')
    print(result)
    h3.cmd('kill %iperf')

    print("\n" + "=" * 60)
    print("  SCENARIO 3: Host Discovery Validation")
    print("=" * 60)
    print("\n[TEST 3]  All hosts ping all other hosts (pingAll)")
    net.pingAll()

    print("\n[DONE]  All test scenarios complete.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='ARP SDN Mininet topology')
    parser.add_argument('--test',       action='store_true',
                        help='Run automated test scenarios then exit')
    parser.add_argument('--controller', default='127.0.0.1',
                        help='Controller IP (default: 127.0.0.1)')
    parser.add_argument('--port',       type=int, default=6633,
                        help='Controller port (default: 6633)')
    args = parser.parse_args()

    setLogLevel('info')

    info("*** Building topology\n")
    net, hosts = build_topology(args.controller, args.port)

    info("*** Starting network\n")
    net.start()

    # Give OVS a moment to connect to the controller
    info("*** Waiting for controller connection (3 s)\n")
    time.sleep(3)

    if args.test:
        run_test_scenarios(net)
    else:
        info("*** Network ready.  Type 'help' in the CLI.\n")
        info("*** Useful commands:\n")
        info("***   h1 ping h2         — triggers ARP\n")
        info("***   h1 arping h2       — raw ARP test\n")
        info("***   s1 ovs-ofctl dump-flows s1  — show flow table\n")
        info("***   wireshark &        — capture on any-s1 port\n")
        CLI(net)

    info("*** Stopping network\n")
    net.stop()


if __name__ == '__main__':
    main()
