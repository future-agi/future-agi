# {{title}}

## What it does

{{tagline}}

## Run it with no key

```bash
omega hub run {{id}}
```

It starts the server, lists its tools and calls one. No model runs.

## How it works

`server.mjs` is an MCP server over stdio. `main.ts` talks to it the way an MCP client does.

## Make it yours

Add tools to `TOOLS` in `server.mjs` and answer them in `tools/call`, then record the run again:

```bash
omega hub check {{id}} --record
```
