# -*- coding: utf-8; -*-

import simtime
import tor

import random
import pprint

def canRetry(g):
    return g._canRetry

class StatePrimaryGuards(object):
    def next(self, context):
        # print("StatePrimaryGuards - NEXT")

        # Using tor.entry_is_live(g) rather than wasNotPossibleToConnect()
        # in markAsUnreachableAndAddToTried() whould remove the need of canRetry(),
        # and also add the same retry conditions tor currently has.
        for g in context._primaryGuards:
            if canRetry(g) or not context.markAsUnreachableAndAddToTried(g, context._triedGuards):
                return g

        ok, fromTransition = context.checkTriedThreshold(context._triedGuards)
        if not ok: return fromTransition

        if context.allHaveBeenTried():
            return context.transitionToPreviousStateOrTryUtopic()

        # is it possible?
        print("No threshold has failed")

class StateTryUtopic(object):
    def next(self, context):
        print("StateTryUtopic - NEXT")

        # This should add back to REMAINING_UTOPIC_GUARDS but
        # when are they removed from REMAINING_UTOPIC_GUARDS?
        context.moveOldTriedGuardsToRemainingList()

        # Try previously used guards. They were PRIMARY_GUARDS at some point.
        # Why did they leave the PRIMARY_GUARDS list?
        guards = [g for g in context._usedGuards if g not in context._primaryGuards]
        for g in guards:
            if not context.markAsUnreachableAndAddToTried(g, context._triedGuards):
                return g

        ok, fromTransition = context.checkTriedThreshold(context._triedGuards)
        if not ok: return fromTransition

        ok, fromTransition = context.checkFailover(context._triedGuards,
                                     context._utopicGuards,
                                     context.STATE_TRY_DYSTOPIC)
        if not ok: return fromTransition

        g = context.getFirstByBandwidthAndAddUnreachableTo(context._remainingUtopicGuards,
                context._triedGuards)
        if g: return g

        # one more time
        ok, fromTransition = context.checkTriedThreshold(context._triedGuards)
        if not ok: return fromTransition

        ok, fromTransition = context.checkFailover(context._triedGuards,
                                     context._utopicGuards,
                                     context.STATE_TRY_DYSTOPIC)
        if not ok: return fromTransition

        # is it possible?
        print("No threshold has failed")

class StateTryDystopic(object):
    def next(self, context):
        # print("StateTryDystopic - NEXT")

        context.moveOldTriedDystopicGuardsToRemainingList()

        distopicGuards = [g for g in context._usedGuards if g._node.seemsDystopic()]
        guards = [g for g in distopicGuards if g not in context._primaryGuards]

        for g in guards:
            if not context.markAsUnreachableAndAddToTried(g, context._triedDystopicGuards):
                return g

        ok, fromTransition = context.checkTriedThreshold(context._triedGuards + context._triedDystopicGuards)
        if not ok: return fromTransition

        ok, fromTransition = context.checkTriedDystopicFailoverAndMarkAllAsUnreachable()
        if not ok: return fromTransition

        g = context.getFirstByBandwidthAndAddUnreachableTo(
                context._remainingDystopicGuards, context._triedDystopicGuards)
        if g: return g

        # one more time
        ok, fromTransition = context.checkTriedThreshold(context._triedGuards + context._triedDystopicGuards)
        if not ok: return fromTransition

        ok, fromTransition = context.checkTriedDystopicFailoverAndMarkAllAsUnreachable()
        if not ok: return fromTransition

        # is it possible?
        print("No threshold has failed")

class StateRetryOnly(object):
    def __init__(self):
        self._shouldMarkForRetry = True

    def next(self, context):
        # print("StateRetryOnly - NEXT")
        guards = context._triedGuards + context._triedDystopicGuards
        guards.sort(key=lambda g: g._lastTried)

        # It will only reach this state if everything has failed so far, so if
        # we filter to return only the guards that are not currently unreachable
        # it wont return anything.
        # We should either not ignore unreachable OR mark all of them for retry
        # before doing this the first time. We chose mark them for retry.
        if self._shouldMarkForRetry:
            context.markForRetry(guards)
            self._shouldMarkForRetry = False

        for g in guards:
            if context.wasNotPossibleToConnect(g): continue
            return g

        # What if it exhaustes this list?
        # We mark them for retry and keep returning - this is an infinite loop
        # anyways.
        self._shouldMarkForRetry = True
        print("Exhausted tried list")

class ChooseGuardAlgorithm(object):
    def __repr__(self):
        vals = vars(self)
        filtered = {k: vals[k] for k in [
            "_state", "_previousState", "_primaryGuards", "_triedGuards"]
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

    def start(self, usedGuards, excludeNodes, nPrimaryGuards, guardsInConsensus, dystopicGuardsInConsensus,
              selectDirGuards=False):
        # This is a reference on purpose, so we dont need to copy it back to the
        # client
        self._usedGuards = usedGuards

        excludeNodesSet = set(excludeNodes)
        self._guardsInConsensus = list(guardsInConsensus)
        self._dystopicGuardsInConsensus = list(dystopicGuardsInConsensus)

        self._guards = self._getGuards(selectDirGuards, excludeNodesSet)
        self._utopicGuards = self._guards

        self._dystopicGuards = self._filterDystopicGuards(selectDirGuards, excludeNodesSet)

        usedGuardsSet = set(usedGuards)
        self._remainingUtopicGuards = self._utopicGuards - usedGuardsSet
        self._remainingDystopicGuards = self._dystopicGuards - usedGuardsSet
        self._triedGuards, self._triedDystopicGuards = [], []
        self._state = self.STATE_PRIMARY_GUARDS
        self._findPrimaryGuards(usedGuards, self._remainingUtopicGuards, nPrimaryGuards)

    def chooseRandomFrom(self, guards):
        if self._params.PRIORITIZE_BANDWIDTH:
            return tor.choose_node_by_bandwidth_weights(guards)

        return random.choice(guards)

    def nextByBandwidth(self, guards):
        # Should a guard be removed from REMAINING_*_GUARDS when it is chosen
        # by nextByBandwidth? Where do we enforce PRIMARY_GUARDS wont contain
        # duplicate guards?
        return self.chooseRandomFrom(guards)

    # How should the transition happen? Immediately or on the next call to NEXT?
    def transitionTo(self, state):
        #return self.transitionOnNextCall(state)
        return self.transitionImmediatelyTo(state)

    def transitionOnNextCall(self, state):
        print("! Transitioned to %s" % state)
        self._state = state
        return None # The infinite While will see a None to indicate a state transition

    def transitionImmediatelyTo(self, state):
        self.transitionOnNextCall(state)
        return self._state.next(self)

    def markForRetry(self, guards):
        for g in guards:
            g.markForRetry()

    def nextGuard(self):
        haveBeenTriedLately = self._hasAnyPrimaryGuardBeenTriedIn(self._params.PRIMARY_GUARDS_RETRY_INTERVAL)
        if haveBeenTriedLately and self._state != self.STATE_PRIMARY_GUARDS:
            # This is intended to retry ALL PRIMARY_GUARDS, but should we really
            # retry ALL of them if only one has been tried more than
            # PRIMARY_GUARDS_RETRY_INTERVAL minutes ago?
            # What if another one of them has just been tried?

            # Mark for retry is the strategy tor currently uses. But comparing
            # to tor code, this happens when a new guard is successfully
            # connectected to for the first time.
            self.markForRetry(self._primaryGuards)
            self._previousState = self._state
            return self.transitionTo(self.STATE_PRIMARY_GUARDS)

        self._lastReturn = None
        g = self._state.next(self)

        return g or self._lastReturn

    def getFirstByBandwidthAndAddUnreachableTo(self, remaining, tried):
        guards = list(remaining)  # must be a list to use nextByBandwidth
        while guards:
            g = self.nextByBandwidth(guards)
            guards.remove(g)     # remove to ensure we "return each"
            if self.markAsUnreachableAndAddToTried(g, tried):
                remaining.remove(g)
            else:
                return g

    def markAsUnreachableAndAddToTried(self, guard, triedList):
        if not self.wasNotPossibleToConnect(guard):
            return None

        self.markAsUnreachable(guard)
        if not guard in triedList: triedList.append(guard)
        return guard

    def wasNotPossibleToConnect(self, guard):
        return not tor.entry_is_live(guard)
        # return guard._unreachableSince != None
        # return guard._madeContact == False

    def markAsUnreachable(self, guard):
        if not guard._unreachableSince:
            guard._unreachableSince = simtime.now()

    def checkTriedThreshold(self, guards):
        timeWindow = simtime.now() - self._params.GUARDS_TRY_THRESHOLD_TIME * 60
        threshold = self._params.GUARDS_TRY_THRESHOLD * len(self._guards)
        tried = [g for g in guards if g._lastTried and g._lastTried > timeWindow]

        print("tried = %s, threshold = %s" % (len(tried), threshold))

        if len(tried) > threshold:
            # Threshold Failed
            return (False, self.transitionTo(self.STATE_RETRY_ONLY))

        return (True, None)

    def checkFailover(self, triedGuards, guards, nextState):
        print("checkFailover: tried = %d, guards = %d " % (len(triedGuards), len(guards)))
        if len(triedGuards) > self._params.GUARDS_FAILOVER_THRESHOLD * len(guards):
            # Threshold Failed
            return (False, self.transitionTo(nextState))

        return (True, None)

    def checkTriedDystopicFailoverAndMarkAllAsUnreachable(self):
        ok, fromTransition = self.checkFailover(self._triedDystopicGuards,
                              self._dystopicGuards, self.STATE_RETRY_ONLY)
        if ok:
            assert(fromTransition == None)
            return (True, None)

        # Should this happen BEFORE transitioning in case of a failover failure?
        # If yes, we can not use checkFailover() the way it is currently written.
        # An alternative is simply do not use transitionImmediatelyTo().
        guards = self._primaryGuards + self._triedGuards + self._triedDystopicGuards
        for g in guards:
            self.markAsUnreachable(g)

        return (False, fromTransition)

    def allHaveBeenTried(self):
        return len([g for g in self._primaryGuards if not g._lastTried]) == 0

    def transitionToPreviousStateOrTryUtopic(self):
        if self._previousState:
            return self.transitionTo(self._previousState)
        else:
            return self.transitionTo(self.STATE_TRY_UTOPIC)

    def end(self, guard):
        if guard not in self._usedGuards: self._usedGuards.append(guard)

    def giveOneMoreChanceTo(self, tried, remaining):
        timeWindow = simtime.now() - self._params.GUARDS_RETRY_TIME * 60
        guards = [g for g in tried if g._unreachableSince]
        for g in guards:
            if g._unreachableSince < timeWindow:
                g._canRetry = True
                remaining.add(g)

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

    def _findPrimaryGuards(self, usedGuards, remainingUtopic, nPrimaryGuards):
        # This is not taking into account the remaining dystopic guards. Is that okay?
        used = list(usedGuards)
        remaining = list(remainingUtopic)
        while len(self._primaryGuards) < nPrimaryGuards:
            g = self._nextPrimaryGuard(used, remaining)
            # XXX Add to spec: PRIMARY_GUARDS is a list of unique elements
            if g and g not in self._primaryGuards:
                self._primaryGuards.append(g)

    def _nextPrimaryGuard(self, usedGuards, remainingUtopic):
        # If USED_GUARDS is empty, use NEXT_BY_BANDWIDTH with REMAINING_UTOPIC_GUARDS.
        # REMAINING_UTOPIC_GUARDS is by definition not bad (they come from the
        # latest consensus).
        if not usedGuards:
            return self.chooseRandomFrom(remainingUtopic)

        while usedGuards:
            guard = usedGuards.pop(0)

            # From proposal §2.2.5:
            # If any PRIMARY_GUARDS have become bad, remove the guard from
            # PRIMARY_GUARDS. Then ensure that PRIMARY_GUARDS contain
            # N_PRIMARY_GUARDS entries by repeatedly calling NEXT_PRIMARY_GUARD.
            # ... so we just don't add it.
            if not guard.isBad():
                return guard

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
