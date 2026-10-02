"""Freeze before/after at application time, using one observation revision."""

import copy


def comparison(state, candidate):
    before = state["active_plan"]
    old = {(m["train_id"], m["leg"]): m for m in before["movements"]}
    changes = []
    for move in candidate["movements"]:
        prior = old.get((move["train_id"], move["leg"]))
        if prior and any(
            prior.get(k) != move.get(k) for k in ("start_s", "end_s", "main_track_id")
        ):
            changes.append(
                dict(
                    train_id=move["train_id"],
                    leg=move["leg"],
                    before={k: prior.get(k) for k in ("start_s", "end_s", "main_track_id")},
                    after={k: move.get(k) for k in ("start_s", "end_s", "main_track_id")},
                )
            )
    same_scope = before.get("forecast", {}).get("scope") == candidate.get("forecast", {}).get(
        "scope"
    )
    valid_before = not state["awaiting_plan"] and same_scope
    # A forecast from before a failure is not a valid counterfactual after it.
    return dict(
        version=1,
        epoch=state["epoch"],
        state_version=state["state_version"],
        constraint_version=state["constraint_version"],
        evaluated_at_s=state["sim_time_s"],
        before_plan_id=before["id"],
        after_plan_id=candidate["id"],
        changes=changes,
        before=copy.deepcopy(before["metrics"]) if valid_before else None,
        after=copy.deepcopy(candidate["metrics"]),
        before_applicable=valid_before,
        note="Сравнение на момент применения. Недействительный исходный прогноз не учитывается как экономический эффект.",
    )
