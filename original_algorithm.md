## Existing Tor Guard Selection Algorithm

- ALL_GUARD_LIST = guard information from latest consensus
- GUARD_LIST = guards persisted to our state file
- DIRECTORY_GUARD = if we select guards with the V2Dir flag. Guards with the V2Dir Flag can be used as entry guards for both fetching information from directories as well as for standard entry guards.
- NUM_NEEDED = the number of entry guards that we want to select from GUARD_LIST to build LIVE_ENTRY_GUARDS.
- LIVE_ENTRY_GUARDS = guards from GUARD_LIST that are listed in the latest consensus and are not both offline and have been previously tried.

### ON_BOOTSTRAP (no existing guards)
  1. RECEIVE_NEW_CONSENSUS
  2. From listed guards in ALL_GUARD_LIST:
    1. Choose 3 new guards using ADD_RANDOM_ENTRYGUARD, with DIRECTORY_GUARD=true, NUM_NEEDED=3
    2. Add these new guards to GUARD_LIST

### RECEIVE_NEW_CONSENSUS
  1. Mark guards that are not listed in the latest consensus as "bad" in ALL_GUARD_LIST
  2. Remove guards that have been dead for 30 days from GUARD_LIST and ALL_GUARD_LIST
  3. Remove guards that were added more than 30 days ago from GUARD_LIST and ALL_GUARD_LIST

### BUILD_NEW_CIRCUIT
  1. First, CHOOSE_A_GUARD
  2. Then CONNECT_ENTRY_GUARD with our chosen guard

### CHOOSE_A_GUARD
  1. Ensure that we have enough entry guards (NUM_NEEDED=1)
    1. If we do not:
      1. Use the ADD_RANDOM_ENTRYGUARD algorithm to choose a new guard, DIRECTORY_GUARD=false
      2. Add new guard to GUARD_LIST
  2. From GUARD_LIST of entry guards:
    1. Build LIVE_GUARDS
      1. If we succeed building LIVE_GUARDS with either (or both) of the following conditions, then we choose one guard at random:
        1. In order to reduce exposure to guards, LIVE_GUARDS will have at max 1 guard that has not been tried.
        2. At max, LIVE_GUARDS contains NUM_NEEDED guards (listed and known to be not offline)
    2. Otherwise:
      1. Relax our constraints (bandwidth, uptime, for directory, etc), and
      2. Use the ADD_RANDOM_ENTRYGUARD algorithm to choose a new guard
      3. Try to choose a new guard, starting over from 2.

### ADD_RANDOM_ENTRYGUARD
  1. Build weighted distribution of all guards based on bandwidth
  2. Pick a guard at random from this distribution

### CONNECT_ENTRY_GUARD
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