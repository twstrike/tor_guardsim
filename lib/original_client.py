from __future__ import print_function

import client
import tor
import random

# TODO implement prioritize bandwith behavior

class Client(object):
    """A stateful client implementation of the guard selection algorithm."""

    def __init__(self, network, stats, parameters):

        # a torsim.Network object.
        self._net = network

        # a ClientParams object
        self._p = parameters

        # all guards we know about. We consider all of them to be directory guards
        # because they play an important role in the original algo, but this
        # behavior could be controlled by (another) flag.
        # XXX we are unsure if this list should include only what is in the
        # latest consensus or if it should include guards from every consensus
        # we ever received
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

        # Mark every Guard we have as listed or unlisted.
        for g in self._ALL_GUARDS:
            g.markUnlisted()

        self._ALL_GUARDS = []
        for node in self._net.new_consensus():
            guard = client.Guard(node)
            guard.markListed()
            self._ALL_GUARDS.append(guard)

    def pickEntryGuards(self):
        # TODO: filter nodes that are not a good fit (already used)?

        # Add new guards until we have enough
        while not self.have_enough_guards():
            self.choose_random_entryguard()

    def choose_random_entryguard(self):
        allButCurrent = [guard for guard in self._ALL_GUARDS if guard not in self._GUARD_LIST]
        guard = tor.choose_node_by_bandwidth_weights(allButCurrent)
        self._GUARD_LIST.append(guard)

    def populateLiveEntryGuards(self, numNeeded):
        liveEntryGuards = []
        for guard in self._GUARD_LIST:
            if not guard.canTry(): continue
            liveEntryGuards.append(guard)

            if not guard._tried: return (liveEntryGuards, True)
            if len(liveEntryGuards) >= numNeeded: return (liveEntryGuards, True)

        return (liveEntryGuards, False)

    def getGuard(self):
        if not self.have_enough_guards():
            self.pickEntryGuards()

        # After bootstrap, Tor requires only 1 guard
        liveEntryGuards, shouldChoose = self.populateLiveEntryGuards(1)

        if shouldChoose:
            return random.choice(liveEntryGuards)

        # 2 is really arbitrary by Tor source code
        if len(liveEntryGuards) < 2:
            # Retry relaxing constraints (we dont have many, but this is how
            # Tor does
            self.choose_random_entryguard()
            return self.getGuard()

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

