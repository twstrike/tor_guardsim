#!/usr/bin/python

from __future__ import print_function

from py3hax import *
import tornet
import simtime
import client
import options


def trivialSimulation(args):
    num = 1000 if not args.total_relays else args.total_relays
    print("Number of nodes in simulated Tor network: %d" % num)

    net = tornet.Network(num)

    # Decorate the network.
    if args.network_down:
        net = tornet.DownNetwork(net)
    if args.fascist_firewall:
        net = tornet.FascistNetwork(net)
    if args.flaky_network:
        net = tornet.FlakyNetwork(net)
    if args.evil_filtering:
        net = tornet.EvilFilteringNetwork(net)
    if args.sniper_network:
        net = tornet.SniperNetwork(net)


    params = client.ClientParams(
        PRIORITIZE_BANDWIDTH=not args.no_prioritize_bandwidth,
        DISJOINT_SETS=args.disjoint_sets)
    stats = client.Stats()
    c = client.Client(net, stats, params)

    sameclient = True
    gc = lambda: c
    if args.separate_clients:
        sameclient = False
        gc = lambda: client.Client(net, stats, params)

    ok = 0
    bad = 0

    for period in xrange(30): # one hour each
        for subperiod in xrange(30): # two minutes each
            if (subperiod % 10) == 0:
                # nodes left and arrived
                net.do_churn()
            # nodes went up and down
            net.updateRunning()

            cc = gc()

            for attempts in xrange(6): # 20 sec each

                # actually have the client act.
                if cc.buildCircuit():
                    ok += 1
                else:
                    bad += 1

                # time passed
                simtime.advanceTime(20)

        # new consensus
        if sameclient:
            c.updateGuardLists()

    print("Successful client circuits (total): %d (%d)" % (ok, (ok + bad)))
    print("Percentage of successful circuits:  %f%%"
          % ((ok / float(ok + bad)) * 100.0))
    print("Average guard bandwidth capacity:   %d KB/s" % stats.averageGuardBandwidth())

if __name__ == '__main__':
    args = options.makeOptionsParser()
    trivialSimulation(args)
