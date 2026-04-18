# ARP Handling in SDN Networks

**Course:** SDN Mininet Simulation Project 
**Controller:** POX (OpenFlow 1.0)  
**Author:** Praveen Balaji P  

---

## About the Project

In a traditional network, every host resolves IP-to-MAC addresses using ARP
(Address Resolution Protocol) broadcasts. These broadcasts flood the entire
network and waste bandwidth.

In an **SDN (Software Defined Network)**, the controller has complete
visibility of the network topology. This project uses a **POX SDN controller**
to intercept every ARP packet, build a centralised ARP table, and answer
future ARP requests directly — eliminating unnecessary floods.

### What this project demonstrates

| Feature | Description |
|---|---|
| **Intercept ARP Packets** | Every ARP packet is sent to the controller via `packet_in` events |
| **Generate ARP Responses** | Controller crafts synthetic ARP Replies (Proxy ARP) when the target is known |
| **Enable Host Discovery** | Controller builds an ARP table `IP → (MAC, switch, port)` from every packet seen |
| **Validate Communication** | `ping`, `pingall`, `iperf`, `ovs-ofctl dump-flows` confirm correct behaviour |
| **Install Flow Rules** | After ARP resolves, OpenFlow rules push IP forwarding into the switch data plane |

---

## Architecture

### Step-by-step flow for a first ping from h1 to h2

```
h1                    OVS Switch (s1)              POX Controller
|                          |                              |
|-- ARP Request ---------->|                              |
|   "Who has 10.0.0.2?"    |-- packet_in ---------------->|
|                          |                   [ARP LEARN] 10.0.0.1 → port 1
|                          |                   [ARP FLOOD] 10.0.0.2 unknown
|                          |<-- OFPP_FLOOD ---|
|                          |-- flood -------->h2,h3,h4
|                          |
|                    h2 sends ARP Reply
|                          |-- packet_in ---------------->|
|                          |                   [ARP LEARN] 10.0.0.2 → port 2
|                          |                   [ARP FWD]   forward reply → port 1
|                          |<-- packet_out port 1 --------|
|<-- ARP Reply ------------|
|   "10.0.0.2 is at 00:02" |
|                          |
|-- ICMP ping ------------>|-- packet_in ---------------->|
|                          |                   [IP FWD] install flow rule
|                          |<-- flow_mod dl_dst=00:02 ----|
|                          |-- ICMP ping ---------------->h2
```

### Step-by-step flow for a second ping (Proxy ARP — no flood)

```
h1                    OVS Switch (s1)              POX Controller
|                          |                              |
|-- ARP Request ---------->|                              |
|   "Who has 10.0.0.2?"    |-- packet_in ---------------->|
|                          |                   [PROXY ARP] table hit!
|                          |                   10.0.0.2 is at 00:02
|                          |<-- packet_out (ARP Reply) ---|
|<-- ARP Reply ------------|
|   No flood. No broadcast.|
```

---

### Network Topology

```
    h1  (10.0.0.1 / 00:00:00:00:00:01) ── port 1 ──┐
    h2  (10.0.0.2 / 00:00:00:00:00:02) ── port 2 ──┤
                                                    S1 ── POX Controller
    h3  (10.0.0.3 / 00:00:00:00:00:03) ── port 3 ──┤     127.0.0.1:6633
    h4  (10.0.0.4 / 00:00:00:00:00:04) ── port 4 ──┘

    Link speed : 10 Mbps
    Link delay : 5 ms
    Packet loss: 0%
```

---

## Directory Structure

```
CN-SDN/
│
├── controller/
│   └── arp_handler.py          ← POX SDN controller (all ARP + forwarding logic)
├── topology/
│   └── topology.py             ← Mininet topology builder + automated test runner
├── tests/
│   └── test_arp_handler.py     ← 10 unit/regression tests (no Mininet required)
├── requirements.txt            ← Dependency list with install instructions
└── README.md                   ← This file
```

### File descriptions

**`controller/arp_handler.py`**  
The main POX controller component. Contains the `ARPHandler` class with:
- `_handle_ConnectionUp` — installs table-miss rule when switch connects
- `_handle_PacketIn` — dispatches every packet by EtherType
- `_handle_arp` — ARP Request/Reply logic, proxy ARP, flooding
- `_send_arp_reply` — crafts synthetic ARP Reply packets
- `_handle_ipv4` — MAC-based forwarding + flow rule installation
- `launch()` — POX entry point

**`topology/topology.py`**  
Mininet script that creates the 4-host single-switch topology.
Supports interactive CLI mode and automated `--test` mode.

**`tests/test_arp_handler.py`**  
10 unit tests covering: ARP learning, Proxy ARP, flood suppression,
table snapshots, connection setup. Runs without Mininet or POX installed.

---

## Requirements

### System requirements

| Component | Version | Purpose |
|---|---|---|
| Ubuntu | 20.04 or 22.04 LTS | Operating system |
| Python | 3.6 – 3.12 | Runtime (3.8 recommended) |
| Mininet | 2.3.0+ | Network emulator |
| Open vSwitch | 2.13+ | OpenFlow-capable virtual switch |
| POX | 0.7.0 (gar) | SDN Controller |
| iperf | 2.x | Bandwidth measurement |
| Wireshark / tshark | any | Packet capture |
| arping | any | Raw ARP testing |

### Python packages (for tests only)

| Package | Purpose |
|---|---|
| `unittest` | Built-in test framework (no install needed) |
| `unittest2` | Optional: compatibility backport |

---

## Installation

### Step 1 — Update system packages

```bash
sudo apt update && sudo apt upgrade -y
```

### Step 2 — Install all system dependencies

```bash
sudo apt install -y \
    mininet \
    openvswitch-switch \
    iperf \
    wireshark \
    tshark \
    arping \
    git \
    python3 \
    python3-pip
```

### Step 3 — Install POX controller

```bash
git clone https://github.com/noxrepo/pox.git ~/pox
```

Verify POX installed correctly:
```bash
ls ~/pox/pox.py
# Should print: /home/<username>/pox/pox.py
```

### Step 4 — Clone this project

```bash
git clone https://github.com/YOUR_USERNAME/arp-sdn-project.git ~/Documents/CN-SDN
cd ~/Documents/CN-SDN
```

### Step 5 — Install Python dependencies (for tests)

```bash
pip3 install -r requirements.txt
```

### Step 6 — Copy controller into POX directory

```bash
cp ~/Documents/CN-SDN/controller/arp_handler.py ~/pox/arp_handler.py
```

Verify:
```bash
ls ~/pox/arp_handler.py
# Should print: /home/<username>/pox/arp_handler.py
```

### Step 7 — Verify everything is ready

```bash
sudo mn --version          # Mininet version
sudo ovs-vsctl --version   # Open vSwitch version
python3 --version          # Python version
ls ~/pox/pox.py            # POX exists
ls ~/pox/arp_handler.py    # Controller copied
iperf --version            # iperf available
```

---

## Steps to Run

> **Always follow this order: clean → POX first → Mininet second**

### Before every session — clean leftover state

```bash
sudo mn -c
sudo fuser -k 6633/tcp 2>/dev/null
```

---

### Terminal 1 — Start the POX Controller

```bash
cd ~/pox
python3 pox.py log.level --DEBUG arp_handler
```

Wait until you see this before moving to Terminal 2:
```
INFO:arp_handler:============================================================
INFO:arp_handler:  ARP Handling SDN Controller (POX)  —  started
INFO:arp_handler:============================================================
INFO:arp_handler:ARPHandler component registered.
INFO:core:POX 0.7.0 (gar) is up.
DEBUG:openflow.of_01:Listening on 0.0.0.0:6633
```

**Do not close Terminal 1.**

---

### Terminal 2 — Start the Mininet Topology

Open a new terminal (Ctrl + Alt + T):

```bash
cd ~/Documents/CN-SDN
sudo python3 topology/topology.py
```

Wait for the prompt:
```
*** Network ready.
mininet>
```

At this point, check Terminal 1 — you should see:
```
INFO:openflow.of_01:[00-00-00-00-00-01 1] connected
INFO:arp_handler:[SWITCH 00-00-00-00-00-01]  connected — installing table-miss rule
```

---

### Terminal 3 (Optional) — Check flow table in real time

Open a third terminal and run:

```bash
# Watch flow table update live
watch -n 1 sudo ovs-ofctl dump-flows s1
```

---

### Run all tests automatically

Instead of the interactive CLI, you can run all test scenarios automatically:

```bash
# Stop any running Mininet session first
# Then in Terminal 2:
sudo python3 topology/topology.py --test
```

---

## Mininet CLI Commands

Once `mininet>` prompt appears, run these commands:

### Ping commands

```bash
# First ping — triggers ARP flood and controller learning
mininet> h1 ping -c 5 h2

# Second ping — controller answers ARP directly (Proxy ARP, no flood)
mininet> h1 ping -c 5 h2

# Other host pairs
mininet> h3 ping -c 5 h4
mininet> h1 ping -c 5 h4

# Full connectivity matrix (all pairs)
mininet> pingall
```

### Flow table commands

```bash
# View installed flow rules (run from OUTSIDE Mininet, in a new terminal)
sudo ovs-ofctl dump-flows s1

# Port statistics (packet/byte counts per port)
sudo ovs-ofctl dump-ports s1

# Show OVS bridge configuration
sudo ovs-vsctl show
```

### iperf throughput commands

```bash
# TCP test — h1 server, h2 client, 10 seconds
mininet> h1 iperf -s &
mininet> h2 iperf -c 10.0.0.1 -t 10

# UDP test — h3 server, h4 client, 5 Mbps offered load
mininet> h3 iperf -s -u &
mininet> h4 iperf -c 10.0.0.3 -u -b 5M -t 10
```

### ARP specific commands

```bash
# Send raw ARP requests (useful with Wireshark)
mininet> h1 arping -c 4 10.0.0.2

# Show ARP cache on h1
mininet> h1 arp -n
```

### Wireshark / tshark capture

Open a new terminal while Mininet is running:

```bash
# Capture ARP packets on h1's interface
sudo tshark -i s1-eth1 -f "arp" -V

# Capture all traffic on switch port 1
sudo tshark -i s1-eth1

# Launch Wireshark GUI on switch port 2
sudo wireshark -i s1-eth2 &
```

Wireshark display filters:
```
arp                  — all ARP packets
arp.opcode == 1      — ARP Requests only
arp.opcode == 2      — ARP Replies only
icmp                 — ping traffic only
ip.src == 10.0.0.1   — traffic from h1 only
```

### Exit Mininet

```bash
mininet> exit
sudo mn -c
```
