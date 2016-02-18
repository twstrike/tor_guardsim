#!/usr/bin/python
# -*- coding: utf-8; -*-
#
# This is distributed under cc0. See the LICENCE file distributed along with
# this code.

from __future__ import print_function

import random

from functools import partial
from math import floor

from py3hax import *
from tornet import compareNodeBandwidth
import simtime
import tor
from guard import GetGuard

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
        self.GUARDS_TRY_TRESHOLD_TIME = 120

        # Percentage of total guards in the latest consensus we want to try in GUARDS_TRY_TRESHOLD_TIME minutes
        self.GUARDS_TRY_TRESHOLD = 0.03

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

        # used guards
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

    # XXX There used to be getGuard (choose_random_entry_impl in tor)
    # but this new structure seems to make it harder
    # Should it be the while? Is so, when should it stop?
    def buildCircuit(self):
        """Try to build a circuit; return true if we succeeded."""

        guardSelection = ChooseGuardAlgorithm(self._net, self._p)

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
                return circuit # We want to break the loop
            else:
                # XXX are we supposed to keep trying forever?
                # What guarantees we will find something?
                return False

    # XXX What is this supposed to do? Build the circuit data structure, OR 
    # connect to the circuit?
    def buildCircuitWith(self, guard):
        # Build the circuit data structure.
        # In the simulation we only require the guard to exists. No middle or
        # exit node.
        if not guard: return None

        # Connect to the circuit
        # This is the semantics of buildCircuit in this simulation
        success = self.connectToGuard(guard)
        self.entryGuardRegisterConnectStatus(guard, success)

        # XXX If this is buildCircuit, success = False means we failed to build
        # the circuit, but we are not terminating the While, so it will never
        # be reported

        return success

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
                pass # remove this guard
            elif not guard._unreachableSince:
                guard._unreachableSince = now
                guard._lastAttempted = now
                guard._canRetry = False
                guard._madeContact = False
            else:
                guard._canRetry = False
                guard._lastAttempted = now


def returnEachEntryInTurn(guards, turn):
    g = None
    if len(guards) > turn+1:
        turn += 1
        g = guards[turn]

    return (g, turn)


# XXX Maybe this is what it means
def returnEachEntryInTurnImNotSure(guards, context):
    for g in guards:
        # XXX this is not clear in the spec
        if not context.wasNotPossibleToConnect(g):
            return g

class StatePrimaryGuards(object):
    def __init__(self):
        self._turn = -1

    def next(self, context):
        #print("StatePrimaryGuards - NEXT")

        context._lastReturn, self._turn = returnEachEntryInTurn(context._primaryGuards,
                self._turn)

        context.markAsUnreachableAndAddToTriedList(context._primaryGuards)

        if not context.checkTriedTreshold(context._triedGuards):
            return

        if context.allHaveBeenTried():
            context.transitionToPreviousStateOrTryUtopic()


class StateTryUtopic(object):
    def __init__(self):
        self._turn = -1
        self._remaining = []

    def next(self, context):
        #print("StateTryUtopic - NEXT")

        # XXX This should add back to REMAINING_UTOPIC_GUARDS
        # When are they taken from REMAINING_UTOPIC_GUARDS?
        context.moveOldTriedGuardsToRemainingList()

        # XXX When are USED_GUARDS removed from PRIMARY_GUARDS?
        # Is not PRIMARY_GUARDS built from USED_GUARDS preferably?
        guards = [g for g in context._usedGuards if g not in context._primaryGuards]
        context._lastReturn, self._turn = returnEachEntryInTurn(guards, self._turn)

        context.markAsUnreachableAndAddToTriedList(guards)

        if not context.checkTriedTreshold(context._triedGuards):
            return

        if not context.checkFailover(context._triedGuards,
                                     context._utopicGuards, context.STATE_TRY_DYSTOPIC):
            return

	# Return each entry from REMAINING_UTOPIC_GUARDS using
  	# NEXT_BY_BANDWIDTH. For each entry, if it was not possible to connect
  	# to it, remove the entry from REMAINING_UTOPIC_GUARDS, mark it as
  	# unreachable and add it to TRIED_GUARDS.
	# XXX Does it mean if we have something to return by this point,
        # we should not proceed?
        # I'll assume so.
        if context._lastReturn:
            return

        if not self._remaining: self._remaining = list(context._remainingUtopicGuards)
        if len(self._remaining) > 0:
            g = context.nextByBandwidth(self._remaining)
            self._remaining.remove(g)
            context._lastReturn = g

        context.removeUnavailableRemainingUtopicGuards()

        # one more time
        if not context.checkTriedTreshold(context._triedGuards):
            return

        if not context.checkFailover(context._triedGuards,
                                     context._utopicGuards, context.STATE_TRY_DYSTOPIC):
            return


class StateTryDystopic(object):
    def __init__(self):
        self._turn = -1
        self._remaining = []

    def next(self, context):
        #print("StateTryDystopic - NEXT")

        context.moveOldTriedDystopicGuardsToRemainingList()

        distopicGuards = [g for g in context._usedGuards if g._node.seemsDystopic()]
        guards = [g for g in distopicGuards if g not in context._primaryGuards]
        context._lastReturn, self._turn = returnEachEntryInTurn(guards, self._turn)

        context.markDystopicAsUnreachableAndAddToTriedList(guards)

        if not context.checkTriedTreshold(context._triedGuards + context._triedDystopicGuards):
            return

        if not context.checkTriedDystopicFailoverAndMarkAllAsUnreachable():
            return

        # Return each entry from REMAINING_DYSTOPIC_GUARDS using
        # NEXT_BY_BANDWIDTH. For each entry, if it was not possible to connect
        # to it, remove the entry from REMAINING_DYSTOPIC_GUARDS, mark it as
        # unreachable and add it to TRIED_DYSTOPIC_GUARDS.
	# XXX Does it mean if we have something to return by this point,
        # we should not proceed?
        # I'll assume so.
        if context._lastReturn:
            return

        if not self._remaining: self._remaining = list(context._remainingDystopicGuards)
        if len(self._remaining) > 0:
            g = context.nextByBandwidth(self._remaining)
            self._remaining.remove(g)
            context._lastReturn = g

        context.removeUnavailableRemainingDystopicGuards()

        # one more time
        if not context.checkTriedTreshold(context._triedGuards + context._triedDystopicGuards):
            return

        if not context.checkTriedDystopicFailoverAndMarkAllAsUnreachable():
            return


class StateRetryOnly(object):
    def __init__(self):
        self._turn = -1

    def next(self, context):
        #print("StateRetryOnly - NEXT")
        guards = context._triedGuards + context._triedDystopicGuards
        guards.sort(key=lambda g: g._lastTried)

        context._lastReturn, self._turn = returnEachEntryInTurn(guards, self._turn)

class ChooseGuardAlgorithm(object):
    def __repr__(self):
        vals = vars(self)
        filtered = { k: vals[k] for k in [
            "_hasFinished", "_state", "_previousState", "_primaryGuards", "_triedGuards"]
        }
        return pprint.pformat(filtered, indent=4, width=1)

    def __init__(self, net, params):
        self._net = net
        self._params = params
        
        self._primaryGuards = []
        self._guardsInConsensus = []
        self._dystopicGuardsInConsensus = []

        self._lastReturn = None
        self._previousState = None

        self.STATE_PRIMARY_GUARDS = StatePrimaryGuards()
        self.STATE_TRY_UTOPIC = StateTryUtopic()
        self.STATE_TRY_DYSTOPIC = StateTryDystopic()
        self.STATE_RETRY_ONLY = StateRetryOnly()

    @property
    def hasFinished(self):
        return self._hasFinished

    def start(self, usedGuards, excludeNodes, nPrimaryGuards, guardsInConsensus, dystopicGuardsInConsensus, selectDirGuards = False):
        self._hasFinished = False
        self._usedGuards = usedGuards

        excludeNodesSet = set(excludeNodes)
        self._guardsInConsensus = list(guardsInConsensus)
        self._dystopicGuardsInConsensus = list(dystopicGuardsInConsensus)

        self._guards = self._getGuards(selectDirGuards, excludeNodesSet)
        self._utopicGuards = self._guards

        # XXX This is also slow. Takes ~5.385 seconds cummulative.
        # We could split utopic/dystopic once per consensus received
        # self._dystopicGuards = self._filterDystopicGuardsFrom(self._utopicGuards)
        self._dystopicGuards = self._filterDystopicGuards(selectDirGuards, excludeNodesSet)

        usedGuardsSet = set(usedGuards)
        self._remainingUtopicGuards = self._utopicGuards - usedGuardsSet
        self._remainingDystopicGuards = self._dystopicGuards - usedGuardsSet
        self._triedGuards, self._triedDystopicGuards = [], []
        self._state = self.STATE_PRIMARY_GUARDS
        self._findPrimaryGuards(usedGuards, self._remainingUtopicGuards, nPrimaryGuards)

    # XXX This is slow
    def nextByBandwidth(self, guards):
        return tor.choose_node_by_bandwidth_weights(guards)

    # XXX How should the transition happen?
    # Immediately, or on the next call to NEXT?
    def transitionTo(self, state):
        return self.transitionOnNextCall(state)
        # return self.transitionImmediatelyTo(state)

    def transitionOnNextCall(self, state):
        print("! Transitioned to %s" % state)
        self._state = state
        return False # should not continue execution

    def transitionImmediatelyTo(self, state):
        self.transitionTo(state)
        return self.nextGuard()

    def nextGuard(self):
        haveBeenTriedLately = self._hasAnyPrimaryGuardBeenTriedIn(self._params.PRIMARY_GUARDS_RETRY_INTERVAL)
        if haveBeenTriedLately and self._state != self.STATE_PRIMARY_GUARDS:
            self._previousState = self._state
            self.transitionTo(self.STATE_PRIMARY_GUARDS)

        self._lastReturn = None
        self._state.next(self)

        return self._lastReturn

    def removeUnavailableRemainingUtopicGuards(self):
        self.removeUnavailableRemainingAndMarkUnreachableAndAddToTried(
            self._remainingUtopicGuards, self._triedGuards)

    def removeUnavailableRemainingDystopicGuards(self):
        self.removeUnavailableRemainingAndMarkUnreachableAndAddToTried(
            self._remainingDystopicGuards, self._triedDystopicGuards)

    def removeUnavailableRemainingAndMarkUnreachableAndAddToTried(self, remaining, tried):
        # XXX What is the difference of doing this by bandwidth if we are not
        # returning anything?
        # Does it make any difference if we are removing and marking in a different order?
        guards = list(remaining)  # makes a copy
        while len(guards) > 0:
            g = self.nextByBandwidth(guards)
            if self.markAsUnreachableAndRemoveAndAddToTriedList(g, tried):
                guards.remove(g)

    def markAsUnreachableAndRemoveAndAddToTriedList(self, guard, triedList):
        if not self.wasNotPossibleToConnect(guard):
            return None

        self.markAsUnreachable(guard)
        triedList.append(guard)
        return guard

    def markAsUnreachableAndAddToTriedList(self, guards):
        for pg in guards:
            self.markAsUnreachableAndRemoveAndAddToTriedList(pg, self._triedGuards)

    def markDystopicAsUnreachableAndAddToTriedList(self, guards):
        for pg in guards:
            self.markAsUnreachableAndRemoveAndAddToTriedList(pg, self._triedDystopicGuards)

    def wasNotPossibleToConnect(self, guard):
        return guard._unreachableSince != None
        #return guard._madeContact == False

    def markAsUnreachable(self, guard):
        if not guard._unreachableSince:
            guard._unreachableSince = simtime.now()

    # XXX should we abort the current state if this transitions to another state?
    def checkTriedTreshold(self, guards):
        timeWindow = simtime.now() - self._params.GUARDS_TRY_TRESHOLD_TIME * 60
        treshold = self._params.GUARDS_TRY_TRESHOLD * len(self._guardsInConsensus)
        tried = [g for g in guards if g._lastTried and g._lastTried > timeWindow]
        if len(tried) > treshold:
            return self.transitionTo(self.STATE_RETRY_ONLY)

        return True

    # XXX should we abort the current state if this transitions to another state?
    def checkFailover(self, triedGuards, guards, nextState):
        if len(triedGuards) > self._params.GUARDS_FAILOVER_THRESHOLD * len(guards):
            return self.transitionTo(nextState)

        return True

    def checkTriedDystopicFailoverAndMarkAllAsUnreachable(self):
        if self.checkFailover(self._triedDystopicGuards,
                              self._dystopicGuards, self.STATE_RETRY_ONLY):
            return True

        guards = self._primaryGuards + self._triedGuards + self._triedDystopicGuards
        for g in guards:
            self.markAsUnreachable(g)

    def allHaveBeenTried(self):
        return len([g for g in self._primaryGuards if not g._lastTried]) == 0

    def transitionToPreviousStateOrTryUtopic(self):
            if self._previousState:
                return self.transitionTo(self._previousState)
            else:
                return self.transitionTo(self.STATE_TRY_UTOPIC)

    def end(self, guard):
        # XXX Why?
        self._hasFinished = True
        if guard not in self._usedGuards: self._usedGuards.append(guard)

    def giveOneMoreChanceTo(self, tried, remaining):
        timeWindow = simtime.now() - self._params.GUARDS_RETRY_TIME * 60
        guards = [g for g in tried if g._unreachableSince]
        for g in guards:
            if g._unreachableSince < timeWindow:
                g._canRetry = True
                remaining.append(g)

    def moveOldTriedGuardsToRemainingList(self):
        self.giveOneMoreChanceTo(self._triedGuards, self._remainingUtopicGuards)

    def moveOldTriedDystopicGuardsToRemainingList(self):
        self.giveOneMoreChanceTo(self._triedDystopicGuards, self._remainingDystopicGuards)

    def filterGuards(self, guards, selectDirGuards, excludeNodes):
        # Optimize happy path
        if not selectDirGuards and not excludeNodes:
            return [g for g in guards if tor.entry_is_live(g)]

        # XXX they should be entry_is_live(g)
        return [g for g in guards if not (selectDirGuards and not g._isDirectoryCache) and not g._node in excludeNodes]

    def _getGuards(self, selectDirGuards, excludeNodesSet):
        guards = self.filterGuards(self._guardsInConsensus, selectDirGuards, excludeNodesSet)
        return set(guards)

    def _filterDystopicGuards(self, selectDirGuards, excludeNodesSet):
        guards = self.filterGuards(self._dystopicGuardsInConsensus, selectDirGuards, excludeNodesSet)
        return set(guards)

    def _filterDystopicGuardsFrom(self, guards):
        return set([dg for dg in guards if dg._node.seemsDystopic()])

    # XXX This is slow
    def _findPrimaryGuards(self, usedGuards, remainingUtopic, nPrimaryGuards):
        #This is not taking into account the remaining dystopic guards. Is that okay?
        used = list(usedGuards)
        remaining = list(remainingUtopic)
        while len(self._primaryGuards) < nPrimaryGuards:
            g = self._nextPrimaryGuard(used, remaining)

            # From proposal:
            # If any PRIMARY_GUARDS have become bad, remove the guard from
            # PRIMARY_GUARDS. Then ensure that PRIMARY_GUARDS contain
            # N_PRIMARY_GUARDS entries by repeatedly calling NEXT_PRIMARY_GUARD.
            if not g or g._bad: continue

            self._primaryGuards.append(g)

    # XXX This is slow
    def _nextPrimaryGuard(self, usedGuards, remainingUtopic):
        if usedGuards:
            while usedGuards:
                guard = usedGuards.pop(0)

                #TODO: What if is a bad guard? whatcha gonna do?
                if guard not in self._primaryGuards and guard in self._guardsInConsensus:
                    return guard
        else:
            # XXX should we remove the chosen from remaining?
            # choose weighted by BW (disabled for performance)
            # we can optimize by calculating the bw weights only once (outside
            # of this function)
            # return tor.choose_node_by_bandwidth_weights(remainingUtopic)
            return random.choice(remainingUtopic)

    # we should first check if it
    #   was at least PRIMARY_GUARDS_RETRY_INTERVAL minutes since we tried
    #     any of the PRIMARY_GUARDS
    def _hasAnyPrimaryGuardBeenTriedIn(self, interval):
        now = simtime.now()
        for pg in self._primaryGuards:
            if not pg._lastTried: continue
            if pg._lastTried + interval * 60 < now:
                return True

        return False
