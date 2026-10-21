from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI


class CongestionTopo(Topo):
    def build(self):
        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        h2 = self.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')
        s1 = self.addSwitch('s1', protocols='OpenFlow13')
        s2 = self.addSwitch('s2', protocols='OpenFlow13')
        s3 = self.addSwitch('s3', protocols='OpenFlow13')
        s4 = self.addSwitch('s4', protocols='OpenFlow13')
        # Link order fixes the switch port numbers used by the controller
        self.addLink(h1, s1, bw=20)
        self.addLink(s1, s2, bw=5)    # Path A (primary)
        self.addLink(s2, s4, bw=5)
        self.addLink(s1, s3, bw=20)   # Path B (alternate)
        self.addLink(s3, s4, bw=20)
        self.addLink(s4, h2, bw=20)


def run_network():
    net = Mininet(topo=CongestionTopo(), controller=None,
                  switch=OVSSwitch, link=TCLink)
    net.addController('c0', controller=RemoteController,
                      ip='127.0.0.1', port=6653)
    net.start()
    # Static ARP: the controller only forwards IPv4, so no ARP flooding/loops
    net.get('h1').setARP('10.0.0.2', '00:00:00:00:00:02')
    net.get('h2').setARP('10.0.0.1', '00:00:00:00:00:01')
    print('Path A: h1-s1-s2-s4-h2 | Path B: h1-s1-s3-s4-h2')
    CLI(net)
    net.stop()


if __name__ == '__main__':
    run_network()
