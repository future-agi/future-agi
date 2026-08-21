# The harness, as a chat

A web page you talk to. Same harness, same stages, same artifacts as the CLI. This is a second
renderer over the event stream the stages already emit, not a second implementation.

There is no separate front end to build or start. The page is one static file this server hands
out on `/`, and it talks to the same server's JSON endpoints. No node, no npm, no build step.

## Setting it up

```bash
git clone https://github.com/future-agi/agent-learning-kit
cd agent-learning-kit

uv sync --extra livekit --group dev
```

`--extra livekit` is required: the voice run path imports it. The UI's own `fastapi` and
`uvicorn` come in with `--group dev`, and are also available as `--extra ui` if you would
rather not pull the dev tooling.

You also need the `claude` command on your PATH (`npm install -g @anthropic-ai/claude-code`).
The harness talks to the model through the Claude Agent SDK, which runs that binary underneath;
without it every stage fails immediately.

Credentials go through **Vertex AI**, not a plain Anthropic key: `config.provider_env` sets
`CLAUDE_CODE_USE_VERTEX=1`, so `ANTHROPIC_API_KEY` on its own will not work. Copy the template
and fill in your service account:

```bash
cp oss/simulation-acceptance/.env.example .env.acceptance
# GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/your-service-account.json
# GOOGLE_CLOUD_PROJECT=your-gcp-project-id
```

`.env.acceptance` is git-ignored and points at a private key. Never commit it or paste its
contents anywhere.

Then load it and pick the model, in each new terminal:

```bash
set -a; . ./.env.acceptance; set +a
export CLOUD_ML_REGION=global
export ALK_HARNESS_MODEL=claude-sonnet-4-6
```

It prints the model and which credentials it found before starting, so a run never begins on
something you did not intend. **Use Sonnet or better.** Haiku has misread an agent's modality,
and modality decides how every later test is run.

## Running it

```bash
.venv/bin/python ui/server.py
```

Open **http://localhost:8777**, press **+ new**, and say what you want tested:

```
i want to test my voice ordering agent. the code is at /absolute/path/to/the/agent
```

Or paste a public GitHub repository URL. The harness shallow-clones it into that session's
`source/` directory before it starts reading it; no local clone is required:

```
i want to test https://github.com/example/voice-agent
```

One message is enough to begin. Reception takes the path or URL out of the sentence and hands
straight over to reading the agent. From there it is a conversation:

```
now build the environment for it
write me 5 scenarios: a plain order, one for something you do not have, one where the
customer changes their mind, one that pushes against a rule, and one with quantity
```

Questions in between are answered without spending a stage, so "can it handle quantity?" is a
fair thing to ask mid-flight.

To stop the server, Ctrl-C. Checking whether it is still up with `lsof -ti:8777` will mislead
you: that matches a browser's leftover sockets. Use `lsof -nP -iTCP:8777 -sTCP:LISTEN`.

**Restart the server after changing anything under `src/harness/`.** A long-lived process
does not pick up code or skills on its own.

## What you can do in it

- **Start, reopen or delete a conversation** from the picker. Everything about one conversation
  lives in its own folder under `artifacts/sessions/<id>/`, so reopening it restores the chat and
  every artifact. A blank slate is `rm -rf artifacts/sessions/* artifacts/.open-session`.
- **Talk.** "build the world", "write 5 hard scenarios", "make that one harder", "add a mango
  smoothie to the menu". Each reply shows the work underneath it: which tool ran, what it
  answered, what it refused.
- **Move between stages** by clicking the roadmap. Stages are not a wizard: going back to correct
  a contract after the world is built is the ordinary case. A stage whose input does not exist
  yet says why it cannot be opened.
- **Read the four tabs.** Contract, Environment, Scenarios and Runs are what is on disk. Each
  scenario shows its instruction, what it changes, its reference solution, its checks, and three
  gate lights; its files open inline.
- **Run the scenarios.** The conversation between the simulated customer and the agent streams
  into the chat as it happens, then a verdict lands with the checks.

## What to expect while it works

The build stage is the long one: roughly 30 turns and about ten minutes for a five-tool agent.
**The Environment tab stays empty until it finishes**, because the world is held in memory until
`save_world` writes it. The chat is where the progress is: schema, seeds, then one line per
handler as each is defined and smoke-called.

## The two files

| File | What it is |
|---|---|
| `server.py` | FastAPI. Holds one `Conversation` open, streams its events as server-sent events, and serves the artifacts as JSON. |
| `static/index.html` | The whole interface: markup, styling and rendering in one file. |

To restyle it, edit the `<style>` block and refresh. To change what a pane shows, edit `loadTab`.

## The endpoints

Anything that can read server-sent events can be a front end for this. If the platform team
builds its own, it talks to these and nothing on the harness side changes.

| Endpoint | Does |
|---|---|
| `POST /api/say` | Send a message; streams `text`, `tool`, `result`, `artifact`, `done` events |
| `POST /api/run` | Run scenarios; streams `exchange` and `result_card` events |
| `POST /api/stop` | Stop the stage that is running, mid-turn |
| `GET /api/status` | Stage, agent, model, spend, which artifacts exist, which stages can be opened |
| `GET`/`POST /api/sessions` · `POST /api/sessions/open` · `DELETE /api/sessions/{id}` | List, start, reopen and delete conversations |
| `GET /api/history` | The stored conversation, for restoring the page |
| `POST /api/stage` | Open a named stage, which is what the roadmap does |
| `GET /api/contract` · `/world` · `/subgoals` · `/scenarios` · `/runs` | The tabs |
| `GET /api/scenario-file` | One file out of a scenario's folder |

Every stream ends with a `status` event, which is what the header and tabs refresh from.

`GET /api/scenarios` re-runs all three gates on every request rather than reporting what was true
when they were written. They are milliseconds of pure code, and a scenario shown as validated
when the world has since changed underneath it is worse than one shown as unknown.

## Known limits

- **One conversation per server.** This is one operator talking to one harness. The open session
  is server-wide, so a second tab left on another conversation can move it between turns; it
  cannot happen mid-turn, because switching and deleting are both refused while a stage runs.
- **A second request while one is working gets a 409** rather than interleaving.
- **The run stage is not the focus yet.** Environment and scenario generation are.
