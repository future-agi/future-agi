// The step parsers of run_harness_and_upload.py (options-truth L2 G6,
// harness-logs F1, F3-F5, F7-F10, F12, F13). One parser per harness CLI
// reads that CLI's own record (stdout stream, trajectory or history files)
// and returns {steps, final, cost_usd, tokens, error}; one emitter turns the
// steps into spans under the row span with their own start and end times,
// tool calls nested under the model turn that made them. Formats come from
// each CLI's source. Pure: Python text only.

/** Parser id per harness (`openhands` follows the version pin). */
export function parserOf(entryId, version) {
  if (entryId === "openhands")
    return /^\s*v?1\b/.test(String(version ?? ""))
      ? "openhands_jsonl"
      : "openhands_v0";
  return (
    {
      claude_code: "claude",
      codex_cli: "codex",
      gemini_cli: "gemini",
      goose: "goose",
      aider: "aider",
      mini_swe_agent: "mini",
      custom_command: "custom",
    }[entryId] ?? "custom"
  );
}

/** The record file each parser reads from the try folder (null = stdout). */
export const RECORD_FILES = Object.freeze({
  openhands_v0: ".fi/openhands.trajectory.json",
  aider: ".fi/aider.llm.history",
  mini: ".fi/mini.traj.json",
  custom: ".fi/trace.json",
});

/** Parsers whose CLI reports no dollars: cost = tokens × the price table. */
export const TOKEN_PRICED = Object.freeze(["claude", "codex", "gemini"]);

const COMMON = String.raw`CAP = 2000  # attribute text cap


def txt(value):
    """Attribute text: strings as they are, anything else as JSON."""
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value)


def step(kind, name, start, end=None, error=None, **attrs):
    """One step (kind llm | tool | agent): ns times, attributes, children."""
    attrs = {
        k: txt(v)[:CAP] if isinstance(v, (str, dict, list)) else v
        for k, v in attrs.items()
        if v is not None
    }
    end = end or start
    return {"kind": kind, "name": name, "start": start, "end": end,
            "attrs": attrs, "children": [], "error": error}


def parsed(steps, final=None, cost=None, tokens=None, error=None):
    """What a parser returns for one try."""
    return {"steps": steps, "final": final, "cost_usd": cost,
            "tokens": tokens or {}, "error": error}


def iso_ns(value, fallback):
    """An ISO-8601 time as epoch ns (a naive time is local time)."""
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return fallback
    if when.tzinfo is None:
        when = when.astimezone()
    return int(when.timestamp() * 1e9)


def sec_ns(value, fallback):
    """Unix seconds as epoch ns."""
    try:
        return int(float(value) * 1e9)
    except (TypeError, ValueError):
        return fallback


def records(lines):
    """[(arrival ns, line)] → [(arrival ns, event)]; non-JSON lines skipped."""
    out = []
    for at, line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            out.append((at, event))
    return out


def read_json(path):
    """A record file the CLI wrote, or None (not written: killed, crashed)."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def unmatched(open_calls):
    """Calls with no result: the try ended first (time limit, max turns)."""
    for s in open_calls.values():
        s["error"] = s["error"] or "no result (try ended first)"


def tail(lines):
    """The stdout tail, never only its last line (the fallback output)."""
    text = "\n".join(line for _, line in lines).strip()
    return text[-CAP:] or None


def first_at(lines):
    """When the first stdout line arrived (the start of the first turn)."""
    return lines[0][0] if lines else time.time_ns()


def usd(value):
    """Dollars: 2 significant figures, at least 3 decimals below $0.10."""
    if value >= 0.1:
        return "$" + f"{value:.2f}"
    digits = max(3, 1 - math.floor(math.log10(value))) if value > 0 else 3
    return "$" + f"{value:.{digits}f}"


def token_cost(tokens):
    """Dollars from tokens × the gateway price of MODEL (USD per 1M tokens)."""
    price = PRICE_PER_1M.get(MODEL)
    used_in = tokens.get("input_tokens") or 0
    used_out = tokens.get("output_tokens") or 0
    if not price or not (used_in or used_out):
        return None
    return round((used_in * price[0] + used_out * price[1]) / 1e6, 6)


def emit(tracer, steps):
    """Steps → spans under the current span, each with its own start and end."""
    for s in steps:
        attrs = {"fi.step.kind": s["kind"], **s["attrs"]}
        if s["error"]:
            attrs["error.message"] = s["error"]
        with tracer.start_as_current_span(
            s["name"], attributes=attrs, start_time=s["start"], end_on_exit=False
        ) as span:
            emit(tracer, s["children"])
        span.end(end_time=max(s["end"], s["start"]))


def show(steps, depth=1):
    """--dry-run: the parsed steps, nested, with each step's duration."""
    for s in steps:
        ms = (max(s["end"], s["start"]) - s["start"]) // 1_000_000
        error = f" · {s['error']}" if s["error"] else ""
        print(f"  {'  ' * depth}{s['name']} · {ms} ms{error}")
        show(s["children"], depth + 1)`;

const CLAUDE = String.raw`def parse_try(lines, folder):
    """Claude Code stream-json: model turns, their tool calls and sub-agents
    (a Task call nests the sub-agent's own turns)."""
    steps, calls, agents = [], {}, {}
    final, cost, tokens, error = None, None, {}, None
    prev = first_at(lines)
    for at, event in records(lines):
        kind = event.get("type")
        if kind == "assistant":
            message = event.get("message") or {}
            agent = agents.get(event.get("parent_tool_use_id"))
            turn = step("llm", "model turn", prev, at,
                        **{"llm.model": message.get("model") or MODEL})
            (agent["children"] if agent else steps).append(turn)
            for block in message.get("content") or []:
                if block.get("type") == "text":
                    final = block.get("text") or final
                    turn["attrs"]["output"] = txt(final)[:CAP]
                if block.get("type") != "tool_use":
                    continue
                name, args = block.get("name") or "tool", block.get("input") or {}
                if name == "Task":
                    title = f"sub-agent {args.get('subagent_type') or 'general'}"
                    s = step("agent", title, at, **{"tool.input": args})
                    agents[block.get("id")] = s
                else:
                    s = step("tool", f"tool {name}", at,
                             **{"tool.name": name, "tool.input": args})
                calls[block.get("id")] = s
                turn["children"].append(s)
        elif kind == "user":
            for block in (event.get("message") or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                s = calls.pop(block.get("tool_use_id"), None)
                if s:
                    s["end"] = at
                    s["attrs"]["tool.output"] = txt(block.get("content"))[:CAP]
                    if block.get("is_error"):
                        s["error"] = txt(block.get("content"))[:300] or "tool error"
        elif kind == "result":
            final = event.get("result") or final
            cost = event.get("total_cost_usd")
            usage = event.get("usage") or {}
            tokens = {k: usage.get(k) for k in ("input_tokens", "output_tokens")}
            if event.get("is_error"):
                error = event.get("subtype") or "error"
        prev = at
    unmatched(calls)
    if cost is None:
        cost = token_cost(tokens)
    return parsed(steps, final, cost, tokens, error)`;

const CODEX = String.raw`def parse_try(lines, folder):
    """Codex CLI exec --json: a model turn per turn, its commands, MCP calls
    (arguments and result), edits, web searches and sub-agents; reasoning on
    the turn; tokens from turn.completed (cost = tokens × the price table)."""
    steps, started, final, tokens, error = [], {}, None, {}, None
    turn, reasoning = None, None

    def open_turn(at):
        nonlocal turn
        if turn is None:
            turn = step("llm", "model turn", at, at, **{"llm.model": MODEL})
            steps.append(turn)
        return turn

    for at, event in records(lines):
        kind, item = event.get("type"), event.get("item") or {}
        if kind == "turn.started":
            turn = None
            open_turn(at)
        elif kind == "item.started":
            started[item.get("id")] = (at, item)
        elif kind == "turn.completed":
            usage = event.get("usage") or {}
            for k in ("input_tokens", "output_tokens"):
                tokens[k] = (tokens.get(k) or 0) + (usage.get(k) or 0)
            if turn:
                turn["end"] = at
        elif kind in ("turn.failed", "error"):
            error = (event.get("error") or {}).get("message") or event.get("message")
        elif kind == "item.completed":
            begin = started.pop(item.get("id"), (at, item))[0]
            it = item.get("type")
            host = open_turn(begin)
            host["end"] = at
            s = None
            if it == "reasoning":
                reasoning = (reasoning or "") + (item.get("text") or "")
                host["attrs"]["llm.reasoning"] = reasoning[:CAP]
            elif it == "agent_message":
                final = item.get("text") or final
                host["attrs"]["output"] = txt(final)[:CAP]
            elif it == "command_execution":
                s = step("tool", "tool shell", begin, at, **{
                    "tool.name": "shell", "tool.input": item.get("command"),
                    "tool.output": item.get("aggregated_output"),
                    "tool.exit_code": item.get("exit_code")})
                if item.get("exit_code") not in (None, 0):
                    s["error"] = f"exit {item.get('exit_code')}"
            elif it == "mcp_tool_call":
                name = f"{item.get('server')}.{item.get('tool')}"
                s = step("tool", f"tool {name}", begin, at, **{
                    "tool.name": name, "mcp.server": item.get("server"),
                    "tool.input": item.get("arguments"),
                    "tool.output": item.get("result")})
                if item.get("error"):
                    s["error"] = (item.get("error") or {}).get("message") or "error"
            elif it == "file_change":
                paths = [c.get("path") for c in item.get("changes") or []]
                s = step("tool", "tool apply_patch", begin, at,
                         **{"tool.name": "apply_patch", "tool.input": paths})
            elif it == "web_search":
                s = step("tool", "tool web_search", begin, at,
                         **{"tool.name": "web_search", "tool.input": item.get("query")})
            elif it == "collab_tool_call":
                s = step("agent", f"sub-agent {item.get('tool') or 'spawn'}", begin, at,
                         **{"tool.input": item.get("prompt"),
                            "fi.sub_agents": item.get("receiver_thread_ids")})
            elif it == "error":
                error = item.get("message") or error
            if s:
                host["children"].append(s)
    for at, item in started.values():  # started, never completed
        name = item.get("tool") or item.get("command") or item.get("type") or "tool"
        open_turn(at)["children"].append(
            step("tool", f"tool {name}", at, error="no result (try ended first)"))
    return parsed(steps, final, token_cost(tokens), tokens, error)`;

const GEMINI = String.raw`SUB_AGENTS = {"codebase_investigator", "generalist", "cli_help", "browser_agent"}


def parse_try(lines, folder):
    """Gemini CLI --output-format stream-json: init / message / tool_use /
    tool_result / error / result, each event with its own ISO timestamp.
    Gemini reports tokens, not dollars: cost = tokens × the price table."""
    steps, calls, parts, tokens, error = [], {}, [], {}, None
    turn, boundary = None, first_at(lines)
    for at, event in records(lines):
        t = iso_ns(event.get("timestamp"), at)
        kind = event.get("type")
        if kind == "init" or (kind == "message" and event.get("role") == "user"):
            boundary = t
        elif kind in ("message", "tool_use"):
            if turn is None:  # the first output after a boundary opens a turn
                turn = step("llm", "model turn", boundary, t,
                            **{"llm.model": event.get("model") or MODEL})
                steps.append(turn)
                parts = []
            turn["end"] = t
            if kind == "message":
                parts.append(event.get("content") or "")
                turn["attrs"]["output"] = "".join(parts)[:CAP]
            else:
                name = event.get("tool_name") or "tool"
                agent = name in SUB_AGENTS
                s = step("agent" if agent else "tool",
                         f"{'sub-agent' if agent else 'tool'} {name}", t,
                         **{"tool.name": name,
                            "tool.input": event.get("parameters") or {}})
                calls[event.get("tool_id")] = s
                turn["children"].append(s)
        elif kind == "tool_result":
            turn, boundary = None, t  # the next model turn starts after the tools
            s = calls.pop(event.get("tool_id"), None)
            if s:
                s["end"] = t
                s["attrs"]["tool.output"] = txt(event.get("output"))[:CAP]
                if event.get("status") == "error":
                    s["error"] = (event.get("error") or {}).get("message") or "error"
        elif kind == "error" and event.get("severity") == "error":
            error = event.get("message")
        elif kind == "result":
            stats = event.get("stats") or {}
            tokens = {k: stats.get(k) for k in ("input_tokens", "output_tokens")}
            if event.get("status") == "error":
                error = (event.get("error") or {}).get("message") or error or "error"
    unmatched(calls)
    final = "".join(parts) or None
    return parsed(steps, final, token_cost(tokens), tokens, error)`;

const GOOSE = String.raw`def parse_try(lines, folder):
    """Goose run --output-format stream-json: message chunks (merged by
    message.id), notification, error, complete (tokens and cost_usd)."""
    msgs, order, notes, done, error = {}, [], [], {}, None
    for at, event in records(lines):
        kind = event.get("type")
        if kind == "message":
            m = event.get("message") or {}
            mid = m.get("id") or f"anon-{len(order)}"
            if mid not in msgs:
                order.append(mid)
                msgs[mid] = {"role": m.get("role"), "first": at, "last": at,
                             "text": [], "calls": [], "results": [], "meta": {}}
            e = msgs[mid]
            e["last"] = at
            e["meta"].update(m.get("metadata") or {})
            for b in m.get("content") or []:
                if b.get("type") == "text":
                    e["text"].append(b.get("text") or "")
                elif b.get("type") == "toolRequest":
                    e["calls"].append(b)
                elif b.get("type") == "toolResponse":
                    e["results"].append(b)
        elif kind == "notification":
            notes.append((at, (event.get("log") or {}).get("message") or ""))
        elif kind == "error":
            error = event.get("error")
        elif kind == "complete":
            done = event
    steps, calls, final = [], {}, None
    for mid in order:
        e = msgs[mid]
        if e["role"] == "assistant":
            usage = e["meta"].get("usage") or {}
            model = (e["meta"].get("inference") or {}).get("requestedModel")
            end = e["last"]
            took = usage.get("elapsedMs")
            start = end - int(took) * 1_000_000 if took else e["first"]
            text = "".join(e["text"])
            turn = step("llm", "model turn", start, end, output=text or None, **{
                "llm.model": model or MODEL,
                "llm.token_count.prompt": usage.get("inputTokens"),
                "llm.token_count.completion": usage.get("outputTokens"),
                "llm.cost_usd": usage.get("cost")})
            steps.append(turn)
            final = text or final
            for c in e["calls"]:
                value = (c.get("toolCall") or {}).get("value") or {}
                name = value.get("name") or "tool"
                agent = name.split("__")[-1] == "delegate"
                s = step("agent" if agent else "tool",
                         f"{'sub-agent' if agent else 'tool'} {name}", end,
                         **{"tool.name": name,
                            "tool.input": value.get("arguments") or {}})
                calls[c.get("id")] = s
                turn["children"].append(s)
        for r in e["results"]:
            s = calls.pop(r.get("id"), None)
            if s:
                s["end"] = e["first"]
                res = r.get("toolResult") or {}
                value = res.get("value") or {}
                s["attrs"]["tool.output"] = txt(value or res.get("error"))[:CAP]
                if res.get("status") == "error" or value.get("isError"):
                    s["error"] = txt(res.get("error") or "tool error")[:300]
    agents = [c for t in steps for c in t["children"] if c["kind"] == "agent"]
    for at, note in notes:  # "[subagent:<id>] <tool> | <extension>": name only
        m = re.match(r"\[subagent:([^\]]+)\]\s+(.*)", note)
        parent = next((a for a in agents if a["start"] <= at <= a["end"]), None)
        if parent and m:
            name = m.group(2).split(" | ")[0]
            parent["children"].append(
                step("tool", f"tool {name}", at, **{"fi.sub_agent": m.group(1)}))
    unmatched(calls)
    tokens = {k: done.get(k) for k in ("input_tokens", "output_tokens")}
    return parsed(steps, final, done.get("cost_usd"), tokens, error)`;

const OPENHANDS_V0 = String.raw`def parse_try(lines, folder):
    """OpenHands 0.x: the trajectory it saves at [core] save_trajectory_path
    (events with timestamp, action | observation, cause, tool_call_metadata,
    llm_metrics.accumulated_cost). Not written (killed): the stdout tail."""
    events = read_json(os.path.join(folder, RECORD_FILE))
    if not isinstance(events, list):
        return parsed([], tail(lines), None, None, "no trajectory written")
    steps, turns, calls, final, cost, prev = [], {}, {}, None, None, None
    for ev in events:
        t = iso_ns(ev.get("timestamp"), time.time_ns())
        cost = (ev.get("llm_metrics") or {}).get("accumulated_cost", cost)
        if "action" in ev and ev.get("source") == "agent":
            meta = ev.get("tool_call_metadata") or {}
            resp = meta.get("model_response") or {}
            args = ev.get("args") or {}
            if resp.get("id") not in turns:
                usage = resp.get("usage") or {}
                turns[resp.get("id")] = step(
                    "llm", "model turn", prev or t, t, output=args.get("thought"), **{
                        "llm.model": resp.get("model") or MODEL,
                        "llm.token_count.prompt": usage.get("prompt_tokens"),
                        "llm.token_count.completion": usage.get("completion_tokens")})
                steps.append(turns[resp.get("id")])
            turn = turns[resp.get("id")]
            turn["end"] = t
            action = ev["action"]
            if action in ("finish", "message"):
                final = args.get("final_thought") or args.get("content") or final
                turn["attrs"]["output"] = txt(final)[:CAP]
            else:
                name = meta.get("function_name") or action
                agent = action == "delegate"
                s = step("agent" if agent else "tool",
                         f"{'sub-agent' if agent else 'tool'} {name}", t,
                         **{"tool.name": name, "tool.input": args})
                calls[ev.get("id")] = s
                turn["children"].append(s)
        elif "observation" in ev and ev.get("cause") in calls:
            s = calls.pop(ev["cause"])
            s["end"] = t
            s["attrs"]["tool.output"] = txt(ev.get("content"))[:CAP]
            if ev["observation"] == "error":
                s["error"] = txt(ev.get("content"))[:300] or "error"
        prev = t
    unmatched(calls)
    return parsed(steps, final, cost)`;

const OPENHANDS_V1 = String.raw`def parse_try(lines, folder):
    """OpenHands CLI 1.x --headless --json: one SDK event per stdout line
    (ActionEvent / ObservationEvent / MessageEvent / AgentErrorEvent /
    ConversationStateUpdateEvent stats with accumulated_cost)."""
    steps, turns, calls, final, cost, prev = [], {}, {}, None, None, None
    for at, ev in records(lines):
        t = iso_ns(ev.get("timestamp"), at)
        kind = ev.get("kind")
        if kind == "ActionEvent":
            rid = ev.get("llm_response_id")
            if rid not in turns:  # parallel tool calls share one model response
                thought = "".join(c.get("text", "") for c in ev.get("thought") or [])
                turns[rid] = step("llm", "model turn", prev or t, t,
                                  output=thought or ev.get("reasoning_content"),
                                  **{"llm.model": MODEL})
                steps.append(turns[rid])
            turn = turns[rid]
            turn["end"] = t
            name = ev.get("tool_name") or "tool"
            action = ev.get("action") or {}
            if name == "finish":
                final = action.get("message") or final
                turn["attrs"]["output"] = txt(final)[:CAP]
            else:
                agent = name in ("delegate", "task")
                args = (ev.get("tool_call") or {}).get("arguments") or action
                s = step("agent" if agent else "tool",
                         f"{'sub-agent' if agent else 'tool'} {name}", t,
                         **{"tool.name": name, "tool.input": args})
                calls[ev.get("tool_call_id")] = s
                turn["children"].append(s)
        elif kind in ("ObservationEvent", "UserRejectObservation", "AgentErrorEvent"):
            s = calls.pop(ev.get("tool_call_id"), None)
            if s:
                s["end"] = t
                obs = ev.get("observation") or {}
                content = obs.get("content")
                if isinstance(content, list):
                    content = "".join(c.get("text", "") for c in content
                                      if isinstance(c, dict))
                s["attrs"]["tool.output"] = txt(content or ev.get("error"))[:CAP]
                if kind != "ObservationEvent" or obs.get("is_error"):
                    s["error"] = ev.get("error") or ev.get("rejection_reason") or "error"
        elif kind == "MessageEvent" and ev.get("source") == "agent":
            content = (ev.get("llm_message") or {}).get("content") or []
            text = "".join(c.get("text", "") for c in content)
            steps.append(step("llm", "model turn", prev or t, t, output=text,
                              **{"llm.model": MODEL}))
            final = text or final
        elif kind == "ConversationStateUpdateEvent" and ev.get("key") == "stats":
            metrics = (ev.get("value") or {}).get("usage_to_metrics") or {}
            cost = sum(float(x.get("accumulated_cost") or 0) for x in metrics.values())
        prev = t
    unmatched(calls)
    return parsed(steps, final, cost)`;

const AIDER = String.raw`LLM_HEADER = re.compile(r"^(TO LLM|LLM RESPONSE) (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)$")


def read_lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines()
    except OSError:
        return []


def parse_try(lines, folder):
    """Aider: each model call from --llm-history-file (TO LLM / LLM RESPONSE
    blocks with times), its tokens and cost from --analytics-log
    (message_send), each edited file from the chat history (Applied edit)."""
    fi = os.path.join(folder, ".fi")
    blocks, cur = [], None
    for line in read_lines(os.path.join(fi, "aider.llm.history")):
        m = LLM_HEADER.match(line)
        if m:
            cur = {"kind": m.group(1), "t": iso_ns(m.group(2), 0), "lines": []}
            blocks.append(cur)
        elif cur:
            cur["lines"].append(line)
    if not blocks:
        return parsed([], tail(lines), None, None, None)
    sends = []
    for line in read_lines(os.path.join(fi, "aider.events.jsonl")):
        event = json.loads(line) if line.strip() else {}
        if event.get("event") == "message_send":
            sends.append(event.get("properties") or {})
    steps, request, final = [], None, None
    for b in blocks:
        if b["kind"] == "TO LLM":
            request = b
            continue
        sent_lines = (request or {}).get("lines", [])
        asked = [x[5:] for x in sent_lines if x.startswith("USER ")]
        out = "\n".join(x[10:] for x in b["lines"] if x.startswith("ASSISTANT "))
        sent = sends[len(steps)] if len(steps) < len(sends) else {}
        lint = any("Fix any errors below" in x for x in asked[-40:])
        start = request["t"] if request else b["t"]
        steps.append(step("llm", "model call", start, b["t"], output=out, **{
            "input": "\n".join(asked[-60:]),
            "llm.model": sent.get("main_model") or MODEL,
            "llm.token_count.prompt": sent.get("prompt_tokens"),
            "llm.token_count.completion": sent.get("completion_tokens"),
            "llm.cost_usd": sent.get("cost"),
            "fi.reflection": "lint" if lint else None}))
        final, request = out or final, None
    for line in read_lines(os.path.join(fi, "aider.chat.history.md")):
        m = re.match(r"^> Applied edit to (.+)$", line)
        if m:  # under the model call whose reply wrote that file's edit block
            path = m.group(1).strip()
            wrote = [s for s in steps if path in s["attrs"].get("output", "")]
            host = (wrote or steps)[-1]
            host["children"].append(step("tool", f"edit {path}", host["end"],
                                         **{"tool.name": "edit",
                                            "tool.input": path}))
    cost = sends[-1].get("total_cost") if sends else None
    tokens = {"input_tokens": sum(s.get("prompt_tokens") or 0 for s in sends),
              "output_tokens": sum(s.get("completion_tokens") or 0 for s in sends)}
    return parsed(steps, final, cost, tokens)`;

const MINI = String.raw`BASH = re.compile(r"${"`"}${"`"}${"`"}bash\s*\n(.*?)\n${"`"}${"`"}${"`"}", re.DOTALL)


def mini_steps(traj, fallback_t):
    """A mini-SWE-agent trajectory: v1 "mini-swe-agent-1" (one bash block per
    assistant message, the observation in the next user message) or v2
    "mini-swe-agent-1.1" (tool_calls + extra.actions, role tool results)."""
    info, msgs = traj.get("info") or {}, traj.get("messages") or []
    steps, calls, prev, turn = [], {}, None, None
    for m in msgs:
        extra = m.get("extra") or {}
        t = sec_ns(m.get("timestamp") or extra.get("timestamp"), prev or fallback_t)
        role = m.get("role")
        if role == "assistant":
            usage = (extra.get("response") or {}).get("usage") or {}
            turn = step("llm", "model call", prev or t, t, output=m.get("content"), **{
                "llm.model": MODEL,
                "llm.token_count.prompt": usage.get("prompt_tokens"),
                "llm.token_count.completion": usage.get("completion_tokens"),
                "llm.cost_usd": extra.get("cost")})
            steps.append(turn)
            actions = extra.get("actions") or [
                {"command": c, "tool_call_id": None}
                for c in BASH.findall(m.get("content") or "")[:1]]
            for a in actions:
                s = step("tool", "bash", t,
                         **{"tool.name": "bash", "tool.input": a.get("command")})
                calls[a.get("tool_call_id") or "v1"] = s
                turn["children"].append(s)
        elif role in ("tool", "user") and calls:
            s = calls.pop(m.get("tool_call_id") or "v1", None)
            if s:
                s["end"] = t
                s["attrs"]["tool.output"] = txt(m.get("content"))[:CAP]
                code = extra.get("returncode")
                if code is None:
                    found = re.search(r"<returncode>(-?\d+)</returncode>",
                                      txt(m.get("content")))
                    code = int(found.group(1)) if found else None
                if code not in (None, 0):
                    s["error"] = f"exit {code}"
        prev = t
    status = info.get("exit_status")
    for key, s in list(calls.items()):  # the submit command ends the try:
        if status == "Submitted" and "SUBMIT_FINAL_OUTPUT" in s["attrs"].get(
                "tool.input", ""):  # its output is the submission
            s["attrs"]["tool.output"] = txt(info.get("submission"))[:CAP]
            calls.pop(key)
    unmatched(calls)
    error = None if status in ("Submitted", None) else f"mini exit_status {status}"
    cost = (info.get("model_stats") or {}).get("instance_cost")
    return parsed(steps, info.get("submission") or None, cost, None, error)


def parse_try(lines, folder):
    """mini -o .fi/mini.traj.json: mini saves it on exit and on interrupt."""
    traj = read_json(os.path.join(folder, RECORD_FILE))
    if not isinstance(traj, dict):
        return parsed([], tail(lines), None, None, "no trajectory written")
    return mini_steps(traj, first_at(lines))`;

const CUSTOM = String.raw`def atif_steps(data):
    """ATIF (Harbor RFC 0001): agent steps with tool calls and observations."""
    steps, base = [], time.time_ns()
    for i, s in enumerate(data.get("steps") or []):
        if s.get("source") != "agent":
            continue
        t = iso_ns(s.get("timestamp"), base + i)
        met = s.get("metrics") or {}
        turn = step("llm", "model turn", t, t, output=s.get("message"), **{
            "llm.model": s.get("model_name") or MODEL,
            "llm.token_count.prompt": met.get("prompt_tokens"),
            "llm.token_count.completion": met.get("completion_tokens"),
            "llm.cost_usd": met.get("cost_usd")})
        steps.append(turn)
        results = (s.get("observation") or {}).get("results") or []
        by_call = {r.get("source_call_id"): r for r in results}
        for c in s.get("tool_calls") or []:
            name = c.get("function_name") or "tool"
            r = by_call.get(c.get("tool_call_id"))
            tool = step("tool", f"tool {name}", t, **{
                "tool.name": name, "tool.input": c.get("arguments"),
                "tool.output": (r or {}).get("content")})
            if r is None:
                tool["error"] = "no result (try ended first)"
            turn["children"].append(tool)
    final = next((s.get("message") for s in reversed(data.get("steps") or [])
                  if s.get("source") == "agent" and s.get("message")), None)
    return parsed(steps, final, (data.get("final_metrics") or {}).get("total_cost_usd"))


def messages_steps(msgs, start):
    """OpenAI-style chat messages: assistant turns, tool calls, tool results."""
    steps, calls, final = [], {}, None
    for i, m in enumerate(msgs):
        t = start + i * 1_000_000
        if m.get("role") == "assistant":
            turn = step("llm", "model turn", t, t, output=m.get("content"),
                        **{"llm.model": MODEL})
            steps.append(turn)
            final = m.get("content") or final
            for c in m.get("tool_calls") or []:
                fn = c.get("function") or {}
                s = step("tool", f"tool {fn.get('name') or 'tool'}", t, **{
                    "tool.name": fn.get("name"), "tool.input": fn.get("arguments")})
                calls[c.get("id")] = s
                turn["children"].append(s)
        elif m.get("role") == "tool":
            s = calls.pop(m.get("tool_call_id"), None)
            if s:
                s["end"] = t
                s["attrs"]["tool.output"] = txt(m.get("content"))[:CAP]
    unmatched(calls)
    return parsed(steps, final)


def parse_try(lines, folder):
    """Your command's trace file (FI_TRACE_FILE) when it writes one: ATIF, a
    mini-SWE-agent trajectory or OpenAI chat messages; else the stdout tail."""
    data = read_json(os.path.join(folder, RECORD_FILE))
    if isinstance(data, dict) and str(data.get("schema_version", "")).startswith(
        "ATIF"
    ):
        return atif_steps(data)
    if isinstance(data, dict) and "mini-swe-agent" in str(
        data.get("trajectory_format", "")
    ):
        return mini_steps(data, first_at(lines))
    msgs = data if isinstance(data, list) else (data or {}).get("messages")
    if isinstance(msgs, list) and msgs and isinstance(msgs[0], dict):
        return messages_steps(msgs, first_at(lines))
    return parsed([], tail(lines))  # no trace file: the stdout tail`;

const BLOCKS = {
  claude: CLAUDE,
  codex: CODEX,
  gemini: GEMINI,
  goose: GOOSE,
  openhands_v0: OPENHANDS_V0,
  openhands_jsonl: OPENHANDS_V1,
  aider: AIDER,
  mini: MINI,
  custom: `${MINI.split("\n\n\ndef parse_try")[0]}\n\n\n${CUSTOM}`,
};

/** The Python lines of section 4 for one parser id: helpers + parse_try. */
export function parserLines(parser) {
  return `${COMMON}\n\n\n${BLOCKS[parser] ?? BLOCKS.custom}`.split("\n");
}
