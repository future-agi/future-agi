# {{title}}

## What it does

{{tagline}}

## Run it with no key

```bash
omega hub run {{id}}
```

It lists the skills and checks their frontmatter. No model runs.

## How it works

Each folder under `skills/` is one skill: a `SKILL.md` with a `name` and a `description` an agent reads to decide when to load it.

## Make it yours

Rename `skills/example-skill`, set `name` to the new folder name, write the steps, then record the run again:

```bash
omega hub check {{id}} --record
```
