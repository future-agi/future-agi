<!--
Thanks for contributing to Future AGI!

For a good review, please make sure:
  • The PR title follows Conventional Commits (feat:, fix:, chore:, docs:, refactor:, test:, perf:)
  • You've linked the relevant issue(s) below
  • The PR description answers **what** and **why** (the diff shows **how**)
  • `bin/test` (backend) or `yarn test:run` (frontend) passes locally
  • `yarn contracts:check` passes if API surface changed
-->

## Summary

<!-- What does this PR change and why? 2–5 sentences. Be specific about the user-facing impact. -->

## Linked issues

<!-- "Closes #123" links and auto-closes on merge. Also link the Linear issue. -->

Closes #
Linear:

## Type of change

- [ ] 🐛 Bug fix (non-breaking change which fixes an issue)
- [ ] ✨ New feature (non-breaking change which adds functionality)
- [ ] 💥 Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] 📖 Documentation only
- [ ] 🧹 Chore / refactor (no user-visible change)
- [ ] 🚀 Performance improvement
- [ ] 🧪 Test-only change

---

## 1) What changes were done

<!-- Use sub-sections A, B, C... to describe each logical group of changes. -->
<!-- Be explicit about what the user sees vs what changed internally. -->

**A. [Change group 1 — e.g. "Eval mapping dropdown for simulations"]**

- [What changed for the user]
- [What changed in the API/serializer, if any]
- [What changed in the model/storage, if any]

**B. [Change group 2]**

- ...

**C. [Architecture / layering changes, if any]**

- [What moved where and why]

---

## 2) Why the changes were done

<!-- Use bullet points. Cover product/UX rationale, technical rationale, and any architectural decisions. -->

- **Product/UX:** [why this matters to the user]
- **Technical:** [why this approach over alternatives]
- **Architecture:** [layering, DRY, separation of concerns — if applicable]

---

## 3) Tests written + scenarios each covers

<!-- List every test file, then every test case with a one-liner description of what it covers. -->
<!-- Format: test_file.py — description of what the suite tests -->

**`test_file_1.py`** — [what this suite tests]

- `test_case_name` — [what it covers]
- `test_case_name` — [what it covers]
- `test_case_name` — [what it covers]

**`test_file_2.py`** — [what this suite tests]

- `test_case_name` — [what it covers]

> Run result: **X passed** across these suites. Lint (ruff) + format (black) clean.

---

## 4) How to run / test

### Backend tests

```bash
cd futureagi

# Create venv with correct Python (one-time):
uv venv --python 3.11
source .venv/bin/activate
uv pip install -r requirements-test.txt

# Full suite:
bin/test

# By app:
bin/test app model_hub

# Specific file:
bin/test model_hub/tests/test_file.py -v

# Single test by name:
bin/test model_hub/tests/test_file.py -k "test_case_name"

# Stop on first failure:
bin/test -x -v

# Service control (if needed):
bin/test up    # start isolated test services
bin/test down  # tear down
```

### Frontend tests (if applicable)

```bash
cd frontend
eval "$(fnm env)" && fnm use 22
yarn install  # first time on Node 22 only
yarn test:run
```

### Contract verification (if API surface changed)

```bash
# Regenerate swagger:
DJANGO_SETTINGS_MODULE=tfc.settings.test ./scripts/generate-openapi-schema.sh

# Regenerate frontend contracts:
cd frontend && yarn contracts:generate

# Verify contracts pass:
cd frontend && yarn contracts:check
```

### UI steps (manual)

1. Start the stack and log in at **http://localhost:3031**.
2. [Step-by-step to reach the affected screen]
3. [What to verify]

---

## 5) Screenshots / recordings

<!-- Drag & drop images or GIFs here. Before / after is especially helpful. -->
<!-- Include: test command output screenshot + UI testing screenshots/recordings -->

### Test output

<!-- Screenshot of the test run passing -->

### UI testing

<!-- Screenshot or video of the feature working in the browser -->

---

## 6) Edge cases & considerations

<!-- List edge cases you've considered, even if they're handled. -->
<!-- This helps reviewers verify you've thought through the boundary conditions. -->

- [Edge case 1]: [how it's handled]
- [Edge case 2]: [how it's handled]

---

## 7) Pre-existing issues (NOT introduced by this PR)

<!-- If any tests fail that are pre-existing failures, document them here. -->
<!-- If none, delete this section. -->

---

## 8) Architectural / important decisions

<!-- For future reference: why you chose approach A over B. -->
<!-- Keep it brief — 1-2 sentences per decision. -->

- **[Decision 1]:** [why this approach was chosen over alternatives]
- **[Decision 2]:** [rationale]

---

## Checklist

- [ ] My code follows the [style guide](../CONTRIBUTING.md#code-style)
- [ ] I've added tests that prove my fix is effective or that my feature works
- [ ] `bin/test` passes locally (backend)
- [ ] `yarn test:run` passes locally (frontend)
- [ ] `yarn contracts:check` passes if API surface changed
- [ ] I've updated the documentation where relevant
- [ ] No hardcoded secrets, URLs, or PII
- [ ] I've signed the [CLA](../CONTRIBUTING.md#contributor-license-agreement-cla)
- [ ] PR title follows Conventional Commits
- [ ] Linear issue is linked and updated with status
