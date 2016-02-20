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
    g._lastTried = when
    g._unreachableSince = g._unreachableSince or when
    g._canRetry = False
    return g

def triedAndSucceeded(g, when):
    g._lastTried = when
    g._unreachableSince = None
    g._canRetry = False
    return g

def createGuard(unreachableSince=None, lastTried=None):
    node = tornet.Node("some node", random.randint(1, 65535))
    g = guard.GetGuard(node)
    g._unreachableSince = unreachableSince
    g._lastTried = lastTried
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
        algo.start(used, [], 3, self.ALL_GUARDS, allDystopic)

        expectedPrimary = [g for g in used if g != notInConsensus]
        self.assertEqual(algo._primaryGuards, expectedPrimary)

    def test_STATE_PRIMARY_GUARD_should_return_each_reachable_guard_in_turn(self):
        unreachableGuard = createGuard(unreachableSince = 10)

        used = [unreachableGuard, createGuard(), createGuard()]
        allDystopic = []

        params = client.ClientParams()
        algo = proposal.ChooseGuardAlgorithm(params)
        algo.start(used, [], 3, self.ALL_GUARDS, allDystopic)

        chosen = algo.nextGuard()

        self.assertEqual(algo._state, algo.STATE_PRIMARY_GUARDS)
        self.assertEqual(algo._triedGuards, used[0:1])
        self.assertEqual(chosen, used[1])

        # Failed to connect
        triedAndFailed(used[1], 15)

        chosen = algo.nextGuard()
        self.assertEqual(algo._state, algo.STATE_PRIMARY_GUARDS)
        self.assertEqual(algo._triedGuards, used[0:2])
        self.assertEqual(chosen, used[2])

        # Failed to connect
        triedAndFailed(used[2], 20)

        chosen = algo.nextGuard()
        self.assertEqual(algo._state, algo.STATE_PRIMARY_GUARDS)
        self.assertEqual(algo._triedGuards, used[0:3])
        # XXX Should it really return NONE?
        self.assertEqual(chosen, None)

    def test_NEXT_should_retry_PRIMARY_GUARDS(self):
        used = [triedAndFailed(createGuard(), (n+1)*10) for n in xrange(3)]
        allDystopic = []

        params = client.ClientParams()
        params.GUARDS_TRY_THRESHOLD = 0.04 # 4 guards, so it does not fail
        algo = proposal.ChooseGuardAlgorithm(params)
        algo.start(used, [], 3, self.ALL_GUARDS, allDystopic)

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
        algo.start(used, [], 3, self.ALL_GUARDS, allDystopic)

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
        algo.start(used, [], 3, self.ALL_GUARDS, allDystopic)

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
        algo.start(used, [], 3, self.ALL_GUARDS, allDystopic)

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

        # Should add the first two back to REMAINING_UTOPIC_GUARDS
        chosen = algo.nextGuard()

        self.assertEqual(algo._state, algo.STATE_TRY_UTOPIC)
        self.assertEqual(algo._triedGuards, used) # the 4th is now tried
        self.assertEqual([g for g in used if g in algo._remainingUtopicGuards], used[0:2])

if __name__ == '__main__':
    unittest.main()

