package endermangriefcontrol.fabric.mixin;

import endermangriefcontrol.fabric.EndermanGriefControlMod;
import net.minecraft.world.entity.monster.EnderMan;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Targets the private inner class net.minecraft.world.entity.monster.EnderMan$EndermanLeaveBlockGoal,
 * which governs enderman block placement. See EndermanTakeBlockGoalMixin for why RETURN (not HEAD)
 * is used.
 */
@Mixin(targets = "net/minecraft/world/entity/monster/EnderMan$EndermanLeaveBlockGoal")
public abstract class EndermanLeaveBlockGoalMixin {

    @Shadow
    private EnderMan enderman;

    @Inject(method = "canUse", at = @At("RETURN"), cancellable = true)
    private void noEndermanGrief$preventPlacement(CallbackInfoReturnable<Boolean> cir) {
        if (cir.getReturnValueZ() && EndermanGriefControlMod.getConfig().enabled) {
            cir.setReturnValue(false);
            EndermanGriefControlMod.announceBlocked(this.enderman, "placement");
        }
    }
}
