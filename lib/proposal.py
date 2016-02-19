# -*- coding: utf-8; -*-

import random
import simtime
import pprint

# XXX On current tor, this only returns LIVE guards.
# Which means not unreachable or ready to be retired
# See: tor.entry_is_live(g)
def returnEachEntryInTurn(guards, turn):
    g = None
    if len(guards) > turn + 1:
        turn += 1
        g = guards[turn]

    #if not tor.entry_is_live(g):
    #    g, turn = returnEachEntryInTurn(guards, turn+1)

    return (g, turn)


# XXX Maybe this is what it means
def returnEachEntryInTurnImNotSure(guards, context):
    for g in guards:
        # XXX this is not clear in the spec
        if not context.wasNotPossibleToConnect(g):
            return g


class StatePrimaryGuards(object):
    def next(self, context):
        # print("StatePrimaryGuards - NEXT")

        for g in context._primaryGuards:
            if not context.markAsUnreachableAndAddToTried(g, context._triedGuards):
                return g

        if not context.checkTriedThreshold(context._triedGuards):
            return

        if context.allHaveBeenTried():
            context.transitionToPreviousStateOrTryUtopic()


class StateTryUtopic(object):
    def __init__(self):
        self._turn = -1
        self._remaining = []

    def next(self, context):
        # print("StateTryUtopic - NEXT")

        #  XXX This should add back to REMAINING_UTOPIC_GUARDS
        # When are they taken from REMAINING_UTOPIC_GUARDS?
        context.moveOldTriedGuardsToRemainingList()

        #  XXX When are USED_GUARDS removed from PRIMARY_GUARDS?
        # Is not PRIMARY_GUARDS built from USED_GUARDS preferably?
        guards = [g for g in context._usedGuards if g not in context._primaryGuards]

        print("Will chose from %s" % guards)

        # For each entry, if it was not possible to connect to it, mark the
        # entry as unreachable and add it to TRIED_GUARDS.
        if self._turn > -1:
            lastTried, _ = returnEachEntryInTurn(guards, self._turn - 1)
            context.markAsUnreachableAndAddToTried(lastTried, context._triedGuards)

        context._lastReturn, self._turn = returnEachEntryInTurn(guards, self._turn)

        print("Will return %s" % context._lastReturn)

        if not context.checkTriedThreshold(context._triedGuards):
            return

        if not context.checkFailover(context._triedGuards,
                                     context._utopicGuards, context.STATE_TRY_DYSTOPIC):
            return

        # Return each entry from REMAINING_UTOPIC_GUARDS using
        #  NEXT_BY_BANDWIDTH. For each entry, if it was not possible to connect
        #  to it, remove the entry from REMAINING_UTOPIC_GUARDS, mark it as
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
        if not context.checkTriedThreshold(context._triedGuards):
            return

        if not context.checkFailover(context._triedGuards,
                                     context._utopicGuards, context.STATE_TRY_DYSTOPIC):
            return


class StateTryDystopic(object):
    def __init__(self):
        self._turn = -1
        self._remaining = []

    def next(self, context):
        # print("StateTryDystopic - NEXT")

        context.moveOldTriedDystopicGuardsToRemainingList()

        distopicGuards = [g for g in context._usedGuards if g._node.seemsDystopic()]
        guards = [g for g in distopicGuards if g not in context._primaryGuards]
        context._lastReturn, self._turn = returnEachEntryInTurn(guards, self._turn)

        context.markDystopicAsUnreachableAndAddToTriedList(guards)

        if not context.checkTriedThreshold(context._triedGuards + context._triedDystopicGuards):
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
        if not context.checkTriedThreshold(context._triedGuards + context._triedDystopicGuards):
            return

        if not context.checkTriedDystopicFailoverAndMarkAllAsUnreachable():
            return

        # XXX what happens if no threshold fails?
        print("No threshold has failed")


class StateRetryOnly(object):
    def __init__(self):
        self._turn = -1

    def next(self, context):
        # print("StateRetryOnly - NEXT")
        guards = context._triedGuards + context._triedDystopicGuards
        guards.sort(key=lambda g: g._lastTried)

        context._lastReturn, self._turn = returnEachEntryInTurn(guards, self._turn)


class ChooseGuardAlgorithm(object):
    def __repr__(self):
        vals = vars(self)
        filtered = {k: vals[k] for k in [
            "_hasFinished", "_state", "_previousState", "_primaryGuards", "_triedGuards"]
                    }
        return pprint.pformat(filtered, indent=4, width=1)

    def __init__(self, params):
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

    def start(self, usedGuards, excludeNodes, nPrimaryGuards, guardsInConsensus, dystopicGuardsInConsensus,
              selectDirGuards=False):
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
        # XXX when we pick a guard from remainingUtopic, as example, should we remove it
        # from the remaining list?
        return tor.choose_node_by_bandwidth_weights(guards)

    # XXX How should the transition happen?
    # Immediately, or on the next call to NEXT?
    def transitionTo(self, state):
        #self.transitionOnNextCall(state)
        self.transitionImmediatelyTo(state)
        return False  # should not continue execution

    def transitionOnNextCall(self, state):
        print("! Transitioned to %s" % state)
        self._state = state

    def transitionImmediatelyTo(self, state):
        self.transitionOnNextCall(state)
        self._state.next(self)

    def nextGuard(self):
        haveBeenTriedLately = self._hasAnyPrimaryGuardBeenTriedIn(self._params.PRIMARY_GUARDS_RETRY_INTERVAL)
        if haveBeenTriedLately and self._state != self.STATE_PRIMARY_GUARDS:
            self._previousState = self._state
            self.transitionTo(self.STATE_PRIMARY_GUARDS)

        self._lastReturn = None
        g = self._state.next(self)

        return g or self._lastReturn

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
        guards = list(remaining)  # must be a list to use nextByBandwidth
        while guards:
            g = self.nextByBandwidth(guards)
            guards.remove(g)     # remove to ensure we "return each"
            if self.markAsUnreachableAndAddToTried(g, tried):
                remaining.remove(g)

    def markAsUnreachableAndAddToTried(self, guard, triedList):
        if not self.wasNotPossibleToConnect(guard):
            return None

        self.markAsUnreachable(guard)
        triedList.append(guard)
        return guard

    def markAsUnreachableAndAddToTriedList(self, guards):
        for pg in guards:
            self.markAsUnreachableAndAddToTried(pg, self._triedGuards)

    def markDystopicAsUnreachableAndAddToTriedList(self, guards):
        for pg in guards:
            self.markAsUnreachableAndAddToTried(pg, self._triedDystopicGuards)

    def wasNotPossibleToConnect(self, guard):
        return guard._unreachableSince != None
        # return guard._madeContact == False

    def markAsUnreachable(self, guard):
        if not guard._unreachableSince:
            guard._unreachableSince = simtime.now()

    # XXX should we abort the current state if this transitions to another state?
    def checkTriedThreshold(self, guards):
        timeWindow = simtime.now() - self._params.GUARDS_TRY_THRESHOLD_TIME * 60
        threshold = self._params.GUARDS_TRY_THRESHOLD * len(guards)
        tried = [g for g in guards if g._lastTried and g._lastTried > timeWindow]

        print("tried = %s, threshold = %s" % (len(tried), threshold))

        if len(tried) > threshold:
            self.transitionTo(self.STATE_RETRY_ONLY)
            # Threshold Failed
            return False

        return True

    # XXX should we abort the current state if this transitions to another state?
    def checkFailover(self, triedGuards, guards, nextState):
        print("checkFailover: tried = %d, guards = %d " % (len(triedGuards), len(guards)))
        if len(triedGuards) > self._params.GUARDS_FAILOVER_THRESHOLD * len(guards):
            self.transitionTo(nextState)
            # Threshold Failed
            return False

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
                self.transitionTo(self._previousState)
            else:
                self.transitionTo(self.STATE_TRY_UTOPIC)

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
        guardsWithoutExluded = [g for g in guards if not g._node in excludeNodes]
        guards = [g for g in liveGuards if g_isDirectoryCache] if selectDirGuards else guardsWithoutExluded
        return set(guards)

    def _getGuards(self, selectDirGuards, excludeNodesSet):
        return self.filterGuards(self._guardsInConsensus, selectDirGuards, excludeNodesSet)

    def _filterDystopicGuards(self, selectDirGuards, excludeNodesSet):
        return self.filterGuards(self._dystopicGuardsInConsensus, selectDirGuards, excludeNodesSet)

    def _filterDystopicGuardsFrom(self, guards):
        return set([dg for dg in guards if dg._node.seemsDystopic()])

    # XXX This is slow
    def _findPrimaryGuards(self, usedGuards, remainingUtopic, nPrimaryGuards):
        # This is not taking into account the remaining dystopic guards. Is that okay?
        used = list(usedGuards)
        remaining = list(remainingUtopic)
        while len(self._primaryGuards) < nPrimaryGuards:
            g = self._nextPrimaryGuard(used, remaining)
            if not g: continue
            self._primaryGuards.append(g)

    # XXX This is slow
    def _nextPrimaryGuard(self, usedGuards, remainingUtopic):
        if usedGuards:
            while usedGuards:
                guard = usedGuards.pop(0)

                # From proposal §2.2.5:
                # If any PRIMARY_GUARDS have become bad, remove the guard from
                # PRIMARY_GUARDS. Then ensure that PRIMARY_GUARDS contain
                # N_PRIMARY_GUARDS entries by repeatedly calling NEXT_PRIMARY_GUARD.
                # ... so we just don't add it.
                if guard not in self._primaryGuards and not guard._bad:
                    return guard

        # If USED_GUARDS is empty, use NEXT_BY_BANDWIDTH with
        # new consensus arrives via the update() function is much more time

        # XXX should we remove the chosen from remaining?
        # XXX also, if it is in remaining we dont care if its already in PRIMARY_GUARDS
        # o if it is bad. We just add.
        return random.choice(remainingUtopic)

        # choose weighted by BW (disabled for performance)
        # we can optimize by calculating the bw weights only once (outside
        # of this function)
        # return tor.choose_node_by_bandwidth_weights(remainingUtopic)

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
