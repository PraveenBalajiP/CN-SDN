#!/bin/bash

# SDN ARP Project Setup Script
# Installs Mininet + Open vSwitch + tools + POX

# HOW TO RUN setup.sh file
# Run chmod +x setup.sh
# Run ./setup.sh
# The core dependencies alongwith POX cloning from repo happens automatically in one-shot

echo "🔄 Updating system packages..."
sudo apt update

echo "📦 Installing SDN system dependencies..."
sudo apt install -y \
    mininet \
    openvswitch-switch \
    openvswitch-testcontroller \
    iperf \
    wireshark \
    tshark \
    arping \
    git \
    python3 \
    python3-pip

echo "📡 Cloning POX controller..."
if [ ! -d "$HOME/pox" ]; then
    git clone https://github.com/noxrepo/pox.git ~/pox
else
    echo "✔ POX already exists, skipping clone."
fi

echo "🐍 Installing Python dependencies..."
pip3 install eventlet

echo "✅ Setup completed successfully!"
echo "👉 Run POX: cd ~/pox && ./pox.py log.level --DEBUG forwarding.l2_learning"
echo "👉 Run Mininet: sudo mn --topo single,3 --mac --switch ovsk --controller remote"
