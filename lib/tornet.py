#!/usr/bin/python
# This is distributed under cc0. See the LICENCE file distributed along with
# this code.

"""
   Let's simulate a tor network!  We're only going to do enough here
   to try out guard selection/replacement algorithms from proposal
   259, and some of its likely variants.
"""

import random
import simtime

from math import floor

from py3hax import *


def compareNodeBandwidth(this, other):
    if this.bandwidth < other.bandwidth:
        return -1
    elif this.bandwidth > other.bandwidth:
        return 1
    else:
        return 0


class Node(object):
    def __init__(self, name, port, evil=False, reliability=0.96):
        """Create a new Tor node."""

        # name for this node.
        self._name = name

        # What port does this node expose?
        assert 1 <= port <= 65535
        self._port = port

        # Is this a hostile node?
        self._evil = evil

        # How much of the time is this node running?
        self._reliability = reliability

        # True if this node is running
        self._up = True

        # True if this node has been killed permanently
        self._dead = False

        # random hex string.
        self._id = "".join(random.choice("0123456789ABCDEF") for _ in xrange(40))

        # Some completely made up number for the bandwidth of this guard.
        self._bandwidth = 0

        # Time went down
        self._down_since = 0

        #####################
        # --- From node_t ---#
        #####################

        # As far as we know, is this OR currently running?
        self._isRunning = random.random() < self._reliability

    @property
    def bandwidth(self, alpha=1.0, beta=0.5, bandwidth_max=100000):
        """Completely make-believe bandwith.  It's calculated as a random point
        on the probability density function of a gamma distribution over
        (0,100000] in KB/s.
        """
        if not self._bandwidth:
            self._bandwidth = \
                int(floor(random.gammavariate(alpha, beta) * bandwidth_max))
        return self._bandwidth

    def getName(self):
        """Return the human-readable name for this node."""
        return self._name

    def getID(self):
        """Return the hex id for this node"""
        return self._id

    def updateRunning(self, recoveryTime=60):
        """Enough time has passed that some nodes are no longer running.
           Update this node randomly to see if it has come up or down."""

        # XXXX Actually, it should probably take down nodes a while to
        # XXXXX come back up.  I wonder if that matters for us.

        if not self._dead:
            if self._down_since and not self._down_since + recoveryTime * random.random() < simtime.now():
                return

            self._up = random.random() < self._reliability
            if self._up:
                self._down_since = None
            elif self._down_since:
                return
            else:
                self._down_since = simtime.now()

    def kill(self):
        """Mark this node as completely off the network, until resurrect
           is called."""
        self._dead = True
        self._up = False

    def resurrect(self):
        """Mark this node as back on the network."""
        self._dead = False
        self.updateRunning()

    def getPort(self):
        """Return this node's ORPort"""
        return self._port

    def isReallyUp(self):
        """Return true iff this node is truly alive.  Client simulation code
           mustn't call this."""
        return self._up

    def isReallyEvil(self):
        """Return true iff this node is truly evil.  Client simulation code
           mustn't call this."""
        return self._evil

    def seemsDystopic(self):
        """Return true iff this node seems like one we could use in a
           dystopic world."""
        return self.getPort() in [80, 443]


def _randport(pfascistfriendly):
    """generate and return a random port.  If 'pfascistfriendly' is true,
       return a port in the FascistPortList.  Otherwise return any random
       TCP  port."""
    if random.random() < pfascistfriendly:
        return random.choice([80, 443])
    else:
        return random.randint(1, 65535)


class Network(object):
    """Base class to represent a simulated Tor network.  Very little is
       actually simulated here: all we need is for guard nodes to come
       up and down over time.

       In this simulation, we ignore bandwidth, and consider every
       node to be a guard.  This shouldn't affect the algorithm.
    """

    def __init__(self, num_nodes, pfascistfriendly=.3, pevil=0.5,
                 avgnew=1.5, avgdel=0.5, nodereliability=0.96):

        """Create a new network with 'num_nodes' randomly generated nodes.
           Each node should be fascist-friendly with probability
           'pfascistfriendly'.  Each node should be evil with
           probability 'pevil'.  Every time the network churns,
           'avgnew' nodes should be added on average, and 'avgdel'
           deleted on average.
        """
        self._pfascistfriendly = pfascistfriendly
        self._pevil = pevil
        self._nodereliability = nodereliability

        # a list of all the Nodes on the network, dead and alive.
        self._wholenet = [Node("node%d" % n,
                               port=_randport(pfascistfriendly),
                               evil=random.random() < pevil,
                               reliability=nodereliability)
                          for n in xrange(num_nodes)]
        for node in self._wholenet:
            node.updateRunning()

        # lambda parameters for our exponential distributions.
        self._lamdbaAdd = 1.0 / avgnew
        self._lamdbaDel = 1.0 / avgdel

        # total number of nodes ever added on the network.
        self._total = num_nodes

    # XXX Why does a consensus returns only guards that are really up?
    # This seems unrealistic
    def new_consensus(self):
        """Return a list of the running guard nodes."""
        return [node for node in self._wholenet if node.isReallyUp()]

    def do_churn(self):
        """Simulate churn: delete and add nodes from/to the network."""
        nAdd = int(random.expovariate(self._lamdbaAdd) + 0.5)
        nDel = int(random.expovariate(self._lamdbaDel) + 0.5)

        # kill nDel non-dead nodes at random.
        random.shuffle(self._wholenet)
        nkilled = 0
        for node in self._wholenet:
            if nkilled == nDel:
                break
            if not node._dead:
                node.kill()
                nkilled += 1

        # add nAdd new nodes.
        num_nodes = len(self._wholenet)
        for n in xrange(self._total, self._total + nAdd):
            node = Node("node%d" % n,
                        port=_randport(self._pfascistfriendly),
                        evil=random.random() < self._pevil,
                        reliability=self._nodereliability)
            self._total += 1
            self._wholenet.append(node)
        self._wholenet = random.sample(self._wholenet, num_nodes)

    def updateRunning(self, recoveryTime=60):
        """Enough time has passed for some nodes to go down and some to come
           up."""
        for node in self._wholenet:
            node.updateRunning(recoveryTime)

    def probe_node_is_up(self, node):
        """Called when a simulated client is trying to connect to 'node'.
           Returns true iff the connection succeeds."""

        up = node.isReallyUp()

        # It takes some time to connect. Not adding this is unfair with the
        # original algorithm which seems to make less connections attempts than
        # the proposal
        if up:
            simtime.advanceTime(2)
        else:
            simtime.advanceTime(4)

        return up


class _NetworkDecorator(object):
    """Decorator class for Network: wraps a network and implements all its
       methods by calling down to the base network.  We use these to
       simulate a client's local network connection."""

    def __init__(self, network):
        self._network = network

    def new_consensus(self):
        return self._network.new_consensus()

    def do_churn(self):
        self._network.do_churn()

    def probe_node_is_up(self, node):
        return self._network.probe_node_is_up(node)

    def updateRunning(self):
        self._network.updateRunning()


class FascistNetwork(_NetworkDecorator):
    """Network that blocks all connections except those to ports 80, 443"""

    def probe_node_is_up(self, node):
        return (node.getPort() in [80, 443] and
                self._network.probe_node_is_up(node))


class EvilFilteringNetwork(_NetworkDecorator):
    """Network that blocks connections to non-evil nodes with P=pBlockGood"""

    def __init__(self, network, pBlockGood=1.0):
        super(EvilFilteringNetwork, self).__init__(network)
        self._pblock = pBlockGood

    def probe_node_is_up(self, node):
        if not node.isReallyEvil() and random.random() < self._pblock:
            return False

        return self._network.probe_node_is_up(node)


class SniperNetwork(_NetworkDecorator):
    """Network that does a DoS attack on a client's non-evil nodes with
       P=pKillGood after each connection."""

    def __init__(self, network, pKillGood=1.0):
        super(SniperNetwork, self).__init__(network)
        self._pkill = pKillGood

    def probe_node_is_up(self, node):
        result = self._network.probe_node_is_up(node)

        if not node.isReallyEvil() and random.random() < self._pkill:
            node.kill()

        return result


class FlakyNetwork(_NetworkDecorator):
    """A network where all connections succeed only with probability
       'reliability', regardless of whether the node is up or down."""

    def __init__(self, network, reliability=0.9):
        super(FlakyNetwork, self).__init__(network)
        self._reliability = reliability

    def probe_node_is_up(self, node):
        if random.random() >= self._reliability:
            return False
        return self._network.probe_node_is_up(node)


class DownNetwork(_NetworkDecorator):
    """A network where no connections succeed, regardless of whether
       the node is up or down. It assumes we can get a consensus, however."""

    def __init__(self, network):
        super(DownNetwork, self).__init__(network)

    def probe_node_is_up(self, node):
        return False


class SlowRecoveryNetwork(_NetworkDecorator):
    """A network where nodes take an hour to recover."""

    def __init__(self, network):
        super(SlowRecoveryNetwork, self).__init__(network)

    def updateRunning(self):
        """Enough time has passed for some nodes to go down and some to come
           up."""
        self._network.updateRunning(recoveryTime=3600)


class SwitchingNetwork(_NetworkDecorator):
    """A network where the network randomly switches between all kinds noted above."""

    def __init__(self, network):
        super(SwitchingNetwork, self).__init__(network)
        self._real_network = network

    def _switch_networks(self):
        allNetworks = [FascistNetwork, EvilFilteringNetwork, SniperNetwork, FlakyNetwork, DownNetwork,
                       self._real_network, SlowRecoveryNetwork]
        newNet = random.choice(allNetworks)
        if newNet == self._real_network:
            print("Network switched to real network")
            self._network = self._real_network
        else:
            print("Network switched to %s" % newNet.__name__)
            self._network = newNet(self._real_network)

    def do_churn(self):
        self._switch_networks()
        self._network.do_churn()
