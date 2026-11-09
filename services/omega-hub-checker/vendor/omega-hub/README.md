# @omega/hub

The Omega Hub entry format, `omega-hub/v1`, in one plain-ESM package with no dependencies, so the `omega hub` CLI, CI, the Discussions Action and the docs site sync share one implementation.

- `schema/omega.hub.schema.json`: the JSON Schema of `hub/<id>/omega.hub.json`, for editors.
- `validateEntry` and `validateAll`: the static rules R1-R13. The CLI's `omega hub check` adds R14 (the keyless test) and R15 (the recording is fresh).
- `buildCatalog(repoRoot, { commit })`: the catalog, `omega-hub-catalog/v1`, a pure function of the files and the commit.
- `parseCast`, `writeCast`, `scrubText`, `redactText`, `compareOutputs`: asciicast v2 recordings.
- `checkPullRequest`: the pull request rules P1-P5.
- `scaffoldFiles` and `remixFiles`: what `omega hub new` writes, from `template/`.

```bash
pnpm --filter @omega/hub test
pnpm --filter @omega/hub typecheck
```

The validator is the judge; `test/schema.test.mjs` keeps the JSON Schema's fields, enums and patterns equal to the validator's. `test/fixtures/rules.json` holds one failing case per rule, including the cases ported from the retired gallery generator.
