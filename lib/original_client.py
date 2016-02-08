from __future__ import print_function

import client
import random

class Client(object):
    """A stateful client implementation of the guard selection algorithm."""

    def __init__(self, network, stats, parameters):

        # a torsim.Network object.
        self._net = network

        # a ClientParams object
        self._p = parameters

        self._ALL_GUARDS = []

        # guard list for this client, default is 3
        self._GUARD_LIST = []

        self.updateGuardLists()

        self._stats = stats

    def have_enough_guards(self):
        return len(self._GUARD_LIST) >= 3

    def updateGuardLists(self):
        """Called at start and when a new consensus should be made & received:
           updates *TOPIC_GUARDS."""

        if self.have_enough_guards():
            return

        # Mark every Guard we have as listed or unlisted.
        for g in self._ALL_GUARDS:
            g.markUnlisted()

        self._ALL_GUARDS = []
        for node in self._net.new_consensus():
            guard = client.Guard(node)
            guard.markListed()
            self._ALL_GUARDS.append(guard)

        # TODO: filter nodes that are not a good fit (already used)?

        # Add new guards until we have enough
        while not self.have_enough_guards():
            self.choose_random_entryguard()

    def choose_random_entryguard(self):
        guard = self.choose_node_by_bandwidth_weights(self._ALL_GUARDS)
        self._GUARD_LIST.append(guard)

    def choose_node_by_bandwidth_weights(self, all_guards):
        return random.choice(all_guards)

    def getGuard(self):
        guards = filter(lambda g: g.canTry(), self._GUARD_LIST)

        # XXX not sure this is really how the current implementation does
        for guard in guards:
            if self.probeGuard(guard):
                return guard

        # XXX: how to chose other guards and expand the list?
        print("All guards are down")
        self.choose_random_entryguard()

    def probeGuard(self, guard):
        """If it's up on the network, mark it up.
           With each try, update the failover threshold
           Return true on success, false on failure."""
        up = self._net.probe_node_is_up(guard.node)
        self.markGuard(guard, up)

        return up

    def connectToGuard(self, guard):
        """Try to connect to 'guard' -- if it's up on the network, mark it up.
           Return true on success, false on failure."""
        up = self.probeGuard(guard)

        if up:
            self._stats.addBandwidth(guard._node.bandwidth)

        return up

    def markGuard(self, guard, up):
        guard.mark(up)

    def buildCircuit(self):
        """Try to build a circuit; return true if we succeeded."""
        g = self.getGuard()

        if not g:
            return False
        return self.connectToGuard(g)

