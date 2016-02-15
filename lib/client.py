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

        # The time at which we failed to connect to this node
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

        self._networkDownRetryTimer = ExponentialTimer(
            parameters.RETRY_DELAY,
            parameters.RETRY_MULT,
            self.retryNetwork,
        )
        self._networkDownRetryTimer.pause()

        self._primaryGuardsRetryTimer = ExponentialTimer(
            3600, # 60 minutes
            0,    # linear?
            self.retryPrimaryGuards)

        # Internal state for whether we think we're on a dystopic network
        self._dystopic = False
        self._networkAppearsDown = False

        self.updateGuardLists()

        self._stats = stats

    @property
    def _state(self):
        """Returns a string describing whether we're dystopic or utopic."""
        return "utopic" if self.inAUtopia else "dystopic"

    @property
    def runningGuards(self):
        if self._p.DISJOINT_SETS:
            return len(self._UTOPIC_GUARDS) + len(self._DYSTOPIC_GUARDS)

        return len(self._UTOPIC_GUARDS)

    @property
    def guardsThresholdDystopic(self):
        return floor(self.runningGuards * self._p.DYSTOPIC_GUARDS_THRESHOLD)

    @property
    def guardsThresholdUtopic(self):
        return floor(self.runningGuards * self._p.UTOPIC_GUARDS_THRESHOLD)

    @property
    def guardsThreshold(self):
        """Determine our ``{U,DYS}TOPIC_GUARDS_THRESHOLD``.

        ``{U,DYS}TOPIC_GUARDS_THRESHOLD`` is interpreted as an float
        representing the percentage of the total running entry guards from the
        most recent consensus to which the client will attempt to connect, and
        these two numbers are multiplied and then floored to arrive at the
        maximum number of attempted entry guards.

        :rtype: int
        :returns: The maximum number of guards in either the
            ``UTOPIC_GUARDLIST`` or the ``DYSTOPIC_GUARDLIST`` to which this
            :class:`Client` will consider connecting.
        """
        if self.inADystopia:
            return self.guardsThresholdDystopic
        else:
            return self.guardsThresholdUtopic

    @property
    def canAddPrimaryDystopicGuard(self):
        """Returns True if we haven't hit guardsThresholdDystopic."""
        return len(self.primaryDystopicGuards) < self.guardsThresholdDystopic

    @property
    def canAddPrimaryUtopicGuard(self):
        """Returns True if we haven't hit guardsThresholdUtopic."""
        return len(self.primaryUtopicGuards) < self.guardsThresholdUtopic

    @property
    def canAddPrimaryGuard(self):
        """Returns True if we haven't hit guardsThreshold."""
        if self.inAUtopia:
            return self.canAddPrimaryUtopicGuard
        return self.canAddPrimaryDystopicGuard

    @property
    def inADystopia(self):
        """Returns ``True`` if we think we're on a dystopic network."""
        return self._dystopic

    @inADystopia.setter
    def inADystopia(self, dystopic):
        """Record whether or not we think we're on a dystopic network.

        :param bool dystopic: Should be ``True`` if we think we're on
            a dystopic network, and ``False`` otherwise.
        """
        self._dystopic = bool(dystopic)
        if self._dystopic:
            print("We're in a dystopia...")

    @property
    def inAUtopia(self):
        """Returns ``True`` if we think we're on a *non-dystopic* network."""
        return not self._dystopic

    @inAUtopia.setter
    def inAUtopia(self, utopic):
        """Record whether or not we think we're on a *non-dystopic* network.

        :param bool utopic: Should be ``True`` if we think we're on
            a *non-dystopic* network, and ``False`` otherwise.
        """
        self._dystopic = not bool(utopic)
        if not self._dystopic:
            print("We're in a utopia...")

    @property
    def networkAppearsDown(self):
        """``True`` if we think the network is down. ``False`` otherwise."""
        return self._networkAppearsDown

    @networkAppearsDown.setter
    def networkAppearsDown(self, isDown):
        """Set whether or not we think the `system is down`__.

        .. _: http://www.homestarrunner.com/systemisdown.html
        """
        # If we're flipping state from the network being up to down, then
        # reschedule a retry timer and unpause it:
        if not self._networkAppearsDown and bool(isDown):
            print("The network went down...")
            self._networkDownRetryTimer.reset()
            self._networkDownRetryTimer.unpause()
        # If we're flipping the state from down to up, then pause the retry
        # timer:
        elif self._networkAppearsDown and not bool(isDown):
            self._stats.reportCurrentStats()
            self._stats.resetCircuitFailureCount()
            self._networkDownRetryTimer.pause()

        self._networkAppearsDown = bool(isDown)

    @property
    def hasAnyPrimaryDystopicGuardsUp(self):
        """Returns True if any of _PRIMARY_DYS are up."""
        return bool(sum([g._markedUp for g in self.primaryDystopicGuards]))

    @property
    def hasAnyPrimaryUtopicGuardsUp(self):
        """Returns True if any of _PRIMARY_U are up."""
        return bool(sum([g._markedUp for g in self.primaryUtopicGuards]))

    @property
    def hasAnyPrimaryGuardsUp(self):
        """Returns True if any of our utopic **or** dystopic primary guards are up."""
        return bool(sum([g._markedUp for g in self.allPrimaryGuards]))

    @property
    def hasAnyCurrentPrimaryGuardsUp(self):
        """Returns True if any of our current primary guards are up."""
        return bool(sum([g._markedUp for g in self.currentPrimaryGuards]))

    @property
    def primaryDystopicGuards(self):
        """Get the list of dystopic guards which we should prioritise trying."""
        return self._PRIMARY_DYS

    @property
    def primaryUtopicGuards(self):
        """Get the list of utopic guards which we should prioritise trying."""
        return self._PRIMARY_U

    @property
    def allPrimaryGuards(self):
        """Get a combined list of primary utopic and dystopic guards."""
        return self.primaryDystopicGuards + self.primaryUtopicGuards

    @property
    def currentPrimaryGuards(self):
        """Get the list of primary guards for the current utopia/dystopia setting."""
        if self.inADystopia:
            return self.primaryDystopicGuards
        return self.primaryUtopicGuards

    def checkFailoverThreshold(self):
        """From prop259:

        5.a. When the GUARDLIST_FAILOVER_THRESHOLD of the UTOPIC_GUARDLIST has
             been tried (without success), Alice should begin trying steps 1-4
             with entry guards from the DYSTOPIC_GUARDLIST as well.  Further,
             if no nodes from UTOPIC_GUARDLIST work, and it appears that the
             DYSTOPIC_GUARDLIST nodes are accessible, Alice should make a note
             to herself that she is possibly behind a fascist firewall.
        """
        if not self.canAddPrimaryGuard:
            if not self.hasAnyCurrentPrimaryGuardsUp:
                print("We already have %d %s guards and can't add more… " %
                      (self.guardsThreshold, self._state))

            if self.inAUtopia and not self.hasAnyPrimaryUtopicGuardsUp:
                self.inADystopia = True
            elif self.inADystopia and not self.hasAnyPrimaryDystopicGuardsUp:
                self.networkAppearsDown = True

            return False
        return True

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

    def getFullList(self):
        """Get the list of possible Nodes from the consensus for a given
           dystopia setting"""
        if self.inADystopia:
            return self._DYSTOPIC_GUARDS
        else:
            return self._UTOPIC_GUARDS

    def addNewGuard(self):
        """Pick a Node and add it to our list of primary(?) Guards.

        XXXX Should it be added to the primary list or is it not a primary
        guard?  If we're picking a random guard, then that means the primary
        ones probably weren't working… so is this a secondary one?
        """

        # 1. [prop259]: Check that we have not already attempted
        # to add too many guards.  If we've added too many guards too recently,
        # then boo-hoo-hoo no tor for you.
        nTriedRecently = 0
        for guard in self.currentPrimaryGuards:
            if guard.addedWithin(self._p.TOO_RECENTLY):
                nTriedRecently += 1
            if nTriedRecently >= self.guardsThreshold:
                return

        # check the threshold before chosing a guard
        if not self.checkFailoverThreshold():
            return

        possible = self.getFullList()
        unused = [n for n in possible if not
                  self.nodeIsInGuardList(n, self.currentPrimaryGuards)]

        if self._p.PRIORITIZE_BANDWIDTH:
            node = unused[0]
        else:
            node = random.choice(unused)

        self.addGuard(node)

    def addGuard(self, node):
        """Try to add a single Node 'node' to the current primary guard list."""
        guard = Guard(node)
        print(("Picked new (%stopic) guard: %s" %
               ("dys" if node.seemsDystopic() else "u", guard)))

        lst = self.currentPrimaryGuards
        lst.append(guard)

    def nodeIsInGuardList(self, n, gl):
        """Return true iff there is a Guard in 'gl' corresponding to the Node
           'n'."""
        for g in gl:
            if g.node == n:
                return True
        return False

    def markGuard(self, guard, up):
        guard.mark(up)

        # If a utopic guard is up, and we previously thought we were in a
        # dystopia, then we must have left the dystopia.
        if up:
            if self.networkAppearsDown:
                self.networkAppearsDown = False

            if not guard.node.seemsDystopic() and self.inADystopia:
                print("A utopic guard suddenly worked while we thought we were "
                      "in a dystopia...")
                self.inAUtopia = True

    def retryNetwork(self, *args, **kwargs):
        """Assuming the network was down, retry from step #0."""
        if not self.networkAppearsDown:
            return

        print("Retrying the network...")
        if self.currentPrimaryGuards and not self.hasAnyCurrentPrimaryGuardsUp:
            print("All %s guards are down!" % self._state)
            self.checkFailoverThreshold()

        # The detection for if we've left the dystopic is done in markGuard().
        if self.networkAppearsDown:
            self.networkAppearsDown = False

        self.getGuardImpl()

    def maybeCheckNetwork(self):
        """In the actual implementation, this functionality should look (in some
        cross-platform manner) to see if we have a network interface available
        which has some plausibly-seeming configured route.
        """
        if self._networkDownRetryTimer.isReady():
            self._networkDownRetryTimer.fire()

    def retryPrimaryGuards(self):
        """Retry our primary guards (from both PRIMARY_UTOPIC_GUARDS and
        PRIMARY_DISTOPIC_GUARDS).

        Cf. prop259 §2, step #2:
            |
            | 2. Then, if the PRIMARY_GUARDS on our list are marked offline,
            | the algorithm attempts to retry them, to ensure that they were not
            | flagged offline erroneously when the network was down.  This retry
            | attempt happens only once every 20 mins to avoid infinite loops.
            |
        """
        print("Retrying primary guards. We're currently %s." % self._state)

        for guard in self.currentPrimaryGuards:
            if guard._markedDown:
                print("Primary %s guard %s was marked down, marking for retry…"
                      % (self._state, guard))
                guard.markForRetry()

    def getGuard(self):
        """We're about to build a circuit: return a guard to try."""

        # 0. Determine if the local network is potentially accessible.
        self.maybeCheckNetwork()
        if self.networkAppearsDown:
            print("The network is (still) down...")
            return

        # 2. [prop259]: If the PRIMARY_GUARDS on our list are marked offline,
        # the algorithm attempts to retry them, to ensure that they were not
        # flagged offline erroneously when the network was down.  This retry
        # attempt happens only once every 20 mins to avoid infinite loops.
        #
        # (This step happens in automatically, because
        # Client.retryPrimaryGuards() is called every sixty minutes on a
        # timer. )

        # 3. Take the list of all available and fitting entry guards and return
        # the top one in the list.
        return self.getGuardImpl()


    def getGuardImpl(self):
        #XXXX Need an easy way to say that the UTOPIC_GUARDS includes
        # routers advertised on 80/443.
        guards = filter(lambda g: g.canTry(), self.currentPrimaryGuards)

        # XXXX [prop259] ADD_TO_SPEC
        # 3.5. If we should retry our primary guards, then do so.
        if not guards:
            if self._primaryGuardsRetryTimer.isReady():
                self._primaryGuardsRetryTimer.fire()
                guards = filter(lambda g: g.canTry(), self.currentPrimaryGuards)

        # 4. If there were no available entry guards, the algorithm adds a new entry
        # guard and returns it.  [XXX detail what "adding" means]
        if not guards:
            self.addNewGuard()
            guards = filter(lambda g: g.canTry(), self.currentPrimaryGuards)

        # Use the first guard that works.
        
        for guard in guards:
            if self.probeGuard(guard):
                return guard
            
    def probeGuard(self, guard):
        """If it's up on the network, mark it up.
           With each try, update the failover threshold
           Return true on success, false on failure."""
        up = self._net.probe_node_is_up(guard.node)
        self.markGuard(guard, up)
        self.checkFailoverThreshold()

        self._stats.addExposedTo(guard, simtime.now())

        return up

    def connectToGuard(self, guard):
        """Try to connect to 'guard' -- if it's up on the network, mark it up.
           Return true on success, false on failure."""
        up = self._net.probe_node_is_up(guard.node)
        self.markGuard(guard, up)

        if up:
            self._stats.addBandwidth(guard.node.bandwidth)

        return up

    def buildCircuit(self):
        """Try to build a circuit; return true if we succeeded."""
        self.maybeCheckNetwork()

        if self.networkAppearsDown:
            self._stats.incrementCircuitFailureCount()
            return False

        g = self.getGuard()
        algo = ChooseGuardAlgorithm(self._net, self._p)
        initialState = algo.start([], [], self._p.N_PRIMARY_GUARDS)
        while not algo.hasFinished:
            entryGuard = algo.nextGuard(initialState)
            #TODO: if circuite was built, then:
            algo.end()

        if not g:
            return False
        return self.connectToGuard(g)


class StatePrimaryGuards(object):

    def next(self, context):
        context.markAsUnreachableAndAddToTriedList(context._primaryGuards)

        context.checkTriedTreshold(context._triedGuards)

        if context.allHaveBeenTried():
            context.transitionToPreviousStateOrTryUtopic()


class StateTryUtopic(object):

    def next(self, context):
        context.moveOldTriedGuardsToRemainingList()

        guards = [g for g in context._usedGuards if g not in context._primaryGuards]
        context.markAsUnreachableAndAddToTriedList(guards)

        context.checkTriedTreshold(context._triedGuards)
        context.checkFailover(context._triedGuards, context._utopicGuards, context.STATE_TRY_DYSTOPIC)
        context.removeUnavailableRemainingUtopicGuardsByBandwidth() 

        # XXX Is this duplicate by mistake?
        context.checkTriedTreshold(context._triedGuards)
        context.checkFailover(context._triedGuards, context._utopicGuards, context.STATE_TRY_DYSTOPIC)

class StateTryDystopic(object):

    def next(self, context):
        pass


class ChooseGuardAlgorithm(object):

    STATE_PRIMARY_GUARDS = StatePrimaryGuards()
    STATE_TRY_UTOPIC = StateTryUtopic()
    STATE_TRY_DYSTOPIC = StateTryDystopic()

    def __init__(self, net, params):
        self._net = net
        self._params = params


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

    def nextGuard(self, initialState):
        haveBeenTriedLately = self._hasAnyPrimaryGuardBeenTriedIn(self._params.PRIMARY_GUARDS_RETRY_INTERVAL)
        if haveBeenTriedLately and self._state != self.STATE_PRIMARY_GUARDS:
            self._previousState = self._state
            self._state = self.STATE_PRIMARY_GUARDS

        return self._state.next(self)

    def removeUnavailableRemainingUtopicGuardsByBandwidth(self):
        # XXX What is the difference of doing this by bandwidth if we are not
        # returning anything?
        # Does it make any difference if we are removing and marking in a different order?
        guards = self._remainingUtopicGuards
        while len(guards) > 0:
            g = self.nextByBandwidth(guards)
            guards.remove(g)

            if self.markAsUnreachableAndRemoveAndAddToTriedList(g):
                # XXX what guarantees it will be in triedGuards?
                self._triedGuards.remove(g)

    def markAsUnreachableAndRemoveAndAddToTriedList(self, guard, removeFrom=None):
            if not self.wasNotPossibleToConnect(guard):
                return None

            self.markAsUnreachable(guard)
            self.addToTried(guard)
            return guard

    def markAsUnreachableAndAddToTriedList(self, guards):
        for pg in guards:
            self.markAsUnreachableAndRemoveAndAddToTriedList(pg)

    def wasNotPossibleToConnect(self, guard):
        return guard._madeContact == False

    def markAsUnreachable(self, guard):
        guard._unreachableSince = simtime.now()
                
    def checkTriedTreshold(self, guards):
        timeWindow = simtime.now() - self._params.GUARDS_TRY_TRESHOLD_TIME * 60
        treshold = self._params.GUARDS_TRY_TRESHOLD * len(self._consensus)
        tried = [g for g in guards if g._lastTried and g._lastTried > timeWindow ]
        if len(tried) > treshold:
            self._state = self._STATE_RETRY_ONLY

    def checkFailover(triedGuards, guards, nextState):
        if len(triedGuards) > self._params.GUARDS_FAILOVER_THRESHOLD * guards:
            self._state = nextState

    def allHaveBeenTried(self):
        return len([g for g in self._primaryGuards if not g._lastTried]) == 0

    def transitionToPreviousStateOrTryUtopic(self):
            if self._previousState:
                self._state = self._previousState
            else:
                self._state = self.STATE_TRY_UTOPIC

    def end(self):
        self._hasFinished = True

    def addToTried(self, guard):
        self._triedGuards.append(guard)


    def moveOldTriedGuardsToRemainingList(self):
        timeWindow = simtime.now() - self._params.GUARDS_RETRY_TIME * 60
        for g in self._triedGuards:
            if g.unreacheableSince and g.unreacheableSince < timeWindow:
                self._remainingUtopicGuards.append(g)


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
