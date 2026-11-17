# Your first agent in one file

## What it does

The smallest useful Omega agent. A `get_weather` tool has a name, a description the model reads, an input schema and the code that runs. One `runAgent` call gives the model the tool; the model asks for it, Omega runs it and the model answers from the result.

## Run it with no key

```bash
omega hub run good-agent
```

A scripted model plays the LLM: it asks for the tool, then answers from the tool's result. No key and no network.

It prints:

```text
answer: In Lisbon today: sunny, 24 degrees.
tool get_weather ok=true -> {"city":"Lisbon","forecast":"sunny, 24 degrees"}
```

## Run it with your key

```bash
export OPENAI_API_KEY=...
omega hub run good-agent --live openai:gpt-4o-mini
```

The same agent runs on your model, capped at two calls.

## How it works

- `getWeather` is the tool; `{ city: "string" }` is shorthand for a JSON Schema object with one required string property.
- `createModel` returns the scripted model, or the live one when `omega hub run --live` names it.
- `runFirstAgent` is the single `runAgent` call.
- Teaching version with more detail: cookbook recipe 01 in the Omega repository.

## Make it yours

- Add a second tool and ask a question that needs both.
- Change the instructions and see how the answer changes with a live model.

After a change, run the test and record the demo again:

```bash
omega hub check good-agent --record
```
