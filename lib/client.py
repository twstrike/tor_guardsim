#!/usr/bin/python
# This is distributed under cc0. See the LICENCE file distributed along with
# this code.

import random

from math import floor

from py3hax import *
import simtime

class GivingUp(Exception):
    pass

class ExponentialTimer(object):
    """ Implements an exponential timer using simulated time. """
    def __init__(self, initial, multiplier):
        """Create a timer that's ready to fire immediately.  After
           it first fires, it won't be ready again until 'initial'
           seconds have passed.  Each time after that, it will
           increase the delay by a factor of 'multiplier'.
        """
        self._initial_delay = initial
        self._multiplier = multiplier

        self.reset()

    def reset(self):
        """Reset the timer to the state when it was first created."""
        self._next = 0
        self._cur_delay = self._initial_delay

    def isReady(self):
        """Return true iff the timer is ready to fire now."""
        return self._next <= simtime.now()

    def fire(self):
        """Fire the timer."""
        assert self.isReady()
        self._next = simtime.now() + self._cur_delay
        self._cur_delay *= self._multiplier


class ClientParams(object):
    """
       Represents the configuration parameters of the client algorithm,
       as given in proposals 259 and 241
    """
    # percentage of guards to keep in a guard list (utopic)
    UTOPIC_GUARDS_THRESHOLD = 0.05
    # percentage of guards to keep in a guard list (dystopic)
    DYSTOPIC_GUARDS_THRESHOLD = 0.05

    def __init__(self,
                 TOO_MANY_GUARDS=100, # XXX too high
                 TOO_RECENTLY=86400,
                 RETRY_DELAY=30,
                 RETRY_MULT=2):

        # prop241: if we have seen this many guards...
        self.TOO_MANY_GUARDS = TOO_MANY_GUARDS
        # ...within this many simulated seconds, then "freak out".
        self.TOO_RECENTLY = TOO_RECENTLY

        # wait this long after retrying guards the first time
        self.RETRY_DELAY = RETRY_DELAY
        # wait this much longer (factor) after the first time.
        self.RETRY_MULT = RETRY_MULT


class Guard(object):
    """
       Represents what a client knows about a guard.
    """
    def __init__(self, node):
        # tornet.Node instance
        self._node = node

        # True iff we have marked this node as down.
        self._markedDown = False

        # True iff we have marked this node as up.
        self._markedUp = False

        # True iff we've attempted to connect to this node.
        self._tried = False

        # When did we add it (simulated)?
        self._addedAt = simtime.now()

        # True iff the node is listed as a guard in the most recent consensus
        self._listed = True

    def getNode(self):
        """Return the underlying torsim.Node object for this guard."""
        return self._node

    def mark(self, up):
        """Mark this guard as up or down because of a successful/unsuccessful
           connection attempt."""
        self._tried = True
        if up:
            self._markedDown = False
            self._markedUp = True
        else:
            self._markedDown = True
            self._markedUp = False

    def markUnlisted(self):
        """Mark this guard as unlisted because it didn't appear in the
           most recent consensus."""
        self._listed = False

    def markListed(self):
        """Mark this guard as listed because it did appear in the
           most recent consensus."""
        self._listed = True

    def canTry(self):
        """Return true iff we can try to make a connection to this guard."""
        return self._listed and not (self._tried and self._markedDown)

    def isListed(self):
        """Return true iff the guard is listed in the most recent consensus
           we've seen."""
        return self._listed

    def markForRetry(self):
        """Mark this guard as untried, so that we will be willing to try it
           again."""
        # XXXX We never call this unless _all_ the guards in group seem
        # XXXX down.  But maybe we should give early guards in a list
        # XXXX a chance again after a while?
        self._tried = False

    def addedWithin(self, nSec):
        """Return true iff this guard was added within the last 'nSec'
           simulated seconds."""
        return self._addedAt + nSec >= simtime.now()

class Client(object):
    """
       A stateful client implementation of the guard selection algorithm.
    """
    def __init__(self, network, parameters):

        # a torsim.Network object.
        self._net = network

        # a ClientParams object
        self._p = parameters

        # lists of current guards in the consensus from the dystopic and
        # utopic sets.  each guard is represented here as a torsim.Node.
        self._DYSTOPIC_GUARDS = self._UTOPIC_GUARDS = None

        # lists of Guard objects for the dystopic and utopic guards
        # configured on this client.
        self._PRIMARY_DYS = []
        self._PRIMARY_U = []

        self._retryTimer = ExponentialTimer(parameters.RETRY_DELAY,
                                            parameters.RETRY_MULT)

        # XXXX document
        self._maybeDystopic = False

        self.updateGuardLists()

    def nodeSeemsDystopic(self,node):
        """Return true iff this node seems like one we could use in a
           dystopic world."""
        return node.getPort() in [80, 443]

    def updateGuardLists(self):
        """Called at start and when a new consensus should be made & received:
           updates *TOPIC_GUARDS."""
        self._DYSTOPIC_GUARDS = []
        self._UTOPIC_GUARDS = []

        # XXXX I'm not sure what happens if a node changes its ORPort
        # XXXX or when the client changes its policies.

        # Temporary set of node IDs for the listed nodes.
        liveIDs = set()

        # We get the latest consensus here.
        for node in self._net.new_consensus():
            liveIDs.add(node.getID())
            if self.nodeSeemsDystopic(node):
                self._DYSTOPIC_GUARDS.append(node)
            else:
                # XXXX Having this be 'else' means that FirewallPorts
                # XXXX has affect even when FascistFirewall is disabled.
                # XXXX Interesting!  And maybe bad!
                self._UTOPIC_GUARDS.append(node)

        # Now mark every Guard we have as listed or unlisted.
        for lst in (self._PRIMARY_DYS, self._PRIMARY_U):
            for g in lst:
                if g.getNode().getID() in liveIDs:
                    g.markListed()
                else:
                    g.markUnlisted()


    def getPrimaryList(self, dystopic):
        """Get the list of primary Guards for a given dystopia setting """
        if dystopic:
            return self._PRIMARY_DYS
        else:
            return self._PRIMARY_U

    def getFullList(self, dystopic):
        """Get the list of possible Nodes from the consensus for a given
           dystopia setting"""
        if dystopic:
            return self._DYSTOPIC_GUARDS
        else:
            return self._UTOPIC_GUARDS

    def getNPrimary(self, dystopic):
        """Return the number of listed primary guards that we'll allow."""
        total_running_guards = len(self._net.new_consensus())

        if dystopic:
            r = floor(total_running_guards * self._p.DYSTOPIC_GUARDS_THRESHOLD)
        else:
            r = floor(total_running_guards * self._p.UTOPIC_GUARDS_THRESHOLD)

        return r

    def addGuard(self, node, dystopic=False):
        """Try to add a single Node 'node' to the 'dystopic' guard list."""
        lst = self.getPrimaryList(dystopic)

        # prop241: if we have added too many guards too recently, die!
        # XXXX Is this what prop241 actually says?

        nRecent = 1 # this guard will be recent.
        for g in lst:
            if g.addedWithin(self._p.TOO_RECENTLY):
                nRecent += 1

        if nRecent >= self._p.TOO_MANY_GUARDS:
            raise GivingUp("Too many guards added too recently!")

        # now actually add the guard.
        lst.append(Guard(node))

    def inADystopia(self):
        return False # Dystopia detection not implemented XXXXX

    def netLooksDown(self):
        return False # Downness detection not implemented XXXXX

    def nodeIsInGuardList(self, n, gl):
        """Return true iff there is a Guard in 'gl' corresponding to the Node
           'n'."""
        for g in gl:
            if g.getNode() == n:
                return True
        return False

    def getGuard(self, dystopic):
        """We're about to build a circuit: return a guard to try."""

        # This is the underlying list that we modify, AND the list
        # we look at.
        # XXXX are these supposed to be different lists?  Are we
        # XXXX also supposed to consider non-dystopian guards
        # XXXX when we think we're not in a dystopia?
        lst = self.getPrimaryList(dystopic)

        usable = [ g for g in lst if g.canTry() ]
        listed = [ g for g in lst if g.isListed() ]

        # See if we should retry or add more or use what we have.
        # Here we consider the number of currently-guardy guards.

        if len(usable) == 0 and len(listed) >= self.getNPrimary(dystopic):
            # We can't add any more and we don't have any to try.

            # XXXX should this be two separate timers, one for each list?
            if self._retryTimer.isReady():
                self._retryTimer.fire()
                for g in lst:
                    g.markForRetry()

            usable = [ g for g in lst if g.canTry() ]

            if not len(usable):
                return None

        if len(usable):
            # Just use the first one that isn't down.
            assert usable[0] != None
            return usable[0]

        # We can add another one.
        full = self.getFullList(dystopic)
        possible = [ n for n in full if not self.nodeIsInGuardList(n, lst) ]
        if len(possible) == 0:
            return None
        newnode = random.choice(possible)
        self.addGuard(newnode, dystopic)
        newguard = lst[-1]
        assert newguard.getNode() == newnode

        return newguard

    def connectToGuard(self, guard):
        """Try to connect to 'guard' -- if it's up on the network, mark it up.
           Return true on success, false on failure."""
        up = self._net.probe_node_is_up(guard.getNode())
        guard.mark(up)
        return up

    def buildCircuit(self):
        """Try to build a circuit; return true if we succeeded."""
        if self.netLooksDown():
            return False
        g = self.getGuard(self._maybeDystopic)
        if g == None and not self._maybeDystopic:
            # Perhaps we are in a dystopia and we don't know it?
            self._maybeDystopic = True
            # XXXX we never notice if we have left a dystopia.
            g = self.getGuard(True)
        if g == None:
            return False
        return self.connectToGuard(g)
