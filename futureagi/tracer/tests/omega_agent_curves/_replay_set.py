"""The replay set behind the curves proof (test_curves_replay.py): spans of an agent's runs as
P1-01 stamps them and the exporter sends them, ready for ``_ch_seed.seed_ch_spans`` (the
production ``adapt()`` input shape). Each case that must, or must not, count is named in the
notes ``build_replay`` returns.
"""

import random
import uuid
from datetime import timedelta


def _new_agent_id() -> str:
    return f"workforce_agent_{uuid.uuid4()}"


def _hex16():
    return uuid.uuid4().hex[:16]


class Replay:
    """Spans of traces as the exporter sends them, in seeding order. ``rows`` holds every row
    sent; ``resends`` holds the ones a later export sent (each is also in ``rows``)."""

    def __init__(self, project_id, org_id, agent_id):
        self.project_id = project_id
        self.org_id = org_id
        self.agent_id = agent_id
        self.rows = []
        self.resends = []
        self.traces = {}

    def resend(self, row):
        """A later export of a span already sent: the same span again, or its deletion. The
        replay fixture seeds these in an INSERT of their own, after the first export, so each
        lands in a part of its own as it does in production."""
        self.rows.append(row)
        self.resends.append(row)
        return row

    def _stamp(self, value, encoding):
        return str(value) if encoding == "string" else value

    def span(
        self,
        trace,
        *,
        kind,
        parent,
        start,
        latency_ms,
        status="OK",
        cost=0.0,
        attributes=None,
        project_id=None,
        agent_id=None,
        version=None,
        encoding="string",
        deleted=False,
        created_at=None,
    ):
        attrs = {
            "omega.agent.id": agent_id or self.agent_id,
            "fi.span.kind": kind.upper(),
        }
        if version is not None:
            attrs["omega.agent.version"] = self._stamp(version, encoding)
        attrs.update(attributes or {})
        row = {
            "id": _hex16(),
            "trace_id": trace,
            "project_id": project_id or self.project_id,
            "org_id": self.org_id,
            "parent_span_id": parent,
            "name": kind,
            "observation_type": kind,
            "status": status,
            "start_time": start,
            "end_time": start + timedelta(milliseconds=latency_ms),
            "created_at": created_at or start,
            "latency_ms": latency_ms,
            "cost": cost,
            "span_attributes": attrs,
            "deleted": deleted,
        }
        self.rows.append(row)
        return row

    def run(
        self,
        *,
        start,
        version,
        status="OK",
        latency_ms=1_000,
        wait_ms=0,
        llm_micros=(1_000,),
        encoding="string",
        project_id=None,
        agent_id=None,
        tool=True,
    ):
        """A trace: its root (agent) span, one LLM span per cost (None: no ledger cost) and a tool
        span. Returns the trace id."""
        trace = str(uuid.uuid4())
        root_attrs = {"omega.task.id": f"task_{uuid.uuid4().hex[:8]}"}
        if wait_ms:
            root_attrs["omega.task.approval_wait_ms"] = self._stamp(wait_ms, encoding)
        root = self.span(
            trace,
            kind="agent",
            parent="",
            start=start,
            latency_ms=latency_ms,
            status=status,
            attributes=root_attrs,
            project_id=project_id,
            agent_id=agent_id,
            version=version,
            encoding=encoding,
        )
        for index, micros in enumerate(llm_micros):
            at = start + timedelta(milliseconds=10 + index)
            if (
                micros is None
            ):  # not priced by the ledger: Future AGI's own table filled it
                self.span(
                    trace,
                    kind="llm",
                    parent=root["id"],
                    start=at,
                    latency_ms=5,
                    cost=0.42,
                    project_id=project_id,
                    agent_id=agent_id,
                    version=version,
                    encoding=encoding,
                )
            else:
                self.span(
                    trace,
                    kind="llm",
                    parent=root["id"],
                    start=at,
                    latency_ms=5,
                    cost=micros / 1_000_000,
                    attributes={"gen_ai.cost.total": micros / 1_000_000},
                    project_id=project_id,
                    agent_id=agent_id,
                    version=version,
                    encoding=encoding,
                )
        if tool:
            self.span(
                trace,
                kind="tool",
                parent=root["id"],
                start=start + timedelta(milliseconds=20),
                latency_ms=3,
                project_id=project_id,
                agent_id=agent_id,
                version=version,
                encoding=encoding,
            )
        self.traces[trace] = root
        return trace


def build_replay(project_id, other_project_id, org_id, agent_id, start, end):
    """The replay set for the window [start, end) of two UTC days. Returns (replay, notes)."""
    rng = random.Random(109)
    replay = Replay(project_id, org_id, agent_id)
    day1, day2 = start, start + timedelta(days=1)
    notes = {}

    def base(day, version, count, failed=(), unfinished=()):
        for i in range(count):
            status = "ERROR" if i in failed else "UNSET" if i in unfinished else "OK"
            replay.run(
                start=day
                + timedelta(hours=1, minutes=17 * i, seconds=rng.randint(0, 59)),
                version=version,
                status=status,
                latency_ms=rng.randint(400, 5_000),
                wait_ms=rng.randint(100, 3_000) if i % 4 == 0 else 0,
                llm_micros=[rng.randint(50, 5_000) for _ in range(rng.randint(1, 3))],
            )

    base(day1, 1, 12, failed={3, 7}, unfinished={10})
    base(day1 + timedelta(hours=12), 2, 3)
    base(day2, 2, 11, failed={5})

    at = day2 + timedelta(hours=20)
    notes["unpriced"] = replay.run(start=at, version=2, llm_micros=(700, None))
    notes["no_model_call"] = replay.run(
        start=at + timedelta(minutes=1), version=2, status="ERROR", llm_micros=()
    )
    notes["charged_nothing"] = replay.run(
        start=at + timedelta(minutes=2), version=2, llm_micros=(0, 0)
    )
    notes["wait_longer_than_run"] = replay.run(
        start=at + timedelta(minutes=3), version=2, latency_ms=2_000, wait_ms=9_000
    )
    notes["collector_encoding"] = replay.run(
        start=at + timedelta(minutes=4),
        version=2,
        latency_ms=3_333,
        wait_ms=1_111,
        llm_micros=(1_234,),
        encoding="number",
    )
    notes["unversioned"] = replay.run(start=at + timedelta(minutes=5), version=None)

    # A span sent twice (a re-export, in a later export): counted once.
    sent_twice = replay.run(
        start=at + timedelta(minutes=6), version=2, llm_micros=(4_321,)
    )
    replay.resend(
        dict(
            next(
                row
                for row in replay.rows
                if row["trace_id"] == sent_twice and row["observation_type"] == "llm"
            )
        )
    )
    notes["sent_twice"] = sent_twice

    # A span deleted after it was sent (in a later export): its newer, deleted version wins.
    deleted = replay.run(
        start=at + timedelta(minutes=7), version=2, llm_micros=(900, 8_000)
    )
    dropped = [
        r
        for r in replay.rows
        if r["trace_id"] == deleted and r["observation_type"] == "llm"
    ][-1]
    replay.resend(
        {
            **dropped,
            "deleted": True,
            "created_at": dropped["created_at"] + timedelta(minutes=1),
        }
    )
    notes["deleted_span"] = deleted

    # A price-table cost on a non-LLM span is never the ledger's.
    chain = replay.run(start=at + timedelta(minutes=8), version=2, llm_micros=(300,))
    replay.span(
        chain,
        kind="chain",
        parent=replay.traces[chain]["id"],
        start=at + timedelta(minutes=8, milliseconds=30),
        latency_ms=4,
        cost=0.5,
        version=2,
    )
    notes["chain_cost"] = chain

    # Distributed tracing across tenants: another project's LLM span in this run's trace. Only
    # the project filter on the LLM read keeps it out of the run.
    shared = replay.run(start=at + timedelta(minutes=9), version=2, llm_micros=(600,))
    replay.span(
        shared,
        kind="llm",
        parent=replay.traces[shared]["id"],
        start=at + timedelta(minutes=9, milliseconds=40),
        latency_ms=5,
        cost=0.007_7,
        attributes={"gen_ai.cost.total": 0.007_7},
        project_id=other_project_id,
        agent_id=_new_agent_id(),
        version=3,
    )
    notes["other_project_llm_in_trace"] = shared

    # A ledger cost stamped as a string lands in attrs_string, not attrs_number: the run counts,
    # unpriced. P1-01 stamps a double.
    as_string = replay.run(start=at + timedelta(minutes=10), version=2, llm_micros=())
    replay.span(
        as_string,
        kind="llm",
        parent=replay.traces[as_string]["id"],
        start=at + timedelta(minutes=10, milliseconds=10),
        latency_ms=5,
        cost=0.000_7,
        attributes={"gen_ai.cost.total": "0.0007"},
        version=2,
    )
    notes["cost_stamped_as_string"] = as_string

    # The window's last seconds: the run's LLM call lands after the window's end.
    late = replay.run(
        start=end - timedelta(seconds=10), version=2, latency_ms=20_000, llm_micros=()
    )
    replay.span(
        late,
        kind="llm",
        parent=replay.traces[late]["id"],
        start=end + timedelta(seconds=5),
        latency_ms=5,
        cost=0.002_5,
        attributes={"gen_ai.cost.total": 0.002_5},
        version=2,
    )
    notes["call_after_window"] = late

    # Never counted.
    early = replay.run(start=start - timedelta(hours=2), version=2, llm_micros=())
    replay.span(
        early,
        kind="llm",
        parent=replay.traces[early]["id"],
        start=start + timedelta(minutes=10),
        latency_ms=5,
        cost=0.009,
        attributes={"gen_ai.cost.total": 0.009},
        version=2,
    )
    notes["began_before"] = early
    two_roots = replay.run(start=start - timedelta(hours=3), version=1)
    replay.span(
        two_roots,
        kind="agent",
        parent="",
        start=start + timedelta(minutes=30),
        latency_ms=500,
        version=1,
        attributes={"omega.task.id": "task_member"},
    )
    notes["first_root_before"] = two_roots
    notes["other_agent"] = replay.run(
        start=day2 + timedelta(hours=3), version=7, agent_id=_new_agent_id()
    )
    notes["other_project"] = replay.run(
        start=day2 + timedelta(hours=3), version=2, project_id=other_project_id
    )
    return replay, notes
