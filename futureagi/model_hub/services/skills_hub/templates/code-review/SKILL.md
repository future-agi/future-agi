---
name: {{name}}
description: {{description}}
---
# Review a code change

Use the [review checklist](references/checklist.md). Review the requested diff
and the code paths it changes; do not mix unrelated refactors into the review.

1. Identify the intended behavior, entry points, trust boundaries, and tests.
   Read repository-specific contribution and execution rules before running code.
2. Trace each candidate issue from a concrete input or state to an incorrect
   output, unauthorized operation, data loss, or crash. Read callers and guards
   before declaring a defect.
3. Distinguish a newly introduced defect from an older problem the diff exposes.
   A changed line alone is not proof that the change caused the bug.
4. Reproduce the smallest case in the approved test environment when possible.
   Do not use live credentials, production data, or destructive setup commands.
5. Report only actionable findings, highest impact first. For each, include a
   file and line, the failing scenario, the consequence, and a focused remedy.

If no verified finding remains, say so and identify the untested areas. Keep
style preferences separate from correctness findings. Never claim execution
from static inspection alone. Apply fixes only when the user requests them.
