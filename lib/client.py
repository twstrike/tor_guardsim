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
        # ...within this many simulated seconds, then "freak out".
        self.TOO_RECENTLY = TOO_RECENTLY

        # wait this long after retrying guards the first time
        self.RETRY_DELAY = RETRY_DELAY
        # wait this much longer (factor) after the first time.
        self.RETRY_MULT = RETRY_MULT

        # prop259: percentage of guards to keep in a guard list (utopic)
        self.UTOPIC_GUARDS_THRESHOLD = 0.005
        # prop259: percentage of guards to keep in a guard list (dystopic)
        self.DYSTOPIC_GUARDS_THRESHOLD = 0.005
        # [prop259] Percentage of UTOPIC_GUARDS we try before also trying
        # the DYSTOPIC_GUARDS.
        self.UTOPIC_GUARDLIST_FAILOVER_THRESHOLD = 0.75
        # [prop259] Percentage of DYSTOPIC_GUARDS we try before concluding
        # that the network is down.
        self.DYSTOPIC_GUARDLIST_FAILOVER_THRESHOLD = 1.00

        # From asn's post and prop259.  This should be a consensus parameter.
        # It stores the number of guards in {U,DYS}TOPIC_GUARDLIST which we
        # (strongly) prefer connecting to above all others.  The ones which we
        # prefer connecting to are those at the top of the
        # {U,DYS}TOPIC_GUARDLIST when said guardlist is ordered in terms of the
        # nodes' measured bandwidth as listed in the most recent consensus.
        self.N_PRIMARY_GUARDS = 3

        # If True, select higher bandwidth guards (rather than random ones) when
        # choosing a new guard.
        self.PRIORITIZE_BANDWIDTH = PRIORITIZE_BANDWIDTH

        # If True, UTOPIC_GUARDS and DISTOPIC_GUARDS are disjoint
        self.DISJOINT_SETS = DISJOINT_SETS

        #Time (in minutes) since we tried any of the primary guards
        self.PRIMARY_GUARDS_RETRY_INTERVAL = 3

        # Time (in minutes)
        self.GUARDS_TRY_TRESHOLD_TIME = 120

        # Percentage of total guards in the latest consensus we want to try in GUARDS_TRY_TRESHOLD_TIME minutes
        self.GUARDS_TRY_TRESHOLD = 0.03


class Guard(object):
    """Represents what a client knows about a guard."""

    def __init__(self, node, pDirectoryCache=0.9):
        # tornet.Node instance
        self._node = node

        # True iff we have marked this node as down.
        self._markedDown = False

        # True iff we have marked this node as up.
        self._markedUp = False

        # True iff the node is listed as a guard in the most recent consensus
        # XXX We are assuming this to be equivalent of
        # node_get_by_id(e->identity) == NULL
        self._listed = True

        # TODO: How is this different from lastAttempted?
        # The timestamp of the last time it tried to connecto to this node.
        self._lastTried = None

        ############################
        #--- From entry_guard_t ---#
        ############################

        # When did we add it (simulated)?
        # XXX is guard._addedAt = entry->chosen_on_date?
        self._addedAt = None

        # Is this node a directory cache?
        # XXX update pDirectoryCache with something closer to reality
        self._isDirectoryCache = random.random() < pDirectoryCache

        # Time when the guard went to a bad state
        # XXX set by pathbias_measure_use_rate() - should we add to simulation?
        # XXX set by add_an_entry_guard()
        self._badSince = None

        # False if we have never connected to this router, True if we have
        # XXX This is set by add_an_entry_guard(), indirectly by learned_bridge_descriptor()
        self._madeContact = None

        # The time at which we first noticed we could not connect to this node
        self._unreachableSince = None

        # None if we can connect to this guard, or the time at which we last
        # failed to connect to this node
        # XXX: I guess this description (from tor) is incorrect, since we mark
        # it as now when a connection succeeds after the guard has been
        # unreachable for some time.
        self._lastAttempted = None

        # Should we retry connecting to this entry, in spite of having it
        # marked as unreachable?
        # XXX this is set by add_an_entry_guard()
        self._canRetry = None

        # XXX should we add path_bias_disabled?

    def __str__(self):
        return "%s" % self._node._id

    @property
    def node(self):
        """Return the underlying torsim.Node object for this guard."""
        return self._node

    def mark(self, up):
        """Mark this guard as up or down because of a successful/unsuccessful
        connection attempt.
        """
        if up:
            if not self._markedUp:
                print("Marked %s (%stopic) up" %
                      (self, "dys" if self._node.seemsDystopic() else "u"))
            self._markedDown = False
            self._markedUp = True
        else:
            if not self._markedDown:
                print("Marked %s (%stopic) down" %
                      (self, "dys" if self._node.seemsDystopic() else "u"))
            self._markedDown = True
            self._markedUp = False

    def markUnlisted(self):
        """Mark this guard as unlisted because it didn't appear in the most
        recent consensus.
        """
        self._listed = False

    def markListed(self):
        """Mark this guard as listed because it did appear in the most recent
        consensus.
        """
        self._listed = True

    def canTry(self):
        """Return true iff we can try to make a connection to this guard."""
        # XXXX this should be extended according to tor code
        return self._listed and not (self._madeContact and self._markedDown)

    def isListed(self):
        """Return true iff the guard is listed in the most recent consensus
        we've seen.
        """
        return self._listed

    def isUp(self):
        """Return true iff the guard is up"""
        return self.node._node_up

    def markForRetry(self):
        """Mark this guard as untried, so that we'll be willing to try it
        again.
        """
        # XXXX We never call this unless _all_ the guards in group seem
        # XXXX down.  But maybe we should give early guards in a list
        # XXXX a chance again after a while?
        self._canRetry = True

    def addedWithin(self, nSec):
        """Return ``True`` iff this guard was added within the last **nSec**
        simulated seconds.
        """
        return self._addedAt + nSec >= simtime.now()

    def isBad(self):
        return self.isListed() or not self.isUp()

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

    def __init__(self, network, stats, parameters):

        # a torsim.Network object.
        self._net = network

        # a ClientParams object
        self._p = parameters

        # lists of current guards in the consensus from the dystopic and
        # utopic sets.  each guard is represented here as a torsim.Node.
        self._DYSTOPIC_GUARDS = self._UTOPIC_GUARDS = None

        # The Node.getID() results for every relay with the Guard flag from
        # the most recent consensus.
        self._ALL_GUARD_NODE_IDS = set()

        # The number of listed primary guards that we prioritise connecting to.
        self.NUM_PRIMARY_GUARDS = 3  # chosen by dice roll, guaranteed to be random

        # lists of Guard objects for the dystopic and utopic guards
        # configured on this client.
        self._PRIMARY_DYS = []
        self._PRIMARY_U = []

        # Internal state for whether we think we're on a dystopic network
        self._dystopic = False
        self._networkAppearsDown = False

        self.updateGuardLists()

        self._stats = stats

    def updateGuardLists(self):
        """Called at start and when a new consensus should be made & received:
           updates *TOPIC_GUARDS."""
        self._DYSTOPIC_GUARDS = []
        self._UTOPIC_GUARDS = []

        # XXXX I'm not sure what happens if a node changes its ORPort
        # XXXX or when the client changes its policies.

        # We get the latest consensus here.
        for node in self._net.new_consensus():
            self._ALL_GUARD_NODE_IDS.add(node.getID())

            if node.seemsDystopic():
                self._DYSTOPIC_GUARDS.append(node)
                if not self._p.DISJOINT_SETS:
                    self._UTOPIC_GUARDS.append(node)
            else:
                self._UTOPIC_GUARDS.append(node)

        # Sort the lists from highest bandwidth to lowest.
        self._UTOPIC_GUARDS.sort(cmp=compareNodeBandwidth, reverse=True)
        self._DYSTOPIC_GUARDS.sort(cmp=compareNodeBandwidth, reverse=True)

        # Now mark every Guard we have as listed or unlisted.
        for lst in (self._PRIMARY_DYS, self._PRIMARY_U):
            for g in lst:
                if g.node.getID() in self._ALL_GUARD_NODE_IDS:
                    g.markListed()
                else:
                    g.markUnlisted()

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

        print("* Will build a circuit")
        guardSelection = ChooseGuardAlgorithm(self._net, self._p)

        # XXX we should save used_guards and pass as parameter
        state = guardSelection.start([], [], self._p.N_PRIMARY_GUARDS)

        # XXX it means we keep trying different guards until we succeed to build
        # a circuit (even if the circuit failed by other reasons)
        while True:
            # XXX will it ALWAYS succeed at returning something?
            guard = guardSelection.nextGuard()

            # XXX this is "circuit = buildCircuitWith(entryGuard)"
            circuit = self.buildCircuitWith(guard)
            if circuit:
                guardSelection.end(guard)

                # Connect to the circuit
                # This is the semantics of buildCircuit in this simulation
                success = self.connectToGuard(guard)
                self.entryGuardRegisterConnectStatus(guard, success)

                return circuit # We want to break the loop
            else:
                # XXX are we supposed to keep trying forever?
                # What guarantees we will find something?
                # return False
                pass

    # XXX What is this supposed to do? Build the circuit data structure, OR 
    # connect to the circuit?
    def buildCircuitWith(self, guard):
        # Build the circuit data structure.
        # In the simulation we only require the guard to exists. No middle or
        # exit node.
        return guard != None

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
            if not guard._unreachableSince:
                guard._unreachableSince = now
                guard._lastAttempted = now 
                guard._canRetry = False
            elif guard._madeContact:
                guard._canRetry = False
                guard._lastAttempted = now

class StatePrimaryGuards(object):
    def __init__(self):
        self._index = -1

    def next(self, context):
        print("StatePrimaryGuards - NEXT")
        print("  len = %d, index = %d" % (len(context._primaryGuards), self._index))

        if len(context._primaryGuards)-1 > self._index:
            self._index += 1
            context._lastReturn = context._primaryGuards[self._index]
        else:
            print("StatePrimaryGuards - ran out of primary")

            # XXX Return what? Should I consider all have been tried?
            # This is not what tried means.
            for g in context._primaryGuards:
                print("- tried: %s, %s" % (g, g._lastTried))

        context.markAsUnreachableAndAddToTriedList(context._primaryGuards)

        if not context.checkTriedTreshold(context._triedGuards):
            return

        if context.allHaveBeenTried():
            context.transitionToPreviousStateOrTryUtopic()

class StateTryUtopic(object):
    # XXX this is supposed to return a guard. How?
    def next(self, context):
        # ???
        context._lastReturn = None

        context.moveOldTriedGuardsToRemainingList()

        guards = [g for g in context._usedGuards if g not in context._primaryGuards]
        context.markAsUnreachableAndAddToTriedList(guards)

        if not context.checkTriedTreshold(context._triedGuards):
            return

        if not context.checkFailover(context._triedGuards,
                context._utopicGuards, context.STATE_TRY_DYSTOPIC):
            return

        context.removeUnavailableRemainingUtopicGuards() 

        # one more time
        if not context.checkTriedTreshold(context._triedGuards):
            return

        if not context.checkFailover(context._triedGuards,
                context._utopicGuards, context.STATE_TRY_DYSTOPIC):
            return

class StateTryDystopic(object):
    # XXX this is supposed to return a guard. How?
    def next(self, context):
        context.moveOldTriedDystopicGuardsToRemainingList()

        distopicGuards = [g for g in context._usedGuards if g._node.seemsDystopic() ]
        guards = [g for g in distopicGuards if g not in context._primaryGuards]
        context.markDystopicAsUnreachableAndAddToTriedList(guards)

        if not context.checkTriedTreshold(context._triedGuards + context._triedDystopicGuards):
            return

        if not context.checkTriedDystopicFailoverAndMarkAllAsUnreachable():
            return

        context.removeUnavailableRemainingDystopicGuards() 
        
        # one more time
        if not context.checkTriedTreshold(context._triedGuards + context._triedDystopicGuards):
            return

        if not context.checkTriedDystopicFailoverAndMarkAllAsUnreachable():
            return

class StateRetryOnly(object):
    # XXX this is supposed to return a guard. How?
    def next(self, context):
        guards = context._triedGuards + context._triedDystopicGuards
        guards.sort(key="_lastTried")

        for g in guards:
            if context.wasNotPossibleToConnect(g):
                context.markAsUnreachable(g)

class ChooseGuardAlgorithm(object):
    def __init__(self, net, params):
        self._net = net
        self._params = params
        self._lastReturn = None

        self.STATE_PRIMARY_GUARDS = StatePrimaryGuards()
        self.STATE_TRY_UTOPIC = StateTryUtopic()
        self.STATE_TRY_DYSTOPIC = StateTryDystopic()
        self.STATE_RETRY_ONLY = StateRetryOnly()

    @property
    def hasFinished(self):
        return self._hasFinished

    def start(self, usedGuards, excludeNodes, nPrimaryGuards, selectDirGuards = False):
        self._hasFinished = False
        self._usedGuards = usedGuards

        excludeNodesSet = set(excludeNodes)
        self._consensus = self._getLatestConsensus()
        self._guards = self._getGuards(selectDirGuards, excludeNodesSet)
        self._utopicGuards = self._guards
        self._dystopicGuards = self._filterDystopicGuardsFrom(self._utopicGuards)
        usedGuardsSet = set(usedGuards)
        self._remainingUtopicGuards = self._utopicGuards - usedGuardsSet
        self._remainingDystopicGuards = self._dystopicGuards - usedGuardsSet
        self._triedGuards, self._triedDystopicGuards = [], []
        self._state = self.STATE_PRIMARY_GUARDS
        self._primaryGuards = self._findPrimaryGuards(usedGuards, self._remainingUtopicGuards, nPrimaryGuards)
        return self._state

    def nextByBandwidth(self, guards):
        return tor.choose_node_by_bandwidth_weights(guards)

    def nextGuard(self):
        haveBeenTriedLately = self._hasAnyPrimaryGuardBeenTriedIn(self._params.PRIMARY_GUARDS_RETRY_INTERVAL)
        if haveBeenTriedLately and self._state != self.STATE_PRIMARY_GUARDS:
            self._previousState = self._state
            self._state = self.STATE_PRIMARY_GUARDS

        self._lastReturn = None
        self._state.next(self)
        print("- will return %s" % self._lastReturn)
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
        guards = list(remaining) # makes a copy
        while len(guards) > 0:
            g = self.nextByBandwidth(guards)
            guards.remove(g)

            if self.markAsUnreachableAndRemoveAndAddToTriedList(g):
                # XXX what guarantees it will be in tried?
                tried.remove(g)

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
        return guard._madeContact == False

    def markAsUnreachable(self, guard):
        guard._unreachableSince = simtime.now()
                
    # XXX should we abort the current state if this transitions to another state?
    def checkTriedTreshold(self, guards):
        timeWindow = simtime.now() - self._params.GUARDS_TRY_TRESHOLD_TIME * 60
        treshold = self._params.GUARDS_TRY_TRESHOLD * len(self._consensus)
        tried = [g for g in guards if g._lastTried and g._lastTried > timeWindow ]
        if len(tried) > treshold:
            self._state = self._STATE_RETRY_ONLY
            return False

        return True

    # XXX should we abort the current state if this transitions to another state?
    def checkFailover(triedGuards, guards, nextState):
        if len(triedGuards) > self._params.GUARDS_FAILOVER_THRESHOLD * guards:
            self._state = nextState
            return False

        return True

    def checkTriedDystopicFailoverAndMarkAllAsUnreachable(self):
        if self.checkFailover(self._triedDystopicGuards,
                self._dystopicGuards, self.STATE_ONLY_RETRY):
            return True

        guards = self._primaryGuards + self._triedGuards + self._triedDystopicGuards
        for g in guards:
            self.markAsUnreachable(g)

    def allHaveBeenTried(self):
        return len([g for g in self._primaryGuards if not g._lastTried]) == 0

    def transitionToPreviousStateOrTryUtopic(self):
            if self._previousState:
                self._state = self._previousState
            else:
                self._state = self.STATE_TRY_UTOPIC

    def end(self, guard):
        # XXX Why?
        self._hasFinished = True
        self._usedGuards.append(guard)

    def giveOneMoreChanceTo(self, tried, remaining):
        timeWindow = simtime.now() - self._params.GUARDS_RETRY_TIME * 60
        guards = [g for g in tried if g._unreacheableSince]
        for g in guards:
            if g.unreacheableSince < timeWindow: remaining.append(g)

    def moveOldTriedGuardsToRemainingList(self):
        self.giveOneMoreChanceTo(self._triedGuards, self._remainingUtopicGuards)

    def moveOldTriedDystopicGuardsToRemainingList(self):
        self.giveOneMoreChanceTo(self._triedDystopicGuards, self._remainingDystopicGuards)

    def _getLatestConsensus(self):
        return self._net.new_consensus()

    def _getGuards(self, selectDirGuards, excludeNodes):
        guards = [n for n in self._consensus if n.V2flag] if selectDirGuards else self._consensus
        guardsLessExclusions = set(guards) - excludeNodes
        return set([Guard(n) for n in guardsLessExclusions])


    def _filterDystopicGuardsFrom(self, guards):
        return set([dg for dg in guards if dg.node.seemsDystopic()])


    def _findPrimaryGuards(self, usedGuards, remainingUtopic, nPrimaryGuards):
        #This is not taking into account the remaining dystopic guards. Is that okay?
        primaryGuards = []
        while len(primaryGuards) < nPrimaryGuards:
            primaryGuards.append(self._nextPrimaryGuard(usedGuards, remainingUtopic))

        return primaryGuards


    def _nextPrimaryGuard(self, usedGuards, remainingUtopic):
        if usedGuards:
            used = list(usedGuards)
            while used:
                guard = used.pop(0)
                #TODO: What if is a bad guard? whatcha gonna do?
                if guard not in self._primary_guards and guard in self._consensus:
                    return guard
        else:
            #TODO: should we weight by bandwidth here? Right now assumes is weighted.
            i = random.randint(0, len(remainingUtopic) - 1)
            return list(remainingUtopic).pop(i)


    def _hasAnyPrimaryGuardBeenTriedIn(self, interval):
        for pg in self._primaryGuards:
            if not pg._lastTried: continue
            if simtime.now() > pg._lastTried + interval * 60:
                return True

        return False
