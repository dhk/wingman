"""Demo seeding: a real watchlist a newcomer can experience before adding their own data.

No synthetic fixtures — the demo seeds real, public Substack publications
(the author's own newsletter plus the publications he follows) and everything
downstream runs the ordinary pipelines: RFC-009 feed fetching, FTS evidence,
RFC-010 similarity. The seed list is public information (publication names
and URLs only); a user replaces it with their own people as they go.
"""

from __future__ import annotations

from pydantic import BaseModel

from wingman.application.people import add_person
from wingman.domain.person import Person
from wingman.infrastructure.storage import Storage

# (name, public Substack URL) — the author's publication first, then follows.
DEMO_WATCHLIST: list[tuple[str, str]] = [
    ("Dave Holmes-Kinsella", "https://dhkondata.substack.com"),
    ("Noah Smith", "https://noahpinion.substack.com"),
    ("Gergely Orosz", "https://pragmaticengineer.substack.com"),
    ("Lenny Rachitsky", "https://lenny.substack.com"),
    ("Azeem Azhar", "https://exponentialview.substack.com"),
    ("Ethan Mollick", "https://oneusefulthing.substack.com"),
    ("Gary Marcus", "https://garymarcus.substack.com"),
    ("Brian Potter", "https://constructionphysics.substack.com"),
    ("Daniel Parris", "https://statsignificant.substack.com"),
    ("Adam Mastroianni", "https://experimentalhistory.substack.com"),
    ("Annie Duke", "https://annieduke.substack.com"),
    ("Adam Grant", "https://adamgrant.substack.com"),
    ("Scott Belsky", "https://scottbelsky.substack.com"),
    ("Yascha Mounk", "https://yaschamounk.substack.com"),
    ("Persuasion", "https://persuasion1.substack.com"),
    ("Turner Novak", "https://turner.substack.com"),
    ("TBPN", "https://tbpn.substack.com"),
    ("EconLab", "https://econlab.substack.com"),
    ("The Data Ecosystem", "https://thedataecosystem.substack.com"),
    ("AI Weekender", "https://aiweekender.substack.com"),
    ("Tayla Burrell", "https://taylaburrell.substack.com"),
    ("Ruben Hassid", "https://ruben.substack.com"),
    ("Phillip Alcock", "https://phillipalcock.substack.com"),
    ("AIGovOps Foundation", "https://aigovops.substack.com"),
    ("Marcus Sawyerr", "https://marcussawyerr.substack.com"),
    ("Yue Zhao", "https://yuezhao.substack.com"),
    ("Overload Rugby", "https://overloadrugby.substack.com"),
    ("Pessimists Archive", "https://pessimistsarchive.substack.com"),
    ("Performance DE", "https://performancede.substack.com"),
    ("Sightbearer", "https://sightbearer.substack.com"),
]

# The reference person for the "who's similar" showcase — the author.
DEMO_REFERENCE_PERSON = DEMO_WATCHLIST[0][0]


class DemoSeedReport(BaseModel):
    added: int
    already_present: int


def seed_demo_watchlist(storage: Storage) -> tuple[DemoSeedReport, list[Person]]:
    """Add the demo publications to the watchlist; existing people are left alone."""
    added = 0
    already = 0
    people: list[Person] = []
    for name, url in DEMO_WATCHLIST:
        person, created = add_person(name, storage, substack_url=url)
        people.append(person)
        added += int(created)
        already += int(not created)
    return DemoSeedReport(added=added, already_present=already), people
