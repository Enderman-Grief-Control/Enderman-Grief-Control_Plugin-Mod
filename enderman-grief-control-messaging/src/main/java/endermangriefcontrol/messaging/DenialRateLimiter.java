package endermangriefcontrol.messaging;

import java.util.HashMap;
import java.util.Map;
import java.util.Optional;

/**
 * Throttles denial chat announcements per world/dimension scope and {@link DenialType},
 * independently of each other.
 *
 * Event-driven rather than scheduled: there's no background task flushing on a timer. Each denial
 * increments a pending count for its scope/type pair; if the cooldown has elapsed since that
 * pair's last flush, the accumulated count is returned (and reset) for the caller to announce,
 * otherwise it's silently tallied. The first denial for a pair always flushes immediately, since
 * there's nothing to wait out yet. The one tradeoff: the tail of a burst isn't flushed until
 * another denial of that pair arrives after the cooldown - acceptable for a debug/audit feature,
 * not worth adding a scheduler to avoid.
 */
public final class DenialRateLimiter {

    private long cooldownMillis;
    private final Map<DenialKey, State> states = new HashMap<>();

    public DenialRateLimiter(long cooldownMillis) {
        this.cooldownMillis = cooldownMillis;
    }

    public void setCooldownMillis(long cooldownMillis) {
        this.cooldownMillis = cooldownMillis;
    }

    public Optional<Integer> recordDenial(String scope, DenialType type) {
        return recordDenial(scope, type, System.currentTimeMillis());
    }

    Optional<Integer> recordDenial(String scope, DenialType type, long nowMillis) {
        State state = states.computeIfAbsent(new DenialKey(scope, type), ignored -> new State());
        state.pendingCount++;

        if (state.lastFlushMillis == null || nowMillis - state.lastFlushMillis >= cooldownMillis) {
            int count = state.pendingCount;
            state.pendingCount = 0;
            state.lastFlushMillis = nowMillis;
            return Optional.of(count);
        }

        return Optional.empty();
    }

    private static final class State {
        int pendingCount;
        Long lastFlushMillis;
    }

    private record DenialKey(String scope, DenialType type) {
        private DenialKey {
            if (scope == null) {
                scope = "";
            }
        }
    }
}
