"""Storage for Agent 7 rankings, recruiter feedback and the ranking weights."""

from __future__ import annotations

import copy
from dataclasses import asdict
from typing import Protocol

from recruiting.agents.ranking import RankingWeights
from recruiting.records.models import RankedCandidate, Ranking, Verdict


class UnknownRankingEntry(KeyError):
    pass


class RankingStore(Protocol):
    def save(self, ranking: Ranking) -> Ranking:
        """Replace the job's ranking, keeping recruiter verdicts for candidates still in it."""
        ...

    def get(self, job_id: str) -> Ranking | None: ...

    def review(
        self, job_id: str, candidate_id: str, verdict: Verdict, note: str | None, by: str
    ) -> RankedCandidate: ...

    def move(self, job_id: str, candidate_id: str, new_rank: int) -> Ranking:
        """Recruiter re-orders the Top 20."""
        ...


def carry_over_reviews(previous: Ranking | None, ranking: Ranking) -> Ranking:
    if previous is None:
        return ranking
    reviews = {c.candidate_id: (c.verdict, c.note) for c in previous.candidates if c.verdict}
    for entry in ranking.candidates:
        if entry.candidate_id in reviews:
            entry.verdict, entry.note = reviews[entry.candidate_id]
    return ranking


def apply_move(ranking: Ranking, candidate_id: str, new_rank: int) -> Ranking:
    ranked = [c for c in ranking.candidates if c.rank is not None]
    entry = next((c for c in ranked if c.candidate_id == candidate_id), None)
    if entry is None:
        raise UnknownRankingEntry(candidate_id)
    ranked.remove(entry)
    ranked.insert(max(0, min(new_rank - 1, len(ranked))), entry)
    for position, c in enumerate(ranked, start=1):
        c.rank = position
    ranking.candidates = ranked + [c for c in ranking.candidates if c.rank is None]
    return ranking


class InMemoryRankings:
    def __init__(self) -> None:
        self._rankings: dict[str, Ranking] = {}

    def save(self, ranking: Ranking) -> Ranking:
        ranking = carry_over_reviews(self._rankings.get(ranking.job_id), ranking)
        self._rankings[ranking.job_id] = copy.deepcopy(ranking)
        return ranking

    def get(self, job_id: str) -> Ranking | None:
        ranking = self._rankings.get(job_id)
        return copy.deepcopy(ranking) if ranking else None

    def review(
        self, job_id: str, candidate_id: str, verdict: Verdict, note: str | None, by: str
    ) -> RankedCandidate:
        ranking = self._rankings.get(job_id)
        entry = (
            next((c for c in ranking.candidates if c.candidate_id == candidate_id), None)
            if ranking
            else None
        )
        if entry is None:
            raise UnknownRankingEntry(candidate_id)
        entry.verdict, entry.note = verdict, note
        return copy.deepcopy(entry)

    def move(self, job_id: str, candidate_id: str, new_rank: int) -> Ranking:
        ranking = self._rankings.get(job_id)
        if ranking is None:
            raise UnknownRankingEntry(job_id)
        return copy.deepcopy(apply_move(ranking, candidate_id, new_rank))


class WeightsStore(Protocol):
    def get(self) -> RankingWeights: ...

    def save(self, weights: RankingWeights, by: str) -> RankingWeights: ...


class InMemoryWeights:
    def __init__(self, weights: RankingWeights | None = None) -> None:
        self._weights = weights or RankingWeights()

    def get(self) -> RankingWeights:
        return self._weights

    def save(self, weights: RankingWeights, by: str) -> RankingWeights:
        self._weights = weights.validate()
        return self._weights


def weights_dict(weights: RankingWeights) -> dict[str, float]:
    return asdict(weights)
