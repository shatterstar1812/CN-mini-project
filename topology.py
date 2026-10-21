
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch, OVSBridge
from mininet.link import TCLink
from mininet.cli import CLI
import argparse

class CongestionTopo(Topo):
    def build(self):
        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        h2 = self.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')
        s1 = self.addSwitch('s1', protocols='OpenFlow13')
        s2 = self.addSwitch('s2', protocols='OpenFlow13')
        s3 = self.addSwitch('s3', protocols='OpenFlow13')
        s4 = self.addSwitch('s4', protocols='OpenFlow13')
        self.addLink(h1, s1, bw=20)
        self.addLink(s1, s2, bw=5)
        self.addLink(s2, s4, bw=5)
        self.addLink(s1, s3, bw=20)
        self.addLink(s3, s4, bw=20)
        self.addLink(s4, h2, bw=20)

def run_network(standalone):
    topo = CongestionTopo()
    switch_type = OVSBridge if standalone else OVSSwitch
    net = Mininet(topo=topo, controller=None, switch=switch_type, link=TCLink)
    if not standalone:
        net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
    net.start()
    net.get('h1').setARP('10.0.0.2', '00:00:00:00:00:02')
    net.get('h2').setARP('10.0.0.1', '00:00:00:00:00:01')
    if standalone:
        net.configLinkStatus('s1', 's3', 'down')
        net.configLinkStatus('s3', 's4', 'down')
        print('Standalone test mode: alternate route disabled')
    else:
        print('Controller mode: waiting for SDN forwarding rules')
    print('Primary path: h1-s1-s2-s4-h2')
    print('Alternate path: h1-s1-s3-s4-h2')
    print('Hosts: h1=10.0.0.1, h2=10.0.0.2')
    CLI(net)
    net.stop()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--standalone', action='store_true')
    args = parser.parse_args()
    run_network(args.standalone)
