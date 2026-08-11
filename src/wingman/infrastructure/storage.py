"""SQLite persistence: one database file in the workspace (RFC-002)."""

from __future__ import annotations

import sqlite3
from array import array
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from wingman.domain import SourceRecord
from wingman.domain.answer import AnswerRecord
from wingman.domain.commentary import CommentaryEntry
from wingman.domain.company import CompanyDossier
from wingman.domain.corpus import CorpusDocument
from wingman.domain.heap import HeapItem
from wingman.domain.opportunity import Opportunity
from wingman.domain.outreach import OutreachBrief
from wingman.domain.person import ExternalDocument, NewsItem, Person, PersonDossier, PersonOrigin
from wingman.domain.persona import Persona
from wingman.domain.pov import PovCard
from wingman.domain.profile import ItemStatus, ProfileItem, ProfileItemKind
from wingman.domain.relationship import RelationshipLogEntry, RelationshipObjective
from wingman.domain.research import CompanySource, NewLinkEvent, ResearchSnapshot
from wingman.domain.source_record import derive_document_key
from wingman.domain.values import ValueProfile

_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_records (
    record_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    document_key TEXT NOT NULL DEFAULT '',
    source_timestamp TEXT,
    ingested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_items (
    item_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name_key TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_profile_items_key ON profile_items (kind, name_key);
CREATE TABLE IF NOT EXISTS personas (
    persona_id TEXT PRIMARY KEY,
    name_key TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS opportunities (
    opportunity_id TEXT PRIMARY KEY,
    source_record_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS corpus_documents (
    doc_id TEXT PRIMARY KEY,
    source_record_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS corpus_fts USING fts5(doc_id UNINDEXED, title, body);
CREATE TABLE IF NOT EXISTS people (
    person_id TEXT PRIMARY KEY,
    name_key TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS external_documents (
    doc_id TEXT PRIMARY KEY,
    source_record_id TEXT NOT NULL UNIQUE,
    person_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_external_documents_person ON external_documents (person_id);
CREATE VIRTUAL TABLE IF NOT EXISTS external_fts USING fts5(doc_id UNINDEXED, title, body);
CREATE TABLE IF NOT EXISTS pov_cards (
    card_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS value_profiles (
    profile_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS person_dossiers (
    dossier_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS answers (
    answer_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS answers_fts USING fts5(answer_id UNINDEXED, question, answer);
CREATE TABLE IF NOT EXISTS action_verdicts (
    action_key TEXT PRIMARY KEY,
    verdict TEXT NOT NULL,
    until TEXT,
    noted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist_members (
    list_key TEXT NOT NULL,
    list_name TEXT NOT NULL,
    member_kind TEXT NOT NULL,
    member_name TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (list_key, member_kind, member_name)
);
CREATE TABLE IF NOT EXISTS news_items (
    item_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_items_person ON news_items (person_id);
CREATE TABLE IF NOT EXISTS outreach_briefs (
    brief_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS company_sources (
    company_key TEXT NOT NULL,
    url TEXT NOT NULL,
    payload TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (company_key, url)
);
CREATE TABLE IF NOT EXISTS research_snapshots (
    company_key TEXT NOT NULL,
    url TEXT NOT NULL,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (company_key, url)
);
CREATE TABLE IF NOT EXISTS new_link_events (
    company_key TEXT NOT NULL,
    url TEXT NOT NULL,
    source_url TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    PRIMARY KEY (company_key, url)
);
CREATE TABLE IF NOT EXISTS dossier_state (
    company_key TEXT PRIMARY KEY,
    last_generated_at TEXT NOT NULL
);
-- A company's open-web deep dive (#350/RFC-059): one row per company,
-- rebuilt rather than versioned, the same lifecycle as person_dossiers.
CREATE TABLE IF NOT EXISTS company_dossiers (
    dossier_id TEXT PRIMARY KEY,
    company_key TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS relationship_objectives (
    objective_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS relationship_log (
    entry_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    happened_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_relationship_log_person ON relationship_log (person_id);
CREATE TABLE IF NOT EXISTS embeddings (
    doc_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS heap_items (
    item_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    added_at TEXT NOT NULL
);
-- The commentary corpus (RFC-058, #339). A table of its own, not a profile_items
-- kind, and deliberately without an FTS index joined to corpus_fts: the
-- isolation is structural, so a query that doesn't name this table cannot
-- return commentary no matter what it asks for.
CREATE TABLE IF NOT EXISTS commentary_entries (
    entry_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class DuplicateRecordError(Exception):
    """Raised when inserting a record whose ID already exists (records are immutable)."""


class CorpusSearchError(Exception):
    """The search query could not be parsed by the full-text index."""


class Storage:
    """Persistence for Wingman records. Source records are insert-only, never updated."""

    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)
        self._migrate_document_key()
        self._conn.commit()

    def _migrate_document_key(self) -> None:
        """Pre-RFC-028 databases lack source_records.document_key: add and backfill.

        The backfill derives each record's document identity from its
        locator's basename — the same derivation new records use — so
        lineage works retroactively for documents already ingested.
        """
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(source_records)")}
        if "document_key" in columns:
            return
        self._conn.execute(
            "ALTER TABLE source_records ADD COLUMN document_key TEXT NOT NULL DEFAULT ''"
        )
        rows = self._conn.execute("SELECT record_id, source_locator FROM source_records").fetchall()
        for record_id, locator in rows:
            self._conn.execute(
                "UPDATE source_records SET document_key = ? WHERE record_id = ?",
                (derive_document_key(locator), record_id),
            )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def add_source_record(self, record: SourceRecord) -> None:
        try:
            self._conn.execute(
                "INSERT INTO source_records"
                " (record_id, source_type, source_locator, content_hash, document_key,"
                " source_timestamp, ingested_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record.record_id,
                    record.source_type,
                    record.source_locator,
                    record.content_hash,
                    record.document_key,
                    record.source_timestamp.isoformat() if record.source_timestamp else None,
                    record.ingested_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(
                f"source record {record.record_id} already exists; "
                "records are immutable — ingest a correction as a new record"
            ) from exc
        self._conn.commit()

    def get_source_record(self, record_id: str) -> SourceRecord | None:
        cursor = self._conn.execute(
            "SELECT record_id, source_type, source_locator, content_hash, document_key,"
            " source_timestamp, ingested_at FROM source_records WHERE record_id = ?",
            (record_id,),
        )
        row: tuple[str, str, str, str, str, str | None, str] | None = cursor.fetchone()
        if row is None:
            return None
        return SourceRecord(
            record_id=row[0],
            source_type=row[1],
            source_locator=row[2],
            content_hash=row[3],
            document_key=row[4],
            source_timestamp=datetime.fromisoformat(row[5]) if row[5] else None,
            ingested_at=datetime.fromisoformat(row[6]),
        )

    def record_ids_for_document(self, document_key: str, exclude_record_id: str = "") -> set[str]:
        """Every source record that is a version of this document (RFC-028)."""
        if not document_key:
            return set()
        cursor = self._conn.execute(
            "SELECT record_id FROM source_records WHERE document_key = ? AND record_id != ?",
            (document_key, exclude_record_id),
        )
        return {row[0] for row in cursor.fetchall()}

    def get_source_record_by_hash(self, content_hash: str) -> SourceRecord | None:
        cursor = self._conn.execute(
            "SELECT record_id FROM source_records WHERE content_hash = ? ORDER BY ingested_at"
            " LIMIT 1",
            (content_hash,),
        )
        row: tuple[str] | None = cursor.fetchone()
        return self.get_source_record(row[0]) if row else None

    def count_source_records(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM source_records")
        count: int = cursor.fetchone()[0]
        return count

    def add_profile_item(self, item: ProfileItem) -> None:
        try:
            self._conn.execute(
                "INSERT INTO profile_items (item_id, kind, name_key, status, payload, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    item.item_id,
                    item.kind.value,
                    item.name_key,
                    item.status.value,
                    item.model_dump_json(),
                    item.extracted_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(
                f"profile item {item.item_id} already exists; corrections create new items"
            ) from exc
        self._conn.commit()

    def find_active_item(self, kind: ProfileItemKind, name_key: str) -> ProfileItem | None:
        cursor = self._conn.execute(
            "SELECT payload FROM profile_items WHERE kind = ? AND name_key = ? AND status = ?"
            " ORDER BY created_at DESC LIMIT 1",
            (kind.value, name_key, ItemStatus.ACTIVE.value),
        )
        row: tuple[str] | None = cursor.fetchone()
        return ProfileItem.model_validate_json(row[0]) if row else None

    def update_profile_item(self, item: ProfileItem) -> None:
        # 'kind' and 'name_key' are columns AND live in the payload, and the
        # two are read by different callers: list_profile_items decodes the
        # payload, while dedup/supersede queries WHERE kind = ? AND name_key = ?
        # against the columns. Writing only the payload leaves an item that
        # looks changed everywhere a human checks while the dedup machinery
        # still matches the old values — a silent half-apply with no symptom
        # until a later ingest collides with a row that no longer presents as
        # that kind (#273). Write all four together.
        cursor = self._conn.execute(
            "UPDATE profile_items SET kind = ?, name_key = ?, status = ?, payload = ? "
            "WHERE item_id = ?",
            (
                item.kind.value,
                item.name_key,
                item.status.value,
                item.model_dump_json(),
                item.item_id,
            ),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"profile item {item.item_id} does not exist")
        self._conn.commit()

    def get_profile_item(self, item_id: str) -> ProfileItem | None:
        cursor = self._conn.execute(
            "SELECT payload FROM profile_items WHERE item_id = ?", (item_id,)
        )
        row: tuple[str] | None = cursor.fetchone()
        return ProfileItem.model_validate_json(row[0]) if row else None

    def delete_profile_item(self, item_id: str) -> bool:
        cursor = self._conn.execute("DELETE FROM profile_items WHERE item_id = ?", (item_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def clear_profile_items(self) -> int:
        cursor = self._conn.execute("DELETE FROM profile_items")
        self._conn.commit()
        return cursor.rowcount

    def list_profile_items(self) -> list[ProfileItem]:
        cursor = self._conn.execute(
            "SELECT payload FROM profile_items ORDER BY created_at, item_id"
        )
        return [ProfileItem.model_validate_json(row[0]) for row in cursor.fetchall()]

    def count_profile_items(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM profile_items")
        count: int = cursor.fetchone()[0]
        return count

    def add_persona(self, persona: Persona) -> None:
        try:
            self._conn.execute(
                "INSERT INTO personas (persona_id, name_key, payload, created_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    persona.persona_id,
                    persona.name_key,
                    persona.model_dump_json(),
                    persona.created_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(f"a persona named {persona.name!r} already exists") from exc
        self._conn.commit()

    def get_persona(self, persona_id: str) -> Persona | None:
        cursor = self._conn.execute(
            "SELECT payload FROM personas WHERE persona_id = ?", (persona_id,)
        )
        row: tuple[str] | None = cursor.fetchone()
        return Persona.model_validate_json(row[0]) if row else None

    def find_persona_by_name(self, name: str) -> Persona | None:
        name_key = " ".join(name.lower().split())
        cursor = self._conn.execute("SELECT payload FROM personas WHERE name_key = ?", (name_key,))
        row: tuple[str] | None = cursor.fetchone()
        return Persona.model_validate_json(row[0]) if row else None

    def list_personas(self) -> list[Persona]:
        cursor = self._conn.execute("SELECT payload FROM personas ORDER BY created_at")
        return [Persona.model_validate_json(row[0]) for row in cursor.fetchall()]

    def save_opportunity(self, opportunity: Opportunity) -> None:
        """Insert or replace the opportunity for its source record (assessments evolve)."""
        self._conn.execute(
            "INSERT INTO opportunities"
            " (opportunity_id, source_record_id, title, status, payload, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(source_record_id) DO UPDATE SET"
            " title = excluded.title, status = excluded.status, payload = excluded.payload",
            (
                opportunity.opportunity_id,
                opportunity.source_record_id,
                opportunity.title,
                opportunity.status.value,
                opportunity.model_dump_json(),
                opportunity.created_at.isoformat(),
            ),
        )
        self._conn.commit()

    def find_opportunity_by_source(self, source_record_id: str) -> Opportunity | None:
        cursor = self._conn.execute(
            "SELECT payload FROM opportunities WHERE source_record_id = ?",
            (source_record_id,),
        )
        row: tuple[str] | None = cursor.fetchone()
        return Opportunity.model_validate_json(row[0]) if row else None

    def list_opportunities(self) -> list[Opportunity]:
        cursor = self._conn.execute("SELECT payload FROM opportunities ORDER BY created_at")
        return [Opportunity.model_validate_json(row[0]) for row in cursor.fetchall()]

    def count_opportunities(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM opportunities")
        count: int = cursor.fetchone()[0]
        return count

    def add_corpus_document(self, document: CorpusDocument, body: str) -> None:
        try:
            self._conn.execute(
                "INSERT INTO corpus_documents (doc_id, source_record_id, payload, created_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    document.doc_id,
                    document.source_record_id,
                    document.model_dump_json(),
                    document.added_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(
                f"corpus document for source {document.source_record_id} already exists"
            ) from exc
        self._conn.execute(
            "INSERT INTO corpus_fts (doc_id, title, body) VALUES (?, ?, ?)",
            (document.doc_id, document.title, body),
        )
        self._conn.commit()

    def find_corpus_document_by_source(self, source_record_id: str) -> CorpusDocument | None:
        cursor = self._conn.execute(
            "SELECT payload FROM corpus_documents WHERE source_record_id = ?",
            (source_record_id,),
        )
        row: tuple[str] | None = cursor.fetchone()
        return CorpusDocument.model_validate_json(row[0]) if row else None

    def list_corpus_documents(self) -> list[CorpusDocument]:
        cursor = self._conn.execute(
            "SELECT payload FROM corpus_documents ORDER BY created_at, doc_id"
        )
        return [CorpusDocument.model_validate_json(row[0]) for row in cursor.fetchall()]

    def count_corpus_documents(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM corpus_documents")
        count: int = cursor.fetchone()[0]
        return count

    def add_person(self, person: Person) -> None:
        try:
            self._conn.execute(
                "INSERT INTO people (person_id, name_key, payload, created_at) VALUES (?, ?, ?, ?)",
                (
                    person.person_id,
                    person.name_key,
                    person.model_dump_json(),
                    person.created_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(f"a person named {person.name!r} already exists") from exc
        self._conn.commit()

    def update_person(self, person: Person) -> None:
        cursor = self._conn.execute(
            "UPDATE people SET name_key = ?, payload = ? WHERE person_id = ?",
            (person.name_key, person.model_dump_json(), person.person_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"person {person.person_id} does not exist")
        self._conn.commit()

    def find_person_by_name_key(self, name_key: str) -> Person | None:
        cursor = self._conn.execute("SELECT payload FROM people WHERE name_key = ?", (name_key,))
        row: tuple[str] | None = cursor.fetchone()
        return Person.model_validate_json(row[0]) if row else None

    def get_person(self, person_id: str) -> Person | None:
        cursor = self._conn.execute("SELECT payload FROM people WHERE person_id = ?", (person_id,))
        row: tuple[str] | None = cursor.fetchone()
        return Person.model_validate_json(row[0]) if row else None

    def list_people(self) -> list[Person]:
        cursor = self._conn.execute("SELECT payload FROM people ORDER BY name_key")
        return [Person.model_validate_json(row[0]) for row in cursor.fetchall()]

    def count_people(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM people")
        count: int = cursor.fetchone()[0]
        return count

    def rename_person(self, person_id: str, new_name: str) -> Person:
        """Rename in place — person_id and every person_id-keyed record are untouched.

        Raises DuplicateRecordError if new_name already belongs to a different person.
        """
        person = self.get_person(person_id)
        if person is None:
            raise KeyError(f"person {person_id} does not exist")
        new_key = " ".join(new_name.lower().split())
        existing = self.find_person_by_name_key(new_key)
        if existing is not None and existing.person_id != person_id:
            raise DuplicateRecordError(f"a person named {new_name!r} already exists")
        old_name = person.name
        renamed = person.model_copy(update={"name": new_name.strip()})
        self.update_person(renamed)
        self.watchlist_rename_member("person", old_name, renamed.name)
        return renamed

    def delete_person(self, person_id: str) -> bool:
        """Delete a person and everything keyed to them: documents (+FTS+embeddings),
        POV card, deep-dive dossier, news, outreach brief, relationship
        objective/log, and any watchlist membership."""
        person = self.get_person(person_id)
        if person is None:
            return False
        for doc in self.list_external_documents(person_id):
            self._conn.execute("DELETE FROM external_documents WHERE doc_id = ?", (doc.doc_id,))
            self._conn.execute("DELETE FROM external_fts WHERE doc_id = ?", (doc.doc_id,))
            self._conn.execute("DELETE FROM embeddings WHERE doc_id = ?", (doc.doc_id,))
        self._conn.execute("DELETE FROM pov_cards WHERE person_id = ?", (person_id,))
        self._conn.execute("DELETE FROM person_dossiers WHERE person_id = ?", (person_id,))
        self._conn.execute("DELETE FROM news_items WHERE person_id = ?", (person_id,))
        self._conn.execute("DELETE FROM outreach_briefs WHERE person_id = ?", (person_id,))
        self._conn.execute("DELETE FROM relationship_objectives WHERE person_id = ?", (person_id,))
        self._conn.execute("DELETE FROM relationship_log WHERE person_id = ?", (person_id,))
        self._conn.execute("DELETE FROM people WHERE person_id = ?", (person_id,))
        self._conn.commit()
        self.watchlist_delete_member("person", person.name)
        return True

    def set_action_verdict(self, action_key: str, verdict: str, until: str | None = None) -> None:
        """Record a triage verdict for a digest action key (RFC-031)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO action_verdicts (action_key, verdict, until, noted_at)"
            " VALUES (?, ?, ?, ?)",
            (action_key, verdict, until, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def clear_action_verdict(self, action_key: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM action_verdicts WHERE action_key = ?", (action_key,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def list_action_verdicts(self) -> list[dict[str, str | None]]:
        cursor = self._conn.execute(
            "SELECT action_key, verdict, until, noted_at FROM action_verdicts ORDER BY noted_at"
        )
        return [
            {"action_key": row[0], "verdict": row[1], "until": row[2], "noted_at": row[3]}
            for row in cursor.fetchall()
        ]

    def save_answer(self, record: AnswerRecord) -> None:
        """Insert or replace one answer-bank record and resync its FTS row (RFC-030)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO answers (answer_id, payload, created_at) VALUES (?, ?, ?)",
            (record.answer_id, record.model_dump_json(), record.created_at.isoformat()),
        )
        self._conn.execute("DELETE FROM answers_fts WHERE answer_id = ?", (record.answer_id,))
        self._conn.execute(
            "INSERT INTO answers_fts (answer_id, question, answer) VALUES (?, ?, ?)",
            (record.answer_id, record.question, record.answer),
        )
        self._conn.commit()

    def get_answer(self, answer_id: str) -> AnswerRecord | None:
        cursor = self._conn.execute("SELECT payload FROM answers WHERE answer_id = ?", (answer_id,))
        row: tuple[str] | None = cursor.fetchone()
        return AnswerRecord.model_validate_json(row[0]) if row else None

    def list_answers(self) -> list[AnswerRecord]:
        cursor = self._conn.execute("SELECT payload FROM answers ORDER BY created_at, answer_id")
        return [AnswerRecord.model_validate_json(row[0]) for row in cursor.fetchall()]

    def delete_answer(self, answer_id: str) -> bool:
        cursor = self._conn.execute("DELETE FROM answers WHERE answer_id = ?", (answer_id,))
        self._conn.execute("DELETE FROM answers_fts WHERE answer_id = ?", (answer_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def search_answers(self, query: str, limit: int = 5) -> list[tuple[AnswerRecord, str]]:
        """Full-text search over questions and answers; (record, snippet) by relevance."""
        try:
            cursor = self._conn.execute(
                "SELECT a.payload, snippet(answers_fts, 1, '[', ']', ' … ', 16)"
                " FROM answers_fts JOIN answers a ON a.answer_id = answers_fts.answer_id"
                " WHERE answers_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            )
            rows: list[tuple[str, str]] = cursor.fetchall()
        except sqlite3.OperationalError as exc:
            raise CorpusSearchError(
                f"search query {query!r} could not be parsed ({exc}). "
                "Use plain words, quoted phrases, or AND/OR/NOT."
            ) from exc
        return [(AnswerRecord.model_validate_json(payload), snippet) for payload, snippet in rows]

    def move_person_content(self, old_person_id: str, new_person_id: str) -> None:
        """Reassign documents and news to a new person id (RFC-029 anchor re-key)."""
        self._conn.execute(
            "UPDATE external_documents SET person_id = ? WHERE person_id = ?",
            (new_person_id, old_person_id),
        )
        self._conn.execute(
            "UPDATE news_items SET person_id = ? WHERE person_id = ?",
            (new_person_id, old_person_id),
        )
        self._conn.commit()

    def merge_person(self, keep_id: str, absorb_id: str) -> Person:
        """Merge absorb_id into keep_id: keep_id's blank fields are filled from absorb_id,
        absorb_id's documents/news reassign to keep_id, its POV card and outreach brief move
        over only if keep_id doesn't already have one, then absorb_id is deleted."""
        keep = self.get_person(keep_id)
        absorb = self.get_person(absorb_id)
        if keep is None or absorb is None:
            raise KeyError("both people must exist to merge")
        if keep_id == absorb_id:
            raise ValueError("cannot merge a person into themself")
        filled = {
            field: getattr(absorb, field)
            for field in ("company", "position", "linkedin_url", "email", "substack_url")
            if not getattr(keep, field) and getattr(absorb, field)
        }
        # The verified-connection signal survives the merge regardless of which
        # record is kept: origin and connected_on feed the warmth score (#65).
        if (
            absorb.origin is PersonOrigin.LINKEDIN_CONNECTIONS
            and keep.origin is not PersonOrigin.LINKEDIN_CONNECTIONS
        ):
            filled["origin"] = absorb.origin
        if not keep.connected_on and absorb.connected_on:
            filled["connected_on"] = absorb.connected_on
        merged = keep.model_copy(update=filled) if filled else keep
        if filled:
            self.update_person(merged)
        self._conn.execute(
            "UPDATE external_documents SET person_id = ? WHERE person_id = ?",
            (keep_id, absorb_id),
        )
        self._conn.execute(
            "UPDATE news_items SET person_id = ? WHERE person_id = ?", (keep_id, absorb_id)
        )
        if self.get_pov_card(keep_id) is None:
            self._conn.execute(
                "UPDATE pov_cards SET person_id = ? WHERE person_id = ?", (keep_id, absorb_id)
            )
        else:
            self._conn.execute("DELETE FROM pov_cards WHERE person_id = ?", (absorb_id,))
        if self.get_person_dossier(keep_id) is None:
            self._conn.execute(
                "UPDATE person_dossiers SET person_id = ? WHERE person_id = ?",
                (keep_id, absorb_id),
            )
        else:
            self._conn.execute("DELETE FROM person_dossiers WHERE person_id = ?", (absorb_id,))
        if self.get_outreach_brief(keep_id) is None:
            self._conn.execute(
                "UPDATE outreach_briefs SET person_id = ? WHERE person_id = ?",
                (keep_id, absorb_id),
            )
        else:
            self._conn.execute("DELETE FROM outreach_briefs WHERE person_id = ?", (absorb_id,))
        if self.get_objective(keep_id) is None:
            self._conn.execute(
                "UPDATE relationship_objectives SET person_id = ? WHERE person_id = ?",
                (keep_id, absorb_id),
            )
        else:
            self._conn.execute(
                "DELETE FROM relationship_objectives WHERE person_id = ?", (absorb_id,)
            )
        self._conn.execute(
            "UPDATE relationship_log SET person_id = ? WHERE person_id = ?", (keep_id, absorb_id)
        )
        self._conn.execute("DELETE FROM people WHERE person_id = ?", (absorb_id,))
        self._conn.commit()
        self.watchlist_delete_member("person", absorb.name)
        return merged

    def add_external_document(self, document: ExternalDocument, body: str) -> None:
        try:
            self._conn.execute(
                "INSERT INTO external_documents"
                " (doc_id, source_record_id, person_id, payload, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    document.doc_id,
                    document.source_record_id,
                    document.person_id,
                    document.model_dump_json(),
                    document.added_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(
                f"external document for source {document.source_record_id} already exists"
            ) from exc
        self._conn.execute(
            "INSERT INTO external_fts (doc_id, title, body) VALUES (?, ?, ?)",
            (document.doc_id, document.title, body),
        )
        self._conn.commit()

    def find_external_document_by_source(self, source_record_id: str) -> ExternalDocument | None:
        cursor = self._conn.execute(
            "SELECT payload FROM external_documents WHERE source_record_id = ?",
            (source_record_id,),
        )
        row: tuple[str] | None = cursor.fetchone()
        return ExternalDocument.model_validate_json(row[0]) if row else None

    def list_external_documents(self, person_id: str | None = None) -> list[ExternalDocument]:
        if person_id is None:
            cursor = self._conn.execute(
                "SELECT payload FROM external_documents ORDER BY created_at, doc_id"
            )
        else:
            cursor = self._conn.execute(
                "SELECT payload FROM external_documents WHERE person_id = ?"
                " ORDER BY created_at, doc_id",
                (person_id,),
            )
        return [ExternalDocument.model_validate_json(row[0]) for row in cursor.fetchall()]

    def save_pov_card(self, card: PovCard) -> None:
        """Insert or replace the card for its person (cards are rebuilt, not versioned)."""
        self._conn.execute(
            "INSERT INTO pov_cards (card_id, person_id, payload, created_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(person_id) DO UPDATE SET card_id = excluded.card_id,"
            " payload = excluded.payload, created_at = excluded.created_at",
            (
                card.card_id,
                card.person_id,
                card.model_dump_json(),
                card.generated_at.isoformat(),
            ),
        )
        self._conn.commit()

    def get_pov_card(self, person_id: str) -> PovCard | None:
        cursor = self._conn.execute(
            "SELECT payload FROM pov_cards WHERE person_id = ?", (person_id,)
        )
        row: tuple[str] | None = cursor.fetchone()
        return PovCard.model_validate_json(row[0]) if row else None

    def list_pov_cards(self) -> list[PovCard]:
        cursor = self._conn.execute("SELECT payload FROM pov_cards ORDER BY created_at")
        return [PovCard.model_validate_json(row[0]) for row in cursor.fetchall()]

    def save_value_profile(self, profile: ValueProfile) -> None:
        """Insert or replace the profile for its subject (rebuilt, not
        versioned — same lifecycle as save_pov_card)."""
        self._conn.execute(
            "INSERT INTO value_profiles (profile_id, subject_id, payload, created_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(subject_id) DO UPDATE SET profile_id = excluded.profile_id,"
            " payload = excluded.payload, created_at = excluded.created_at",
            (
                profile.profile_id,
                profile.subject_id,
                profile.model_dump_json(),
                profile.generated_at.isoformat(),
            ),
        )
        self._conn.commit()

    def get_value_profile(self, subject_id: str) -> ValueProfile | None:
        cursor = self._conn.execute(
            "SELECT payload FROM value_profiles WHERE subject_id = ?", (subject_id,)
        )
        row: tuple[str] | None = cursor.fetchone()
        return ValueProfile.model_validate_json(row[0]) if row else None

    def save_person_dossier(self, dossier: PersonDossier) -> None:
        """Insert or replace the dossier for its person (rebuilt, not versioned — #222)."""
        self._conn.execute(
            "INSERT INTO person_dossiers (dossier_id, person_id, payload, created_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(person_id) DO UPDATE SET dossier_id = excluded.dossier_id,"
            " payload = excluded.payload, created_at = excluded.created_at",
            (
                dossier.dossier_id,
                dossier.person_id,
                dossier.model_dump_json(),
                dossier.generated_at.isoformat(),
            ),
        )
        self._conn.commit()

    def get_person_dossier(self, person_id: str) -> PersonDossier | None:
        cursor = self._conn.execute(
            "SELECT payload FROM person_dossiers WHERE person_id = ?", (person_id,)
        )
        row: tuple[str] | None = cursor.fetchone()
        return PersonDossier.model_validate_json(row[0]) if row else None

    def save_company_dossier(self, dossier: CompanyDossier) -> None:
        """Insert or replace a company's deep dive (rebuilt, not versioned — #350)."""
        self._conn.execute(
            "INSERT INTO company_dossiers (dossier_id, company_key, payload, created_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(company_key) DO UPDATE SET dossier_id = excluded.dossier_id,"
            " payload = excluded.payload, created_at = excluded.created_at",
            (
                dossier.dossier_id,
                dossier.company_key,
                dossier.model_dump_json(),
                dossier.generated_at.isoformat(),
            ),
        )
        self._conn.commit()

    def get_company_dossier(self, company_key: str) -> CompanyDossier | None:
        cursor = self._conn.execute(
            "SELECT payload FROM company_dossiers WHERE company_key = ?", (company_key,)
        )
        row: tuple[str] | None = cursor.fetchone()
        return CompanyDossier.model_validate_json(row[0]) if row else None

    def delete_company_dossier(self, company_key: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM company_dossiers WHERE company_key = ?", (company_key,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def move_company_dossier(self, old_key: str, new_key: str, new_name: str) -> bool:
        """Re-key a stored deep dive on rename. A dossier already present under
        new_key wins; the old one is dropped rather than overwriting it (the
        same rule move_pov_card follows)."""
        dossier = self.get_company_dossier(old_key)
        if dossier is None:
            return False
        self.delete_company_dossier(old_key)
        if self.get_company_dossier(new_key) is None:
            self.save_company_dossier(
                dossier.model_copy(update={"company_key": new_key, "company_name": new_name})
            )
        return True

    def save_objective(self, objective: RelationshipObjective) -> None:
        """Insert or replace the objective for its person (RFC-037: revised, not versioned)."""
        self._conn.execute(
            "INSERT INTO relationship_objectives"
            " (objective_id, person_id, payload, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(person_id) DO UPDATE SET objective_id = excluded.objective_id,"
            " payload = excluded.payload, updated_at = excluded.updated_at",
            (
                objective.objective_id,
                objective.person_id,
                objective.model_dump_json(),
                objective.updated_at.isoformat(),
            ),
        )
        self._conn.commit()

    def get_objective(self, person_id: str) -> RelationshipObjective | None:
        cursor = self._conn.execute(
            "SELECT payload FROM relationship_objectives WHERE person_id = ?", (person_id,)
        )
        row: tuple[str] | None = cursor.fetchone()
        return RelationshipObjective.model_validate_json(row[0]) if row else None

    def list_objectives(self) -> list[RelationshipObjective]:
        cursor = self._conn.execute(
            "SELECT payload FROM relationship_objectives ORDER BY updated_at"
        )
        return [RelationshipObjective.model_validate_json(row[0]) for row in cursor.fetchall()]

    def delete_objective(self, person_id: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM relationship_objectives WHERE person_id = ?", (person_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def add_log_entry(self, entry: RelationshipLogEntry) -> None:
        try:
            self._conn.execute(
                "INSERT INTO relationship_log"
                " (entry_id, person_id, source_record_id, payload, happened_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    entry.entry_id,
                    entry.person_id,
                    entry.source_record_id,
                    entry.model_dump_json(),
                    entry.happened_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(
                f"log entry for source {entry.source_record_id} already exists"
            ) from exc
        self._conn.commit()

    def list_log_entries(self, person_id: str | None = None) -> list[RelationshipLogEntry]:
        if person_id is None:
            cursor = self._conn.execute(
                "SELECT payload FROM relationship_log ORDER BY happened_at, entry_id"
            )
        else:
            cursor = self._conn.execute(
                "SELECT payload FROM relationship_log WHERE person_id = ?"
                " ORDER BY happened_at, entry_id",
                (person_id,),
            )
        return [RelationshipLogEntry.model_validate_json(row[0]) for row in cursor.fetchall()]

    def list_news_items(self) -> list[NewsItem]:
        cursor = self._conn.execute("SELECT payload FROM news_items ORDER BY fetched_at, item_id")
        return [NewsItem.model_validate_json(row[0]) for row in cursor.fetchall()]

    def list_research_snapshots(self) -> list[ResearchSnapshot]:
        cursor = self._conn.execute(
            "SELECT payload FROM research_snapshots ORDER BY fetched_at, url"
        )
        return [ResearchSnapshot.model_validate_json(row[0]) for row in cursor.fetchall()]

    def list_outreach_briefs(self) -> list[OutreachBrief]:
        cursor = self._conn.execute("SELECT payload FROM outreach_briefs ORDER BY created_at")
        return [OutreachBrief.model_validate_json(row[0]) for row in cursor.fetchall()]

    def watchlist_add(self, list_name: str, member_kind: str, member_name: str) -> bool:
        """Add a member to a (implicitly created) watchlist; False if already there."""
        list_key = " ".join(list_name.lower().split())
        try:
            self._conn.execute(
                "INSERT INTO watchlist_members"
                " (list_key, list_name, member_kind, member_name, added_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    list_key,
                    list_name.strip(),
                    member_kind,
                    member_name.strip(),
                    datetime.now(UTC).isoformat(),
                ),
            )
        except sqlite3.IntegrityError:
            return False
        self._conn.commit()
        return True

    def watchlist_remove(self, list_name: str, member_kind: str, member_name: str) -> bool:
        list_key = " ".join(list_name.lower().split())
        cursor = self._conn.execute(
            "DELETE FROM watchlist_members"
            " WHERE list_key = ? AND member_kind = ? AND member_name = ?",
            (list_key, member_kind, member_name.strip()),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def watchlists(self) -> list[tuple[str, int]]:
        """All watchlists with member counts, by name."""
        cursor = self._conn.execute(
            "SELECT MIN(list_name), COUNT(*) FROM watchlist_members"
            " GROUP BY list_key ORDER BY list_key"
        )
        return [(row[0], row[1]) for row in cursor.fetchall()]

    def watchlist_members(self, list_name: str) -> list[tuple[str, str]]:
        """(member_kind, member_name) rows for one list, in added order."""
        list_key = " ".join(list_name.lower().split())
        cursor = self._conn.execute(
            "SELECT member_kind, member_name FROM watchlist_members"
            " WHERE list_key = ? ORDER BY added_at, member_name",
            (list_key,),
        )
        return [(row[0], row[1]) for row in cursor.fetchall()]

    def watchlist_rename_member(self, member_kind: str, old_name: str, new_name: str) -> int:
        """Rename a member across every list it's on; returns the number of lists touched."""
        cursor = self._conn.execute(
            "UPDATE watchlist_members SET member_name = ?"
            " WHERE member_kind = ? AND member_name = ?",
            (new_name, member_kind, old_name),
        )
        self._conn.commit()
        return cursor.rowcount

    def watchlist_delete_member(self, member_kind: str, name: str) -> int:
        """Remove a member from every list it's on; returns the number of lists touched."""
        cursor = self._conn.execute(
            "DELETE FROM watchlist_members WHERE member_kind = ? AND member_name = ?",
            (member_kind, name),
        )
        self._conn.commit()
        return cursor.rowcount

    def replace_person_news(self, person_id: str, items: list[NewsItem]) -> None:
        """News is a refreshed snapshot, not an archive: old items are replaced."""
        self._conn.execute("DELETE FROM news_items WHERE person_id = ?", (person_id,))
        self._conn.executemany(
            "INSERT INTO news_items (item_id, person_id, payload, fetched_at) VALUES (?, ?, ?, ?)",
            [
                (item.item_id, item.person_id, item.model_dump_json(), item.fetched_at.isoformat())
                for item in items
            ],
        )
        self._conn.commit()

    def list_person_news(self, person_id: str) -> list[NewsItem]:
        cursor = self._conn.execute(
            "SELECT payload FROM news_items WHERE person_id = ? ORDER BY fetched_at, item_id",
            (person_id,),
        )
        return [NewsItem.model_validate_json(row[0]) for row in cursor.fetchall()]

    def save_outreach_brief(self, brief: OutreachBrief) -> None:
        """Insert or replace the brief for its person (briefs are rebuilt, not versioned)."""
        self._conn.execute(
            "INSERT INTO outreach_briefs (brief_id, person_id, payload, created_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(person_id) DO UPDATE SET brief_id = excluded.brief_id,"
            " payload = excluded.payload, created_at = excluded.created_at",
            (
                brief.brief_id,
                brief.person_id,
                brief.model_dump_json(),
                brief.generated_at.isoformat(),
            ),
        )
        self._conn.commit()

    def get_outreach_brief(self, person_id: str) -> OutreachBrief | None:
        cursor = self._conn.execute(
            "SELECT payload FROM outreach_briefs WHERE person_id = ?", (person_id,)
        )
        row: tuple[str] | None = cursor.fetchone()
        return OutreachBrief.model_validate_json(row[0]) if row else None

    def add_company_source(self, source: CompanySource) -> bool:
        """Record a user-approved research URL; False if it was already approved."""
        try:
            self._conn.execute(
                "INSERT INTO company_sources (company_key, url, payload, added_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    source.company_key,
                    source.url,
                    source.model_dump_json(),
                    source.added_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError:
            return False
        self._conn.commit()
        return True

    def remove_company_source(self, company_key: str, url: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM company_sources WHERE company_key = ? AND url = ?",
            (company_key, url),
        )
        self._conn.execute(
            "DELETE FROM research_snapshots WHERE company_key = ? AND url = ?",
            (company_key, url),
        )
        self._conn.execute(
            "DELETE FROM new_link_events WHERE company_key = ? AND source_url = ?",
            (company_key, url),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def list_company_sources(self, company_key: str) -> list[CompanySource]:
        cursor = self._conn.execute(
            "SELECT payload FROM company_sources WHERE company_key = ? ORDER BY added_at, url",
            (company_key,),
        )
        return [CompanySource.model_validate_json(row[0]) for row in cursor.fetchall()]

    def save_research_snapshot(self, snapshot: ResearchSnapshot) -> None:
        """One snapshot per (company, url): refreshed, not archived."""
        self._conn.execute(
            "INSERT INTO research_snapshots (company_key, url, payload, fetched_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(company_key, url) DO UPDATE SET"
            " payload = excluded.payload, fetched_at = excluded.fetched_at",
            (
                snapshot.company_key,
                snapshot.url,
                snapshot.model_dump_json(),
                snapshot.fetched_at.isoformat(),
            ),
        )
        self._conn.commit()

    def get_research_snapshot(self, company_key: str, url: str) -> ResearchSnapshot | None:
        cursor = self._conn.execute(
            "SELECT payload FROM research_snapshots WHERE company_key = ? AND url = ?",
            (company_key, url),
        )
        row: tuple[str] | None = cursor.fetchone()
        return ResearchSnapshot.model_validate_json(row[0]) if row else None

    def record_new_links(
        self, company_key: str, source_url: str, links: list[str], discovered_at: datetime
    ) -> None:
        """Append newly-diffed links to the accumulating history a dossier reads
        from. Idempotent per (company_key, url): first sighting wins, so a link
        that later drops out of a snapshot and re-diffs as 'new' again does not
        reset its discovered_at."""
        if not links:
            return
        for link in links:
            self._conn.execute(
                "INSERT INTO new_link_events (company_key, url, source_url, discovered_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(company_key, url) DO NOTHING",
                (company_key, link, source_url, discovered_at.isoformat()),
            )
        self._conn.commit()

    def list_new_links_since(self, company_key: str, since: datetime | None) -> list[NewLinkEvent]:
        """Every accumulated new-link event for a company, oldest first. `since`
        of None returns the full history (there has never been a dossier)."""
        if since is None:
            cursor = self._conn.execute(
                "SELECT url, source_url, discovered_at FROM new_link_events"
                " WHERE company_key = ? ORDER BY discovered_at",
                (company_key,),
            )
        else:
            cursor = self._conn.execute(
                "SELECT url, source_url, discovered_at FROM new_link_events"
                " WHERE company_key = ? AND discovered_at > ? ORDER BY discovered_at",
                (company_key, since.isoformat()),
            )
        return [
            NewLinkEvent(
                company_key=company_key,
                url=url,
                source_url=source_url,
                discovered_at=datetime.fromisoformat(discovered_at),
            )
            for url, source_url, discovered_at in cursor.fetchall()
        ]

    def get_dossier_generated_at(self, company_key: str) -> datetime | None:
        cursor = self._conn.execute(
            "SELECT last_generated_at FROM dossier_state WHERE company_key = ?", (company_key,)
        )
        row: tuple[str] | None = cursor.fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def mark_dossier_generated(self, company_key: str, generated_at: datetime) -> None:
        self._conn.execute(
            "INSERT INTO dossier_state (company_key, last_generated_at) VALUES (?, ?)"
            " ON CONFLICT(company_key) DO UPDATE SET last_generated_at = excluded.last_generated_at",
            (company_key, generated_at.isoformat()),
        )
        self._conn.commit()

    def move_company_sources(self, old_key: str, new_key: str, new_name: str) -> int:
        """Re-key every company_sources/research_snapshots row from old_key to new_key,
        renaming the denormalized display name. A row already present at new_key for the
        same url is left as-is (no overwrite). Returns the number of sources moved."""
        moved = 0
        for url, payload in self._conn.execute(
            "SELECT url, payload FROM company_sources WHERE company_key = ?", (old_key,)
        ).fetchall():
            source = CompanySource.model_validate_json(payload)
            renamed = source.model_copy(update={"company_key": new_key, "company_name": new_name})
            try:
                self._conn.execute(
                    "INSERT INTO company_sources (company_key, url, payload, added_at)"
                    " VALUES (?, ?, ?, ?)",
                    (new_key, url, renamed.model_dump_json(), renamed.added_at.isoformat()),
                )
                moved += 1
            except sqlite3.IntegrityError:
                pass
        self._conn.execute("DELETE FROM company_sources WHERE company_key = ?", (old_key,))
        for url, payload in self._conn.execute(
            "SELECT url, payload FROM research_snapshots WHERE company_key = ?", (old_key,)
        ).fetchall():
            self._conn.execute(
                "INSERT INTO research_snapshots (company_key, url, payload, fetched_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(company_key, url) DO NOTHING",
                (new_key, url, payload, datetime.now(UTC).isoformat()),
            )
        self._conn.execute("DELETE FROM research_snapshots WHERE company_key = ?", (old_key,))
        for url, source_url, discovered_at in self._conn.execute(
            "SELECT url, source_url, discovered_at FROM new_link_events WHERE company_key = ?",
            (old_key,),
        ).fetchall():
            self._conn.execute(
                "INSERT INTO new_link_events (company_key, url, source_url, discovered_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(company_key, url) DO NOTHING",
                (new_key, url, source_url, discovered_at),
            )
        self._conn.execute("DELETE FROM new_link_events WHERE company_key = ?", (old_key,))
        dossier_row: tuple[str] | None = self._conn.execute(
            "SELECT last_generated_at FROM dossier_state WHERE company_key = ?", (old_key,)
        ).fetchone()
        if dossier_row is not None:
            self._conn.execute(
                "INSERT INTO dossier_state (company_key, last_generated_at) VALUES (?, ?)"
                " ON CONFLICT(company_key) DO UPDATE SET"
                " last_generated_at = excluded.last_generated_at",
                (new_key, dossier_row[0]),
            )
            self._conn.execute("DELETE FROM dossier_state WHERE company_key = ?", (old_key,))
        self._conn.commit()
        return moved

    def delete_company_sources(self, company_key: str) -> int:
        """Delete every approved source, research snapshot, new-link history, and
        dossier cursor for a company."""
        cursor = self._conn.execute(
            "DELETE FROM company_sources WHERE company_key = ?", (company_key,)
        )
        self._conn.execute("DELETE FROM research_snapshots WHERE company_key = ?", (company_key,))
        self._conn.execute("DELETE FROM new_link_events WHERE company_key = ?", (company_key,))
        self._conn.execute("DELETE FROM dossier_state WHERE company_key = ?", (company_key,))
        self._conn.commit()
        return cursor.rowcount

    def move_pov_card(self, old_id: str, new_id: str) -> bool:
        """Re-key a stored card's identity (company rename). If a card already exists at
        new_id, the old one is dropped instead of overwriting it. The move is an
        in-place UPDATE: re-inserting under the same card_id would collide with the
        still-present original row's primary key (#62)."""
        card = self.get_pov_card(old_id)
        if card is None:
            return False
        if self.get_pov_card(new_id) is None:
            self._conn.execute(
                "UPDATE pov_cards SET person_id = ? WHERE person_id = ?", (new_id, old_id)
            )
        else:
            self._conn.execute("DELETE FROM pov_cards WHERE person_id = ?", (old_id,))
        self._conn.commit()
        return True

    def delete_pov_card(self, subject_id: str) -> bool:
        cursor = self._conn.execute("DELETE FROM pov_cards WHERE person_id = ?", (subject_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def has_external_url(self, url: str) -> bool:
        cursor = self._conn.execute(
            "SELECT 1 FROM external_documents WHERE json_extract(payload, '$.url') = ? LIMIT 1",
            (url,),
        )
        return cursor.fetchone() is not None

    def count_external_documents(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM external_documents")
        count: int = cursor.fetchone()[0]
        return count

    def get_corpus_body(self, doc_id: str) -> str | None:
        cursor = self._conn.execute("SELECT body FROM corpus_fts WHERE doc_id = ?", (doc_id,))
        row: tuple[str] | None = cursor.fetchone()
        return row[0] if row else None

    def get_external_body(self, doc_id: str) -> str | None:
        cursor = self._conn.execute("SELECT body FROM external_fts WHERE doc_id = ?", (doc_id,))
        row: tuple[str] | None = cursor.fetchone()
        return row[0] if row else None

    def upsert_embedding(
        self, doc_id: str, scope: str, provider: str, model: str, vector: list[float]
    ) -> None:
        """Store a document's vector; re-embedding replaces the previous vector."""
        blob = array("f", vector).tobytes()
        self._conn.execute(
            "INSERT INTO embeddings (doc_id, scope, provider, model, dim, vector, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(doc_id) DO UPDATE SET scope = excluded.scope,"
            " provider = excluded.provider, model = excluded.model, dim = excluded.dim,"
            " vector = excluded.vector, created_at = excluded.created_at",
            (
                doc_id,
                scope,
                provider,
                model,
                len(vector),
                blob,
                datetime.now(UTC).isoformat(),
            ),
        )
        self._conn.commit()

    def get_embedding(self, doc_id: str) -> tuple[list[float], str, str] | None:
        """Return (vector, provider, model) for a document, if embedded."""
        cursor = self._conn.execute(
            "SELECT vector, provider, model FROM embeddings WHERE doc_id = ?", (doc_id,)
        )
        row: tuple[bytes, str, str] | None = cursor.fetchone()
        if row is None:
            return None
        vector = array("f")
        vector.frombytes(row[0])
        return list(vector), row[1], row[2]

    def count_embeddings(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM embeddings")
        count: int = cursor.fetchone()[0]
        return count

    def embedding_models_in_use(self) -> set[tuple[str, str]]:
        cursor = self._conn.execute("SELECT DISTINCT provider, model FROM embeddings")
        return {(row[0], row[1]) for row in cursor.fetchall()}

    def add_heap_item(self, item: HeapItem) -> None:
        self._conn.execute(
            "INSERT INTO heap_items (item_id, payload, added_at) VALUES (?, ?, ?)",
            (item.item_id, item.model_dump_json(), item.added_at.isoformat()),
        )
        self._conn.commit()

    def list_heap_items(self) -> list[HeapItem]:
        cursor = self._conn.execute("SELECT payload FROM heap_items ORDER BY added_at")
        return [HeapItem.model_validate_json(row[0]) for row in cursor.fetchall()]

    def delete_heap_item(self, item_id: str) -> bool:
        cursor = self._conn.execute("DELETE FROM heap_items WHERE item_id = ?", (item_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def add_commentary_entry(self, entry: CommentaryEntry) -> None:
        """Store one reading (#339). Its own table, read only by callers that
        name it — nothing that walks the evidence stores can reach it."""
        try:
            self._conn.execute(
                "INSERT INTO commentary_entries (entry_id, payload, created_at) VALUES (?, ?, ?)",
                (entry.entry_id, entry.model_dump_json(), entry.created_at.isoformat()),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(f"commentary entry {entry.entry_id} already exists") from exc
        self._conn.commit()

    def list_commentary_entries(self) -> list[CommentaryEntry]:
        cursor = self._conn.execute(
            "SELECT payload FROM commentary_entries ORDER BY created_at, entry_id"
        )
        return [CommentaryEntry.model_validate_json(row[0]) for row in cursor.fetchall()]

    def get_commentary_entry(self, entry_id: str) -> CommentaryEntry | None:
        cursor = self._conn.execute(
            "SELECT payload FROM commentary_entries WHERE entry_id = ?", (entry_id,)
        )
        row: tuple[str] | None = cursor.fetchone()
        return CommentaryEntry.model_validate_json(row[0]) if row else None

    def delete_commentary_entry(self, entry_id: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM commentary_entries WHERE entry_id = ?", (entry_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def count_commentary_entries(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM commentary_entries")
        count: int = cursor.fetchone()[0]
        return count

    def search_external(self, query: str, limit: int = 10) -> list[tuple[ExternalDocument, str]]:
        """Full-text search over people's writing; returns (document, snippet) by rank."""
        try:
            cursor = self._conn.execute(
                "SELECT e.payload, snippet(external_fts, 2, '[', ']', ' … ', 20)"
                " FROM external_fts JOIN external_documents e ON e.doc_id = external_fts.doc_id"
                " WHERE external_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            )
            rows: list[tuple[str, str]] = cursor.fetchall()
        except sqlite3.OperationalError as exc:
            raise CorpusSearchError(
                f"search query {query!r} could not be parsed ({exc}). "
                "Use plain words, quoted phrases, or AND/OR/NOT."
            ) from exc
        return [
            (ExternalDocument.model_validate_json(payload), snippet) for payload, snippet in rows
        ]

    def search_corpus(self, query: str, limit: int = 10) -> list[tuple[CorpusDocument, str]]:
        """Full-text search; returns (document, snippet) ranked by relevance."""
        try:
            cursor = self._conn.execute(
                "SELECT c.payload, snippet(corpus_fts, 2, '[', ']', ' … ', 20)"
                " FROM corpus_fts JOIN corpus_documents c ON c.doc_id = corpus_fts.doc_id"
                " WHERE corpus_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            )
            rows: list[tuple[str, str]] = cursor.fetchall()
        except sqlite3.OperationalError as exc:
            raise CorpusSearchError(
                f"search query {query!r} could not be parsed ({exc}). "
                "Use plain words, quoted phrases, or AND/OR/NOT."
            ) from exc
        return [(CorpusDocument.model_validate_json(payload), snippet) for payload, snippet in rows]
