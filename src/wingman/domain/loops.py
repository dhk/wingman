"""Declarative interviews: data, never executable expressions."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LoopData(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoopExit(LoopData):
    label: str = Field(min_length=1, max_length=200)
    route: str


class Repeat(LoopData):
    max: int = Field(ge=1, le=3)
    until: Literal["user_stops"]


class Card(LoopData):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    explain: str = Field(min_length=1, max_length=2000)
    type: Literal["free_text", "options", "scale", "scale_then_free_text", "commit_action"]
    ask: str = ""
    ask_if_goal: str = ""
    ask_if_problem: str = ""
    anchoring: Literal["none"] | None = None
    options: list[str] = Field(default_factory=list, max_length=20)
    options_from: Literal["ladder.goals", "playbook(stuck)"] | None = None
    max_picks: int = Field(default=1, ge=1, le=3)
    effect: Literal["show_rationale"] | None = None
    recall: list[str] = Field(default_factory=list)
    then: Literal["categorize"] | None = None
    classify: Literal["goal_or_problem"] | None = None
    repeat: Repeat | None = None
    exit: list[LoopExit] = Field(default_factory=list)
    queue_rest: bool = False
    owner: Literal["user", "coach"] = "user"
    stores_with: list[Literal["loop_id", "loop_version"]] = Field(default_factory=list)
    display_only: bool = False

    @model_validator(mode="after")
    def valid_question(self) -> Self:
        if not self.ask.strip() and not (self.ask_if_goal and self.ask_if_problem):
            raise ValueError(f"{self.id}: a question is required")
        if self.type == "free_text" and self.anchoring != "none":
            raise ValueError(f"{self.id}: free text requires anchoring: none")
        if self.type == "free_text" and (self.options or self.options_from):
            raise ValueError(f"{self.id}: free text cannot supply preset answers")
        if self.type == "options" and not (self.options or self.options_from):
            raise ValueError(f"{self.id}: options require a source")
        return self


class Move(LoopData):
    category: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    move: str = Field(min_length=1, max_length=1000)
    first_step: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=1000)


class LoopDefinition(LoopData):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    archetype: Literal["reaction", "nomination", "area-walk", "ladder"]
    experience_promise: str = Field(min_length=1, max_length=1000)
    duration_min: int = Field(ge=1, le=120)
    sensitivity: list[str]
    writes: list[Literal["goals_plan"]] = Field(min_length=1, max_length=1)
    routes: list[str] = Field(default_factory=list)
    cards: list[Card] = Field(min_length=1, max_length=40)
    playbook: list[Move] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_library_rules(self) -> Self:
        ids = [c.id for c in self.cards]
        if len(ids) != len(set(ids)):
            raise ValueError("card ids must be unique")
        if self.cards[-1].id != "felt" or self.cards[-1].type != "scale_then_free_text":
            raise ValueError("a loop must end with a felt scale and free-text reason")
        seen: set[str] = set()
        for card in self.cards:
            if not set(card.recall) <= seen:
                raise ValueError("recall must name earlier cards")
            if any(e.route not in self.routes for e in card.exit):
                raise ValueError("card route is not declared")
            seen.add(card.id)
        if set(self.routes) - {"reactions"}:
            raise ValueError("stage 1 supports only the existing reactions handoff")
        if re.search(r"\b(agent|assistant|model)\b", self.model_dump_json(), re.IGNORECASE):
            raise ValueError("loop content cannot address an agent, assistant or model")
        for card in self.cards:
            prose = f"{card.explain} {card.ask} {card.ask_if_goal} {card.ask_if_problem}"
            if re.search(r"https?://", prose) and not card.display_only:
                raise ValueError("links must be marked display_only")
            if card.type == "free_text" and re.search(
                r"\b(for example|e\.g\.|such as|example answer)\b", prose, re.IGNORECASE
            ):
                raise ValueError("free-text cards cannot contain example answers")
        return self


class Capture(LoopData):
    capture_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    subject: str
    loop_id: str
    loop_version: str
    loop_hash: str
    card_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: Literal["answer", "inference", "action", "action_status", "felt", "rescue"]
    text: str
    details: dict[str, str | int | list[str]] = Field(default_factory=dict)
    authorship: Literal["person", "coach_inference"] = "person"
    classification: Literal["own_words", "inference"] = "own_words"
    store: Literal["goals_plan"] = "goals_plan"


class LoopSession(LoopData):
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    subject: str
    definition: LoopDefinition
    loop_hash: str
    tier: Literal["official", "local"]
    secret: str
    revision: int = 0
    index: int = 0
    phase: str = "answer"
    repeat_count: int = 0
    answers: dict[str, list[str]] = Field(default_factory=dict)
    selected_moves: list[str] = Field(default_factory=list)
    category: str = ""
    framing: Literal["goal", "problem"] = "goal"
    learning: str = ""
    step: str = ""
    scale: int | None = None
    status: Literal["open", "completed"] = "open"
    previous_action: str = ""
    reentry_done: bool = False
    previous_completed: bool = False
    remaining_queue: list[str] = Field(default_factory=list)
    queue_offer: str = ""
