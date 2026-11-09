// Static test data for component and model contracts; no simulation runtime.
export const TS3_CONFIGS = Object.freeze({
  A: {
    kind: "code",
    framework: {
      id: "langgraph",
      name: "LangGraph",
      version: "0.4.8",
      language: "python",
      tracing: "auto",
      graph_source: "connect_sdk",
    },
    execution: {
      mode: "local",
      label: "alice@mbp-14",
      machine_id: "cm_alice_mbp14",
      agent_id: "ca_support_agent",
      owner: {
        id: "7a0f3c2e-5b1d-4c8e-9f21-0d6a1b2c3d46",
        name: "Alice Park",
      },
      concurrency: 4,
    },
    models: [
      {
        id: "gpt-4.1-mini",
        role: "main",
        calls: null,
        source: "both",
      },
      {
        id: "gpt-4o-mini",
        role: "node:classify_intent",
        calls: null,
        source: "observed",
      },
    ],
    tools: [
      {
        name: "lookup_order",
        mode: "live",
        declared: true,
        called: true,
      },
      {
        name: "lookup_customer",
        mode: "live",
        declared: true,
        called: true,
      },
      {
        name: "check_stock",
        mode: "live",
        declared: true,
        called: true,
      },
      {
        name: "search_kb_v2",
        mode: "live",
        declared: true,
        called: true,
      },
      {
        name: "issue_refund",
        mode: "live",
        declared: true,
        called: true,
        side_effect: true,
      },
      {
        name: "offer_replacement",
        mode: "live",
        declared: true,
        called: true,
        side_effect: true,
      },
      {
        name: "escalate_to_human",
        mode: "live",
        declared: true,
        called: true,
      },
    ],
    mcp_servers: [
      {
        name: "acme-support",
        transport: "stdio",
        tools: 12,
        permission: "edit",
      },
    ],
    params: {
      schema: [
        {
          name: "system_prompt",
          type: "prompt",
          default:
            "You are Acme's support agent. Copy order references exactly as the customer wrote them.",
          label: "System prompt",
          source: "code",
        },
        {
          name: "model",
          type: "model",
          default: "gpt-4.1-mini",
          options: ["gpt-4.1-mini", "gpt-4o-mini", "claude-haiku-4-5"],
          label: "Model",
          source: "code",
        },
        {
          name: "temperature",
          type: "float",
          default: 0.2,
          min: 0,
          max: 1,
          step: 0.1,
          label: "Temperature",
          source: "code",
        },
        {
          name: "retriever_k",
          type: "int",
          default: 5,
          min: 1,
          max: 20,
          step: 1,
          label: "Retriever k",
          help: "How many knowledge-base passages the agent reads per turn.",
          source: "code",
        },
      ],
      values: {
        system_prompt:
          "You are Acme's support agent. Copy order references exactly as the customer wrote them.",
        model: "gpt-4.1-mini",
        temperature: 0.2,
        retriever_k: 5,
      },
      set_name: "default",
    },
    prompts: [
      {
        name: "system_prompt",
        chars: 412,
        text_hash: "9c1e",
        source: "declared",
      },
    ],
    secrets: {
      model_keys: "machine",
      ref: "OPENAI_API_KEY",
    },
    safety: {
      side_effects: "real",
    },
    code: {
      mode: "connect_preview",
      repo: "acme/support-agent",
      branch: "main",
      commit: "9f2c1a7",
      dirty: {
        files: [],
      },
      entry: "agent.py:graph",
      command: null,
      sdk: null,
      runtime: "Python 3.12",
      host: "alice@mbp-14",
      diff_snapshot: null,
    },
    dataset: {
      version: "v2",
      split: null,
      rows: 30,
    },
    evals: [
      {
        id: "task_success",
        version: "v1",
      },
      {
        id: "reply_rubric",
        version: "v1",
      },
    ],
    trials: 3,
  },
  B: {
    kind: "code",
    framework: {
      id: "openai_agents",
      name: "OpenAI Agents SDK",
      version: "0.3",
      language: "python",
      tracing: "auto",
      graph_source: null,
      agents: ["triage_agent", "refunds_agent", "orders_agent"],
      guardrails: 1,
    },
    execution: {
      mode: "ci",
      label: "GitHub Actions #1182",
      runner: "GitHub Actions #1182",
      concurrency: 4,
    },
    models: [
      {
        id: "gpt-4.1-mini",
        role: "main",
        calls: null,
        source: "both",
      },
    ],
    tools: [
      {
        name: "lookup_order",
        mode: "live",
        declared: true,
        called: true,
      },
      {
        name: "lookup_customer",
        mode: "live",
        declared: true,
        called: true,
      },
      {
        name: "issue_refund",
        mode: "live",
        declared: true,
        called: true,
        side_effect: true,
      },
      {
        name: "schedule_callback",
        mode: "live",
        declared: true,
        called: true,
      },
      {
        name: "create_ticket",
        mode: "live",
        declared: true,
        called: true,
      },
      {
        name: "search_kb",
        mode: "live",
        declared: true,
        called: false,
      },
    ],
    mcp_servers: [],
    params: {
      schema: [],
      values: {},
      set_name: "default",
    },
    prompts: [
      {
        name: "triage_instructions",
        chars: 318,
        text_hash: "4b7d",
        source: "observed",
      },
    ],
    secrets: {
      model_keys: "workspace",
      ref: "OPENAI_API_KEY",
    },
    safety: {
      side_effects: "real",
    },
    code: {
      mode: "trace_upload",
      repo: "acme/support-agents",
      branch: "main",
      commit: "4be2d90",
      dirty: {
        files: [],
      },
      entry: "agent.py:triage_agent",
      command: null,
      sdk: "fi-instrumentation-otel · traceAI-openai-agents",
      runtime: "Python 3.12",
      host: "GitHub Actions #1182",
      diff_snapshot: null,
    },
    dataset: {
      version: "v2",
      split: null,
      rows: 30,
    },
    evals: [
      {
        id: "task_success",
        version: "v1",
      },
      {
        id: "reply_rubric",
        version: "v1",
      },
    ],
    trials: 3,
  },
  C: {
    kind: "code",
    framework: {
      id: "crewai",
      name: "CrewAI",
      version: "0.80",
      language: "python",
      tracing: "auto",
      graph_source: null,
      agents: ["manager", "intake", "refund_specialist", "writer"],
    },
    execution: {
      mode: "local",
      label: "alice@mbp-14",
      machine_id: "cm_alice_mbp14",
      owner: {
        id: "7a0f3c2e-5b1d-4c8e-9f21-0d6a1b2c3d46",
        name: "Alice Park",
      },
      concurrency: 4,
    },
    models: [
      {
        id: "gpt-4o",
        role: "main",
        calls: null,
        source: "both",
      },
    ],
    tools: [
      {
        name: "lookup_order",
        mode: "live",
        declared: true,
        called: true,
      },
      {
        name: "issue_refund",
        mode: "live",
        declared: true,
        called: true,
        side_effect: true,
      },
      {
        name: "offer_replacement",
        mode: "live",
        declared: true,
        called: true,
        side_effect: true,
      },
      {
        name: "search_kb",
        mode: "live",
        declared: true,
        called: true,
      },
      {
        name: "create_ticket",
        mode: "live",
        declared: true,
        called: true,
      },
    ],
    mcp_servers: [],
    params: {
      schema: [],
      values: {},
      set_name: "default",
    },
    prompts: [
      {
        name: "manager_goal",
        chars: 266,
        text_hash: "e0a4",
        source: "observed",
      },
    ],
    secrets: {
      model_keys: "machine",
      ref: "OPENAI_API_KEY",
    },
    safety: {
      side_effects: "real",
    },
    code: {
      mode: "trace_upload",
      repo: "acme/support-agents",
      branch: "main",
      commit: "4be2d90",
      dirty: {
        files: [],
      },
      entry: "crew.py:crew",
      command: null,
      sdk: "fi-instrumentation-otel · traceAI-crewai",
      runtime: "Python 3.12",
      host: "alice@mbp-14",
      diff_snapshot: null,
    },
    dataset: {
      version: "v2",
      split: null,
      rows: 30,
    },
    evals: [
      {
        id: "task_success",
        version: "v1",
      },
      {
        id: "reply_rubric",
        version: "v1",
      },
    ],
    trials: 3,
  },
  D: {
    kind: "code",
    framework: {
      id: "pydantic_ai",
      name: "Pydantic AI",
      version: "1.0",
      language: "python",
      tracing: "otel",
      graph_source: null,
    },
    execution: {
      mode: "sandbox",
      label: "Future AGI sandbox",
      image: "python:3.12 + acme/support-agents@4be2d90",
      network: "allowlist",
      concurrency: 4,
    },
    models: [
      {
        id: "claude-haiku-4-5",
        role: "main",
        calls: null,
        source: "both",
      },
    ],
    tools: [
      {
        name: "lookup_order",
        mode: "live",
        declared: true,
        called: true,
      },
      {
        name: "lookup_customer",
        mode: "live",
        declared: true,
        called: true,
      },
      {
        name: "issue_refund",
        mode: "live",
        declared: true,
        called: true,
        side_effect: true,
      },
      {
        name: "schedule_callback",
        mode: "live",
        declared: true,
        called: true,
      },
      {
        name: "search_kb",
        mode: "live",
        declared: true,
        called: true,
      },
      {
        name: "create_ticket",
        mode: "live",
        declared: true,
        called: true,
      },
    ],
    mcp_servers: [],
    params: {
      schema: [],
      values: {},
      set_name: "default",
    },
    prompts: [
      {
        name: "system_prompt",
        chars: 380,
        text_hash: "71fd",
        source: "observed",
      },
    ],
    secrets: {
      model_keys: "workspace",
      ref: "ANTHROPIC_API_KEY",
    },
    safety: {
      side_effects: "real",
    },
    code: {
      mode: "trace_upload",
      repo: "acme/support-agents",
      branch: "main",
      commit: "4be2d90",
      dirty: {
        files: [],
      },
      entry: "agent.py:agent",
      command: null,
      sdk: "fi-instrumentation-otel (OTel)",
      runtime: "Python 3.12",
      host: "Future AGI sandbox",
      diff_snapshot: null,
    },
    dataset: {
      version: "v2",
      split: null,
      rows: 30,
    },
    evals: [
      {
        id: "task_success",
        version: "v1",
      },
      {
        id: "reply_rubric",
        version: "v1",
      },
    ],
    trials: 3,
  },
  E: {
    kind: "harness",
    execution: {
      mode: "sandbox",
      label: "Future AGI sandbox",
      image: "ghcr.io/acme/acme-support-sandbox:1.4",
      network: "off",
      concurrency: 2,
    },
    harness: {
      id: "claude_code",
      name: "Claude Code",
      version: "2.x (headless -p)",
      model: "claude-sonnet-4-5",
      sandbox: "Future AGI sandbox · acme-support-sandbox:1.4",
      mcp_servers: [
        {
          name: "acme-support",
          tools: 12,
        },
      ],
      max_turns: 40,
      timeout_s: 600,
      max_cost_usd: 0.5,
      user_sim: {
        model: "gpt-4o-mini",
        from_column: "customer_goal",
      },
      instructions_file: "CLAUDE.md (412 words)",
      permissions: "edit",
      builtin_tools: {
        shell: true,
        edit: true,
        web_fetch: false,
        web_search: false,
      },
      budget: {
        max_turns: 40,
        max_cost_usd: 0.5,
        timeout_s: 600,
      },
      artifacts: ["git_diff", "final_message", "terminal_log"],
      capture: {
        model_calls: "gateway",
        steps: "transcript",
        auth: "api_key",
      },
      subagents: ["order-investigator"],
    },
    model: {
      id: "claude-sonnet-4-5",
      params: {},
    },
    models: [
      {
        id: "claude-sonnet-4-5",
        role: "main",
        calls: null,
        source: "observed",
      },
      {
        id: "claude-haiku-4-5",
        role: "sub-calls",
        calls: null,
        source: "observed",
      },
    ],
    mcp_servers: [
      {
        name: "acme-support",
        transport: "stdio",
        tools: 12,
        permission: "edit",
      },
    ],
    params: {
      schema: [],
      values: {},
      set_name: "default",
    },
    prompts: [
      {
        name: "CLAUDE.md",
        chars: 2480,
        text_hash: "a3c0",
        source: "declared",
      },
    ],
    secrets: {
      model_keys: "workspace",
      ref: "ANTHROPIC_API_KEY",
    },
    safety: {
      side_effects: "real",
    },
    dataset: {
      version: "v2",
      split: null,
      rows: 30,
    },
    evals: [
      {
        id: "task_success",
        version: "v1",
      },
      {
        id: "reply_rubric",
        version: "v1",
      },
    ],
    trials: 3,
  },
  F: {
    kind: "harness",
    execution: {
      mode: "sandbox",
      label: "Future AGI sandbox",
      image: "ghcr.io/acme/acme-support-sandbox:1.4",
      network: "off",
      concurrency: 2,
    },
    harness: {
      id: "openhands",
      name: "OpenHands",
      version: "0.59",
      model: "gemini-2.5-pro",
      sandbox: "Future AGI sandbox · acme-support-sandbox:1.4",
      mcp_servers: [
        {
          name: "acme-support",
          tools: 12,
        },
      ],
      max_turns: 40,
      timeout_s: 900,
      max_cost_usd: 0.5,
      user_sim: {
        model: "gpt-4o-mini",
        from_column: "customer_goal",
      },
      instructions_file: null,
      permissions: "full_auto",
      builtin_tools: {
        shell: true,
        edit: true,
        web_fetch: false,
        web_search: false,
      },
      budget: {
        max_turns: 40,
        max_cost_usd: 0.5,
        timeout_s: 900,
      },
      artifacts: ["git_diff", "final_message"],
      capture: {
        model_calls: "gateway",
        steps: null,
        auth: "api_key",
      },
    },
    model: {
      id: "gemini-2.5-pro",
      params: {},
    },
    models: [
      {
        id: "gemini-2.5-pro",
        role: "main",
        calls: null,
        source: "observed",
      },
    ],
    mcp_servers: [
      {
        name: "acme-support",
        transport: "stdio",
        tools: 12,
        permission: "full_auto",
      },
    ],
    params: {
      schema: [],
      values: {},
      set_name: "default",
    },
    prompts: [],
    secrets: {
      model_keys: "workspace",
      ref: "GEMINI_API_KEY",
    },
    safety: {
      side_effects: "real",
    },
    dataset: {
      version: "v2",
      split: null,
      rows: 30,
    },
    evals: [
      {
        id: "task_success",
        version: "v1",
      },
      {
        id: "reply_rubric",
        version: "v1",
      },
    ],
    trials: 3,
  },
});
