"""
Brand deduplication — Milestone 7.

Milestone 6 creates a new `Brand` row the first time a name is seen and
matches on *exact* case-insensitive equality after that — deliberately, its
own docstring defers anything fuzzier to this module. Two observations of
what is really the same brand ("Acme Kitchenware" from one listing,
"Acme Kitchenware Srl" from another, or a later listing spelled it
"Acme Kitchen Ware") can therefore end up as two separate `Brand` rows with
separate `AmazonSeller`/`AmazonProduct`/`ResearchResult` links. This module
finds and reconciles those.

Two distinct confidence tiers, kept deliberately apart — the same
discipline `brand_resolution.py` applies to `AmazonSeller.verified`: an
operation only runs unattended if it is actually as certain as it claims
to be.

* **Exact-after-normalization -> auto-merge.** Lowercasing, stripping
  accents/punctuation, and dropping a known *unambiguous* legal-entity
  suffix (Srl, Ltd, GmbH, ...) does not change *which* company a name
  refers to — "Acme Kitchenware" and "ACME KITCHENWARE, S.R.L." are the
  same claim in two spellings, not two claims. Rows in this tier are
  merged automatically, same as Milestone 6 auto-links an exact
  brand-name match. Only `_STRONG_LEGAL_SUFFIXES` are stripped here —
  see the note on `_WEAK_LEGAL_SUFFIXES` below for why some suffix tokens
  are deliberately excluded from this tier.
* **High textual similarity after normalization, but not exact -> flagged,
  never merged.** "Acme Kitchenware" vs "Acme Kitchen Ware" is *probably*
  the same brand; "Acme Kitchenware" vs "Zenith Kitchenware" is *probably
  not*, and a threshold-based similarity score cannot reliably tell those
  apart on its own. Auto-merging here risks silently fusing two distinct
  real companies into one row — a much worse failure than leaving two rows
  around a bit longer. Candidates are recorded as `AgentEvent`s
  (`duplicate_candidate`) for a human (or, later, Milestone 10's AI layer,
  which can bring more signal than a name string) to resolve — not
  auto-resolved here. This tier ALSO strips `_WEAK_LEGAL_SUFFIXES` (see
  below), so two names that differ only by one of those ambiguous tokens
  end up flagged, not silently ignored.

**Why two suffix lists, not one.** `_STRONG_LEGAL_SUFFIXES` (Srl, Spa,
Snc, Sas, Ltd, LLC, Inc, Corp, GmbH, Plc) are long enough, and specific
enough as legal-form markers, that stripping them from the end of a name
is safe to do unattended -- there's no real brand out there that just
happens to end in the word "Gmbh" for an unrelated reason.
`_WEAK_LEGAL_SUFFIXES` (Co, Sa, Ag, Sl, Nv, Bv) are two- or three-letter
tokens that double as initials, abbreviations, or plain words having
nothing to do with legal form -- stripping them in the auto-merge tier
would have meant "Roma Co" and "Roma Corp" (or, worse, two *actually
unrelated* short brand names that happen to both end in a common
two-letter combination) collapse into one Brand row with no way back
(`merged_into_id` records direction, nothing reverses it — see "out of
scope" below). Keeping them in the auto-merge tier optimizes for catching
more spelling variants at the cost of a real, unbounded risk of silently
fusing two distinct companies; keeping them out of it and only using them
to widen tier 2's net accepts a few more candidates needing a human look
in exchange for zero risk of an unattended false merge. Discovered while
building this module, not left in from a first draft: the original
suffix list stripped both kinds in the same pass, entirely in the
auto-merge tier, before this tradeoff was made explicit and fixed.

Both tiers use `difflib.SequenceMatcher` (Python stdlib) for the similarity
score — not an embedding model, not AI. Rules before AI is a standing
discipline on this project (see `brand_resolution.py`'s own docstring);
Milestone 7 is explicitly listed as needing no credentials, and a stdlib
string metric is the deterministic tool actually available today.

Explicitly out of scope:

* **Non-Latin-script normalization.** `normalize_brand_name()` strips
  accents and lowercases via `unicodedata`/ASCII folding — a brand name in
  Cyrillic, CJK, or Arabic script normalizes to itself as far as case is
  concerned, and the legal-suffix list below is Latin-alphabet only
  (Italian/English/German/Dutch/Spanish/UK forms). Not a bug, a documented
  boundary: nothing in this pipeline has produced or is expected to produce
  non-Latin brand names yet (see `docs/providers.md` — the only real
  discovery data comes from the IT marketplace mock fixture).
* **A merge review UI/queue.** `duplicate_candidate` events are visible via
  `AgentEvent`, queryable, but there is no table or endpoint yet to work
  through them — that is a CLI/API concern (Milestone 12/13), not this
  module's job.
* **Un-merging.** `Brand.merged_into_id` records the direction of a merge
  but nothing here reverses one; a wrong auto-merge (only possible if the
  legal-suffix list or normalization itself is wrong, not from the
  fuzzy-match tier, which never auto-merges) would need a manual fix today.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentEvent,
    AgentRun,
    AmazonProduct,
    AmazonSeller,
    Brand,
    ErrorLog,
    ResearchResult,
)
from app.models.enums import ErrorCategory

# Not exhaustive — Italian (this project's only live marketplace, "IT") and
# the most common English/EU forms. Expanding either list is cheap, but the
# two are NOT interchangeable — see the module docstring's "why two suffix
# lists" note. Long/unambiguous legal-form markers only: safe to strip
# unattended, used by the auto-merge tier.
_STRONG_LEGAL_SUFFIXES = frozenset(
    {
        "srl", "srls", "spa", "snc", "sas",  # Italian
        "ltd", "llc", "inc", "corp",  # English / US
        "gmbh",  # German
        "plc",  # UK
    }
)
# Short tokens that double as initials/abbreviations/ordinary words as
# often as they mark a legal form. Stripped ONLY when explicitly requested
# (the near-duplicate flagging tier) -- never in the auto-merge tier.
_WEAK_LEGAL_SUFFIXES = frozenset(
    {
        "co",  # English "Company" -- also just two letters, common as initials
        "sa",  # Spanish/French "Sociedad/Société Anonyme" -- also just initials
        "ag",  # Swiss/German "Aktiengesellschaft" -- also just initials
        "sl",  # Spanish "Sociedad Limitada" -- also just initials
        "nv", "bv",  # Dutch/Belgian -- also just initials
    }
)

_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")
_WHITESPACE_RE = re.compile(r"\s+")
# A period sitting between two letters ("s.r.l.") is acronym punctuation,
# not a word boundary -- removed outright (not turned into a space) so
# "S.R.L." collapses to the single token "srl" before the suffix list
# below ever sees it. Without this, "Acme Kitchenware, S.R.L." would
# normalize to "acme kitchenware s r l" instead of "acme kitchenware",
# silently missing the exact-match merge with "Acme Kitchenware Srl".
_ACRONYM_DOT_RE = re.compile(r"(?<=[a-z0-9])\.(?=[a-z0-9])")

# Below this similarity score, two normalized names are treated as
# unrelated -- not even worth a duplicate_candidate event. Calibrated
# against real examples (see the module's own test file for the exact
# scores): "acme kitchenware"/"acme kitchen ware" -> 0.97, "acme
# kitchenware"/"zenith kitchenware" -> 0.765, "acme kitchenware"/"acme"
# -> 0.4. 0.88 catches spelling/spacing variants without catching two
# brands that merely share a word.
DEFAULT_SIMILARITY_THRESHOLD = 0.88


def normalize_brand_name(name: str, *, strip_weak_suffixes: bool = False) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace, drop a
    trailing legal-entity suffix token. Never returns an empty string for a
    non-empty input -- if stripping suffix tokens would consume the whole
    name (e.g. a brand literally named "LLC"), the pre-suffix-strip form is
    kept instead.

    `strip_weak_suffixes=False` (the default, used for the auto-merge
    tier) only strips `_STRONG_LEGAL_SUFFIXES` -- unambiguous legal-form
    markers. `strip_weak_suffixes=True` (used only by the near-duplicate
    flagging tier, which never auto-merges) additionally strips
    `_WEAK_LEGAL_SUFFIXES` -- short tokens too easily confused with
    initials or ordinary words to strip unattended. See the module
    docstring's "why two suffix lists" note.
    """
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = _ACRONYM_DOT_RE.sub("", text)
    text = _NON_ALNUM_RE.sub(" ", text)
    tokens = _WHITESPACE_RE.sub(" ", text).strip().split(" ")
    tokens = [t for t in tokens if t]

    allowed_suffixes = _STRONG_LEGAL_SUFFIXES | _WEAK_LEGAL_SUFFIXES if strip_weak_suffixes else _STRONG_LEGAL_SUFFIXES
    without_suffix = list(tokens)
    while without_suffix and without_suffix[-1] in allowed_suffixes:
        without_suffix.pop()

    final_tokens = without_suffix if without_suffix else tokens
    return " ".join(final_tokens)


@dataclass
class BrandMergeOutcome:
    canonical_id: uuid.UUID
    duplicate_id: uuid.UUID
    sellers_repointed: int
    products_repointed: int
    research_results_repointed: int
    company_conflict: bool = False


@dataclass
class DeduplicationSummary:
    agent_run_id: uuid.UUID
    brands_considered: int
    merged_pairs: list[BrandMergeOutcome] = field(default_factory=list)
    candidates_flagged: int = 0
    truncated: bool = False
    """True when more than max_brands ACTIVE brands existed and the run
    only considered the first max_brands (ordered by first_seen_at) --
    calling this again will pick up more on a later run, same pattern as
    resolve_research_job's max_results."""


async def _merge_brand(session: AsyncSession, canonical: Brand, duplicate: Brand) -> BrandMergeOutcome:
    sellers = (
        (await session.execute(select(AmazonSeller).where(AmazonSeller.brand_id == duplicate.id)))
        .scalars()
        .all()
    )
    for seller in sellers:
        seller.brand_id = canonical.id

    products = (
        (await session.execute(select(AmazonProduct).where(AmazonProduct.brand_id == duplicate.id)))
        .scalars()
        .all()
    )
    for product in products:
        product.brand_id = canonical.id

    results = (
        (await session.execute(select(ResearchResult).where(ResearchResult.brand_id == duplicate.id)))
        .scalars()
        .all()
    )
    for result in results:
        result.brand_id = canonical.id

    company_conflict = False
    if duplicate.company_id is not None:
        if canonical.company_id is None:
            canonical.company_id = duplicate.company_id
        elif canonical.company_id != duplicate.company_id:
            # Same discipline as brand_resolution.py's seller/brand
            # conflict: keep the canonical row's own link, never silently
            # overwritten, but surface the mismatch instead of losing it.
            company_conflict = True

    canonical.first_seen_at = min(canonical.first_seen_at, duplicate.first_seen_at)
    canonical.last_seen_at = max(canonical.last_seen_at, duplicate.last_seen_at)

    duplicate.status = "MERGED"
    duplicate.merged_into_id = canonical.id

    await session.flush()

    return BrandMergeOutcome(
        canonical_id=canonical.id,
        duplicate_id=duplicate.id,
        sellers_repointed=len(sellers),
        products_repointed=len(products),
        research_results_repointed=len(results),
        company_conflict=company_conflict,
    )


async def run_brand_deduplication(session: AsyncSession, max_brands: int = 200) -> DeduplicationSummary:
    """Finds and merges exact-after-normalization duplicate Brand rows, and
    flags (without merging) near-duplicate pairs above
    DEFAULT_SIMILARITY_THRESHOLD. Considers at most `max_brands` ACTIVE
    brands (ordered by first_seen_at) per call -- same reasoning as
    resolve_research_job's max_results: an all-pairs similarity comparison
    is O(n^2), and this is meant to be called repeatedly (e.g. by a future
    scheduler, Milestone 14) rather than assumed to finish the whole table
    in one call. `summary.truncated` says whether this call saw everything.

    Deliberately does NOT touch any AmazonSeller.verified value -- merging
    two Brand rows is an identity claim about brand *names*, not a new
    claim about which seller represents that brand; Milestone 6's own
    verified=False discipline is untouched by this module entirely.
    """
    agent_run = AgentRun(
        agent_name="brand_deduplicator",
        input={"max_brands": max_brands, "similarity_threshold": DEFAULT_SIMILARITY_THRESHOLD},
        status="RUNNING",
    )
    session.add(agent_run)
    await session.flush()

    merged_pairs: list[BrandMergeOutcome] = []
    candidates_flagged = 0
    brands_considered = 0
    truncated = False

    try:
        all_active = (
            (
                await session.execute(
                    select(Brand).where(Brand.status == "ACTIVE").order_by(Brand.first_seen_at, Brand.id)
                )
            )
            .scalars()
            .all()
        )
        truncated = len(all_active) > max_brands
        brands = list(all_active[:max_brands])
        brands_considered = len(brands)

        # --- Tier 1: exact-after-normalization -> auto-merge ---
        groups: dict[str, list[Brand]] = {}
        for brand in brands:
            groups.setdefault(normalize_brand_name(brand.name), []).append(brand)

        # Brands actually merged away in tier 1 must not also be compared
        # in tier 2 below -- a row with status MERGED is no longer a live
        # candidate for anything.
        merged_away: set[uuid.UUID] = set()

        for group in groups.values():
            if len(group) < 2:
                continue
            group_sorted = sorted(group, key=lambda b: (b.first_seen_at, str(b.id)))
            canonical = group_sorted[0]
            for duplicate in group_sorted[1:]:
                outcome = await _merge_brand(session, canonical, duplicate)
                merged_pairs.append(outcome)
                merged_away.add(duplicate.id)
                if outcome.company_conflict:
                    session.add(
                        AgentEvent(
                            agent_run_id=agent_run.id,
                            event_type="brand_merge_company_conflict",
                            payload={
                                "canonical_brand_id": str(canonical.id),
                                "duplicate_brand_id": str(duplicate.id),
                            },
                        )
                    )
                session.add(
                    AgentEvent(
                        agent_run_id=agent_run.id,
                        event_type="brand_merged",
                        payload={
                            "canonical_brand_id": str(canonical.id),
                            "duplicate_brand_id": str(duplicate.id),
                            "sellers_repointed": outcome.sellers_repointed,
                            "products_repointed": outcome.products_repointed,
                            "research_results_repointed": outcome.research_results_repointed,
                        },
                    )
                )

        # --- Tier 2: high similarity, not exact -> flag only ---
        # Uses strip_weak_suffixes=True -- safe here specifically because
        # this tier never auto-merges (see module docstring): a pair that
        # only differs by "Co"/"Sa"/"Ag"/... now surfaces as a candidate
        # instead of being silently invisible to both tiers. A ratio of
        # exactly 1.0 here (weak-normalized forms identical, e.g. "Acme Co"
        # vs "Acme AG") is still only ever flagged, never merged -- survivors
        # already exclude everything tier 1 merged, and tier 1 only strips
        # strong suffixes, so two survivors can't share a strict-normalized
        # identity here.
        survivors = [b for b in brands if b.id not in merged_away]
        for i, brand_a in enumerate(survivors):
            norm_a = normalize_brand_name(brand_a.name, strip_weak_suffixes=True)
            for brand_b in survivors[i + 1 :]:
                norm_b = normalize_brand_name(brand_b.name, strip_weak_suffixes=True)
                ratio = difflib.SequenceMatcher(None, norm_a, norm_b).ratio()
                if ratio >= DEFAULT_SIMILARITY_THRESHOLD:
                    candidates_flagged += 1
                    session.add(
                        AgentEvent(
                            agent_run_id=agent_run.id,
                            event_type="duplicate_candidate",
                            payload={
                                "brand_id_a": str(brand_a.id),
                                "brand_id_b": str(brand_b.id),
                                "name_a": brand_a.name,
                                "name_b": brand_b.name,
                                "similarity": round(ratio, 3),
                            },
                        )
                    )

        agent_run.status = "SUCCESS"
        agent_run.output = {
            "brands_considered": brands_considered,
            "merged_pairs": len(merged_pairs),
            "candidates_flagged": candidates_flagged,
            "truncated": truncated,
        }
        await session.flush()

    except Exception as exc:  # noqa: BLE001 -- same agent-run boundary discipline as brand_resolution.py
        agent_run.status = "FAILED"
        agent_run.error = str(exc)
        session.add(
            ErrorLog(
                category=ErrorCategory.UNKNOWN.value,
                message=str(exc),
                agent_run_id=agent_run.id,
            )
        )
        await session.flush()

    return DeduplicationSummary(
        agent_run_id=agent_run.id,
        brands_considered=brands_considered,
        merged_pairs=merged_pairs,
        candidates_flagged=candidates_flagged,
        truncated=truncated,
    )
