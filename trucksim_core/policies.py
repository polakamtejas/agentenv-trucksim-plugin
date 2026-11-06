"""Rule-based operators. `greedy` is the naive reference (PLAN.md M4): each idle unit takes the feasible board load it
can carry with the best revenue per total mile (empty + loaded), one load at a time, no chaining, no sharing a
trailer, no repositioning, no maintenance, no crew changes, and nothing an owner-operator would turn down. Breakdowns
get the cheaper repair. Policies use the same tool methods an agent does."""

from .game import ActionError, Game


def handle_breakdowns(game: Game, seat: str) -> None:
    for tid in game.seat_trucks(seat):
        tr = game.trucks[tid]
        if tr.broken and tr.broken["option"] is None and tr.spec.owner != "owner_operator":
            options = game.repair_options(tr)
            game.handle_breakdown(seat, tid, min(options, key=lambda k: (options[k]["usd"], k)))


def idle(game: Game, tid: str) -> bool:
    tr = game.trucks[tid]
    return bool(tr.crew) and not tr.broken and not tr.tasks and not tr.onboard


def acceptable(plan: dict) -> bool:
    """A feasible plan its unit's owner-operator (if it has one) would take."""
    return plan["feasible"] and plan.get("owner_operator", {"accepts": True})["accepts"]


def greedy(game: Game, seat: str) -> None:
    handle_breakdowns(game, seat)
    taken = set()
    for tid in game.seat_trucks(seat):
        if not idle(game, tid):
            continue
        best = None
        for offer in game.list_loads(seat)["loads"]:
            if offer["id"] in taken or offer["claimed_by_you"]:
                continue
            if game.trucks[tid].spec.vclass not in offer["fits"]:
                continue
            try:
                plan = game.plan_trip(seat, tid, [offer["id"]])
            except ActionError:
                continue
            if not acceptable(plan):
                continue
            miles = plan["deadhead_miles"] + plan["loaded_miles"]
            score = (offer["revenue_usd"] / miles if miles else 0.0, offer["id"])
            if best is None or score[0] > best[0][0] or (score[0] == best[0][0] and score[1] < best[0][1]):
                best = (score, offer["id"])
        if best:
            lid = best[1]
            game.accept_load(seat, lid)
            game.dispatch(seat, [{"truck": tid, "load_ids": [lid]}])
            taken.add(lid)
    game.end_turn(seat)
