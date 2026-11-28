<div align="center">

<a href="https://futureagi.com?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=logo">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/assets/logo-dark.svg">
    <img alt="Future AGI" src=".github/assets/logo-light.svg" width="300">
  </picture>
</a>

# The infrastructure for self-improving AI agents

Future AGI turns an agent's traces, evaluations and simulations into improvements to the agent, and is open source and self-hostable.

<p>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-4b5563?style=flat-square" alt="Apache 2.0 license"></a>
  <a href="https://github.com/future-agi/future-agi/releases/latest"><img src="https://img.shields.io/github/v/release/future-agi/future-agi?style=flat-square&label=release&color=4b5563" alt="Latest release"></a>
  <a href="https://hub.docker.com/r/futureagi/standalone"><img src="https://img.shields.io/docker/pulls/futureagi/standalone?style=flat-square&logo=docker&logoColor=white&label=docker%20pulls&color=4b5563" alt="Docker pulls"></a>
  <a href="https://pypi.org/project/fi-instrumentation-otel/"><img src="https://img.shields.io/pypi/dm/fi-instrumentation-otel?style=flat-square&logo=python&logoColor=white&label=pypi&color=4b5563" alt="fi-instrumentation-otel downloads on PyPI"></a>
  <a href="https://www.npmjs.com/package/@traceai/fi-core"><img src="https://img.shields.io/npm/dm/@traceai/fi-core?style=flat-square&logo=npm&logoColor=white&label=npm&color=4b5563" alt="@traceai/fi-core downloads on npm"></a>
  <br>
  <a href="https://discord.com/invite/cBnWkd7T9v"><img src="https://img.shields.io/badge/discord-join-5865F2?style=flat-square&logo=discord&logoColor=white" alt="Chat on Discord"></a>
  <a href="https://x.com/FutureAGI_"><img src="https://img.shields.io/badge/follow-%40FutureAGI__-000000?style=flat-square&logo=x&logoColor=white" alt="Follow on X"></a>
  <a href="https://www.linkedin.com/company/futureagi/"><img src="https://img.shields.io/badge/follow-LinkedIn-0a66c2?style=flat-square&logo=linkedin&logoColor=white" alt="Follow on LinkedIn"></a>
  <a href="https://github.com/future-agi/future-agi/pulse"><img src="https://img.shields.io/github/commit-activity/m/future-agi/future-agi?style=flat-square&label=commits&color=4b5563" alt="Commits last month"></a>
  <a href="https://github.com/future-agi/future-agi/issues?q=is%3Aissue+is%3Aclosed"><img src="https://img.shields.io/github/issues-search/future-agi/future-agi?query=is%3Aissue%20is%3Aclosed&style=flat-square&label=issues%20closed&color=4b5563" alt="Issues closed"></a>
</p>

<p>
  <a href="https://app.futureagi.com/auth/jwt/register?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=header_cta_cloud"><b>Start on Cloud (free)</b></a> ·
  <a href="#-quickstart"><b>Self-host</b></a> ·
  <a href="https://docs.futureagi.com?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=header_docs"><b>Docs</b></a> ·
  <a href="https://discord.com/invite/cBnWkd7T9v"><b>Discord</b></a>
</p>

<p>
  <b>SDKs:</b>
  <a href="https://github.com/future-agi/traceAI#python">Python</a> ·
  <a href="https://github.com/future-agi/traceAI#typescript--javascript">TypeScript</a> ·
  <a href="https://github.com/future-agi/traceAI#java">Java</a> ·
  <a href="https://github.com/future-agi/traceAI#go">Go</a> ·
  <a href="https://github.com/future-agi/traceAI#c">C#</a> ·
  <a href="https://github.com/future-agi/futureagi-sdk">Platform SDK</a> ·
  <a href="https://github.com/future-agi/agent-command-center-sdk">Gateway SDK</a>
</p>

<p>
  <sub>
    <b>English</b> ·
    <a href="docs/readme/README.zh-CN.md">简体中文</a> ·
    <a href="docs/readme/README.ja.md">日本語</a> ·
    <a href="docs/readme/README.ko.md">한국어</a> ·
    <a href="docs/readme/README.es.md">Español</a> ·
    <a href="docs/readme/README.pt-BR.md">Português</a> ·
    <a href="docs/readme/README.fr.md">Français</a> ·
    <a href="docs/readme/README.de.md">Deutsch</a>
  </sub>
</p>

<a href="https://www.youtube.com/watch?v=6keOTAOUUWI"><img alt="Watch the Future AGI demo on YouTube" src="frontend/public/assets/readme/demo-6keOTAOUUWI-v4.png" width="100%"></a>

</div>

## How it works

A better model helps every agent. But a model alone doesn't know which of last week's answers were wrong, can't test a fix before your users see it, and can't tell whether the fix broke something else. Future AGI does that work for you, in a loop around your agent.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/readme/system-dark.png">
  <img alt="The self-improving loop around your agent, from v1 to v2. Real users and production traffic feed Trace, which records every real run step by step. Evaluate scores each run with the reason it passed or failed. Simulate replays the failures with simulated users in a test world. Improve: Epoch tries fixes as full runs and keeps the winner; a fix that breaks a passing case is rejected and a fix that did not help is dropped. The winning fix goes to Ship and protect, where you approve the change and the gateway guards it in production. The next round starts on new runs." src=".github/assets/readme/system-light.png" width="100%">
</picture>

1. **[Trace](#trace-and-monitor)** every run of your agent, step by step.<br><sub>Tracing · Sessions and users · Live evals · Instant eval · Dashboards · Alerts · Error Feed · Insights</sub>
2. **[Evaluate](#evaluate-and-label)** each run and see why it passed or failed.<br><sub>Agent, LLM and code evals · Jev · Composite evals · Datasets · Experiments · Annotations · Evals in CI</sub>
3. **[Simulate](#simulate-and-test)** realistic users against your agent in a test world built around it.<br><sub>Environments · Scenarios and personas · Text and voice · Scenarios from real traffic · Debug failures</sub>
4. **[Improve](#improve)** the agent: find a change that fixes the failures and prove it before it ships.<br><sub>Epoch · Prompt optimization</sub>
5. **[Protect](#gateway-and-guardrails)** live traffic with a gateway and guardrails. New runs are traced, and the loop starts again.<br><sub>One API for every model · Routing and fallbacks · Guardrails · Caching · Keys and budgets · Logs and analytics · Shadow experiments · MCP and A2A</sub>

Around the loop, **[Build](#build)** gives you Prompts, Agent Playground, Knowledge bases, Falcon AI, an MCP server and team workspaces. Start with any one part. They all work on the same traces, datasets and evals, so you can add the next one when you need it.

<a id="-quickstart"></a>
<a id="-quickstart-60-seconds"></a>

<a id="get-started"></a>

## Get started

**1. Get your keys.** [Create a free Cloud account](https://app.futureagi.com/auth/jwt/register?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=quickstart_cloud), or run it on your machine with Docker (Compose v2.24+, 2 vCPUs and 4 GB for Docker):

macOS / Linux / WSL:

```bash
git clone https://github.com/future-agi/future-agi.git
cd future-agi
./bin/install
```

Windows (PowerShell):

```powershell
git clone https://github.com/future-agi/future-agi.git
cd future-agi
.\bin\install.ps1
```

The installer writes this install's secrets to `.env`, pulls the images (about 800 MB) and waits until everything is up; the first boot takes a few minutes. Then open <http://localhost:3000> and create your account.

In either case, open **Keys** in the sidebar, select **Add API Key**, and copy the API key and secret key before you close the dialog.

**2. Install the SDK and set your keys.**

```bash
pip install -U fi-instrumentation-otel traceai-openai openai
export FI_API_KEY="<your API key>" FI_SECRET_KEY="<your secret key>" FI_PROJECT_NAME="my-agent"
export OPENAI_API_KEY="<your OpenAI key>"
# Self-hosted only: send traces to your install's collector
export FI_BASE_URL="http://localhost:4318"
```

**3. Run your app with auto-tracing.** Your code needs no tracing imports. Start it through `fi-instrument`, and the libraries it uses are traced when their traceAI package is installed:

```python
# app.py: plain OpenAI code, nothing added
from openai import OpenAI

response = OpenAI().chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Plan a three-step onboarding email."}],
)
print(response.choices[0].message.content)
```

```bash
fi-instrument python app.py      # also works with uvicorn, celery and other commands
```

Open **Tracing** in the sidebar and select `my-agent` to see the call with its input, output, tokens and timing. Next: [score it with an eval](https://docs.futureagi.com/docs/evaluation?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=quickstart_next) or [build a test environment](https://docs.futureagi.com/docs/environments?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=quickstart_next) for your agent.

<details>
<summary><b>Prefer one line in code?</b></summary>

```python
import fi_instrumentation

fi_instrumentation.init(project_name="my-agent")
# ...your app code; installed traceAI integrations are traced from here
```

Or keep your exact start command and set `FI_AUTO_INSTRUMENT=true`. Most integrations switch on by themselves; a few, such as Strands and Temporal, need one setup call. See [zero-code tracing for Python](https://github.com/future-agi/traceAI/blob/main/docs/python/auto-instrumentation.md) for the full list, privacy settings and `fi-instrument doctor`.

</details>

<details>
<summary><b>TypeScript</b></summary>

```bash
npm install "@traceai/fi-core@^1.4.0" @traceai/openai openai
```

With Node.js 22.23.4 or later, save as `trace.mts` and run `node trace.mts`:

```typescript
import OpenAI from "openai";
import { register, ProjectType } from "@traceai/fi-core";
import { OpenAIInstrumentation } from "@traceai/openai";

const provider = register({ projectName: "my-agent", projectType: ProjectType.OBSERVE });
const instrumentation = new OpenAIInstrumentation();
instrumentation.setTracerProvider(provider);
instrumentation.manuallyInstrument(OpenAI);

const response = await new OpenAI().chat.completions.create({
  model: "gpt-4o-mini",
  messages: [{ role: "user", content: "Plan a three-step onboarding email." }],
});
console.log(response.choices[0].message.content);
await provider.shutdown(); // send the trace before the script exits
```

</details>

## What's inside

<a id="trace-and-monitor"></a>

### Trace and monitor

See what your agent did, what it cost, and where it went wrong.

- **[Tracing](https://docs.futureagi.com/docs/observe?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_tracing)**: every LLM call, tool call and agent step, with inputs, outputs, tokens, cost and latency, and a graph of the path your agent took. Built on OpenTelemetry, with ready-made instrumentation for 50+ frameworks, and voice calls with their recordings.
- **[Sessions and users](https://docs.futureagi.com/docs/observe/features/users?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_users)**: group traces into conversations, and see every trace and metric for one end user.
- **[Evals on live traffic](https://docs.futureagi.com/docs/observe/features/evals?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_live_evals)**: score production traces automatically as they arrive, on every span or a sample.
- **[Instant eval](https://docs.futureagi.com/docs/observe/features/instant-eval?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_instant_eval)**\*: ask Jev a yes/no or category question about the traces, sessions or users in any view, like "did the assistant resolve the request?", without building an eval first.
- **[Dashboards](https://docs.futureagi.com/docs/observe/features/dashboard?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_dashboards)**: build and share charts of error rate, latency, tokens, cost and eval scores.
- **[Alerts](https://docs.futureagi.com/docs/observe/features/alerts?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_alerts)**: get an email or Slack message when a metric or eval score crosses a threshold.
- **[Error Feed](https://docs.futureagi.com/docs/error-feed?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_error_feed)**\*: groups failing traces into issues, explains the likely cause, and suggests a fix.
- **[Insights](https://docs.futureagi.com/docs/observe/features/insights?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_insights)**\*: reports that sort traces, sessions or users into categories, measure how often each happens, and explain the findings with linked examples.

<img alt="Animated: opening a trace of a LangGraph ride-booking agent, zooming into its step tree, then into the model call where the tool reports the driver is en route and the agent tells the rider the truth" src=".github/assets/readme/trace.gif" width="100%">

<a id="evaluate-and-label"></a>

### Evaluate and label

Know whether each answer was right, and why. An eval can be as cheap as a regex or as thorough as an agent that checks every claim.

| Eval type | How it decides | Reach for it when |
|---|---|---|
| **Agent as a judge** | An evaluator agent works through the run in several steps, looks things up, and then gives a verdict and its reason. | The check needs investigation: a long multi-step agent run, or claims that must be looked up. |
| **LLM as a judge** | A model reads the output against your criteria in one pass and returns a verdict with its reason. | Quality you can describe in words: faithfulness, tone, safety, instruction following, your own rules. |
| **Code** | Your Python or JavaScript runs in a sandbox. Same input, same result, no model call. | The answer is computable: valid JSON, a regex, BLEU or ROUGE, similarity, your business logic. |

**Agent as a judge** is built for agents, so it judges the way a careful reviewer would:

- **No variable mapping.** Write `{{trace}}`, `{{span}}`, `{{session}}`, `{{row}}` or `{{call}}` in your rule, in plain words, and the judge receives that whole object. No columns to wire up.
- **It reads the whole run.** The judge walks the trace tree span by span to find the step that went wrong, instead of seeing one input and one output.
- **It checks facts while it judges.** Turn on [web search, your knowledge bases, or your own tools over MCP](https://docs.futureagi.com/docs/evaluation/guides/advanced-usage?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_agent_tools), so a claim is checked against your docs, your database or the web.
- **[Choose how much context it gets](https://docs.futureagi.com/docs/evaluation/guides/advanced-usage?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_data_injection)**: the mapped fields, the dataset row, the span, the whole trace, the whole session, or a call's transcript and recording.
- **Long runs fit.** Very long traces and conversations are compacted to fit the judge's context, and images and audio are kept intact.
- **Pick the depth.** *Quick* answers in one pass, *Agent* investigates in several steps, and *Auto* balances the two. Choose a short, long or custom write-up of the reason.
- Runs on your OpenAI, Anthropic, Bedrock or Vertex AI key, or on Future AGI's managed models.

- **[130+ built-in evals](https://docs.futureagi.com/docs/evaluation/builtin?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_builtin_evals)**, including hallucination, groundedness, tool use, task completion, PII, toxicity, tone and JSON validity, or [write your own](https://docs.futureagi.com/docs/evaluation/guides/custom-evals?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_custom_evals) in plain language or code.
- **[Pick the judge](https://docs.futureagi.com/docs/evaluation/concepts/evaluator-models?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_judges)**: Jev, TypeSafe's decision model, for typed verdicts (pass/fail, one category, or a score on your rubric) with probabilities, through your TypeSafe or OpenRouter key; Future AGI's Turing models\* for text, image and audio; or your own model from OpenAI, Bedrock, Vertex AI, Azure or any endpoint.
- **[Composite evals](https://docs.futureagi.com/docs/evaluation/concepts/composite-evals?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_composite)**: combine several evals, with weights, into one score and one threshold, for example faithful *and* on-tone *and* complete.
- **[Error localization](https://docs.futureagi.com/docs/evaluation/concepts/error-localization?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_error_localization)**: when an eval fails, it tells you which input caused it.
- **[Feedback](https://docs.futureagi.com/docs/evaluation/concepts/feedback?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_feedback)**: correct a result and rescore, so evals converge on what your team means by correct.
- **Run them anywhere**: on datasets, on live traces, on every simulated call, [in CI on every pull request](https://docs.futureagi.com/docs/evaluation/guides/cicd?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_cicd), or as guardrails in the gateway.
- **[Datasets](https://docs.futureagi.com/docs/dataset?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_datasets)**: tables of test cases from files, production traces, Hugging Face, the SDK or generated data. Prompts and evals run on the rows and write their results back as new columns.
- **[Experiments](https://docs.futureagi.com/docs/dataset/guides/run-an-experiment?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_experiments)**: run several prompt, agent and model combinations on the same dataset, compare them side by side, and pick the winner.
- **[Annotations](https://docs.futureagi.com/docs/annotations?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_annotations)**: queues where people review outputs, with labels, assignments, agreement between reviewers, and export back to a dataset.

<img alt="Animated: writing an eval rule in plain words, testing it on a transcript where the agent claimed success after a failed tool call, the LLM judge failing it with a written reason, then the same check with Jev returning Fail with probability 0.04" src=".github/assets/readme/evals.gif" width="100%">

<a id="simulate-and-test"></a>

### Simulate and test

Try every change against realistic users before your real users meet it. A transcript can read like a pass while the agent got it wrong, so every test call is checked against what actually changed.

1. **[Connect your agent](https://docs.futureagi.com/docs/environments/guides/build-an-environment?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_connect)**: a code repository, an upload, or an agent running on Vapi, Retell AI, LiveKit or a phone number.
2. **It reads your agent** and writes down what it's for, the tools it calls, the rules it must keep and the jobs it handles. Ask it questions or steer it in plain words.
3. **It builds a test world around your agent**: a test copy of everything its tools touch, seeded with the records each scenario needs, so a tool call in a test really changes something. Never your live data.
4. **[Simulated users play the scenarios](https://docs.futureagi.com/docs/environments/concepts/scenarios-and-trials?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_scenarios)**, in text or voice, each with a persona: name, age, personality, language and accent. Run each scenario several times to catch answers that only fail sometimes.
5. **[Every call is graded](https://docs.futureagi.com/docs/environments/guides/read-run-results?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_results)** goal by goal against the world, plus any evals you add. An agent that says "your booking is cancelled" while the booking is still active fails.
6. **[Debug failures](https://docs.futureagi.com/docs/environments/guides/debug-failures?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_debug)** reads the whole run and explains what went wrong, goal by goal. From there, one click starts [Epoch](#improve).

What the test world can hold:

- **Every kind of tool.** Built-in functions, HTTP APIs and MCP servers all answer from the test world, so the agent behaves exactly as it does in production.
- **The apps your agent works in**, such as a mailbox, a Slack workspace or a calendar, simulated with realistic content.
- **The databases it needs.** If your agent's code expects Postgres, MySQL, ClickHouse, MongoDB, Redis, Qdrant, RabbitMQ, NATS or MinIO, the builder provides it.
- **More than one participant.** Multi-player scenarios put several simulated people or agents in the same conversation.
- **Scale.** Up to 1,000 scenarios per build, several worlds running in parallel, and up to 20 repeats per scenario.

Turn [production sessions, calls, transcripts or dataset rows](https://docs.futureagi.com/docs/environments/guides/import-evidence?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_import) into scenarios, so the cases that went wrong last week become tests that run on every change.

<img alt="Animated: a simulation run with Debug failures naming each goal the agent broke, then Run Epoch, then an Epoch ready for review with held-back pass rate from 0% to 75% and four candidates" src=".github/assets/readme/simulate.gif" width="100%">

<a id="improve"></a>

### Improve

Find a change that fixes the failures, and prove it before it ships.

- **[Epoch](https://docs.futureagi.com/docs/environments/guides/run-epoch?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_epoch)**\*: starts from a failing run and searches for a version of your agent that fixes the failing cases without breaking the passing ones. You decide what it may change (instructions, tools, settings or code) and its limits on cost and reply time. It proves the result on cases it never saw while searching. You read the diff and decide whether to ship it.
- **[Prompt optimization](https://docs.futureagi.com/docs/optimization?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_optimization)**: improve a prompt on a dataset with GEPA, ProTeGi, PromptWizard, Bayesian search, meta-prompting or random search, and compare every trial with a diff.

<a id="gateway-and-guardrails"></a>

### Gateway and guardrails

Call any model through one endpoint, and check every request on the way.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/readme/gateway-dark.png">
  <img alt="Your app points an OpenAI-compatible client at Agent Command Center. On every request the gateway checks the key, budget and rate limit, runs guardrails on the request, checks the cache, routes to a provider, runs guardrails on the response, and logs and traces it. When OpenAI times out, the request falls back to Anthropic; Gemini, Bedrock and your own vLLM are also available." src=".github/assets/readme/gateway-light.png" width="100%">
</picture>

- **[One API for every model](https://docs.futureagi.com/docs/command-center?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_gateway)**: an OpenAI-compatible endpoint, written in Go. Native support for OpenAI, Anthropic, Gemini, Bedrock, Azure OpenAI and Cohere, plus any OpenAI-compatible provider, including models you host yourself.
- **[Routing and fallbacks](https://docs.futureagi.com/docs/command-center/features/routing?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_routing)**: round-robin, weighted, lowest-latency, lowest-cost, adaptive or fastest-response routing, with retries, failover and circuit breakers.
- **[Guardrails](https://docs.futureagi.com/docs/command-center/features/guardrails?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_guardrails)**: built-in scanners check requests and responses for PII, prompt injection, leaked secrets, unsafe tool use and more. Add Future AGI's Protect models\* or plug in 15 vendors such as Lakera, Presidio and Llama Guard. Try rules in a playground first.
- **[Caching](https://docs.futureagi.com/docs/command-center/features/caching?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_caching)**: exact and semantic caching, in memory, Redis or object storage.
- **[Keys, budgets and rate limits](https://docs.futureagi.com/docs/command-center/features/rate-limiting?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_limits)**: separate keys for each app or team, with spend budgets and rate limits.
- **[Logs and analytics](https://docs.futureagi.com/docs/command-center/features/cost-tracking?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_cost)**: every request logged with its cost, tokens, latency and your own properties, plus analytics, monitoring, sessions and webhooks.
- **[Shadow experiments](https://docs.futureagi.com/docs/command-center/features/shadow-experiments?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_shadow)**: copy a share of live traffic to another model and compare cost, latency and quality before you switch.
- **[MCP and A2A](https://docs.futureagi.com/docs/command-center/features/mcp-a2a?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_mcp)**: send your agents' tool calls and agent-to-agent traffic through the same gateway.

<a id="build"></a>

### Build

- **[Prompts](https://docs.futureagi.com/docs/prompt?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_prompts)**: write prompt templates, version them, run them against real data and models, and watch them in production.
- **[Agent Playground](https://docs.futureagi.com/docs/agent-playground?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_agent_playground)**: build a multi-step agent from prompt and agent nodes on a canvas, run it, and read what each step produced.
- **[Knowledge bases](https://docs.futureagi.com/docs/knowledge-base?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_knowledge_base)**: upload your documents so prompts and evals can read from them.
- **[Falcon AI](https://docs.futureagi.com/docs/falcon-ai?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_falcon)**\*: an agent inside the dashboard. Ask in plain language, for example "why did this trace fail?" or "build a dataset from these production traces", and it does the work.
- **[MCP server](https://docs.futureagi.com/docs/falcon-ai/guides/use-the-mcp-server?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_mcp_server)**: use Future AGI from your editor or coding agent, for example to find and fix failing traces without leaving it.
- **Workspaces and access**: separate workspaces, members and roles for each team, with audit logs and SCIM for larger organizations.

<sub>\* Needs Future AGI Cloud, or an Enterprise license key on a self-hosted install. See [Open source and your data](#open-source-and-your-data).</sub>

## Works with your stack

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/readme/integrations-dark.png">
  <img alt="Integrations: agent frameworks (LangChain, LangGraph, LlamaIndex, CrewAI, OpenAI Agents SDK, Claude Agent SDK, Google ADK, PydanticAI, DSPy, Haystack, Mastra, Vercel AI SDK, smolagents, AutoGen, Instructor, LiteLLM), model providers (OpenAI, Anthropic, Gemini, Bedrock, Azure OpenAI, Mistral, Groq, OpenRouter, Ollama, vLLM, Hugging Face, xAI, DeepSeek, Together, Fireworks, Cohere), voice and retrieval (LiveKit, Pipecat, ElevenLabs, Qdrant, Milvus, MongoDB, Redis, pgvector), and standards and tools (OpenTelemetry, MCP, Agent2Agent, n8n, Guardrails AI, Elasticsearch, watsonx)." src=".github/assets/readme/integrations-light.png" width="100%">
</picture>

Future AGI uses open interfaces, so you keep the framework and models you already have: OpenTelemetry for traces, an OpenAI-compatible API for the gateway, and SQL on Postgres and ClickHouse for storage.

| Integration | Languages | How it connects |
|---|---|---|
| [OpenTelemetry](https://docs.futureagi.com/docs/observe?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=int_otel) | Any | Send OTLP traces from your app or collector. |
| [Auto-tracing](https://github.com/future-agi/traceAI/blob/main/docs/python/auto-instrumentation.md) | Python | Start your app with `fi-instrument`. Most libraries with a traceAI package are traced with no code changes; a few need one setup call. |
| [traceAI SDKs](https://github.com/future-agi/traceAI) | Python, TypeScript, Java, Go, C# | Instrumentors in Python and TypeScript, client wrappers in Java, middleware in Go, and spans in C#. |
| LangChain and LangGraph | Python, TypeScript | `traceai-langchain` or `@traceai/langchain` |
| OpenAI and OpenAI Agents SDK | Python, TypeScript | `traceai-openai`, `traceai-openai-agents` |
| Anthropic and Claude Agent SDK | Python, TypeScript | `traceai-anthropic`, `traceai-claude-agent-sdk` |
| LlamaIndex | Python, TypeScript | `traceai-llamaindex` or `@traceai/llamaindex` |
| CrewAI, AutoGen, DSPy, PydanticAI, Google ADK | Python | One traceAI package each |
| Vercel AI SDK, Mastra | TypeScript | `@traceai/vercel`, `@traceai/mastra` |
| [Agent Command Center](https://docs.futureagi.com/docs/command-center?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=int_gateway) | Any OpenAI-compatible client | Point your client's base URL at the gateway. |
| [MCP server](https://docs.futureagi.com/docs/falcon-ai/guides/use-the-mcp-server?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=int_mcp) | Any MCP client | Use Future AGI from your editor or coding agent. |
| Vapi, Retell AI, LiveKit | Voice agents | Test them in Simulate where they run. |
| LiveKit, Pipecat | Python | Trace voice pipelines with `traceai-livekit` and `traceai-pipecat`. |

Also supported: Pinecone, Weaviate, Chroma, LanceDB, Agno, Strands, Semantic Kernel, BeeAI, Cerebras, Vertex AI, SageMaker and more in [traceAI](https://github.com/future-agi/traceAI).

<details>
<summary><b>SDKs</b></summary>

| Package | Install | What it is |
|---|---|---|
| [traceAI](https://github.com/future-agi/traceAI) | `pip install fi-instrumentation-otel` · `npm i @traceai/fi-core` | OpenTelemetry tracing for AI frameworks, with zero-code auto-tracing for Python |
| [futureagi](https://github.com/future-agi/futureagi-sdk) | `pip install futureagi` | Platform SDK: datasets, prompts, knowledge bases, evals |
| [agent-learning-kit](https://github.com/future-agi/agent-learning-kit) | `pip install agent-learning-kit` | Local testing, simulation and optimization for agents |
| [agentcc](https://github.com/future-agi/agent-command-center-sdk) | `pip install agentcc` · `npm i @agentcc/client` | Client SDKs for the gateway |

</details>

[All integrations →](https://docs.futureagi.com/docs/integrations?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=integrations_catalog)

## Self-host

| | **Standalone** (default) | **Distributed** | **Kubernetes** |
|---|---|---|---|
| Install | `./bin/install` | `./bin/install --distributed` | [Helm chart](deploy/helm/futureagi) |
| Runs | one app container, Postgres, ClickHouse | one container per service, PeerDB, Kafka | the Distributed setup on Kubernetes 1.27+ |
| Resources | 2 vCPUs, 4 GB | 4+ vCPUs, 12–16 GB | about 4 CPUs and 8 GiB to try it |

**Choose before you add data:** there is no supported way to move a Standalone install's data to Distributed or Helm later ([Switching](INSTALLATION.md#switching-between-standalone-and-distributed)). If an install is enrolled in automatic updates, complete the [safe handoff to manual operations](deploy/update/README.md#safe-handoff-to-manual-operations) before you stop or uninstall it.

Every option, upgrades, air-gapped installs and troubleshooting: [INSTALLATION.md](INSTALLATION.md), [deploy/README.md](deploy/README.md) and the [self-hosting guide](https://docs.futureagi.com/docs/self-hosting?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=self_host).

<a id="open-source-and-your-data"></a>

## Open source and your data

The core is Apache 2.0. Code in directories named `ee/` is under the [Future AGI Enterprise License](LICENSE-EE). On a self-hosted install, Error Feed, Insights, Instant eval, the Turing and Protect models and Epoch need an Enterprise license key that lists them, and Falcon AI needs an Enterprise plan; the [Enterprise Edition reference](https://docs.futureagi.com/docs/self-hosting/configuration/reference#enterprise-edition) has the details. Jev evals work in every edition with your own TypeSafe or OpenRouter key. See also [NOTICE](NOTICE).

<a id="telemetry"></a>

### Telemetry

Self-hosted installs send deployment telemetry, **on by default**: one registration that includes the **email addresses and email domains of the install's owner, admin and staff accounts**, then usage counts on a schedule. **No traces, prompts, completions, datasets or API keys, ever.** To opt out, install with `./bin/install --no-telemetry` or set `FUTURE_AGI_TELEMETRY_DISABLED=true` in `.env` (`config.telemetry=false` on Helm). An opted-out install still sends one registration without email addresses; block `api.futureagi.com` to send nothing. [Telemetry and outbound connections](https://docs.futureagi.com/docs/self-hosting/configuration/telemetry) lists the exact payloads and every outbound connection.

## Support

Finding an answer:

- The [documentation](https://docs.futureagi.com?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=support_docs) covers every feature, with guides and cookbooks. Suggest edits to the docs on GitHub.
- The [FAQ](https://docs.futureagi.com/docs/faq?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=support_faq) answers the most common questions.
- The [MCP server](https://docs.futureagi.com/docs/falcon-ai/guides/use-the-mcp-server?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=support_mcp) lets your editor or coding agent look things up in your workspace.

Support channels:

- **Ask a question** in [GitHub Discussions](https://github.com/orgs/future-agi/discussions) or on [Discord](https://discord.com/invite/cBnWkd7T9v). Include code, screenshots and versions so we can help faster.
- **Report a bug** or **request a feature** with the [issue templates](https://github.com/future-agi/future-agi/issues/new/choose).
- **Cloud account or billing:** email [support@futureagi.com](mailto:support@futureagi.com).
- **Security:** report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
- **Service status:** [status.futureagi.com](https://status.futureagi.com).

## Contributing

Contributions are welcome:

- Read [CONTRIBUTING.md](CONTRIBUTING.md) to set up a development environment, then pick a [`good first issue`](https://github.com/future-agi/future-agi/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22).
- Comment on [issues](https://github.com/future-agi/future-agi/issues), and share ideas in [Discussions](https://github.com/orgs/future-agi/discussions).
- See what's next on the [public roadmap](https://futureagi.com/roadmap?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=roadmap), in [releases](https://github.com/future-agi/future-agi/releases) and in the [changelog](CHANGELOG.md).

Follow along on [X](https://x.com/FutureAGI_), [LinkedIn](https://www.linkedin.com/company/futureagi/), [YouTube](https://www.youtube.com/@Future_AGI) and the [blog](https://futureagi.com/blog?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=community).

## Star history

<a href="https://star-history.com/#future-agi/future-agi&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=future-agi/future-agi&type=Date&theme=dark">
    <img alt="Star history of future-agi/future-agi" src="https://api.star-history.com/svg?repos=future-agi/future-agi&type=Date" width="100%">
  </picture>
</a>

<div align="center">

If Future AGI helps your agent get better, a ⭐ helps other developers find it.

</div>
