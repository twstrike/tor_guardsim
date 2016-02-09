from __future__ import print_function

import client
import random
import sys

class Client(object):
    """A stateful client implementation of the guard selection algorithm."""

    def __init__(self, network, stats, parameters):

        # a torsim.Network object.
        self._net = network

        # a ClientParams object
        self._p = parameters

        # all guards we know about. We consider all of them to be directory guards
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
        bandwidths = self.compute_weighted_bandwidths()
        bandwidths = self.scale_array_elements_to_u64(bandwidths)
        idx = self.choose_array_element_by_weight(bandwidths)
        
        if idx < 0: return None 
        return all_guards[idx]

    def scale_array_elements_to_u64(self, bandwidths):
        scale_max = sys.maxint / 4
        total = sum(bandwidths)
        scale_factor = scale_max / total

        return [int(round(i * scale_factor)) for i in bandwidths]

    def choose_array_element_by_weight(self, bandwidths):
        total = sum(bandwidths)

        if len(bandwidths) == 0: return -1
        if total == 0: return random.randint(0, len(bandwidths)-1)

        rand_value = random.randint(0, total-1)

        i = 0
        partial = 0
        for bw in bandwidths:
            partial += bw
            if partial > rand_value: return i
            i += 1

        assert(false)

    def compute_weighted_bandwidths(self):
        weight_scale = 10000
    
        # For GUARD
        wg = 6134.0
        wm = 6134.0
        we = 0.0
        wd = 0.0
        wgb = 10000.0
        wmb = 10000.0
        web = 10000.0
        wdb = 10000.0

        wg /= weight_scale
        wm /= weight_scale
        we /= weight_scale
        wd /= weight_scale
        wgb /= weight_scale
        wmb /= weight_scale
        web /= weight_scale
        wdb /= weight_scale

        bandwidths = []
        for guard in self._ALL_GUARDS:
            bw_in_bytes = guard._node.bandwidth * 1000

            # the weights consider guards to be directory guards
            weight = wgb*wg
            weight_without_guard_flag = wmb*wm

            final_weight = weight*bw_in_bytes

            bandwidths.append(final_weight + 0.5)

        return bandwidths

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

