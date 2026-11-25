"""Load test data into PostgreSQL, and remove it again.

    python -m recruiting.testdata load     fictional pipeline with work waiting for every role
    python -m recruiting.testdata clean    remove all business data (asks for --yes)
    python -m recruiting.testdata status   row counts

`load` runs the same pipeline as the in-memory demo (recruiting.demo) through the real API, so
the data follows every rule and approval gate: companies and hiring signals, first client
emails (some approved, some waiting), two JDs, ranked candidates, candidate outreach, a details
form completed, a submission waiting for approval, a client portal user and follow-ups. Nothing
leaves the machine while loading: emails go to an in-memory outbox, career pages come from
canned HTML and the agents run on their rule-based paths. Every company, person and address is
fictional (.example domains). The five demo staff users are added if missing.

`clean` deletes every company, job, candidate, approval, message, follow-up, agent run and
audit entry, client users and this environment's search index (Qdrant points). It keeps the staff
users and the Administrator's settings (gate policies, ranking weights, follow-up cadence).

Both refuse to run with RAP_ENVIRONMENT=prod. Run them where the API runs, with its settings
(scripts/testdata.sh or scripts\\testdata.ps1 run them in the API container).
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from recruiting.config import Settings

# Business data, removed by `clean`. Kept: app_user (staff), gate_policy, ranking_settings,
# follow_up_cadence, scheduler_job and checkpoint_migrations (LangGraph's schema version).
DATA_TABLES = (
    "agent_run", "approval_decision", "approval_request", "audit_log", "candidate",
    "candidate_engagement", "candidate_ranking", "candidate_ranking_entry",
    "candidate_submission_consent", "checkpoint_blobs", "checkpoint_writes", "checkpoints",
    "client_submission", "company", "company_contact", "follow_up", "inbound_message", "job",
    "job_posting", "llm_usage", "opt_out", "outreach_message", "reminder_digest",
)  # fmt: skip
COUNTED = ("company", "job", "candidate", "approval_request", "client_submission", "follow_up")


class TestDataError(Exception):
    pass


def _check(settings: Settings) -> None:
    if settings.environment == "prod":
        raise TestDataError("Refusing to touch production data (RAP_ENVIRONMENT=prod)")
    if settings.storage != "postgres":
        raise TestDataError(
            "Test data goes into PostgreSQL (RAP_STORAGE=postgres). "
            "For in-memory runs use RAP_DEMO_DATA=true instead."
        )


def _connect(settings: Settings):
    from recruiting.db import connection_factory

    return connection_factory(settings.database_url)


def counts(settings: Settings) -> dict[str, int]:
    with _connect(settings)() as conn:
        result = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in COUNTED}
        result["client_users"] = conn.execute(
            "SELECT count(*) FROM app_user WHERE role = 'client'"
        ).fetchone()[0]
    return result


def load(settings: Settings) -> dict[str, int]:
    _check(settings)
    if counts(settings)["company"]:
        raise TestDataError("The database already has companies: run `clean` first")
    from recruiting.api.services import build_services, demo_users
    from recruiting.demo import WEBHOOK_SECRET, DemoCareerPages, DemoOutbox, seed

    with _connect(settings)() as conn:
        for user in demo_users():
            conn.execute(
                "INSERT INTO app_user (email, display_name, role) VALUES (%s, %s, %s)"
                " ON CONFLICT (email) DO NOTHING",
                (user.email, user.display_name, user.role.value),
            )
    # Only this process: outreach on but into the outbox, rule-based agents, demo thresholds.
    seeding = settings.model_copy(
        update={
            "outreach_enabled": True,
            "outreach_allow_list": [],
            "email_providers": [],
            "inbound_webhook_secret": WEBHOOK_SECRET,
            "llm_provider": "none",
            "agents_enabled": False,
            "read_mailbox": False,
            "reminder_digest": False,
            "client_outreach_min_priority": 50,
            # Never used: seed() signs in through its own in-process app. OIDC only because the
            # dev header authenticator refuses to start outside RAP_ENVIRONMENT=local.
            "auth_provider": "oidc",
            "oidc_issuer": "https://unused.example",
            "oidc_audience": "unused",
            "oidc_jwks_url": "https://unused.example/jwks",
        }
    )
    outbox = DemoOutbox()
    services = build_services(seeding, email_channel=outbox, career_pages=DemoCareerPages())
    seed(services, outbox)  # signs in as the demo users through its own in-process app
    return counts(settings)


def _delete_order(conn, tables: tuple[str, ...]) -> list[str]:
    """Tables ordered so that each is emptied before the tables it references."""
    rows = conn.execute(
        """
        SELECT child.relname, parent.relname
        FROM pg_constraint c
        JOIN pg_class child ON child.oid = c.conrelid
        JOIN pg_class parent ON parent.oid = c.confrelid
        WHERE c.contype = 'f' AND child.relnamespace = 'public'::regnamespace
        """
    ).fetchall()
    present = {
        r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    }  # fmt: skip
    wanted = [t for t in tables if t in present]
    refs = {t: {p for c, p in rows if c == t and p in wanted and p != t} for t in wanted}
    order: list[str] = []
    while refs:
        # Tables nothing remaining points at can go first.
        free = sorted(t for t in refs if not any(t in ps for ps in refs.values()))
        if not free:
            raise TestDataError(f"Foreign keys form a cycle between {sorted(refs)}")
        order += free
        for t in free:
            del refs[t]
    return order


def clean(settings: Settings) -> dict[str, int]:
    _check(settings)
    with _connect(settings)() as conn:  # one transaction: all or nothing
        conn.execute("DELETE FROM app_user WHERE role = 'client'")
        for table in _delete_order(conn, DATA_TABLES):
            conn.execute(f"DELETE FROM {table}")
    if settings.vector_store == "qdrant":
        # Only this environment's collections (api/platform.py names them rap_<environment>_*).
        _clear_qdrant(settings.qdrant_url, prefix=f"rap_{settings.environment}_")
    return counts(settings)


def _clear_qdrant(url: str, prefix: str) -> None:
    """Delete every point in the platform's collections. The collections stay, because running
    API processes remember that they exist."""
    import httpx

    with httpx.Client(base_url=url, timeout=30) as http:
        names = [c["name"] for c in http.get("/collections").json()["result"]["collections"]]
        for name in names:
            if name.startswith(prefix):
                http.post(
                    f"/collections/{name}/points/delete",
                    params={"wait": "true"},
                    json={"filter": {"must": []}},
                ).raise_for_status()


def main(argv: list[str] | None = None) -> int:
    from recruiting.config import Settings

    parser = argparse.ArgumentParser(
        prog="python -m recruiting.testdata",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("action", choices=("load", "clean", "status"))
    parser.add_argument("--yes", action="store_true", help="clean without asking")
    args = parser.parse_args(argv)
    settings = Settings()
    try:
        if args.action == "status":
            result = counts(settings)
        elif args.action == "load":
            print("Loading test data (nothing is sent: emails go to an in-memory outbox)...")
            result = load(settings)
        else:
            if not args.yes:
                answer = input("Delete ALL business data from this database? Type yes: ")
                if answer.strip().lower() != "yes":
                    print("Nothing deleted.")
                    return 1
            result = clean(settings)
    except TestDataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for key, value in result.items():
        print(f"  {key:18} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
