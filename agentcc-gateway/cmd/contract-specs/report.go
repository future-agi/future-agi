package main

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// writeReports writes, under dir, <provider>.md for every drifted provider
// (the body of its tracking issue) and summary.md (the job summary).
func writeReports(dir string, results []providerResult) error {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	var summary bytes.Buffer
	summary.WriteString("## Provider spec drift\n\n| provider | status |\n|---|---|\n")
	for _, r := range results {
		status := r.status
		if r.err != nil {
			status += ": " + strings.ReplaceAll(r.err.Error(), "|", `\|`)
		}
		fmt.Fprintf(&summary, "| %s | %s |\n", r.provider, status)
	}
	for _, r := range results {
		if r.status != "drift" {
			continue
		}
		body := issueBody(r)
		if err := os.WriteFile(filepath.Join(dir, r.provider+".md"), []byte(body), 0o644); err != nil {
			return err
		}
		fmt.Fprintf(&summary, "\n### %s\n\n%s", r.provider, diffBlock(r))
	}
	return os.WriteFile(filepath.Join(dir, "summary.md"), summary.Bytes(), 0o644)
}

func diffBlock(r providerResult) string {
	var b bytes.Buffer
	b.WriteString("```text\n")
	if r.signal != "" {
		b.WriteString(r.signal + "\n")
	}
	for _, d := range r.diffs {
		if d.Failing() > 0 || len(d.Changes) > 0 {
			d.Format(&b)
		}
	}
	b.WriteString("```\n")
	return b.String()
}

// issueBody is the tracking issue's text for one drifted provider.
func issueBody(r providerResult) string {
	var b bytes.Buffer
	fmt.Fprintf(&b, "The scheduled spec-drift check found that **%s**'s official API spec changed in a part the gateway parses.\n\n", r.provider)
	if r.from != "" || r.to != "" {
		fmt.Fprintf(&b, "- committed: %s\n- latest: %s\n\n", orNone(r.from), orNone(r.to))
	}
	b.WriteString("### Diff (FAIL lines are what the gateway's contract does not know yet)\n\n")
	b.WriteString(diffBlock(r))
	if r.signal != "" && len(r.diffs) == 0 {
		fmt.Fprintf(&b, `
### Triage

This provider publishes no fetchable spec, only a value that changes when its spec does. Read the provider's changelog and API reference for what changed, then:

1. If a response the gateway parses changed, add or update a contract fixture (internal/providers/contracttest/README.md, "Adding a model version") and fix or allowlist what it reports.
2. Record the new value, from agentcc-gateway/:

   `+"```sh\n   go run ./cmd/contract-specs -fetch -update -provider %s\n   ```\n", r.provider)
	} else {
		fmt.Fprintf(&b, `
### Triage

1. Regenerate the subset from agentcc-gateway/ and review the diff:

   `+"```sh\n   go run ./cmd/contract-specs -fetch -update -provider %[1]s\n   git diff internal/providers/contractspec/subsets/%[1]s\n   ```\n"+`
2. Run the adapter's spec coverage test, which now reports every new path, enum value and event:

   `+"```sh\n   go test ./internal/providers/... -run SpecCoverage\n   ```\n"+`
3. For each finding, either **map it in the parser** (add the field to the adapter's struct and translate it; handle the new enum value in its lookup/map function; handle the new event), or **allowlist it with a reason** in the adapter's testdata/spec_allowlist.json: "known gap <ID>: ..." (reuse an existing gap ID from the contract tests' meta.json when it is the same loss, else take the provider's next ID) or "no canonical field: ...".
4. Commit the regenerated subset, its source.json and the parser or allowlist change together. This issue is updated by each run while the drift persists; close it once the subset is re-pinned.

See agentcc-gateway/internal/providers/contractspec/README.md.
`, r.provider)
	}
	return b.String()
}

func orNone(s string) string {
	if s == "" {
		return "(none)"
	}
	return s
}
