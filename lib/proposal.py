# -*- coding: utf-8; -*-

import simtime
import tor

import random
import pprint

def canRetry(g):
    return g._canRetry

class StatePrimaryGuards(object):
    def next(self, context):
        # Using tor.entry_is_live(g) rather than wasNotPossibleToConnect()
        # would remove the need of canRetry(), and also add the same
        # progressive retry conditions tor currently has.
        for g in context._primaryGuards:
            if not canRetry(g) and context.wasNotPossibleToConnect(g): continue
            return g

        if context.allHaveBeenTried():
            return context.transitionToPreviousStateOrTryUtopic()

class StateTryUtopic(object):
    def next(self, context):
        guards = context.usedGuardsNotInPrimary()
        # I'm unsure if this is intended to force a retry on these guards
        # regardless of whether they were unreachable before reaching this state
        # OR if we simply want to expand the primaryGuards to include the guards
        # left behind by the restriction of N_PRIMARY_GUARDS. The former would require:
        # self.markForRetry(guards)
        for g in guards:
            if not canRetry(g) and context.wasNotPossibleToConnect(g): continue
            return g

        g = context.getFirstByBandwidthAndRemoveUnreachable(context._remainingUtopicGuards)
        if g:
            return g

        assert(not context._remainingUtopicGuards)
        return context.transitionTo(context.STATE_TRY_DYSTOPIC)

class StateTryDystopic(object):
    def next(self, context):
        g = context.getFirstByBandwidthAndRemoveUnreachable(context._remainingDystopicGuards)
        if g:
            return g

        assert(not context._remainingDystopicGuards)

        # I assume this transition is intended to force a retry on ALL primary
        # guards regardless of their latest reachability, so I'm marking them
        # for retry.
        context.markForRetry(context._primaryGuards)
        return context.transitionTo(context.STATE_PRIMARY_GUARDS)

class ChooseGuardAlgorithm(object):
    def __repr__(self):
        vals = vars(self)
        filtered = {k: vals[k] for k in [
            "_state", "_previousState", "_primaryGuards" ]
                    }
        return pprint.pformat(filtered, indent=4, width=1)


    def __init__(self, params):
        self._params = params

        self._usedGuards = None
        self._sampledUtopicGuards = None
        self._sampledDystopicGuards = None

        self._primaryGuards = []
        
        self._lastSuccess = None
        self._previousState = None

        self.STATE_PRIMARY_GUARDS = StatePrimaryGuards()
        self.STATE_TRY_UTOPIC = StateTryUtopic()
        self.STATE_TRY_DYSTOPIC = StateTryDystopic()

    def onNewConsensus(self, utopicGuards, dystopicGuards):
        # Here we ensure all guard profiles won't have bad guards, and
        # when the bad guards are not bad anymore we ensure they will be back
        # to each profile in the same position.

        # We dont need to care about USED_GUARDS
        # Because it is only used to filter out guards from SAMPLED_* and these
        # sets should not have bad guards.

        self._SAMPLED_UTOPIC_THRESHOLD = self._sampleThreshold(utopicGuards)
        self._SAMPLED_DYSTOPIC_THRESHOLD = self._sampleThreshold(dystopicGuards)

        # Ensure SAMPLED_UTOPIC_GUARDS and SAMPLED_DYSTOPIC_GUARDS meet the thresholds
        self._fillInSample(self._sampledUtopicGuards, utopicGuards)
        self._fillInSample(self._sampledDystopicGuards, dystopicGuards)

        # print("sampledUtopicGuards has %d / %d" % (len(self._sampledUtopicGuards), len(self.sampledUtopicGuards)))
        # print("sampledDystopicGuards has %d / %d" % (len(self._sampledDystopicGuards), len(self.sampledDystopicGuards)))

    @property
    def sampledUtopicGuards(self):
        allNotBad = [g for g in self._sampledUtopicGuards if not g.isBad()]
        return allNotBad[:self._SAMPLED_UTOPIC_THRESHOLD]

    @property
    def sampledDystopicGuards(self):
        allNotBad = [g for g in self._sampledDystopicGuards if not g.isBad()]
        return allNotBad[:self._SAMPLED_DYSTOPIC_THRESHOLD]

    @property
    def usedGuards(self):
        return [g for g in self._usedGuards if g._madeContact]

    def _sampleThreshold(self, fullSet):
        return int(self._params.SAMPLE_SET_THRESHOLD * len(fullSet))

    def start(self, usedGuards, sampledUtopicGuards, sampledDystopicGuards,
              excludeNodes, nPrimaryGuards, guardsInConsensus,
              dystopicGuardsInConsensus, selectDirGuards=False):

        # They are references and will be changed by the algorithm if needed
        self._usedGuards = usedGuards
        self._sampledUtopicGuards = sampledUtopicGuards
        self._sampledDystopicGuards = sampledDystopicGuards

        utopicGuards = self._filterGuards(
                guardsInConsensus, selectDirGuards, excludeNodes)
        dystopicGuards = self._filterGuards(
                dystopicGuardsInConsensus, selectDirGuards, excludeNodes)

        # Fill in samples
        self.onNewConsensus(utopicGuards, dystopicGuards)

        usedGuardsSet = set(self.usedGuards)
        # XXX they should be refilled
        # the spec mentions they should be refilled, but I'm not sure when
        self._remainingUtopicGuards = set(self._sampledUtopicGuards) - usedGuardsSet
        self._remainingDystopicGuards = set(self._sampledDystopicGuards) - usedGuardsSet

        self._state = self.STATE_PRIMARY_GUARDS
        self._findPrimaryGuards(self.usedGuards, self._remainingUtopicGuards, nPrimaryGuards)

    def _chooseRandomFrom(self, guards):
        if self._params.PRIORITIZE_BANDWIDTH:
            return tor.choose_node_by_bandwidth_weights(guards)

        return random.choice(guards)

    def _nextByBandwidth(self, guards):
        # Assume subsequent calls to _nextByBandwidth should not return previously
        # returned guards, so we remove them once they are chosen
        # This is not explicit in the spec, though
        g = self._chooseRandomFrom(guards)
        if g: guards.remove(g)
        return g

    # How should the transition happen? Immediately or on the next call to NEXT?
    # We've experienced some problems with too deep recursion when transitioning
    # immediately
    def transitionTo(self, state):
        # return self.transitionOnNextCall(state)
        return self.transitionImmediatelyTo(state)

    def transitionOnNextCall(self, state):
        # print("! Transitioned to %s" % state)
        self._state = state
        return None # The infinite While will see a None to indicate a state transition

    def transitionImmediatelyTo(self, state):
        self.transitionOnNextCall(state)
        return self._state.next(self)

    def markForRetry(self, guards):
        for g in guards:
            g.markForRetry()

    def shouldContinue(self, success):
        if not success: return True

        now = simtime.now()
        shouldContinue = False
        interval = self._params.INTERNET_LIKELY_DOWN_INTERVAL * 60
        if self._lastSuccess and self._lastSuccess + interval < now:
            self.transitionOnNextCall(self.STATE_PRIMARY_GUARDS)
            shouldContinue = True

        self._lastSuccess = now
        return shouldContinue

    def nextGuard(self):
        # print("\nSearch next guard with current state %s" % self._state)
        pgsToRetry = self._primaryGuardsTriedIn(self._params.PRIMARY_GUARDS_RETRY_INTERVAL)
        if pgsToRetry and self._state != self.STATE_PRIMARY_GUARDS:
            # print("Will retry primary tried more than PRIMARY_GUARDS_RETRY_INTERVAL minutes ago.")
            self._previousState = self._state

            # I assume this transition is intended to force a retry on ALL primary
            # guards regardless of their latest reachability, so I'm marking them
            # for retry.
            # Alternatively, we could only mark the guards that were tried at
            # least PRIMARY_GUARDS_RETRY_INTERVAL ago with:
            # self.markForRetry(pgsToRetry)
            self.markForRetry(self._primaryGuards)
            return self.transitionTo(self.STATE_PRIMARY_GUARDS)

        return self._state.next(self)


    # we should first check if it
    #   was at least PRIMARY_GUARDS_RETRY_INTERVAL minutes since we tried
    #     any of the PRIMARY_GUARDS
    def _primaryGuardsTriedIn(self, interval):
        now = simtime.now()
        seconds = interval * 60
        return [g for g in self._primaryGuards
                if g._lastTried and g._lastTried + seconds < now]

    def getFirstByBandwidthAndRemoveUnreachable(self, remainingSet):
        # must be a list to use nextByBandwidth, and must be a copy
        remainingList = list(remainingSet)
        while remainingList:
            g = self._nextByBandwidth(remainingList)
            assert(g)
            if not self.wasNotPossibleToConnect(g):
                return g

            remainingSet.remove(g) # remove if it was not possible to connect

    def wasNotPossibleToConnect(self, guard):
        # Using entry_is_live() would add existing progressive retry window
        # strategy. For now, we keep it clean from previous tor implementation.
        # return not tor.entry_is_live(guard)
        return guard._unreachableSince != None

    def markAsUnreachable(self, guard):
        if not guard._unreachableSince:
            guard._unreachableSince = simtime.now()

    def allHaveBeenTried(self):
        return len([g for g in self._primaryGuards if not g._lastTried]) == 0

    def transitionToPreviousStateOrTryUtopic(self):
        if self._previousState:
            return self.transitionTo(self._previousState)
        else:
            self.markForRetry(self.usedGuardsNotInPrimary())
            return self.transitionTo(self.STATE_TRY_UTOPIC)

    def end(self, guard):
        if guard not in self._usedGuards: self._usedGuards.append(guard)

    def _filterGuards(self, guards, selectDirGuards, excludeNodes):
        guardsWithoutExluded = [g for g in guards if not g._node in excludeNodes]
        guards = [g for g in guardsWithoutExluded if g._isDirectoryCache] if selectDirGuards else guardsWithoutExluded
        return set(guards)

    def _findPrimaryGuards(self, usedGuards, remainingUtopic, nPrimaryGuards):
        used = list(usedGuards)
        while len(self._primaryGuards) < nPrimaryGuards:
            g = self._nextPrimaryGuard(used, remainingUtopic)
            if not g: break # ran out of used and remainingUtopic

            # XXX Add to spec: PRIMARY_GUARDS is a list of unique elements
            if g in self._primaryGuards: continue

            if not g.isBad():
                self._primaryGuards.append(g)

    # Ensure sampledSet has has SAMPLE_SET_THRESHOLD not bad elements from fullSet
    # adding elements if needed
    def _fillInSample(self, sampledSet, fullSet):
        threshold = self._sampleThreshold(fullSet)
        fullSetCopy = list(fullSet) # must be a copy, because it is changed by nextByBandwidth
        while len([g for g in sampledSet if not g.isBad()]) < threshold:
            g = self._nextByBandwidth(fullSetCopy)
            assert(g)
            sampledSet.append(g)

    def _nextPrimaryGuard(self, usedGuards, remainingUtopic):
        if not usedGuards:
            if not remainingUtopic: return None

            g = self._nextByBandwidth(list(remainingUtopic))
            # Assume we should remove a chosen guard from REMAINING_UTOPIC
            # The spec is not explicit about it, though
            # XXX compare implication of this
            assert(g)
            remainingUtopic.remove(g)
            return g

        # Dont worry about removing, because this is a copy of the original -
        # and this prevents returning always the same
        return usedGuards.pop(0)

    def usedGuardsNotInPrimary(self):
        return [g for g in self.usedGuards if g not in self._primaryGuards]

