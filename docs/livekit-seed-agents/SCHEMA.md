# Seed Agent — LiveKit-Native Definition Format

Each seed agent is a **self-contained LiveKit agent definition**: a JSON `config.json` that maps 1:1 onto
[LiveKit Agents](https://docs.livekit.io/agents/) primitives, plus a runnable `agent.py`. Nothing here is
tied to the FutureAGI platform — the target is standing the agent up **on LiveKit directly** (hosted on
GitHub). Scenario generation is handled elsewhere by the simulation stack, so no scenario/verifier data
lives in a seed.

Each agent folder ships a hand-written **`agent.py`** — the phase-agents, tools, and handoffs written
explicitly, so the deterministic guardrails (verification gates, step-up auth, scripted disclosures) are
visible in code rather than hidden in a generic interpreter. A generic `config.json` → LiveKit loader that
builds an `AgentSession` (one `Agent` per phase, one `@function_tool` per tool) is a possible future
addition; the mapping table below is what such a loader — or an engineer — follows.

---

## Top-level `config.json`

```jsonc
{
  "id": "debt_collection",                     // stable slug
  "display_name": "Collections — Payment Reminder",
  "channel": "voice",                          // "voice" | "chat"
  "direction": "outbound",                     // "inbound" | "outbound"
  "languages": ["en", "es"],
  "description": "one-liner",

  // → AgentSession(stt=…, llm=…, tts=…, vad=…). For "chat", stt/tts/vad are omitted.
  "runtime": {
    "type": "pipeline",                        // "pipeline" (STT→LLM→TTS) | "realtime"
    "llm": { "provider": "openai",   "model": "gpt-4o", "temperature": 0.2 },
    "stt": { "provider": "deepgram", "model": "nova-2", "language": "multi" },
    "tts": { "provider": "cartesia", "voice": "<voice-id>" },
    "vad": { "provider": "silero" },
    "allow_interruptions": true
  },

  // → @dataclass carried on AgentSession[T] as userdata, preserved across every handoff.
  //    "field": "type=default"   (default optional)
  "session_state": { "customer_name": "str", "verified": "bool=false" },

  "workflow": {
    "entry_agent": "phase_id",
    "agents": [
      {
        "id": "phase_id",
        "title": "Phase name",
        "first_message": "Spoken/sent in on_enter (usually only the entry agent).",
        "instructions": "FULL system prompt for THIS phase — production copy a customer would ship: role, what to collect, tone, what NOT to do, when to hand off.",
        "tools": ["tool_name"],                // subset of `tools[]` this phase exposes
        "handoffs": [ { "to": "next_phase", "when": "plain-language branch condition" } ],
        "terminal": false                      // true = this phase may end the session
      }
    ],
    // available from EVERY phase → tools on a shared BaseAgent (pattern-interrupts)
    "global_handlers": [
      { "id": "handler", "tool": "tool_name", "when": "trigger", "then": "handoff:phase | end:CODE" }
    ]
  },

  "tools": [ /* see Tool object */ ],

  "guardrails": {
    "deterministic": [ "rules enforced in code, not by the model" ],
    "content":       [ "prompt-level content limits" ],
    "notes":         "free text"
  },

  "dispositions": [ "TERMINAL_OUTCOME_CODES" ]
}
```

### Tool object

```jsonc
{
  "name": "verify_identity",
  "description": "What the LLM sees — becomes the @function_tool docstring.",
  "parameters": {                              // JSON Schema → LiveKit type-hinted args
    "type": "object",
    "properties": {
      "answer": { "type": "string", "description": "value the caller gave" },
      "factor": { "type": "string", "enum": ["dob", "zip", "last4"] }
    },
    "required": ["answer", "factor"]
  },
  "execution": { "type": "webhook", "method": "POST", "url": "{{FAI_TOOL_BASE}}/verify_identity" },
  "mock": { "verified": true, "method": "last4" },   // deterministic stub for non-prod
  "sets": { "verified": "$.verified" },              // write tool result → userdata (JSONPath)
  "returns_agent": "disclosure",                      // if set: this tool triggers a handoff
  "errors": [ "no account in context" ]               // → raise ToolError(...) (correctable)
}
```

`execution.type`: `webhook` (HTTP), `transfer` (SIP/warm transfer), or `mock` (stub only). Real endpoints
resolve `{{FAI_TOOL_BASE}}` / SIP targets from env at deploy time; secrets never live in a seed.

---

## LiveKit mapping

| config.json | LiveKit Agents (Python) |
|---|---|
| `runtime.*` | `AgentSession(stt=…, llm=…, tts=…, vad=…)` |
| `session_state` | `@dataclass` on `AgentSession[T]` |
| each `workflow.agents[]` | an `Agent` subclass (`instructions=`) |
| `first_message` | `async def on_enter(self): await self.session.generate_reply(...)` |
| `tools[]` | `@function_tool()` methods (docstring = description, params = type hints) |
| `handoffs` / `returns_agent` | tool returns `tuple[Agent, str]` (next agent + line) |
| `global_handlers` | tools on a shared `BaseAgent` every phase inherits |
| `mock` | stub body used when `execution.type == "mock"` or in non-prod |
| `errors` | `raise ToolError(...)` |
| `guardrails.deterministic` | `if not ctx.userdata.x: raise ToolError(...)` + scripted `on_enter` |

Entrypoint is a standard LiveKit worker: `cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))`, where
`entrypoint` builds the `AgentSession`, registers the entry `Agent`, and `await session.start(...)`. See any
agent's `agent.py` for a complete, runnable example.
