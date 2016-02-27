import random
import pprint

class Guard(object):
    """Represents what a client knows about a guard."""

    GUARDS = {}

    @classmethod
    def get(cls, node):
        if node not in cls.GUARDS: cls.GUARDS[node] = Guard(node)
        return cls.GUARDS[node]

    @classmethod
    def markAllUnlisted(cls):
        for g in cls.GUARDS:
            cls.GUARDS[g].markUnlisted()

    def __repr__(self):
        return pprint.pformat(vars(self), indent=6, width=2)

    def __init__(self, node, pDirectoryCache=0.9):
        # tornet.Node instance
        self._node = node

        # True iff we have marked this node as down.
        self._markedDown = False

        # True iff we have marked this node as up.
        self._markedUp = False

        # True iff the node is listed as a guard in the most recent consensus
        self._listed = False

        # TODO: How is this different from lastAttempted?
        # The timestamp of the last time it tried to connecto to this node.
        self._lastTried = None

        ############################
        # --- From entry_guard_t ---#
        ############################

        # When did we add it (simulated)?
        # XXX is guard._addedAt = entry->chosen_on_date?
        self._addedAt = None

        # Is this node a directory cache?
        # XXX update pDirectoryCache with something closer to reality
        self._isDirectoryCache = random.random() < pDirectoryCache

        # Time when the guard went to a bad state
        self._badSince = None

        # False if we have never connected to this router, True if we have
        self._madeContact = None

        # The time at which we first noticed we could not connect to this node
        self._unreachableSince = None

        # None if we can connect to this guard, or the time at which we last
        # failed to connect to this node
        # XXX: I guess this description (from tor) is incorrect, since we mark
        # it as now when a connection succeeds after the guard has been
        # unreachable for some time.
        self._lastAttempted = None

        # Should we retry connecting to this entry, in spite of having it
        # marked as unreachable?
        self._canRetry = None

    def __str__(self):
        return "%s" % self._node._id

    @property
    def node(self):
        """Return the underlying torsim.Node object for this guard."""
        return self._node

    def mark(self, up):
        """Mark this guard as up or down because of a successful/unsuccessful
        connection attempt.
        """
        if up:
            if not self._markedUp:
                print("Marked %s (%stopic) up" %
                      (self, "dys" if self._node.seemsDystopic() else "u"))
            self._markedDown = False
            self._markedUp = True
        else:
            if not self._markedDown:
                print("Marked %s (%stopic) down" %
                      (self, "dys" if self._node.seemsDystopic() else "u"))
            self._markedDown = True
            self._markedUp = False

    def markUnlisted(self):
        """Mark this guard as unlisted because it didn't appear in the most
        recent consensus.
        """
        self._listed = False

    def markListed(self):
        """Mark this guard as listed because it did appear in the most recent
        consensus.
        """
        self._listed = True

    def canTry(self):
        """Return true iff we can try to make a connection to this guard."""
        # XXXX this should be extended according to tor code
        return self._listed and not (self._madeContact and self._markedDown)

    def isListed(self):
        """Return true iff the guard is listed in the most recent consensus
        we've seen.
        """
        return self._listed

    def isBad(self):
        return not self._listed

    def isUp(self):
        """Return true iff the guard is up"""
        return self.node.isReallyUp()

    def markForRetry(self):
        """Mark this guard as untried, so that we'll be willing to try it
        again.
        """
        # XXXX We never call this unless _all_ the guards in group seem
        # XXXX down.  But maybe we should give early guards in a list
        # XXXX a chance again after a while?
        self._canRetry = True

    def addedWithin(self, nSec):
        """Return ``True`` iff this guard was added within the last **nSec**
        simulated seconds.
        """
        return self._addedAt + nSec >= simtime.now()

    def isBad(self):
        return self.isListed() or not self.isUp()
