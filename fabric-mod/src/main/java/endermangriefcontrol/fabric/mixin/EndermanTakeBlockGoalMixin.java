package endermangriefcontrol.fabric.mixin;

import endermangriefcontrol.fabric.EndermanGriefControlMod;
import net.minecraft.world.entity.monster.EnderMan;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Targets the private inner class net.minecraft.world.entity.monster.EnderMan$EndermanTakeBlockGoal,
 * which governs enderman block pickup. canUse() already gates on the mobGriefing gamerule and a
 * random chance; we inject at RETURN so we only override (and log) the rare ticks where the goal
 * would have actually activated, rather than every tick it's polled.
 */
@Mixin(targets = "net/minecraft/world/entity/monster/EnderMan$EndermanTakeBlockGoal")
public abstract class EndermanTakeBlockGoalMixin {

    @Shadow
    private EnderMan enderman;

    @Inject(method = "canUse", at = @At("RETURN"), cancellable = true)
    private void noEndermanGrief$preventPickup(CallbackInfoReturnable<Boolean> cir) {
        if (cir.getReturnValueZ() && EndermanGriefControlMod.getConfig().enabled) {
            cir.setReturnValue(false);
            EndermanGriefControlMod.announceBlocked(this.enderman, "pickup");
        }
    }
}
