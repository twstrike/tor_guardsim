#!/usr/bin/python
# -*- coding: utf-8; -*-

from __future__ import print_function

import unittest
import random

import simtime
import tornet
import client
import proposal
import guard

#stats = client.Stats()
#client.Client
# if cc.buildCircuit():
#net = tornet.Network(num, nodereliability=1)
#c.updateGuardLists()
#guard = guardSelection.nextGuard()
#guardSelection.end(guard)

def triedAndFailed(g, when):
    g._lastAttempted = when
    g._lastTried = when
    g._unreachableSince = g._unreachableSince or when
    g._canRetry = False
    return g

def triedAndSucceeded(g, when):
    g._lastAttempted = when
    g._lastTried = when
    g._unreachableSince = None
    g._canRetry = False
    return g

def createGuard():
    node = tornet.Node("some node", random.randint(1, 65535))
    g = guard.Guard.get(node)
    return g

class TestProposal259(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        super(TestProposal259, self).__init__(*args, **kwargs)
        self.ALL_GUARDS = [createGuard() for n in xrange(100)]

    def setUp(self):
        simtime.reset()

    def test_choose_primary_guards_with_preference_to_USED_GUARDS(self):
        notInConsensus = createGuard()
        notInConsensus._bad = True

        used = [createGuard(), notInConsensus, createGuard(), createGuard()]
        allDystopic = []

        params = client.ClientParams()
        algo = proposal.ChooseGuardAlgorithm(params)
        algo.start(used, [], [], [], 3, self.ALL_GUARDS, allDystopic)

        expectedPrimary = [g for g in used if g != notInConsensus]
        self.assertEqual(algo._primaryGuards, expectedPrimary)

    def test_STATE_PRIMARY_GUARD_should_return_each_reachable_guard_in_turn(self):
        unreachableGuard = triedAndFailed(createGuard(), 10)

        used = [unreachableGuard, createGuard(), createGuard()]
        allDystopic = []

        params = client.ClientParams()
        algo = proposal.ChooseGuardAlgorithm(params)
        algo.start(used, [], [], [], 3, self.ALL_GUARDS, allDystopic)

        chosen = algo.nextGuard()

        self.assertEqual(algo._state, algo.STATE_PRIMARY_GUARDS)
        self.assertEqual(algo._triedGuards, used[0:1])
        self.assertEqual(chosen, used[1])

        # Failed to connect
        triedAndFailed(chosen, 15)
        chosen = algo.nextGuard()

        self.assertEqual(algo._state, algo.STATE_PRIMARY_GUARDS)
        self.assertEqual(algo._triedGuards, used[0:2])
        self.assertEqual(chosen, used[2])

        # Failed to connect
        triedAndFailed(chosen, 20)
        chosen = algo.nextGuard()

        self.assertEqual(algo._triedGuards, used[0:3])

        # self.assertEqual(algo._state, algo.STATE_PRIMARY_GUARDS)
        # XXX Should it really return NONE?
        self.assertEqual(chosen, None)

    def test_NEXT_should_retry_PRIMARY_GUARDS(self):
        used = [triedAndFailed(createGuard(), (n+1)*10) for n in xrange(3)]
        allDystopic = []

        params = client.ClientParams()
        params.GUARDS_TRY_THRESHOLD = 0.04 # 4 guards, so it does not fail
        algo = proposal.ChooseGuardAlgorithm(params)
        algo.start(used, [], [], [], 3, self.ALL_GUARDS, allDystopic)

        chosen = algo.nextGuard()

        self.assertEqual(algo._state, algo.STATE_TRY_DYSTOPIC)
        self.assertEqual(algo._triedGuards, used)
        # XXX Should it really return NONE?
        self.assertEqual(chosen, None)

        # At least one have been tried more than PRIMARY_GUARDS_RETRY_INTERVAL
        # minutes ago
        # XXX I think this will not work, because there will never be a previous
        # state.
        simtime.advanceTime(3*60 + 11)

        chosen = algo.nextGuard()

        self.assertEqual(algo._state, algo.STATE_PRIMARY_GUARDS)
        self.assertEqual(chosen, used[0])

        triedAndFailed(chosen, simtime.now()+10)
        chosen = algo.nextGuard()

        self.assertEqual(algo._state, algo.STATE_PRIMARY_GUARDS)
        self.assertEqual(chosen, used[1])

        triedAndFailed(chosen, simtime.now()+20)
        chosen = algo.nextGuard()

        self.assertEqual(algo._state, algo.STATE_PRIMARY_GUARDS)
        self.assertEqual(chosen, used[2])

        # All have failed during retry, so return to previous state
        triedAndFailed(chosen, simtime.now()+30)
        chosen = algo.nextGuard()

        self.assertEqual(algo._state, algo.STATE_TRY_DYSTOPIC)

    def test_STATE_PRIMARY_GUARD_transitions_to_STATE_RETRY_ONLY_when_tried_threshold_fails(self):
        simtime.advanceTime(50)
        used = [triedAndFailed(createGuard(), (n+1)*10) for n in xrange(3)]
        allDystopic = []

        params = client.ClientParams()
        params.GUARDS_TRY_THRESHOLD = 0.02 # 2 guards

        algo = proposal.ChooseGuardAlgorithm(params)
        algo.start(used, [], [], [], 3, self.ALL_GUARDS, allDystopic)

        chosen = algo.nextGuard()

        self.assertEqual(algo._state, algo.STATE_RETRY_ONLY)
        self.assertEqual(algo._triedGuards, used)
        self.assertEqual(chosen, used[0]) # Should return the older

    def test_STATE_PRIMARY_GUARD_transitions_to_TRY_UTOPIC(self):
        used = [triedAndFailed(createGuard(), (n+1)*10) for n in xrange(3)]
        used.append(createGuard())
        allDystopic = []

        params = client.ClientParams()
        # Make sure the threshold checks will not fail
        params.GUARDS_TRY_THRESHOLD = 2
        params.GUARDS_FAILOVER_THRESHOLD = 2

        algo = proposal.ChooseGuardAlgorithm(params)
        algo.start(used, [], [], [], 3, self.ALL_GUARDS, allDystopic)

        chosen = algo.nextGuard()

        self.assertEqual(algo._state, algo.STATE_TRY_UTOPIC)
        self.assertEqual(algo._triedGuards, used[0:-1])
        self.assertEqual(chosen, used[-1])

    def test_STATE_TRY_UTOPIC_add_unreachable_back_to_remaining(self):
        used = [triedAndFailed(createGuard(), (n+1)*10) for n in xrange(3)]
        used.append(createGuard())
        allDystopic = []

        params = client.ClientParams()
        # Make sure the threshold checks will not fail
        params.GUARDS_TRY_THRESHOLD = 2
        params.GUARDS_FAILOVER_THRESHOLD = 2

        algo = proposal.ChooseGuardAlgorithm(params)
        algo.start(used, [], [], [], 3, self.ALL_GUARDS, allDystopic)

        chosen = algo.nextGuard()
        self.assertEqual(chosen, used[-1])

        self.assertEqual(algo._state, algo.STATE_TRY_UTOPIC)
        self.assertEqual(algo._triedGuards, used[0:-1]) # All but the last
        self.assertEqual([g for g in used if g in algo._remainingUtopicGuards], [])

        triedAndFailed(chosen, 40)
        simtime.advanceTime(20*60 + 20) # GUARDS_RETRY_TIME + 20, has passed

        # Will retry the PRIMARY_GUARDS because PRIMARY_GUARDS_RETRY_INTERVAL has
        # passed. Note it is lesser than GUARDS_RETRY_TIME
        primary = list(algo._primaryGuards)
        for g in primary:
            simtime.advanceTime(1)
            chosen = algo.nextGuard()
            triedAndFailed(chosen, simtime.now())
            self.assertEqual(algo._state, algo.STATE_PRIMARY_GUARDS)
            self.assertEqual(chosen, g)

        # Make sure the used guards are unlikely to be chosen, so we can assert
        # they are still on REMAINING_UTOPIC_GUARDS
        for g in used: g.node._bandwidth = 1

        # Should add the first two back to REMAINING_UTOPIC_GUARDS
        chosen = algo.nextGuard()
        self.assertTrue(chosen not in used)
        self.assertTrue(chosen in algo._remainingUtopicGuards)

        # First two used will go back, because they were unreachable since time
        # 10 and 20, and now we are at GUARDS_RETRY_TIME + 23
        self.assertEqual(simtime.now(), 20*60 + 23)
        self.assertEqual([g for g in used if g in algo._remainingUtopicGuards], used[0:2])

        self.assertEqual(algo._state, algo.STATE_TRY_UTOPIC)
        self.assertEqual(algo._triedGuards, used) # the 4th is now tried

    def test_STATE_TRY_UTOPIC_transitions_to_STATE_RETRY_ONLY_when_tried_threshold_surpassed(self):
        used = [triedAndFailed(createGuard(), (n+1)*10) for n in xrange(3)]
        used.append(createGuard())
        # Move time to match last tried-and-failed guard added
        simtime.advanceTime(30)
        allDystopic = []

        params = client.ClientParams()
        # Make this interval smaller the PRIMARY_GUARDS_RETRY_INTERVAL
        params.GUARDS_RETRY_TIME = params.PRIMARY_GUARDS_RETRY_INTERVAL - 1

        algo = proposal.ChooseGuardAlgorithm(params)
        algo.start(used, [], [], [], 3, self.ALL_GUARDS, allDystopic)
        # Move to STATE_TRY_UTOPIC
        chosen = algo.nextGuard()
        self.assertEqual(algo._state, algo.STATE_TRY_UTOPIC)

        # Time is 20 secs over GUARDS_RETRY_TIME
        retryTimeInMinutes = params.GUARDS_RETRY_TIME * 60
        simtime.advanceTime(retryTimeInMinutes + 20)
        # Make chosen guard fail so it can't build a circuit and
        # tries to get a new guard.
        triedAndFailed(chosen, simtime.now())

        algo.nextGuard()

        self.assertEqual(algo._state, algo.STATE_RETRY_ONLY)


    def test_STATE_TRY_UTOPIC_transitions_to_STATE_TRY_DYSTOPIC_when_larger_failover(self):
        used = [triedAndFailed(createGuard(), (n+1)*10) for n in xrange(3)]
        allDystopic = [createGuard()]

        params = client.ClientParams()
        params.GUARDS_TRY_THRESHOLD = 1

        algo = proposal.ChooseGuardAlgorithm(params)
        algo.start(used, [], [], [], 3, self.ALL_GUARDS, allDystopic)

        chosen = algo.nextGuard()

        self.assertEqual(algo._state, algo.STATE_TRY_DYSTOPIC)
        self.assertEqual(chosen, allDystopic[0])


    def test_STATE_TRY_UTOPIC_returns_guard_from_REMAINING_UTOPIC_GUARDS(self):
        allGuards = [createGuard() for n in xrange(4)]
        used = [triedAndFailed(allGuards[n], (n+1)*10) for n in xrange(3)]
        allDystopic = []

        params = client.ClientParams()
        params.GUARDS_TRY_THRESHOLD = 1
        params.GUARDS_FAILOVER_THRESHOLD = 1

        algo = proposal.ChooseGuardAlgorithm(params)
        algo.start(used, [], [], [], 3, allGuards, allDystopic)

        chosen = algo.nextGuard()

        self.assertEqual(algo._state, algo.STATE_TRY_UTOPIC)
        self.assertEqual(len(algo._remainingUtopicGuards), 1)
        self.assertEqual(chosen, algo._remainingUtopicGuards.pop())


    def test_STATE_RETRY_ONLY_returns_none_when_all_exhausted(self):
        allGuards = [createGuard() for n in xrange(3)]
        used = [triedAndFailed(allGuards[n], (n+1)*10) for n in xrange(3)]
        allDystopic = []

        params = client.ClientParams()
        params.GUARDS_TRY_THRESHOLD = 0.5

        algo = proposal.ChooseGuardAlgorithm(params)
        algo.start(used, [], [], [], 3, allGuards, allDystopic)

        # Move time to equal last attempt
        simtime.advanceTime(30)
        # Fail all re-tried guards
        for g in used:
            chosen = algo.nextGuard()
            self.assertEqual(chosen, g)
            triedAndFailed(g, simtime.now() + g._lastTried)

        chosen = algo.nextGuard()

        self.assertEqual(algo._state, algo.STATE_RETRY_ONLY)
        self.assertEqual(chosen, None)


if __name__ == '__main__':
    unittest.main()

