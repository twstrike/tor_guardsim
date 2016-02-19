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

def createGuard(unreachableSince=None, lastTried=None):
    node = tornet.Node("some node", random.randint(1, 65535))
    g = guard.GetGuard(node)
    g._unreachableSince = unreachableSince
    g._lastTried = lastTried
    return g

class TestProposal259(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        super(TestProposal259, self).__init__(*args, **kwargs)
        self.ALL_GUARDS = [createGuard() for n in xrange(10)]

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

    def test_STATE_PRIMARY_GUARD_should_prefer_reachable_guards(self):
        unreachableGuard = createGuard(unreachableSince = 10)

        used = [unreachableGuard, createGuard(), createGuard()]
        allDystopic = []

        params = client.ClientParams()
        algo = proposal.ChooseGuardAlgorithm(params)
        algo.start(used, [], 3, self.ALL_GUARDS, allDystopic)

        chosen = algo.nextGuard()

        self.assertEqual(algo._state, algo.STATE_PRIMARY_GUARDS)
        self.assertEqual(algo._triedGuards, [unreachableGuard])
        self.assertEqual(chosen, used[1])

    def test_STATE_PRIMARY_GUARD_transitions_to_STATE_RETRY_ONLY_when_tried_threshold_fails(self):
        simtime.advanceTime(50)
        used = [createGuard(unreachableSince = (n+1)*10, lastTried = (n+1)*10) for n in xrange(3)]
        allDystopic = []

        params = client.ClientParams()
        params.GUARDS_TRY_THRESHOLD = 0.5

        algo = proposal.ChooseGuardAlgorithm(params)
        algo.start(used, [], 3, self.ALL_GUARDS, allDystopic)

        chosen = algo.nextGuard()

        self.assertEqual(algo._state, algo.STATE_RETRY_ONLY)
        self.assertEqual(algo._triedGuards, used)
        self.assertEqual(chosen, used[0]) # Should return the older

    def test_STATE_PRIMARY_GUARD_transitions_to_TRY_UTOPIC(self):
        used = [createGuard(unreachableSince = (n+1)*10, lastTried = (n+1)*10) for n in xrange(3)]
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

if __name__ == '__main__':
    unittest.main()
