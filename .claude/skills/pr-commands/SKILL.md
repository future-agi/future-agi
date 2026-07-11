# PR Commands (future-agi)

Run the PR pre-flight checklist: tests, contract regen, verification, and pre-push hygiene for the `future-agi` monorepo. Models "what every PR on `dev` has to clear before review."

All paths assume the monorepo root: `future-agi/` (Django backend at `futureagi/`, frontend at `frontend/`).

---

## When to use

Use this skill when the user says:
- "Run the PR commands" / "PR checklist" / "pre-flight checks"
- "Regenerate contracts" / "regenerate swagger" / "regenerate OpenAPI"
- "Run tests" / "test this PR"
- "Check if PR passes CI" / "verify contracts"
- "Pre-push hygiene" / "what do I need to run before pushing"
- "Generate OpenAPI schema"
- "yarn contracts:check" / "yarn contracts:generate"
- Any mention of fixing CI failures related to API contracts, swagger, or generated artefacts

Do not use for: code review, general development work, debugging business logic.

---

## Process

### 1. Backend tests

From `futureagi/`:

```bash
# Activate venv first (required for bin/test to work)
source .venv/bin/activate

# Full suite (unit + integration)
bin/test

# By marker
bin/test unit
bin/test integration

# By app
bin/test app model_hub

# One file
bin/test model_hub/tests/test_user_evaluation_tasks.py -v

# One class / one test
bin/test model_hub/tests/test_user_evaluation_tasks.py::TestClass -v
bin/test model_hub/tests/test_user_evaluation_tasks.py::TestClass::test_name

# Filter by keyword
bin/test model_hub/tests/test_user_evaluation_tasks.py -k "pattern"

# Faster rerun once docker services are already up
bin/test --no-services <files>

# Stop on first failure
bin/test -x -v

# Service control
bin/test up        # start isolated test services (PG 15432, Redis 16379, CH 18123, MinIO 19005)
bin/test down      # tear down
bin/test reset     # reset test database
bin/test status    # show service status
```

If `bin/test` aborts with no test output, run migrate by hand to see the real error:

```bash
cd futureagi
source .venv/bin/activate
DJANGO_SETTINGS_MODULE=tfc.settings.test \
DATABASE_URL=postgres://test_user:test_password@localhost:15432/test_tfc \
python manage.py migrate --run-syncdb
```

### 2. Frontend tests

From `frontend/` (needs **Node 22.18+**):

```bash
eval "$(fnm env)" && fnm use 22
yarn install            # first time on Node 22 only
yarn test:run           # *.mjs tests
```

### 3. Backend OpenAPI swagger regeneration

From the monorepo root:

```bash
DJANGO_SETTINGS_MODULE=tfc.settings.test ./scripts/generate-openapi-schema.sh
```

Writes `api_contracts/openapi/swagger.json`. Uses `--mock-request` so no live API hits — Django just needs to boot.

### 4. Frontend contracts regeneration

From `frontend/`:

```bash
eval "$(fnm env)" && fnm use 22
yarn install            # first time on Node 22 only
yarn contracts:generate
```

Writes (under monorepo root):
- `api_contracts/openapi/management-api-contract-debt.generated.json`
- `api_contracts/openapi/runtime-management-api-contract-debt.generated.json`
- `frontend/src/api/contracts/api-surface.generated.js`
- `frontend/src/api/contracts/openapi-contract.generated.js`
- `frontend/src/generated/api-contracts/api.schemas.ts`
- `frontend/src/generated/api-contracts/api.ts`
- `frontend/src/generated/api-contracts/api.zod.ts`

### 5. Contract verification (the CI gate)

From `frontend/`:

```bash
yarn contracts:check
```

Pass means: generated artefacts match `swagger.json`, coverage minimums hit, no new mutation endpoints without body schemas, runtime-debt baseline matches.

### 6. Local-vs-CI swagger divergence (the documented workaround)

If `yarn contracts:check` fails locally with messages like

```
Mutation endpoints without request body schemas increased from 0 to 12.
  - POST /tracer/eval-task/...
```

for endpoints the PR did **not** touch, the local `drf-yasg` introspection is missing the EE viewset surface (CI clones `ee/` via a secret; local may not have it on the matching branch):

**A — clone EE alongside `futureagi/` at the same branch:**

```bash
cd futureagi
git clone git@github.com:future-agi/ee.git ee
cd ee && git checkout <pr-branch> && cd ../..
DJANGO_SETTINGS_MODULE=tfc.settings.test ./scripts/generate-openapi-schema.sh
```

**B — pin generated artefacts to `dev`'s exact bytes** (use only when the PR adds NO new endpoints / serializer fields):

```bash
git checkout origin/dev -- \
  api_contracts/openapi/swagger.json \
  api_contracts/openapi/management-api-contract-debt.generated.json \
  api_contracts/openapi/runtime-management-api-contract-debt.generated.json \
  frontend/src/api/contracts/api-surface.generated.js \
  frontend/src/api/contracts/openapi-contract.generated.js \
  frontend/src/generated/api-contracts/api.schemas.ts \
  frontend/src/generated/api-contracts/api.ts \
  frontend/src/generated/api-contracts/api.zod.ts
```

Check whether the `API Contracts` workflow is broken on `dev` itself before chasing failures:

```bash
gh run list --repo future-agi/future-agi --branch dev --workflow "API Contracts" --limit 5 --json conclusion,headSha,createdAt
```

### 7. Pre-push hygiene (the bug-class catalogue)

Every contracts:check failure on a PR ends up being one of these. Run through this list before pushing and the CI gate stays green.

#### 7.1 Any serializer edit triggers the full regen pipeline
If the diff touches **any** serializer file at all, run section 3 + section 4 immediately, then commit the regen artefacts in the same push as the source edit. Don't let the source change ship alone.

#### 7.2 Commit ALL the regen artefacts, not the obvious three
Safe stage idiom after regen:

```bash
git add api_contracts/openapi/ \
        frontend/src/api/contracts/ \
        frontend/src/generated/api-contracts/
```

#### 7.3 Always end the pipeline with a verification check
Run section 5 last, locally, before pushing. If local passes but CI fails → section 6 divergence path.

#### 7.4 Stacked PRs: the FE PR needs the BE PR's swagger merged in
When BE PR advances, merge BE PR into FE PR → rerun section 4 on the FE PR → push.

#### 7.5 Source files outside `contracts.py` can still cascade
The schema reads from viewsets (`@swagger_auto_schema`), `validated_request` serializers, response serializers, even pydantic models bridged into DRF. If the PR edited any of those, regen first.

#### 7.6 Never hand-edit a `.generated.` artefact
Revert the manual edit, fix the source, regen, commit the artefact alongside the source change.

#### 7.7 FE unit tests pin request-body shape
Any time you change a request-body shape, grep `frontend/src/**/__tests__/` for the URL, hook name, or field name, and update the assertion in the same commit. Run `yarn test:run path/to/test.js` locally before pushing.

#### 7.8 FE source-to-registry renames need a Vite smoke boot
Renaming an axios endpoint entry, a hook export, or a generated-URL helper can break at runtime without breaking `contracts:check` or `test:run`. Boot Vite and click the affected surface before pushing.

---

## Completion criterion

All PR pre-flight checks pass: backend tests, frontend tests, regenerated OpenAPI schema, regenerated frontend contracts, and contracts verification. Generated artefacts are committed alongside source changes.
