import random
import sys
import simtime

def choose_node_by_bandwidth_weights(all_guards):
    bandwidths = compute_weighted_bandwidths(all_guards)
    bandwidths = scale_array_elements_to_u64(bandwidths)
    idx = choose_array_element_by_weight(bandwidths)
    
    if idx < 0: return None 
    return all_guards[idx]

def scale_array_elements_to_u64(bandwidths):
    scale_max = sys.maxint / 4
    total = sum(bandwidths)
    if total == 0: return []

    scale_factor = scale_max / total

    return [int(round(i * scale_factor)) for i in bandwidths]

def choose_array_element_by_weight(bandwidths):
    total = sum(bandwidths)

    if len(bandwidths) == 0: return -1
    if total == 0: return random.randint(0, len(bandwidths)-1)

    rand_value = random.randint(0, total-1)

    i = 0
    partial = 0
    for bw in bandwidths:
        partial += bw
        if partial > rand_value: return i
        i += 1

    assert(false)

def compute_weighted_bandwidths(guards):
    weight_scale = 10000

    # For GUARD
    wg = 6134.0
    wm = 6134.0
    we = 0.0
    wd = 0.0
    wgb = 10000.0
    wmb = 10000.0
    web = 10000.0
    wdb = 10000.0

    wg /= weight_scale
    wm /= weight_scale
    we /= weight_scale
    wd /= weight_scale
    wgb /= weight_scale
    wmb /= weight_scale
    web /= weight_scale
    wdb /= weight_scale

    bandwidths = []
    for guard in guards:
        bw_in_bytes = guard._node.bandwidth * 1000

        # the weights consider guards to be directory guards
        weight = wgb*wg
        weight_without_guard_flag = wmb*wm

        final_weight = weight*bw_in_bytes

        bandwidths.append(final_weight + 0.5)

    return bandwidths

def entry_is_time_to_retry(guard, time):
    if guard._lastAttempted < guard._unreachableSince:
        return True

    unreachableFor = time - guard._unreachableSince

    TIME_MAX = 0x7fffffff
    retryPeriods = [
        (6*60*60,    60*60),
        (3*24*60*60, 4*60*60),
        (7*24*60*60, 18*60*60),
        (TIME_MAX,   36*60*60)
    ]

    for periodDuration, intervalDuringPeriod in retryPeriods:
        if unreachableFor <= periodDuration:
            # XXX _lastAttempted can be None?
            deadlineForRetry = guard._lastAttempted + intervalDuringPeriod
            return time > deadlineForRetry

    return False

def entry_is_live(guard):
    if guard._badSince:
        return False

    if not guard._canRetry and guard._unreachableSince and not entry_is_time_to_retry(guard, simtime.now()):
        return False

    if not guard._listed:
        return False

    #XXX Add node_is_unreliable ?

    return True
