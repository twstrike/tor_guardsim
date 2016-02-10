## Existing Tor Guard Selection Algorithm

- ALL_GUARD_LIST = guard information from latest consensus
- GUARD_LIST = guards persisted to our state file
- DIRECTORY_GUARD = if we select guards with the V2Dir flag. Guards with the V2Dir Flag can be used as entry guards for both fetching information from directories as well as for standard entry guards.   

### ON_BOOTSTRAP (no existing guards)
  1. RECEIVE_NEW_CONSENSUS
  2. From listed guards in ALL_GUARD_LIST with DIRECTORY_GUARD=true: 
    1. 3 times do (default guard value on startup): 
      1. ADD_RANDOM_ENTRYGUARD to choose a guard
      2. Add this new guard to GUARD_LIST

### RECEIVE_NEW_CONSENSUS
  1. Mark guards that are not listed in the latest consensus as "bad" in ALL_GUARD_LIST
  2. Remove guards that have been dead for 30 days from GUARD_LIST and ALL_GUARD_LIST
  3. Remove guards that were added more than 30 days ago from GUARD_LIST and ALL_GUARD_LIST

### BUILD_NEW_CIRCUIT
  1. First, CHOOSE_A_GUARD
  2. Then CONNECT_ENTRY_GUARD with our chosen guard

### CHOOSE_A_GUARD
  1. Ensure that we have enough entry guards (only need 1)
    1. If we do not:
      1. Use the ADD_RANDOM_ENTRYGUARD algorithm to choose a new guard, DIRECTORY_GUARD=false
      2. Add new guard to GUARD_LIST
  2. From GUARD_LIST of entry guards:
    1. Select only the live guards that are:
      1. listed, except:
        1. offline and previously tried
    2. From this list of live guards
      1. Choose one at random if the list contains:
        1. Guards that we have not tried, or
        2. Or the list contains at least the number that we needed (which is 1)
    3. Otherwise (if the list of live guards contains only 1):
      1. Relax our constraints (bandwidth, uptime, for directory, etc), and
      2. Use the ADD_RANDOM_ENTRYGUARD algorithm to choose a new guard
      3. Try to choose a new guard, starting over from b.


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
      2. [what is the fallback behavior? Do we choose another guard from GUARD_LIST]?
  2. If we have made contact with this guard before:
    1. If we cannot connect:
      1. Mark this guard as offline
