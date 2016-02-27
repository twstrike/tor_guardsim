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
                 TOO_RECENTLY=86400,
                 RETRY_DELAY=30,
                 RETRY_MULT=2,
                 PRIORITIZE_BANDWIDTH=True,
                 DISJOINT_SETS=False):
        # From asn's post and prop259.  This should be a consensus parameter.
        # It stores the number of guards in {U,DYS}TOPIC_GUARDLIST which we
        # (strongly) prefer connecting to above all others.  The ones which we
        # prefer connecting to are those at the top of the
        # {U,DYS}TOPIC_GUARDLIST when said guardlist is ordered in terms of the
        # nodes' measured bandwidth as listed in the most recent consensus.
        self.N_PRIMARY_GUARDS = 3

        self.GUARDS_RETRY_TIME = 20

        # Time (in minutes) since we tried any of the primary guards
        self.PRIMARY_GUARDS_RETRY_INTERVAL = 3

        # Time (in minutes)
        self.GUARDS_TRY_THRESHOLD_TIME = 120

        # Percentage of total guards in the latest consensus we want to try in GUARDS_TRY_THRESHOLD_TIME minutes
        self.GUARDS_TRY_THRESHOLD = 0.03

        self.GUARDS_FAILOVER_THRESHOLD = 0.02

        self.PRIORITIZE_BANDWIDTH = PRIORITIZE_BANDWIDTH

class Stats(object):
    """Contains information about the stats of several runs over potentially
    different clients."""

    def __init__(self):
        # Statistics keeping variables:
        self._GUARD_BANDWIDTHS = []
        self._CIRCUIT_FAILURES_TOTAL = 0
        self._CIRCUIT_FAILURES = 0

        self._EXPOSED_TO_GUARDS = []
        self._EXPOSURE_AT = {}

    def addExposedTo(self, guard, when):
        if guard not in self._EXPOSED_TO_GUARDS:
            self._EXPOSED_TO_GUARDS.append(guard)

        exp = self._EXPOSURE_AT[when] = len(self._EXPOSED_TO_GUARDS)

    def incrementCircuitFailureCount(self):
        self._CIRCUIT_FAILURES += 1

    def resetCircuitFailureCount(self):
        self._CIRCUIT_FAILURES_TOTAL += self._CIRCUIT_FAILURES
        self._CIRCUIT_FAILURES = 0

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

        #  used guards
        self._usedGuards = []

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

    def updateGuardLists(self):
        """Called at start and when a new consensus should be made & received:
           updates *TOPIC_GUARDS."""

        Guard.markAllUnlisted()

        # We received a new consensus now, and use THIS until we receive a new
        # consensus
        self._ALL_GUARDS = []
        for n in list(self._net.new_consensus()):
            guard = Guard.get(n)
            guard.markListed()
            self._ALL_GUARDS.append(guard)

        # Filter dystopics
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

        gs.start(self._usedGuards, [], self._p.N_PRIMARY_GUARDS,
                 self._ALL_GUARDS, self._ALL_DYSTOPIC)

        tried = 0

        while tried < self._BUILD_CIRCUIT_TIMEOUT:
            guard = gs.nextGuard()
            circuit = self.composeCircuitAndConnect(guard)
            if circuit:
                gs.end(guard)
                return circuit

            tried += 1

        print("Timed out while trying to build a circuit")
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

        # Here we try the same heuristic existing in
        # entry_guard_register_connect_status() on the first contact made to a
        # new guard.
        # See: https://gitweb.torproject.org/tor.git/tree/src/or/entrynodes.c?id=tor-0.2.7.6#n803
	if self.entryGuardRegisterConnectStatus(guard, succeeded):
            # discard this circuit and try a new with previously used guards
	    return self.buildCircuit()

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

            # First contact made with this guard
            if not guard._madeContact:
                guard._madeContact = True
                return self.markAllBeforeThisForRetry(guard)
        else:
            if not guard._madeContact:
                pass  # remove this guard
            elif not guard._unreachableSince:
                guard._unreachableSince = now
                guard._lastAttempted = now
                guard._canRetry = False
                guard._madeContact = False
            else:
                guard._canRetry = False
                guard._lastAttempted = now

	return False

    # Returns true if the circuit we just built should be discarded to retry
    # primary guards with higher preference. This happens so we can detect a
    # network reconnect and try again "better" guards.
    def markAllBeforeThisForRetry(self, guard):
        print("Mark all before %s for RETRY" % guard)

        refuseConnection = False
        for g in self._usedGuards:
            if g == guard: break

            if g._madeContact and tor.entry_is_live(guard) and guard._unreachableSince:
                g._canRetry = True
                refuseConnection = True

        return refuseConnection
