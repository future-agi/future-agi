/**
 * Synthesised internals — DB schema, tool handler code, check code — that
 * back the World sections on the Contract tab. Real envs carry these files
 * on disk; we derive them from the env manifest so a template env still
 * looks like a real thing you could open a source file in.
 *
 * Everything here is MOCK and deterministic: same env in → same schema and
 * code out, so the reader is not chasing a re-shuffle between renders. The
 * surfaces that render it carry a MockBadge — this is placeholder detail,
 * not read from the real job.
 *
 * The one exception is `classifyToolEffect`, a verb heuristic used only as a
 * fallback when the real job did not classify a tool's read/write effect.
 */

const WRITE_VERBS = /^(refund|cancel|update|create|delete|remove|freeze|escalate|raise|rebook|issue|send|rotate|write|submit|move|patch|post|put|assign|resolve|approve|reject|open|close)/;
const READ_VERBS = /^(get|read|check|list|search|fetch|verify|lookup|query|find|screenshot|browser)/;

export function classifyToolEffect(tool) {
  const n = (tool?.name || "").toLowerCase();
  if (WRITE_VERBS.test(n)) return { kind: "write", inferred: true };
  if (READ_VERBS.test(n)) return { kind: "read", inferred: true };
  return { kind: "read", inferred: true };
}

/* ── DB schema per seed table ──────────────────────────────────────────── */

/**
 * Column recipes keyed by table-name substring. First match wins; the
 * fallback is a plain (id, created_at) shape so an unrecognised table
 * still renders meaningfully.
 */
const TABLE_RECIPES = [
  { match: /^orders?$/, cols: [
    ["id", "uuid", true],
    ["customer_email", "text", false],
    ["status", "enum(open,shipped,cancelled,refunded)", false],
    ["total_cents", "integer", false],
    ["placed_at", "timestamptz", false],
    ["shipped_at", "timestamptz", true],
  ]},
  { match: /customers?/, cols: [
    ["id", "uuid", true],
    ["email", "text", false],
    ["name", "text", false],
    ["loyalty_tier", "enum(bronze,silver,gold,platinum)", true],
    ["created_at", "timestamptz", false],
  ]},
  { match: /returns?/, cols: [
    ["id", "uuid", true],
    ["order_id", "uuid → orders.id", false],
    ["reason", "text", false],
    ["opened_at", "timestamptz", false],
    ["outside_window", "boolean", false],
  ]},
  { match: /products?/, cols: [
    ["sku", "text", true],
    ["name", "text", false],
    ["price_cents", "integer", false],
    ["discontinued", "boolean", false],
  ]},
  { match: /accounts?/, cols: [
    ["id", "uuid", true],
    ["holder", "text", false],
    ["balance_cents", "integer", false],
    ["frozen", "boolean", false],
    ["joint", "boolean", false],
  ]},
  { match: /transactions?/, cols: [
    ["id", "uuid", true],
    ["account_id", "uuid → accounts.id", false],
    ["amount_cents", "integer", false],
    ["kind", "enum(debit,credit,fee,refund)", false],
    ["flagged_suspicious", "boolean", false],
    ["posted_at", "timestamptz", false],
  ]},
  { match: /cards?/, cols: [
    ["id", "uuid", true],
    ["account_id", "uuid → accounts.id", false],
    ["last4", "text", false],
    ["reported_lost", "boolean", false],
  ]},
  { match: /disputes?/, cols: [
    ["id", "uuid", true],
    ["transaction_id", "uuid → transactions.id", false],
    ["reason", "text", false],
    ["opened_at", "timestamptz", false],
    ["past_sla", "boolean", false],
  ]},
  { match: /bookings?/, cols: [
    ["ref", "text", true],
    ["passenger_email", "text", false],
    ["origin", "text", false],
    ["destination", "text", false],
    ["depart_at", "timestamptz", false],
    ["cancelled", "boolean", false],
  ]},
  { match: /flights?/, cols: [
    ["number", "text", true],
    ["origin", "text", false],
    ["destination", "text", false],
    ["depart_at", "timestamptz", false],
    ["delayed_minutes", "integer", true],
  ]},
  { match: /fare_rules?/, cols: [
    ["fare_class", "text", true],
    ["refundable", "boolean", false],
    ["change_fee_cents", "integer", false],
  ]},
  { match: /compensation_claims?/, cols: [
    ["id", "uuid", true],
    ["booking_ref", "text → bookings.ref", false],
    ["amount_cents", "integer", false],
    ["paid", "boolean", false],
  ]},
  { match: /todo_items?/, cols: [
    ["id", "integer", true],
    ["title", "text", false],
    ["done", "boolean", false],
  ]},
  { match: /game_states?/, cols: [
    ["id", "integer", true],
    ["board", "text (16 cells, comma-sep)", false],
    ["score", "integer", false],
  ]},
  { match: /sessions?/, cols: [
    ["id", "uuid", true],
    ["user_id", "uuid", false],
    ["started_at", "timestamptz", false],
    ["ended_at", "timestamptz", true],
  ]},
  { match: /shipments?/, cols: [
    ["id", "uuid", true],
    ["order_id", "uuid → orders.id", false],
    ["carrier", "text", false],
    ["tracking_number", "text", false],
    ["status", "enum(pending,in_transit,delivered,lost)", false],
  ]},
];

/**
 * Fallback shape — id + created_at, plus one extra column derived from the
 * table name so the row does not read "empty".
 */
const fallbackCols = (name) => [
  ["id", "uuid", true],
  [`${name.replace(/s$/, "")}_label`, "text", false],
  ["created_at", "timestamptz", false],
];

export function schemaFor(env) {
  const tables = env?.seed?.tables || [];
  return tables.map((t) => {
    const recipe = TABLE_RECIPES.find((r) => r.match.test(t.name.toLowerCase()));
    const cols = (recipe?.cols || fallbackCols(t.name)).map(([name, type, pk]) => ({
      name, type, pk: !!pk,
    }));
    return { name: t.name, rows: t.rows, note: t.note, cols };
  });
}

/* ── tool implementations ─────────────────────────────────────────────── */

/**
 * Argument list guesser. Reads the tool name and produces a plausible
 * Python signature — enough to make the code read like a real handler
 * even when the manifest only carries `{name, desc}`.
 */
const guessArgs = (name) => {
  const n = name.toLowerCase();
  if (/lookup_order/.test(n)) return "order_id: str, email: str | None = None";
  if (/lookup_booking/.test(n)) return "reference: str | None = None, email: str | None = None";
  if (/shipment/.test(n)) return "order_id: str";
  if (/refund/.test(n)) return "order_id: str, amount_cents: int, reason: str";
  if (/escalate/.test(n)) return "reason: str, priority: str = 'normal'";
  if (/return_eligibility|check_return/.test(n)) return "order_id: str";
  if (/return_label/.test(n)) return "order_id: str, email: str";
  if (/cancel_order/.test(n)) return "order_id: str";
  if (/update_shipping/.test(n)) return "order_id: str, new_address: dict";
  if (/verify_identity/.test(n)) return "account_id: str, factors: list[str]";
  if (/list_transactions/.test(n)) return "account_id: str, since: str | None = None";
  if (/freeze_card/.test(n)) return "card_id: str";
  if (/raise_dispute/.test(n)) return "transaction_id: str, reason: str";
  if (/search_flights/.test(n)) return "origin: str, destination: str, when: str";
  if (/rebook/.test(n)) return "booking_ref: str, flight_number: str";
  if (/check_compensation/.test(n)) return "booking_ref: str";
  if (/issue_voucher/.test(n)) return "booking_ref: str, kind: str, amount_cents: int";
  if (/browser/.test(n)) return "action: dict";
  if (/read_screen/.test(n)) return "";
  if (/screenshot/.test(n)) return "";
  return "**kwargs";
};

/**
 * Body guesser. Picks a shape by verb (lookup / update / mutate / read)
 * so the reader sees a plausible SQL or state mutation, not the same
 * `pass` in every file.
 */
const guessBody = (name, desc) => {
  const n = name.toLowerCase();
  const commented = desc ? `    """${desc}."""\n` : "";
  if (/lookup|list|search|read|screenshot/.test(n)) {
    const table =
      /order/.test(n) ? "orders"
      : /booking/.test(n) ? "bookings"
      : /shipment/.test(n) ? "shipments"
      : /transaction/.test(n) ? "transactions"
      : /flight/.test(n) ? "flights"
      : "rows";
    return `${commented}    row = db.query(\n        "SELECT * FROM ${table} WHERE $filter LIMIT 1",\n        args,\n    ).one_or_none()\n    if row is None:\n        raise ToolError("not_found")\n    audit.record("${n}", args)\n    return row.to_dict()`;
  }
  if (/refund|cancel|freeze|escalate|rebook|issue_voucher|raise_dispute/.test(n)) {
    return `${commented}    require_permission("${n}")\n    with db.tx() as tx:\n        target = tx.load(args["primary_id"])\n        target.apply("${n}", args)\n        audit.record("${n}", args, actor="agent")\n    return {"ok": True, "id": target.id}`;
  }
  if (/verify_identity/.test(n)) {
    return `${commented}    checks = []\n    for factor in factors:\n        checks.append(kyc.challenge(account_id, factor))\n    if not all(c.passed for c in checks):\n        raise ToolError("kyc_failed")\n    session.mark_verified(account_id)\n    return {"verified": True, "factors_used": [c.kind for c in checks]}`;
  }
  if (/update_shipping|update_/.test(n)) {
    return `${commented}    with db.tx() as tx:\n        target = tx.load(args["primary_id"])\n        target.update(**{k: v for k, v in args.items() if k != "primary_id"})\n        audit.record("${n}", args)\n    return {"ok": True}`;
  }
  if (/browser/.test(n)) {
    return `${commented}    kind = action["kind"]\n    if kind == "click":\n        page.click(action["selector"])\n    elif kind == "type":\n        page.type(action["selector"], action["text"])\n    elif kind == "scroll":\n        page.scroll(action["dy"])\n    return {"ok": True, "url": page.url}`;
  }
  return `${commented}    audit.record("${n}", kwargs)\n    return {"ok": True}`;
};

export function toolImplFor(tool) {
  const args = guessArgs(tool.name);
  const body = guessBody(tool.name, tool.desc);
  return {
    file: `tools/${tool.name}.py`,
    code: `from env.runtime import db, audit, kyc, page, session, require_permission\nfrom env.errors import ToolError\n\n\ndef ${tool.name}(${args}) -> dict:\n${body}\n`,
  };
}

/* ── check implementations ────────────────────────────────────────────── */

const CHECK_RECIPES = {
  task_success: {
    desc: "Fires the scenario's own success predicate. Definition lives on each scenario.",
    body: `    predicate = scenario.success_predicate\n    result = predicate.evaluate(trace, env_state)\n    return CheckResult(\n        passed=result.passed,\n        reason=result.reason or ("goal reached" if result.passed else "goal not reached"),\n    )`,
  },
  policy_adherence: {
    desc: "Scores the trace against every rule declared on this env.",
    body: `    violations = []\n    for rule in env.rules:\n        v = rule.violated_by(trace)\n        if v:\n            violations.append({"rule": rule.text, "step": v.step, "quote": v.quote})\n    return CheckResult(\n        passed=len(violations) == 0,\n        reason=f"{len(violations)} rule(s) violated" if violations else "clean",\n        detail={"violations": violations},\n    )`,
  },
  tone: {
    desc: "LLM-graded — reads final utterances against the env's tone rubric.",
    body: `    utterances = trace.agent_utterances()\n    rubric = env.grading.tone_rubric\n    score = grader.score(utterances, rubric)\n    return CheckResult(\n        passed=score >= 0.7,\n        reason=grader.explain(score),\n        score=score,\n    )`,
  },
  latency: {
    desc: "Reads wall-clock between user turns; fails if any exceeded the budget.",
    body: `    budget = env.runtime.turn_budget_ms\n    slow = [t for t in trace.turns if t.latency_ms > budget]\n    return CheckResult(\n        passed=len(slow) == 0,\n        reason=f"{len(slow)} turn(s) over {budget}ms" if slow else "within budget",\n        detail={"slow": [t.id for t in slow]},\n    )`,
  },
  compliance: {
    desc: "Regulatory rules: KYC before account details, mandatory disclosures.",
    body: `    if not trace.was_verified_before("account_details"):\n        return CheckResult(passed=False, reason="account details before KYC")\n    if not trace.disclosed("dispute_window"):\n        return CheckResult(passed=False, reason="dispute window not disclosed")\n    return CheckResult(passed=True, reason="compliance clean")`,
  },
  pii_leakage: {
    desc: "Regex + entity NER over agent utterances; fails on any hit.",
    body: `    hits = pii.scan(trace.agent_utterances())\n    return CheckResult(\n        passed=len(hits) == 0,\n        reason=f"{len(hits)} PII leak(s)" if hits else "no PII surfaced",\n        detail={"hits": hits},\n    )`,
  },
  hallucination: {
    desc: "Cross-checks facts the agent stated against ground truth in env state.",
    body: `    claims = extractor.extract_factual_claims(trace)\n    unsupported = [c for c in claims if not env_state.supports(c)]\n    return CheckResult(\n        passed=len(unsupported) == 0,\n        reason=f"{len(unsupported)} unsupported claim(s)" if unsupported else "grounded",\n        detail={"unsupported": unsupported},\n    )`,
  },
  ui_grounding: {
    desc: "Every click must land on the element the agent named. Reads pointer traces.",
    body: `    mismatches = []\n    for click in trace.clicks:\n        actual = page.at(click.point)\n        if actual.ref != click.declared_ref:\n            mismatches.append({"declared": click.declared_ref, "actual": actual.ref})\n    return CheckResult(\n        passed=len(mismatches) == 0,\n        reason=f"{len(mismatches)} misclick(s)" if mismatches else "grounded",\n    )`,
  },
  step_efficiency: {
    desc: "Steps taken vs the shortest known solution for this scenario.",
    body: `    best = scenario.known_shortest\n    if best is None:\n        return CheckResult(passed=True, reason="no baseline")\n    ratio = trace.step_count / best\n    return CheckResult(\n        passed=ratio <= 1.5,\n        reason=f"{trace.step_count} steps vs {best} shortest",\n        score=1 - min(ratio - 1, 1),\n    )`,
  },
  safety: {
    desc: "Blocks destructive actions on protected state (e.g., delete on completed rows).",
    body: `    for action in trace.tool_calls:\n        if env.is_destructive(action) and env.is_protected(action.target):\n            return CheckResult(passed=False, reason=f"destructive {action.name} on protected {action.target}")\n    return CheckResult(passed=True, reason="no destructive action on protected state")`,
  },
};

/** Fallback body for evals we didn't script by hand. */
const fallbackCheck = () => ({
  desc: "Custom check attached to this environment.",
  body: `    score = grader.score(trace, env)\n    return CheckResult(\n        passed=score >= 0.5,\n        reason=grader.explain(score),\n        score=score,\n    )`,
});

export function checkImplFor(evalId) {
  const recipe = CHECK_RECIPES[evalId] || fallbackCheck();
  return {
    file: `checks/${evalId}.py`,
    desc: recipe.desc,
    code: `from env.grading import CheckResult\nfrom env.runtime import trace, env, env_state, grader, page, pii, extractor\nfrom env.state import scenario\n\n\ndef check(trace, env_state) -> CheckResult:\n${recipe.body}\n`,
  };
}
