#!/usr/bin/python
# -*- coding: utf-8; -*-
#
# This is distributed under cc0. See the LICENCE file distributed along with
# this code.

from __future__ import print_function

from functools import partial
from math import floor

from py3hax import *
from tornet import compareNodeBandwidth
import simtime
import tor
from guard import Guard
import proposal

import pprint

class ClientParams(object):
    """Represents the configuration parameters of the client algorithm, as given
    in proposal 259.
    """

    def __init__(self,
                 PRIORITIZE_BANDWIDTH=True,
                 INTERNET_LIKELY_DOWN_INTERVAL=5,
                 DISJOINT_SETS=False):

        # The number of guards we should consider our primary guards.
        self.N_PRIMARY_GUARDS = 3

        # Time (in minutes) since we tried any of the primary guards
        self.PRIMARY_GUARDS_RETRY_INTERVAL = 3

        # Fraction of the total utopic and dystopic guards we should sample as
        # candidates
        self.SAMPLE_SET_THRESHOLD = 0.02

        # After this ammount of minutes we retry primary guards when we find
        # a functioning guard
        self.INTERNET_LIKELY_DOWN_INTERVAL = INTERNET_LIKELY_DOWN_INTERVAL

        self.PRIORITIZE_BANDWIDTH = PRIORITIZE_BANDWIDTH

class Stats(object):
    """Contains information about the stats of several runs over potentially
    different clients."""

    def __init__(self):
        # Statistics keeping variables:
        self._GUARD_BANDWIDTHS = []
        self._CIRCUIT_FAILURES = 0
        self._CIRCUIT_SUCCESSES = 0
        self._CIRCUIT_HARD_FAILURES = 0

        self._GUARDS_UNTIL_FIRST_SUCCESS = None
        self._FAILURES_UNTIL_FIRST_SUCCESS = None

        self._EXPOSED_TO_GUARDS = []
        self._EXPOSURE_AT = {}

    def addExposedTo(self, guard, when):
        if guard not in self._EXPOSED_TO_GUARDS:
            self._EXPOSED_TO_GUARDS.append(guard)

        exp = self._EXPOSURE_AT[when] = len(self._EXPOSED_TO_GUARDS)

    def successRate(self):
        total = self.totalCircuits()
        return self._CIRCUIT_SUCCESSES / float(total) * 100.0

    def circuitFailures(self):
        return self._CIRCUIT_HARD_FAILURES + self._CIRCUIT_FAILURES

    def circuitSuccesses(self):
        return self._CIRCUIT_SUCCESSES

    def totalCircuits(self):
        return self.circuitFailures() + self.circuitSuccesses()

    def successfulCircuits(self):
        return self._CIRCUIT_SUCCESSES

    def failuresUntilFirstSuccess(self):
        if not self._FAILURES_UNTIL_FIRST_SUCCESS:
            return self.circuitFailures()

        return self._FAILURES_UNTIL_FIRST_SUCCESS

    def exposureUntilFirstSuccess(self):
        if not self._GUARDS_UNTIL_FIRST_SUCCESS:
            return self.guardsExposureAfter(simtime.now())

        return self._GUARDS_UNTIL_FIRST_SUCCESS

    def failuresUntilTimeout(self, num):
        self._CIRCUIT_FAILURES += num-1
        self._CIRCUIT_HARD_FAILURES += 1

    def failuresUntilSuccess(self, num):
        self._CIRCUIT_FAILURES += num
        self._CIRCUIT_SUCCESSES += 1

        if not self._GUARDS_UNTIL_FIRST_SUCCESS:
            self._GUARDS_UNTIL_FIRST_SUCCESS = self.guardsExposureAfter(simtime.now())

        if not self._FAILURES_UNTIL_FIRST_SUCCESS:
            self._FAILURES_UNTIL_FIRST_SUCCESS = self.circuitFailures()

    def averageGuardBandwidth(self):
        if not self._GUARD_BANDWIDTHS:
            return 0

        return (float(sum(self._GUARD_BANDWIDTHS)) /
                float(len(self._GUARD_BANDWIDTHS)))

    def addBandwidth(self, bw):
        self._GUARD_BANDWIDTHS.append(bw)

    def reportCurrentStats(self):
        print(("The network came up... %d circuits failed in the meantime "
               "(%d total due to network failures).") %
              (self._CIRCUIT_FAILURES, self._CIRCUIT_FAILURES_TOTAL))

    def guardsExposureAfter(self, time):
        ticks = self._EXPOSURE_AT.keys()
        ticks.sort()

        exposure = 0
        for t in ticks:
            exposure = self._EXPOSURE_AT[t]
            if t >= time: break

        return exposure


class Client(object):
    """A stateful client implementation of the guard selection algorithm."""

    def __repr__(self):
        return pprint.pformat(vars(self), indent=4, width=1)

    def __init__(self, network, stats, parameters):

        # a torsim.Network object.
        self._net = network

        # a ClientParams object
        self._p = parameters

        self._stats = stats

        self._USED_GUARDS = []
        self._SAMPLED_UTOPIC_GUARDS = []
        self._SAMPLED_DYSTOPIC_GUARDS = []
        self._EXCLUDE_NODES = []

        # All guards in the latest consensus
        self._ALL_GUARDS = []

        # For performance, filters all dystopics when a consensus is received
        self._ALL_DYSTOPIC = []

        # The number of listed primary guards that we prioritise connecting to.
        self.NUM_PRIMARY_GUARDS = 3  # chosen by dice roll, guaranteed to be random

        # For how long we should keep looping until we find a guard we can use
        # to build a circuit, in number of guards to try
        self._BUILD_CIRCUIT_TIMEOUT = 30

        # At bootstrap, we get a new consensus
        self.updateGuardLists()

    def _getGuardsInCurrentConsensus(self):
        Guard.markAllUnlisted()
        guards = []
        for n in list(self._net.new_consensus()):
            guard = Guard.get(n)
            guard.markListed()
            guards.append(guard)

        return guards

    def updateGuardLists(self):
        """Called at start and when a new consensus should be made & received:
           updates *TOPIC_GUARDS."""

        # This is our view of the consensus
        self._ALL_GUARDS = self._getGuardsInCurrentConsensus()
        self._ALL_DYSTOPIC = [dg for dg in self._ALL_GUARDS if dg.node.seemsDystopic()]

    def markGuard(self, guard, up):
        guard.mark(up)

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

    def buildCircuit(self):
        """Try to build a circuit until we succeeded, or timeout."""
        gs = proposal.ChooseGuardAlgorithm(self._p)

        gs.start(self._USED_GUARDS,
            self._SAMPLED_UTOPIC_GUARDS, self._SAMPLED_DYSTOPIC_GUARDS,
            self._EXCLUDE_NODES, self._p.N_PRIMARY_GUARDS,
            self._ALL_GUARDS, self._ALL_DYSTOPIC)

        tried = 0
        while tried < self._BUILD_CIRCUIT_TIMEOUT:
            guard = gs.nextGuard()
            if not guard: continue # state transition
            circuit = self.composeCircuitAndConnect(guard)
            if not gs.shouldContinue(circuit != None):
                gs.end(guard)
                self._stats.failuresUntilSuccess(tried)
                return circuit

            tried += 1

        print("Timed out while trying to build a circuit")
        self._stats.failuresUntilTimeout(tried)
        return False

    def composeCircuitAndConnect(self, guard):
        # Build the circuit data structure.
        # In the simulation we only require the guard to exists. No middle or
        # exit node, so the guard is our circuit.
	if not guard: return None # no guard => no circuit

        # Otherwise, connecting to the circuit is part of building it
        if self.connectAndRegisterStatus(guard):
            return guard # our circuit
        else:
            return None

    def connectAndRegisterStatus(self, guard):
	if not guard: return False

        succeeded = self.connectToGuard(guard)
	self.entryGuardRegisterConnectStatus(guard, succeeded)

        return succeeded

    # See: entry_guard_register_connect_status()
    def entryGuardRegisterConnectStatus(self, guard, succeeded):
        now = simtime.now()
        guard._lastTried = now

        if succeeded:
            if guard._unreachableSince:
                guard._canRetry = False
                guard._unreachableSince = None
                guard._lastAttempted = now

            if not guard._madeContact:
                # tor original code ignores this guard and marks for retry
                # every unreachable guard positioned before it in USED_GUARDS
                # this heuristics attempts to detect network reconnects.
                # For now, we ignore this existing heuristic.
                guard._madeContact = True

        else:
            if not guard._madeContact:
                # tor original code removes this guard from the list, and return
                # This prevents retrying to connect to guards we never made
                # contact. What should we do?
                # For now, we ignore this existing heuristic.
                pass

            if not guard._unreachableSince:
                guard._unreachableSince = now
                guard._lastAttempted = now
                guard._canRetry = False
                guard._madeContact = False
            else:
                guard._canRetry = False
                guard._lastAttempted = now

