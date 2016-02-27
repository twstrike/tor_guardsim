#!/usr/bin/python
# -*- coding: utf-8; -*-

from __future__ import print_function

import unittest

import simtime
import tornet
import client

class TestClient(unittest.TestCase):
    def setUp(self):
        simtime.reset()

    def test_should_build_circuit_with_same_guard(self):
        net = tornet.Network(100, nodereliability=1)
        stats = client.Stats()
        params = client.ClientParams()
        c = client.Client(net, stats, params)

        circuit = c.buildCircuit()
        simtime.advanceTime(20)
        print("Got: %s" % circuit)
        self.assertTrue(circuit)

        nextCircuit = c.buildCircuit()
        simtime.advanceTime(20)
        print("Got: %s" % nextCircuit)
        self.assertEquals(nextCircuit, circuit)

        anotherCircuit = c.buildCircuit()
        simtime.advanceTime(20)
        print("Got: %s" % anotherCircuit)
        self.assertEquals(anotherCircuit, nextCircuit)

if __name__ == '__main__':
    unittest.main()

