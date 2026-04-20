#!/usr/bin/env python3

import sys
import time
import argparse
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info, output
from mininet.link import TCLink

def build_network(controller_ip='127.0.0.1', controller_port=6633):
    """
    Build and return a Mininet network (not yet started).

    Key choices:
      autoSetMacs=True   → deterministic MACs (00:00:00:00:00:01 … :04)
      autoStaticArp=False → IMPORTANT: let our POX controller handle all ARP
      OVSSwitch          → supports OpenFlow 1.0 (default for POX)
      TCLink             → lets us set bw/delay per link
    """
    net = Mininet(
        controller=RemoteController,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=False
    )
    info("*** Adding controller\n")
    net.addController(
        'c0',
        controller=RemoteController,
        ip=controller_ip,
        port=controller_port
    )
    info("*** Adding switch\n")
    s1 = net.addSwitch('s1')   # defaults to OpenFlow 1.0 — compatible with POX
    info("*** Adding hosts\n")
    for i in range(1, 5):
        h = net.addHost(
            f'h{i}',
            ip=f'10.0.0.{i}/24',
            mac=f'00:00:00:00:00:0{i}'
        )
        # 10 Mbps link, 5 ms delay — makes iperf & latency results meaningful
        net.addLink(h, s1, bw=10, delay='5ms', loss=0)
    return net

def run_tests(net):
    """
    Two required test scenarios:
    Scenario 1  –  Normal communication (ARP discovery + ping)
      1a. First ping h1→h2:  ARP Request floods, controller learns both hosts.
      1b. Second ping h1→h2: Controller answers ARP via Proxy (no flood).
      1c. Cross-pair ping h1→h4.
      1d. Full pingAll matrix.
    Scenario 2  –  Throughput measurement with iperf
      2a. TCP throughput h1↔h2.
      2b. UDP throughput h3↔h4.
    """
    h1 = net.get('h1')
    h2 = net.get('h2')
    h3 = net.get('h3')
    h4 = net.get('h4')
    s1 = net.get('s1')
    sep = "=" * 60
    # Scenario 1
    output(f"\n{sep}\n  SCENARIO 1: ARP Discovery + Ping Reachability\n{sep}\n")
    output("\n[TEST 1a]  h1 -> h2  (FIRST ping — ARP flood + controller learns)\n")
    output(h1.cmd('ping -c 3 -W 2 10.0.0.2'))
    output("\n[TEST 1b]  h1 -> h2  (REPEAT ping — controller answers ARP directly)\n")
    output(h1.cmd('ping -c 3 -W 2 10.0.0.2'))
    output("\n[TEST 1c]  h3 -> h4\n")
    output(h3.cmd('ping -c 3 -W 2 10.0.0.4'))
    output("\n[TEST 1d]  h1 -> h4  (cross pair)\n")
    output(h1.cmd('ping -c 3 -W 2 10.0.0.4'))
    output("\n[TEST 1e]  pingAll — full connectivity matrix\n")
    net.pingAll()
    # Flow table snapshot
    output("\n[FLOW TABLE]  ovs-ofctl dump-flows s1\n")
    output(s1.cmd('ovs-ofctl dump-flows s1'))
    # Scenario 2
    output(f"\n{sep}\n  SCENARIO 2: Throughput Measurement (iperf)\n{sep}\n")
    output("\n[TEST 2a]  TCP  h1 (server) <-> h2 (client)  10 seconds\n")
    h1.cmd('iperf -s -D')
    time.sleep(1)
    output(h2.cmd('iperf -c 10.0.0.1 -t 10'))
    h1.cmd('kill %iperf')
    output("\n[TEST 2b]  UDP  h3 (server) <-> h4 (client)  10 seconds\n")
    h3.cmd('iperf -s -u -D')
    time.sleep(1)
    output(h4.cmd('iperf -c 10.0.0.3 -u -t 10 -b 5M'))
    h3.cmd('kill %iperf')
    output(f"\n{sep}\n  ALL TESTS COMPLETE\n{sep}\n")

def main():
    parser = argparse.ArgumentParser(
        description='ARP SDN Mininet topology (POX controller)')
    parser.add_argument('--test',
                        action='store_true',
                        help='Run automated test scenarios then exit')
    parser.add_argument('--controller',
                        default='127.0.0.1',
                        help='Controller IP  (default: 127.0.0.1)')
    parser.add_argument('--port',
                        type=int,
                        default=6633,
                        help='Controller port (default: 6633)')
    args = parser.parse_args()
    setLogLevel('info')
    info("*** Building network\n")
    net = build_network(args.controller, args.port)
    info("*** Starting network\n")
    net.start()
    info("*** Waiting 3 s for switch → controller connection\n")
    time.sleep(3)
    if args.test:
        run_tests(net)
    else:
        info("\n*** Network ready.  Useful Mininet CLI commands:\n")
        info("***   h1 ping -c 3 h2\n")
        info("***   h1 arping -c 2 10.0.0.2\n")
        info("***   s1 ovs-ofctl dump-flows s1\n")
        info("***   s1 ovs-ofctl dump-ports s1\n")
        info("***   iperf h1 h2\n")
        info("***   pingAll\n\n")
        CLI(net)
    info("*** Stopping network\n")
    net.stop()


if __name__ == '__main__':
    main()
