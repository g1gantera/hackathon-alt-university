import {translate, displayText} from './i18n/core.ts';
import type { EcoResult, Profile, Snapshot } from './types.ts';
export const advicePhase = { get waiting() {
        return translate("Ожидать отправления");
    }, get held() {
        return translate("Ожидать нового плана");
    }, get accelerating() {
        return translate("Набирать скорость");
    }, get cruising() {
        return translate("Поддерживать скорость");
    }, get braking() {
        return translate("Тормозить к станции");
    }, get completed() {
        return translate("Маршрут завершён");
    } };
export function currentProfile(profile: Profile | null, snapshot: Snapshot, trainId: string): Profile | null {
    if (!profile || profile.train_id !== trainId || profile.epoch !== snapshot.epoch || profile.plan_id !== snapshot.active_plan_id || profile.constraint_version !== snapshot.constraint_version)
        return null;
    if ((snapshot.awaiting_plan || snapshot.dispatch?.valid === false) && !profile.provisional)
        return null;
    return profile;
}
export function currentProposal(result: EcoResult | null, snapshot: Snapshot, trainId: string): boolean {
    return !!result && result.epoch === snapshot.epoch && result.base_plan_id === snapshot.active_plan_id && result.constraint_version === snapshot.constraint_version && result.sim_time_s === snapshot.sim_time_s && !snapshot.awaiting_plan && !snapshot.replanning && !snapshot.running && (!result.plan || result.plan.eco?.train_id === trainId);
}
