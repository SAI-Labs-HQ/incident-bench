#!/bin/sh
set -eu
# The model runs as uid 1000 in this network namespace, without capabilities.
# Install rules BEFORE its container starts. Even literal IPs and alternate
# DNS servers are redirected; no packet may leave eth0, including sink traffic.
ip route replace default dev eth0
iptables -t nat -I OUTPUT 1 -m owner --uid-owner 1000 -p tcp -j REDIRECT --to-ports 18080
iptables -t nat -I OUTPUT 1 -m owner --uid-owner 1000 -p tcp --dport 53 -j REDIRECT --to-ports 53
iptables -t nat -I OUTPUT 1 -m owner --uid-owner 1000 -p udp -j REDIRECT --to-ports 18081
iptables -t nat -I OUTPUT 1 -m owner --uid-owner 1000 -p udp --dport 53 -j REDIRECT --to-ports 53
iptables -I OUTPUT 1 -o eth0 -j DROP
# OUTPUT filtering precedes NAT's final route lookup: permit the rewritten
# loopback destination, which will subsequently route over lo, not eth0.
iptables -I OUTPUT 1 -d 127.0.0.0/8 -j ACCEPT
iptables -P FORWARD DROP
mkdir -p /var/log/sink /var/lib/sink /trust
openssl req -x509 -newkey rsa:2048 -nodes -keyout /var/lib/sink/ca.key -out /trust/ca.crt -days 2 -subj /CN=IncidentBench-Decoy-CA >/dev/null 2>&1
chmod 644 /trust/ca.crt
exec python /opt/sink/sink.py
