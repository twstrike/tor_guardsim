## Existing Tor Guard Selection Algorithm

### Data Structures
- ALL_GUARD_LIST = guard information from latest consensus
- GUARD_LIST = guards persisted to our state file
- LIVE_ENTRY_GUARDS = guards from GUARD_LIST that is: 1) listed in the latest consensus, 2) not offline and 3) we have not previously tried. See `entry_is_live` for more criteria.

### Guard criteria
- DIRECTORY_GUARD = if we select guards with the V2Dir flag. Guards with the V2Dir Flag can be used as entry guards for both fetching information from directories as well as for standard entry guards.
- MADE_CONTACT = we have tried and succeeded in connecting to this guard
- TRIED = we have tried connected and either succeeded or failed to this guard
- ADDED_AT = when this guard was added to the consensus

### Other
- NUM_NEEDED = the number of entry guards that we want to select from GUARD_LIST to build LIVE_ENTRY_GUARDS.

### ON_BOOTSTRAP (no existing guards) `(Should be Deprecated)`
  1. Reloading_NEW_CONSENSUS -> Reloading_NEW_CONSENSUS
  2. From listed guards in ALL_GUARD_LIST:
    1. Choose 3 new guards that are both DIRECTORY_GUARDS and LIVE_ENTRY_GUARDS
    2. Add these new guards to GUARD_LIST

### RECEIVE_NEW_CONSENSUS `(Should be Deprecated)`
  1. Mark guards that are not listed in the latest consensus as "bad" in ALL_GUARD_LIST
  2. Remove guards that have been dead for 30 days from GUARD_LIST and ALL_GUARD_LIST
  3. Remove guards that were added more than 30 days ago from GUARD_LIST and ALL_GUARD_LIST

### BUILD_NEW_CIRCUIT `(Should be Deprecated)`
  1. CHOOSE_A_GUARD (DIRECTORY_GUARD=FALSE)
  2. CONNECT_ENTRY_GUARD with our chosen guard

### BUILD_NEW_CIRCUIT
  1. Keep trying until CIRCUIT is not None
      1. use NEXT algo to get a GUARD
      2. try use GUARD to build a CIRCUIT
  2. add the GUARD to USED_GUARDS
  3. return the CIRCUIT

### CHOOSE_A_GUARD (NUM_NEEDED, DIRECTORY_GUARD) `(Should be Deprecated)`
  1. Ensure that we have enough entry guards, for the DIRECTORY_GUARD condition
    1. If we do not:
      1. Add new guard to GUARD_LIST
    1. If we do:
      1. Use CHOOSE_RANDOM_ENTRYGUARD to choose a new guard
  2. From GUARD_LIST of entry guards:
    1. Build LIVE_ENTRY_GUARDS
      1. If we succeed building LIVE_GUARDS with either (or both) of the following conditions, then we choose one guard at random:
        1. In order to reduce exposure to guards, LIVE_ENTRY_GUARDS will have at max 1 guard that we have not MADE_CONTACT
        2. At max, LIVE_ENTRY_GUARDS contains NUM_NEEDED guards (listed and known to be not offline)
    2. Otherwise:
      1. Relax our constraints (bandwidth, uptime, for directory, etc), and
      2. Use the CHOOSE_RANDOM_ENTRYGUARD algorithm to choose a new guard
      3. Try to choose a new guard, starting over from 2.

### NEXT algo
  1. USE the current Consensus to build an algo state machine contains USED_GUARDS, excludeNodes, nPrimaryGuards, guardsInConsensus, dystopicGuardsInConsensus, selectDirGuards=False
  2. GetNext using StateMachine and try directly after , it may failover between
  ```
        StatePrimaryGuards,
        StateTryUtopic,
        StateTryDystopic,
        StateRetryOnly
  ```
     until we find a guard which is able to connect

### CHOOSE_RANDOM_ENTRYGUARD `(Should be Deprecated)`
  1. Build weighted distribution of all guards based on bandwidth
  2. Pick a guard at random from this distribution

### CONNECT_ENTRY_GUARD `(Maybe need to be kept ??)`
  1. If we have never made contact with this guard before:
    1. If we can connect:
      1. Mark guard as we have made contact
      2. Assume the network was down, and mark all guards in GUARD_LIST for retry (except the guard that we just connected)
    2. If we cannot connect:
      1. Remove it from GUARD_LIST
      2. [Maybe look at fallback behavior if this fails]
  2. If we have made contact with this guard before:
    1. If we cannot connect:
      1. Mark this guard as offline
     2. [Maybe look at fallback behavior if this fails]

