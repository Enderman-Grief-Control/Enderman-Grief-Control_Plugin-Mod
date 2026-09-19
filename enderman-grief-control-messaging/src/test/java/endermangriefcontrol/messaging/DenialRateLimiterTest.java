package endermangriefcontrol.messaging;

import org.junit.jupiter.api.Test;

import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DenialRateLimiterTest {

    @Test
    void firstDenialFlushesImmediately() {
        DenialRateLimiter limiter = new DenialRateLimiter(5000);

        Optional<Integer> result = limiter.recordDenial("world", DenialType.PLACEMENT, 0);

        assertEquals(Optional.of(1), result);
    }

    @Test
    void denialsWithinCooldownAreSilentlyTallied() {
        DenialRateLimiter limiter = new DenialRateLimiter(5000);
        limiter.recordDenial("world", DenialType.PLACEMENT, 0);

        Optional<Integer> result = limiter.recordDenial("world", DenialType.PLACEMENT, 1000);

        assertTrue(result.isEmpty());
    }

    @Test
    void denialAfterCooldownFlushesAccumulatedCount() {
        DenialRateLimiter limiter = new DenialRateLimiter(5000);
        limiter.recordDenial("world", DenialType.PLACEMENT, 0);
        limiter.recordDenial("world", DenialType.PLACEMENT, 1000);
        limiter.recordDenial("world", DenialType.PLACEMENT, 2000);

        Optional<Integer> result = limiter.recordDenial("world", DenialType.PLACEMENT, 5000);

        assertEquals(Optional.of(3), result);
    }

    @Test
    void placementAndPickupCooldownsAreIndependent() {
        DenialRateLimiter limiter = new DenialRateLimiter(5000);
        limiter.recordDenial("world", DenialType.PLACEMENT, 0);

        Optional<Integer> pickupResult = limiter.recordDenial("world", DenialType.PICKUP, 1000);

        assertEquals(Optional.of(1), pickupResult);
    }

    @Test
    void worldsHaveIndependentCooldownsForSameDenialType() {
        DenialRateLimiter limiter = new DenialRateLimiter(5000);
        limiter.recordDenial("world", DenialType.PLACEMENT, 0);

        Optional<Integer> otherWorldResult = limiter.recordDenial("world_nether", DenialType.PLACEMENT, 1000);

        assertEquals(Optional.of(1), otherWorldResult);
    }

    @Test
    void updatingCooldownPreservesPendingCounts() {
        DenialRateLimiter limiter = new DenialRateLimiter(5000);
        limiter.recordDenial("world", DenialType.PLACEMENT, 0);
        limiter.recordDenial("world", DenialType.PLACEMENT, 1000);

        limiter.setCooldownMillis(2000);
        Optional<Integer> result = limiter.recordDenial("world", DenialType.PLACEMENT, 2000);

        assertEquals(Optional.of(2), result);
    }
}
