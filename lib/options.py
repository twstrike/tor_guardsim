#!/usr/bin/python
# This is distributed under cc0. See the LICENCE file distributed along with
# this code.

"""Commandline options for simulation."""

import argparse


def makeOptionsParser():
    """Initialise an :class:`argparse.ArgumentParser`, set up some options
    flags, and parse any commandline arguments we received.

    :rtype: tuple
    :returns: A 2-tuple of ``(namespace, parser)``.
    """
    parser = argparse.ArgumentParser()

    # How should we simulate the network?
    net_group = parser.add_argument_group(
        title="Network Simulation Options",
        description=("Control various simulation options regarding how the local"
                     " network connection for the client is simulated."))
    net_group.add_argument(
        "-N", "--total-relays", type=int,
        help=("The total number of relays in the simulated network.  If this "
              "argument is not given, then a random number of relays in "
              "[100, 10000] will be used."))
    net_group.add_argument(
        "-R", "--node-reliability", type=float,
        help=("The reliability of each node - each node will be up by this probability after each churn. "
              "The default is 0.96."))
    net_group.add_argument(
        "-F", "--fascist-firewall", action="store_true",
        help=("Simulate only a FascistFirewall network which only allows "
              "connections to ports 80 and 443."))
    net_group.add_argument(
        "-f", "--flaky-network", action="store_true",
        help=("Simulate a flaky local network connection."))
    net_group.add_argument(
        "-e", "--evil-filtering", action="store_true",
        help=("Simulate a network that blocks connections to non-evil guard "
              "nodes with some probability"))
    net_group.add_argument(
        "-s", "--sniper-network", action="store_true",
        help=("Simulate a network that does a DoS attack on a client's "
              "non-evil guard nodes with some probability after each "
              "connection."))
    net_group.add_argument(
        "-d", "--network-down", action="store_true",
        help=("Simulate a network that is completely down."))
    net_group.add_argument(
        "-S", "--switching-network", action="store_true",
        help=("Simulate a network that periodically switches between types."))

    # Other miscellaneous options
    parser.add_argument(
        "-r", "--no-prioritize-bandwidth", action="store_true",
        help=("When selecting a new guard node, the default is to prioritize "
              "nodes with higher bandwidth capacity.  This option causes random "
              "nodes to be chosen"))

    parser.add_argument(
        "-D", "--disjoint-sets", action="store_true",
        help=("When building the set of utopic/dystopic guards, the default is "
              "to have an intersecting set. This option causes them to be "
              "disjoint sets"))

    parser.add_argument(
        "-C", "--separate-clients", action="store_true",
        help=("If this flag is set, we will simulate many clients, while the "
              "default behavior is to simulate one client doing many circuits."))

    return parser.parse_args()
