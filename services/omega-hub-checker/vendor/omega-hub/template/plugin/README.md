# {{title}}

## What it does

{{tagline}}

## Run it with no key

```bash
omega hub run {{id}}
```

It starts the server, lists its tools and calls one. No model runs.

## How it works

`plugin/omega.plugin.json` declares the MCP server in `plugin/server.mjs`. `omega plugins add` installs it switched off; `main.ts` talks to the server the way an MCP client does.

## Make it yours

Add tools to `TOOLS` in `plugin/server.mjs` and list them in the manifest and answer them in `tools/call`, then record the run again:

```bash
omega hub check {{id}} --record
```
