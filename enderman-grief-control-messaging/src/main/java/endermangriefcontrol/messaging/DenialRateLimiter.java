package endermangriefcontrol.messaging;

import java.util.EnumMap;
import java.util.Map;
import java.util.Optional;

/**
 * Throttles denial chat announcements per {@link DenialType}, independently of each other.
 *
 * Event-driven rather than scheduled: there's no background task flushing on a timer. Each denial
 * increments a pending count for its type; if the cooldown has elapsed since that type's last
 * flush, the accumulated count is returned (and reset) for the caller to announce, otherwise it's
 * silently tallied. The first denial for a type always flushes immediately, since there's nothing
 * to wait out yet. The one tradeoff: the tail of a burst isn't flushed until another denial of
 * that type arrives after the cooldown - acceptable for a debug/audit feature, not worth adding a
 * scheduler to avoid.
 */
public final class DenialRateLimiter {

    private final long cooldownMillis;
    private final Map<DenialType, State> states = new EnumMap<>(DenialType.class);

    public DenialRateLimiter(long cooldownMillis) {
        this.cooldownMillis = cooldownMillis;
    }

    public Optional<Integer> recordDenial(DenialType type) {
        return recordDenial(type, System.currentTimeMillis());
    }

    Optional<Integer> recordDenial(DenialType type, long nowMillis) {
        State state = states.computeIfAbsent(type, ignored -> new State());
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
}
