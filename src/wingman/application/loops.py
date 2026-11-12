"""One-card interview engine, with signed previews and atomic confirmed writes.

No model or network calls. Classification is selected and confirmed by the
person; playbooks are authored suggestions, never diagnoses or evidence.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import date
from typing import Any, Literal, cast

from wingman.domain.loops import Capture, LoopSession
from wingman.infrastructure.config import Config
from wingman.infrastructure.loop_library import catalogue
from wingman.infrastructure.storage import Storage

MAX_CONFIRMATION_CHARS = 200_000

CATEGORIES = {
    "getting_started": "Getting started",
    "too_much": "Too many competing demands",
    "unclear_next_step": "Unclear next step",
    "keeping_going": "Keeping going consistently",
    "confidence": "Confidence or rehearsal",
    "support": "Needing support",
    "energy_or_time": "Limited time or energy",
    "none": "None of these fits / I don't know",
}


def list_loops(config: Config) -> dict[str, Any]:
    return {
        "loops": [
            {
                "id": d.id,
                "version": d.version,
                "hash": digest,
                "tier": tier,
                "experience_promise": d.experience_promise,
                "duration_min": d.duration_min,
                "sensitivity": d.sensitivity,
                "writes": d.writes,
            }
            for d, digest, tier in catalogue(config.data_dir / "loops")
        ]
    }


def _active_action(storage: Storage, subject: str, loop_id: str) -> Capture | None:
    records = storage.list_goals_plan(subject)
    finished = {
        r.details.get("action_id")
        for r in records
        if r.kind == "action_status" and r.text in {"done", "superseded"}
    }
    return next(
        (
            r
            for r in reversed(records)
            if r.kind == "action" and r.loop_id == loop_id and r.capture_id not in finished
        ),
        None,
    )


def start_loop(
    config: Config, storage: Storage, loop_id: str = "goals-friction", subject: str = "self"
) -> dict[str, Any]:
    existing = storage.open_loop_session(subject, loop_id)
    if existing:
        return _with_previous(storage, existing)
    entry = next((e for e in catalogue(config.data_dir / "loops") if e[0].id == loop_id), None)
    if entry is None:
        raise ValueError("unknown loop; use loop_list")
    definition, digest, tier = entry
    previous = _active_action(storage, subject, loop_id)
    session = LoopSession(
        subject=subject,
        definition=definition,
        loop_hash=digest,
        tier=cast(Literal["official", "local"], tier),
        secret=secrets.token_hex(32),
        previous_action=previous.capture_id if previous else "",
        phase="reentry_status" if previous else "answer",
    )
    # Only session metadata and the public loop definition are saved at start.
    storage.create_loop_session(session)
    result = _with_previous(storage, session)
    if previous:
        result["last_committed_action"] = previous.model_dump(mode="json")
    return result


def _with_previous(storage: Storage, session: LoopSession) -> dict[str, Any]:
    result = render_card(session)
    if session.previous_action:
        previous = next(
            (
                c
                for c in storage.list_goals_plan(session.subject)
                if c.capture_id == session.previous_action
            ),
            None,
        )
        if previous:
            result["last_committed_action"] = previous.model_dump(mode="json")
            if session.previous_completed:
                queued = previous.details.get("queued_moves", [])
                if isinstance(queued, list) and queued:
                    result["unlocked_move"] = queued[0]
                    result["locked_move_count"] = max(0, len(queued) - 1)
    return result


def _options(session: LoopSession) -> list[dict[str, str]]:
    card = session.definition.cards[session.index]
    if session.phase == "queue_pick":
        return [
            {"id": "next", "label": "Work on the next queued move"},
            {"id": "new", "label": "Start with what matters now"},
        ]
    if session.phase == "category":
        return [{"id": key, "label": label} for key, label in CATEGORIES.items()]
    if session.phase == "frame":
        return [{"id": "goal", "label": "A goal"}, {"id": "problem", "label": "A problem"}]
    if session.phase == "more":
        return [
            {"id": "continue", "label": "Explore one level further"},
            {"id": "stop", "label": "That's enough for now"},
        ]
    if session.phase == "reentry_status":
        return [{"id": "done", "label": "I did it"}, {"id": "not_yet", "label": "Not yet"}]
    if session.phase != "answer":
        return []
    if card.options_from == "ladder.goals":
        words = session.answers.get("want", []) + session.answers.get("ladder", [])
        return [{"id": str(i), "label": word} for i, word in enumerate(dict.fromkeys(words))]
    if card.options_from == "playbook(stuck)":
        return [
            {"id": str(i), "label": move.move}
            for i, move in enumerate(session.definition.playbook)
            if move.category == session.category
        ] + [{"id": "own_step", "label": "Choose my own first step"}]
    return [{"id": str(i), "label": label} for i, label in enumerate(card.options)]


def render_card(session: LoopSession) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": session.session_id,
        "subject": session.subject,
        "loop_id": session.definition.id,
        "loop_version": session.definition.version,
        "loop_hash": session.loop_hash,
        "tier": session.tier,
        "revision": session.revision,
        "status": session.status,
        "experience_promise": session.definition.experience_promise,
        "duration_min": session.definition.duration_min,
        "sensitivity": session.definition.sensitivity,
        "acting_as": "yourself" if session.subject == "self" else f"coach for {session.subject}",
        "privacy": "Answers and felt responses stay in this workspace. Nothing is fetched or sent.",
    }
    if session.status == "completed":
        base.update({"next_action": session.step or None, "message": "Loop complete."})
        return base
    card = session.definition.cards[session.index]
    ask = card.ask or (card.ask_if_problem if session.framing == "problem" else card.ask_if_goal)
    explain = card.explain
    shape = card.type
    if session.phase == "queue_pick":
        explain = "You finished the last step. Only the next queued move is available now."
        ask, shape = "Would you like to work on that move next?", "options"
    elif session.phase == "category":
        explain = (
            "This is an optional interpretation of your obstacle, never evidence or a diagnosis."
        )
        ask, shape = "Which category, if any, fits what you said?", "options"
    elif session.phase == "frame":
        explain = (
            "Your wording can describe either a goal or a problem. You choose how to frame it."
        )
        ask, shape = "How should we frame what you want to work on?", "options"
    elif session.phase == "more":
        explain = f"You have explored {session.repeat_count} of at most {card.repeat.max if card.repeat else 1} levels."
        ask, shape = "Would you like to explore one level further?", "options"
    elif session.phase == "due":
        explain = (
            "This makes only your first step a commitment. The other selected moves stay queued."
        )
        ask, shape = "By what date will you do that first step? (YYYY-MM-DD)", "free_text"
        base["first_step"] = session.step
    elif session.phase == "felt_reason":
        explain = "Your rating is saved. Your own words explain what made the step land that way."
        ask, shape = "What made you feel that way?", "free_text"
    elif session.phase == "reentry_status":
        explain = "Before starting again, check your last committed action. There is no penalty for not doing it."
        ask, shape = "Did you do your last committed action?", "options"
    elif session.phase == "reentry_friction":
        explain = "What happened is useful information for the next attempt. This stays with your goals and friction."
        ask, shape = "What got in the way?", "free_text"
    elif card.type == "scale_then_free_text":
        shape = "scale"
    base["card"] = {
        "id": card.id,
        "phase": session.phase,
        "position": session.index + 1,
        "total": len(session.definition.cards),
        "explain": explain,
        "ask": ask,
        "type": shape,
        "anchoring": "none" if shape == "free_text" else None,
        "options": _options(session),
        "max_picks": card.max_picks,
        "exits": [
            {"action": "skip", "label": "Skip this question"},
            {"action": "pause", "label": "Stop and resume later"},
        ]
        + [{"action": "route", "label": e.label, "route": e.route} for e in card.exit],
        "recall": {key: session.answers.get(key, []) for key in card.recall},
        "display_only": card.display_only,
    }
    if card.repeat:
        target = session.answers.get(card.id, []) or session.answers.get("want", [])
        base["ladder_target"] = target[-1] if target else None
    if session.queue_offer:
        base["selected_queued_move"] = session.queue_offer
    if shape == "scale":
        base["card"]["scale"] = {"min": 1, "max": 5, "low": "Very uneasy", "high": "Very ready"}
    if card.options_from == "playbook(stuck)" and session.phase == "answer":
        moves = [
            {
                "id": str(i),
                "move": move.move,
                "first_step": move.first_step,
                **({"rationale": move.rationale} if session.learning == "1" else {}),
            }
            for i, move in enumerate(session.definition.playbook)
            if move.category == session.category
        ]
        base["suggestions"] = moves
        base["presentation"] = {
            "0": "move_first",
            "1": "reasoning_with_move",
            "2": "first_step_example_first",
        }.get(session.learning, "move_first")
        if session.learning == "2":
            base["suggestions"] = [{"example_first_step": m["first_step"], **m} for m in moves]
        if not moves:
            base["message"] = (
                "No suitable playbook move is available. You can choose your own first step."
            )
    return base


def _advance(session: LoopSession) -> None:
    session.index += 1
    session.phase = "answer"
    session.repeat_count = 0
    if session.index == len(session.definition.cards):
        session.status = "completed"
        return
    card = session.definition.cards[session.index]
    if card.classify:
        session.phase = "frame"


def _capture(session: LoopSession, text: str, persona_authored: bool) -> Capture:
    return Capture(
        session_id=session.session_id,
        subject=session.subject,
        loop_id=session.definition.id,
        loop_version=session.definition.version,
        loop_hash=session.loop_hash,
        card_id=session.definition.cards[session.index].id,
        kind="answer",
        text=text,
        authorship="coach_inference"
        if session.subject != "self" and not persona_authored
        else "person",
        classification="inference"
        if session.subject != "self" and not persona_authored
        else "own_words",
    )


def _preview(
    session: LoopSession,
    answer: str,
    choices: list[str],
    persona_authored: bool,
    owner: str,
    rescue: bool = False,
    step_source: Capture | None = None,
) -> tuple[LoopSession, list[Capture]]:
    next_session = session.model_copy(deep=True)
    card = session.definition.cards[session.index]
    if len(answer) > 10_000 or len(choices) > 3:
        raise ValueError("answer exceeds the card limits")
    capture = _capture(session, answer, persona_authored)
    phase = session.phase
    options = {o["id"]: o["label"] for o in _options(session)}
    if rescue:
        if session.subject == "self" or not answer.strip():
            raise ValueError("rescue notes require an active coaching persona and text")
        capture.kind = "rescue"
        capture.authorship, capture.classification = "coach_inference", "inference"
        return next_session, [capture]
    if options:
        selected = choices or [answer]
        if len(set(selected)) != len(selected) or any(s not in options for s in selected):
            raise ValueError("choose only ids offered on the current card")
        if len(selected) > (card.max_picks if phase == "answer" else 1):
            raise ValueError("too many selections")
        if "own_step" in selected and len(selected) != 1:
            raise ValueError("choose your own step or playbook moves, not both")
        capture.text = "\n".join(options[s] for s in selected)
        capture.details["choice_ids"] = selected
        if card.options_from != "ladder.goals":
            capture.classification = "inference"
        choice = selected[0]
        if phase == "queue_pick":
            if choice == "next":
                next_session.queue_offer = session.remaining_queue[0]
                next_session.remaining_queue = session.remaining_queue[1:]
                next_session.index = next(
                    i for i, c in enumerate(session.definition.cards) if c.type == "commit_action"
                )
            next_session.phase = "answer"
        elif phase == "category":
            next_session.category = choice
            capture.kind, capture.classification = "inference", "inference"
            _advance(next_session)
        elif phase == "frame":
            next_session.framing = cast(Literal["goal", "problem"], choice)
            next_session.phase = "answer"
            capture.kind, capture.classification = "inference", "inference"
        elif phase == "more":
            if choice == "continue":
                next_session.phase = "answer"
            else:
                _advance(next_session)
        elif phase == "reentry_status":
            capture.card_id, capture.kind, capture.text = "reentry", "action_status", choice
            capture.details["action_id"] = session.previous_action
            if choice == "done":
                next_session.phase = "answer"
                next_session.reentry_done = True
                next_session.previous_completed = True
            else:
                next_session.phase = "reentry_friction"
        else:
            if card.effect:
                next_session.learning = choice
            if card.options_from == "playbook(stuck)":
                next_session.selected_moves = [] if choice == "own_step" else selected
            next_session.answers.setdefault(card.id, []).append(capture.text)
            _advance(next_session)
    elif card.type == "options" and phase == "answer":
        raise ValueError("no choices are available; skip this card")
    elif phase == "due":
        try:
            date.fromisoformat(answer)
        except ValueError:
            raise ValueError("due date must be YYYY-MM-DD; nothing saved") from None
        if owner not in {"user", "coach"} or (owner == "coach" and session.subject == "self"):
            raise ValueError("owner must be user, or coach in coaching mode")
        if step_source is None:
            raise ValueError(
                "confirmed first-step record unavailable; skip this commitment and start again"
            )
        capture.kind, capture.text = "action", session.step
        capture.details.update(
            {
                "step_capture_id": step_source.capture_id,
                "deadline_authorship": capture.authorship,
                "deadline_classification": capture.classification,
                "due_date": answer,
                "owner": owner,
                "status": "committed",
                "queued_moves": session.remaining_queue
                if session.queue_offer
                else [session.definition.playbook[int(i)].move for i in session.selected_moves[1:]],
            }
        )
        capture.authorship = step_source.authorship
        capture.classification = step_source.classification
        _advance(next_session)
    elif phase == "reentry_friction":
        if not answer.strip():
            raise ValueError("answer is empty; use skip or pause instead")
        capture.card_id = "reentry_friction"
        next_session.phase = "answer"
        next_session.reentry_done = True
    elif phase == "felt_reason":
        if not answer.strip():
            raise ValueError("answer is empty; use skip or pause instead")
        capture.kind = "felt"
        capture.details["scale"] = session.scale if session.scale is not None else 0
        _advance(next_session)
    elif card.type in {"scale", "scale_then_free_text"}:
        if answer not in {"1", "2", "3", "4", "5"}:
            raise ValueError("choose a rating from 1 to 5")
        next_session.scale = int(answer)
        capture.kind = "felt"
        capture.details["scale"] = int(answer)
        if card.type == "scale_then_free_text":
            next_session.phase = "felt_reason"
        else:
            _advance(next_session)
    else:
        if not answer.strip():
            raise ValueError("answer is empty; use skip or pause instead")
        next_session.answers.setdefault(card.id, []).append(answer)
        if card.type == "commit_action":
            next_session.step = answer
            next_session.phase = "due"
        elif card.then:
            next_session.phase = "category"
        elif card.repeat:
            next_session.repeat_count += 1
            if next_session.repeat_count < card.repeat.max:
                next_session.phase = "more"
            else:
                _advance(next_session)
        else:
            _advance(next_session)
    return next_session, [capture]


def _encode(session: LoopSession, data: dict[str, Any]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data, ensure_ascii=False).encode()).decode()
    signature = hmac.new(session.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    token = f"{payload}.{signature}"
    if len(token) > MAX_CONFIRMATION_CHARS:
        raise ValueError(
            "answer is too large to confirm; shorten it and answer this card again. Nothing saved."
        )
    return token


def _decode(session: LoopSession, token: str) -> dict[str, Any]:
    try:
        if len(token) > MAX_CONFIRMATION_CHARS:
            raise ValueError("oversized confirmation")
        payload, signature = token.rsplit(".", 1)
        expected = hmac.new(session.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid confirmation")
        value: dict[str, Any] = json.loads(base64.urlsafe_b64decode(payload))
        if value["revision"] != session.revision or value["session_id"] != session.session_id:
            raise ValueError("stale confirmation; resume and review the current card")
        return value
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid confirmation; nothing saved") from exc


def answer_loop(
    storage: Storage,
    session_id: str,
    subject: str = "self",
    *,
    action: str = "answer",
    answer: str = "",
    choices: list[str] | None = None,
    confirmation: str = "",
    persona_authored: bool = False,
    owner: str = "user",
) -> dict[str, Any]:
    session = storage.get_loop_session(session_id, subject)
    if session is None:
        raise ValueError("no session in the current subject's workspace")
    if action == "show":
        return _with_previous(storage, session)
    if session.status == "completed":
        raise ValueError("session completed; start the loop again for a check-in")
    if action == "pause":
        return {**render_card(session), "message": "Paused. Resume at this card with loop_start."}
    if action == "route":
        card = session.definition.cards[session.index]
        if answer not in {e.route for e in card.exit}:
            raise ValueError("route not offered on the current card")
        return {
            **render_card(session),
            "handoff": {
                "route": "reactions",
                "tool": "perspectives_start",
                "message": "Choose the reaction interview if you want. This loop is paused.",
            },
        }
    if action == "skip":
        next_session = session.model_copy(deep=True)
        if session.phase.startswith("reentry") or session.phase == "queue_pick":
            next_session.phase = "answer"
        else:
            if session.definition.cards[session.index].type == "commit_action":
                next_session.step = ""
            _advance(next_session)
        next_session.revision += 1
        storage.commit_loop_step(next_session, session.revision, [])
        return render_card(next_session)
    if action == "confirm":
        data = _decode(session, confirmation)
        state = session.model_dump(mode="json")
        state.update(data["next"])
        # Retain support for pending previews from the earlier full-list format.
        state["answers"] = {**session.answers, **data.get("answers", {})}
        for key, additions in data.get("answers_append", {}).items():
            state["answers"][key] = session.answers.get(key, []) + additions
        next_session = LoopSession.model_validate(state)
        captures = [Capture.model_validate(c) for c in data["captures"]]
        # The signed next state is scoped and version-pinned, never supplied by a loop.
        storage.commit_loop_step(next_session, session.revision, captures)
        return {
            **_with_previous(storage, next_session),
            "saved": [c.model_dump(mode="json") for c in captures],
        }
    if action not in {"answer", "rescue"}:
        raise ValueError("unknown action; use answer, confirm, skip, pause, route, show or rescue")
    step_source = None
    if session.phase == "due" and action == "answer":
        # Read the already-confirmed step, including for sessions created by an
        # older server. The deadline's author cannot relabel those earlier words.
        step_source = next(
            (
                record
                for record in reversed(storage.list_goals_plan(subject))
                if record.session_id == session_id
                and record.card_id == session.definition.cards[session.index].id
                and record.kind == "answer"
                and record.text == session.step
            ),
            None,
        )
    next_session, captures = _preview(
        session,
        answer,
        choices or [],
        persona_authored,
        owner,
        rescue=action == "rescue",
        step_source=step_source,
    )
    if captures[0].kind == "action_status" and captures[0].text == "done":
        previous = _active_action(storage, subject, session.definition.id)
        queued = previous.details.get("queued_moves", []) if previous else []
        if (
            isinstance(queued, list)
            and queued
            and any(c.type == "commit_action" for c in session.definition.cards)
        ):
            next_session.remaining_queue = queued
            next_session.phase = "queue_pick"
    if captures[0].kind == "action":
        previous = _active_action(storage, subject, session.definition.id)
        if previous:
            retired = _capture(session, "superseded", persona_authored)
            retired.kind = "action_status"
            retired.details["action_id"] = previous.capture_id
            captures.append(retired)
    next_session.revision += 1
    before = session.model_dump(mode="json")
    after = next_session.model_dump(mode="json")
    # Sign only the transition, not the entire pinned definition and history.
    # This bounds tokens even for long loops. Secrets never reach the client.
    data = {
        "session_id": session.session_id,
        "revision": session.revision,
        "next": {
            key: value
            for key, value in after.items()
            if key not in {"secret", "definition", "answers"} and value != before[key]
        },
        "answers_append": {
            key: value[len(session.answers.get(key, [])) :]
            for key, value in next_session.answers.items()
            if value != session.answers.get(key)
        },
        "captures": [c.model_dump(mode="json") for c in captures],
    }
    token = _encode(session, data)
    return {
        "session_id": session.session_id,
        "subject": subject,
        "status": "confirm_required",
        "card_id": session.definition.cards[session.index].id,
        "explain": "Review this exact capture. Confirm to save it, or answer again to revise it. Nothing from this answer has been saved.",
        "preview": data["captures"],
        "confirmation": token,
        "exits": [
            {"action": "pause", "label": "Stop without saving"},
            {"action": "skip", "label": "Skip without saving"},
        ],
    }
