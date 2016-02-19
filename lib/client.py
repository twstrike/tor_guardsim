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
from guard import GetGuard
import proposal

import pprint

class ExponentialTimer(object):
    """Implements an exponential timer using simulated time."""

    def __init__(self, initial, multiplier, action, *args, **kwargs):
        """Create a timer that's ready to fire immediately.

        After it first fires, it won't be ready again until **initial** seconds
        have passed.  Each time after that, it will increase the delay by a
        factor of **multiplier**.
        """
        self._initial_delay = initial
        self._multiplier = multiplier
        self._paused = False

        # This is a callable which should be called when the timer fires.  It
        # should return a bool, and if that is ``False``, then we should
        # reschedule (with exponential delay, of course).  Otherwise, do
        # nothing (because we assume that the scheduled action was successful).
        self.fireAction = partial(action, *args, **kwargs)

        self.reset()

    def pause(self):
        """Pause this timer."""
        self._paused = True

    def unpause(self):
        """Resume this timer."""
        self._paused = False

    def reset(self):
        """Reset the timer to the state when it was first created."""
        self._next = 0
        self._cur_delay = self._initial_delay

    def isReady(self):
        """Return true iff the timer is ready to fire now."""
        if self._paused:
            return False
        return self._next <= simtime.now()

    def fire(self):
        """Fire the timer."""
        assert self.isReady()
        self._next = simtime.now() + self._cur_delay
        self._cur_delay *= self._multiplier
        self.fireAction()


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
        # XXX net._get_consensus() should return Guards
        self._ALL_GUARDS = []

        # For performance, filters all dystopics when a consensus is received
        self._ALL_DYSTOPIC = []

        # The number of listed primary guards that we prioritise connecting to.
        self.NUM_PRIMARY_GUARDS = 3  # chosen by dice roll, guaranteed to be random

        # For how long we should keep looping until we find a guard we can use
        # to build a circuit, in seconds
        self._BUILD_CIRCUIT_TIMEOUT = 30

        # XXX What is buildCircuitWith()?
        # Given the appendix, if this is False the success rate is 0%
        # When we cant connect to the first guard we try.
        self._BUILD_CIRCUIT_WITH_CONNECTS_TO_GUARD = True

        # At bootstrap, we get a new consensus
        self.updateGuardLists()

    def inLatestConsensus(self, guard):
        return guard._listed

    def updateGuardLists(self):
        """Called at start and when a new consensus should be made & received:
           updates *TOPIC_GUARDS."""

        for g in self._usedGuards:
            g.markUnlisted()

        # We received a new consensus now, and use THIS until we receive a new
        # consensus
        self._ALL_GUARDS = []
        for n in list(self._net.new_consensus()):
            guard = GetGuard(n)
            guard.markListed()
            self._ALL_GUARDS.append(guard)

        # Filter dystopics
        self._ALL_DYSTOPIC = [dg for dg in self._ALL_GUARDS if dg._node.seemsDystopic()]

        # Update BAD status for usedGuards
        for g in self._usedGuards:
            g._bad = not self.inLatestConsensus(g)

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

        if up:
            self._stats.addBandwidth(guard.node.bandwidth)

        return up

    def connectAndRegisterStatus(self, guard):
        succeeded = self.connectToGuard(guard)
        self.entryGuardRegisterConnectStatus(guard, succeeded)
        return succeeded

    def buildCircuit(self):
        """Try to build a circuit; return true if we succeeded."""

        g = self.getGuard()

        if self._BUILD_CIRCUIT_WITH_CONNECTS_TO_GUARD:
            return g

        return self.connectAndRegisterStatus(g)

    # XXX What is this supposed to do? Build the circuit data structure, OR 
    # connect to the circuit?
    def buildCircuitWith(self, guard):
        # Build the circuit data structure.
        # In the simulation we only require the guard to exists. No middle or
        # exit node, so the guard is our circuit.
        circuit = guard

        if not self._BUILD_CIRCUIT_WITH_CONNECTS_TO_GUARD:
            return circuit

        # Otherwise, connecting to the circuit is part of building it
        if self.connectAndRegisterStatus(guard):
            return circuit
        else:
            return None 

    # XXX This is choose_random_entry_impl in tor
    def getGuard(self):
        guardSelection = proposal.ChooseGuardAlgorithm(self._p)

        # XXX we should save used_guards and pass as parameter
        guardSelection.start(self._usedGuards, [], self._p.N_PRIMARY_GUARDS,
                             self._ALL_GUARDS, self._ALL_DYSTOPIC)

        # XXX it means we keep trying different guards until we succeed to build
        # a circuit (even if the circuit failed by other reasons)
        tries = 0
        startTime = simtime.now()
        while True:
            if simtime.now() - startTime > self._BUILD_CIRCUIT_TIMEOUT:
                print("Timed out while trying to build a circuit")
                return False

            # XXX will it ALWAYS succeed at returning something?
            guard = guardSelection.nextGuard()
            tries += 1

            if tries % 100 == 0:
                print("We tried 100 without success")

            circuit = self.buildCircuitWith(guard)
            if circuit:
                guardSelection.end(guard)

                # Copy used guards so it can be used in the next START
                self._usedGuards = list(guardSelection._usedGuards)

                return circuit  # We want to break the loop
            else:
                # XXX are we supposed to keep trying forever?
                # What guarantees we will find something?
                return False

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

