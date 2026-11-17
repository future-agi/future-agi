# {{title}}

## What it does

{{tagline}}

## Run it with no key

```bash
omega hub run {{id}}
```

A scripted model plays the LLM, so the run needs no key and no network.

## Run it with your key

```bash
export OPENAI_API_KEY=...
omega hub run {{id}} --live openai:gpt-4o-mini
```

The run makes at most two model calls, billed to your account.

## How it works

`main.ts` defines one tool and one agent. `createModel()` returns the scripted model when no live model is named, and the live one when `omega hub run --live` names it.

## Make it yours

Change the tool, the instructions and the scripted turns in `main.ts`, then record the run again:

```bash
omega hub check {{id}} --record
```
