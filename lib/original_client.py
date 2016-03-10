# -*- coding: utf-8; -*-

from __future__ import print_function

import tor
import random
import simtime
from guard import Guard

# XXX implement prioritize bandwith behavior

# This client always initializes with an empty tor state.
class Client(object):
    """A stateful client implementation of the guard selection algorithm."""

    def __init__(self, network, stats, parameters):

        # a torsim.Network object.
        self._net = network

        # a ClientParams object
        self._p = parameters

        # client statistics
        self._stats = stats

        self._ALL_GUARDS = []

        # guard list for this client, default is 3
        self._GUARD_LIST = []

        # For how long we should keep looping until we find a guard we can use
        # to build a circuit, in number of guards to try
        self._BUILD_CIRCUIT_TIMEOUT = 30

        # Bootstrap Tor
        self.updateGuardLists()
        self.pickEntryGuards(True)

    def updateGuardLists(self):
        """Called at start and when a new consensus should be made & received:
           updates *TOPIC_GUARDS."""

        self.updateListedStatus()

        self.entryGuardsComputeStatus()

    # Mark every Guard we have as listed or unlisted.
    # This is actually for convenience, so we don't have to store the consensus
    # and find a guard by ID to determine if it is listed.
    def updateListedStatus(self):
        for g in self._GUARD_LIST:
            g.markUnlisted()

        self._ALL_GUARDS = []
        for node in self._net.new_consensus():
            guard = Guard.get(node)
            guard.markListed()  # by defition listed is in the latest consensus
            self._ALL_GUARDS.append(guard)

        # Whatever is not in the consensus, we dont know about
        # See nodelist_set_consensus()
        for guard in [g for g in self._GUARD_LIST if not g._listed]:
            g.node._isRunning = False

    def entryGuardsComputeStatus(self):
        self.entryGuardSetStatus()
        self.removeDeadEntryGuards()
        self.removeObsoleteEntryGuards()

    def entryGuardSetStatus(self):
        for guard in self._GUARD_LIST:
            hasReason = not guard._listed or not guard.node._isRunning

            if not hasReason:
                guard._badSince = None
            elif not guard._badSince:
                guard._badSince = simtime.now()

    def removeDeadEntryGuards(self):
        entryGuardRemoveAfter = 30 * 24 * 60 * 60

        toRemove = []
        for guard in self._GUARD_LIST:
            if not guard._badSince: continue
            if guard._badSince + entryGuardRemoveAfter < simtime.now():
                toRemove.append(guard)

        self._GUARD_LIST = [guard for guard in self._GUARD_LIST if guard not in toRemove]

    def removeObsoleteEntryGuards(self):
        guardLifetime = 86400 * 30  # one month, is the minimum

        toRemove = []
        for guard in self._GUARD_LIST:
            if guard._addedAt + guardLifetime < simtime.now():
                toRemove.append(guard)

        self._GUARD_LIST = [guard for guard in self._GUARD_LIST if guard not in toRemove]

    def pickEntryGuards(self, forDirectory):
        numNeeded = self.decideNumGuards(forDirectory)
        while self.numLiveEntryGuards(forDirectory) < numNeeded:
            if not self.addAnEntryGuard(forDirectory): break

    def numLiveEntryGuards(self, forDirectory):
        live = [g for g in self._GUARD_LIST if not (forDirectory and not g._isDirectoryCache) and tor.entry_is_live(g)]
        return len(live)

    def addAnEntryGuard(self, forDirectory):
        g = None

        if not forDirectory:
            g = self.chooseGoodEntryServer()
        else:
            g = self.routerPickDirectoryServer()

        if not g: return None

        # Dont add what it already in the list.
        if g in self._GUARD_LIST: return None

        self._GUARD_LIST.append(g)

        now = simtime.now()
        g._addedAt = random.randint(now - 3600 * 24 * 30, now - 1)

        #assert (tor.entry_is_live(g))

        return g

    def routerPickDirectoryServer(self):
        guards = [g for g in self._ALL_GUARDS if
                  g not in self._GUARD_LIST and g.node._isRunning and g._isDirectoryCache]
        g = random.choice(guards)

        # XXX should we simulate the busy behavior here?
        # if g._isBusy: return None
        return g

    # This is the slowest function in this simulation
    def chooseGoodEntryServer(self):
        allButCurrent = [guard for guard in self._ALL_GUARDS if guard not in self._GUARD_LIST]
        # This is VERY SLOW
        guard = tor.choose_node_by_bandwidth_weights(allButCurrent)
        # guard = random.choice(allButCurrent)
        return guard

    def populateLiveEntryGuards(self, forDirectory):
        numNeeded = self.decideNumGuards(forDirectory)

        liveEntryGuards = []
        for guard in self._GUARD_LIST:
            if forDirectory and not guard._isDirectoryCache: continue
            if not tor.entry_is_live(guard): continue

            liveEntryGuards.append(guard)

            if not guard._madeContact: return (liveEntryGuards, True)
            if len(liveEntryGuards) >= numNeeded: return (liveEntryGuards, True)

        return (liveEntryGuards, False)

    # This is choose_random_entry_impl in tor
    def chooseRandomGuard(self):
        return self.chooseRandomEntryImpl(False)

    def decideNumGuards(self, forDirectory):
        # After bootstrap, Tor requires only 1 guard
        if forDirectory: return 3
        return 1

    def chooseRandomEntryImpl(self, forDirectory):
        # ensure we have something to populate from
        self.pickEntryGuards(forDirectory)
        liveEntryGuards, shouldChoose = self.populateLiveEntryGuards(forDirectory)

        if shouldChoose:
            return random.choice(liveEntryGuards)

        # 2 is really arbitrary by Tor source code
        if len(liveEntryGuards) < 2:
            self.addAnEntryGuard(forDirectory)

        # Retry relaxing constraints (we dont have many, but this is how Tor does)
        return self.chooseRandomEntryImpl(forDirectory)

    def probeGuard(self, guard):
        """If it's up on the network, mark it up.
           With each try, update the failover threshold
           Return true on success, false on failure."""
        up = self._net.probe_node_is_up(guard.node)
        self.markGuard(guard, up)

        self._stats.addExposedTo(guard, simtime.now())

        return up

    def connectToGuard(self, guard):
        """Try to connect to 'guard' -- if it's up on the network, mark it up.
           Return true on success, false on failure."""
        up = self.probeGuard(guard)

        # time pass while connecting, otherwise wont timeout
        simtime.advanceTime(1)

        if up:
            self._stats.addBandwidth(guard.node.bandwidth)

        return up

    def markGuard(self, guard, up):
        guard.mark(up)

    def buildCircuit(self):
        """Try to build a circuit until we succeeded, or timeout."""

        tried = 0
        while tried < self._BUILD_CIRCUIT_TIMEOUT:
            g = self.chooseRandomGuard()
            assert (g)

            succeeded = self.connectToGuard(g)
            if self.entryGuardRegisterConnectStatus(g, succeeded):
                # close any circuits pending on this channel.
                # The channel is left in state OPEN because it did not fail,
                # we just chose not to use it.
                # See: channel_do_open_actions()
                # In our simulation, we just ignore this guard
                # and try again.
                self._stats.failuresUntilSuccess(tried)
                continue

            if succeeded:
                self._stats.failuresUntilSuccess(tried)
                return g # our circuit

            tried += 1

        print("Timed out while trying to build a circuit")
        self._stats.failuresUntilTimeout(tried)
        return False

    # Returns True iff previous guards will be retried later
    def entryGuardRegisterConnectStatus(self, guard, succeeded):
        if guard not in self._GUARD_LIST:
            return False

        now = simtime.now()
        if succeeded:
            if guard._unreachableSince:
                guard._canRetry = False
                guard._unreachableSince = None  # should it be 0?
                guard._lastAttempted = now

            # First contact made with this guard
            if not guard._madeContact:
                guard._madeContact = True
                # We've just added a new long-term entry guard. Perhaps the network just
                # came back? We should give our earlier entries another try too,
                # and close this connection so we don't use it before we've given
                # the others a shot.
                return self.markAllBeforeThisForRetry(guard)
        else:
            if not guard._madeContact:
                # print("Remove guard we never made contact with %s" % guard)
                self._GUARD_LIST = [g for g in self._GUARD_LIST if g != guard]
            elif not guard._unreachableSince:
                guard._unreachableSince = now
                guard._lastAttempted = now
                guard._canRetry = False
            else:
                # We might neet to introduce can_retry
                # This prevents the guard to be tried again. See canTry
                # entry->can_retry = False
                guard._canRetry = False
                guard._lastAttempted = now

        return False

    # Returns True iff previous guards will be retried later
    def markAllBeforeThisForRetry(self, guard):
        # print("Mark all before %s for RETRY" % guard)

        refuseConnection = False
        for g in self._GUARD_LIST:
            if g == guard: break

            if g._madeContact and tor.entry_is_live(guard) and guard._unreachableSince:
                g._canRetry = True
                refuseConnection = True

        return refuseConnection
