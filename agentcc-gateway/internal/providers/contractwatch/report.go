package contractwatch

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// WriteText prints the report for a terminal: one line per provider, then
// NEW, RETIRED, deprecations and stale coverage.
func (r *Report) WriteText(w io.Writer) {
	p := func(format string, args ...any) { _, _ = fmt.Fprintf(w, format+"\n", args...) }
	for _, s := range r.Sections {
		switch s.State {
		case StateListed:
			extra := ""
			if s.Detail != "" {
				extra = "; " + s.Detail
			}
			p("%s: listed %d models: %d new, %d covered, %d alias, %d ignored, %d by rule, %d by metadata%s",
				s.Name, s.Listed, len(s.New), s.Covered, s.Aliases+s.GeoAliases, s.Ignored, s.RuleIgnored, s.AutoIgnored, extra)
		case StateSkipped:
			p("%s: skipped: %s", s.Name, s.Detail)
		case StateNone:
			p("%s: not listed: %s", s.Name, s.Detail)
		case StateError:
			p("%s: FAILED to list: %s", s.Name, s.Detail)
		}
		for _, id := range s.Invalid {
			p("%s: FAILED: upstream listed a model ID model-watch refuses: %s", s.Name, id)
		}
	}
	for _, e := range r.Plan {
		tmpl := e.TemplateModel
		if tmpl == "" {
			tmpl = "(none)"
		}
		p("NEW %s %s: template %s: %s", e.Provider, e.NewModel, tmpl, e.Reason)
		if e.Metadata != nil && len(e.Metadata.Variants) > 0 {
			p("    also listed in other geographies: %s", strings.Join(e.Metadata.Variants, ", "))
		}
		for _, w := range e.Warnings {
			p("    warning: %s", w)
		}
	}
	for _, s := range r.Sections {
		for _, f := range s.Retired {
			p("RETIRED %s %s (%s): %s", f.Section, f.Model, f.Status, f.Detail)
		}
	}
	for _, s := range r.Sections {
		for _, f := range s.Deprecating {
			p("DEPRECATING %s %s (%s): %s", f.Section, f.Model, f.Status, f.Detail)
		}
		for _, f := range s.Gone {
			p("note: %s %s: ignored entry %s", f.Section, f.Model, f.Detail)
		}
		for _, m := range s.UnusedRules {
			p("note: %s: ignore rule %q matched nothing listed", s.Name, m)
		}
	}
	for _, st := range r.Stale {
		p("STALE %s", st)
	}
	for _, ph := range r.Placeholders {
		p("TRIAGE %s still has the placeholder reason from -update-registry", ph)
	}
	for _, n := range r.Notes {
		p("note: %s", n)
	}
}

// Markdown renders the discovery half of the job summary: what each
// provider listed, the NEW models with their templates, RETIRED and
// deprecated covered models, and stale coverage. The workflow appends the
// recording results (RenderResults).
func (r *Report) Markdown() string {
	var b strings.Builder
	w := func(format string, args ...any) { fmt.Fprintf(&b, format+"\n", args...) }
	w("## Upstream model listings\n")
	w("| provider | listing | listed | new | covered | alias | ignored (entry / rule / metadata) | retired |")
	w("|---|---|---:|---:|---:|---:|---|---:|")
	for _, s := range r.Sections {
		switch s.State {
		case StateListed:
			w("| %s | %s | %d | %d | %d | %d | %d / %d / %d | %d |", s.Name, cell(firstNonEmpty(s.Detail, "listed")), s.Listed, len(s.New),
				s.Covered, s.Aliases+s.GeoAliases, s.Ignored, s.RuleIgnored, s.AutoIgnored, len(s.Retired))
		default:
			w("| %s | %s: %s | | | | | | |", s.Name, s.State, cell(s.Detail))
		}
	}
	w("")
	if len(r.Plan) == 0 {
		w("No new models.\n")
	} else {
		w("## New models (%d)\n", len(r.Plan))
		w("| provider | new model | template | why this template | recorded this run | modeldb pricing |")
		w("|---|---|---|---|---|---|")
		for _, e := range r.Plan {
			reason := e.Reason
			if len(e.Warnings) > 0 {
				reason += " **Warning:** " + strings.Join(e.Warnings, "; ")
			}
			w("| %s | `%s` | %s | %s | %s | %s |", e.Provider, e.NewModel, codeOr(e.TemplateModel, "none"), cell(reason),
				yesNo(e.Record, "yes", "no (over the cap, or no template)"), yesNo(e.InModelDB, "yes", "**missing**"))
		}
		w("")
	}
	var retired, deprecating []Finding
	for _, s := range r.Sections {
		retired = append(retired, s.Retired...)
		deprecating = append(deprecating, s.Deprecating...)
	}
	if len(retired) > 0 {
		w("## Retired: covered models upstream no longer lists (%d)\n", len(retired))
		w("Their fixtures can no longer be re-recorded; the weekly drift job will fail on them.\n")
		for _, f := range retired {
			w("- %s `%s` (%s)", f.Section, f.Model, f.Status)
		}
		w("")
	}
	if len(deprecating) > 0 {
		w("## Deprecated upstream (%d)\n", len(deprecating))
		for _, f := range deprecating {
			w("- %s `%s` (%s): %s", f.Section, f.Model, f.Status, f.Detail)
		}
		w("")
	}
	if len(r.Stale) > 0 {
		w("## Stale coverage (%d)\n", len(r.Stale))
		for _, s := range r.Stale {
			w("- %s", cell(s.String()))
		}
		w("")
	}
	if len(r.Notes) > 0 {
		w("## Notes\n")
		for _, n := range r.Notes {
			w("- %s", n)
		}
		w("")
	}
	return b.String()
}

// Result is one recording the workflow made, as a line of results.jsonl.
type Result struct {
	// Kind is "new" (a NEW model cloned from its template) or "newest" (a
	// covered model re-recorded).
	Kind     string `json:"kind"`
	Provider string `json:"provider"`
	Model    string `json:"model"`
	Template string `json:"template,omitempty"`
	// RecordExit and TestExit are the recorder's and the live contract
	// test's exit codes; TestExit is -1 when the test did not run.
	RecordExit int `json:"record_exit"`
	TestExit   int `json:"test_exit"`
	// Recorded is how many response files the recording wrote.
	Recorded int `json:"recorded"`
	// RecordLog and TestLog are paths relative to the results file.
	RecordLog string `json:"record_log,omitempty"`
	TestLog   string `json:"test_log,omitempty"`
	// Dir is the recording directory, relative to the artifact root.
	Dir string `json:"dir,omitempty"`
}

// RecorderExitNothing is cmd/contract-recorder's exit status when no
// recordable case matched the selection, so nothing was sent: the plan named
// only cases the recorder does not send (not recordable, or an excluded
// endpoint). Not drift and not a recording failure.
const RecorderExitNothing = 3

// Outcome is a short verdict for a result.
func (r Result) Outcome() string {
	switch {
	case r.Recorded == 0 && r.RecordExit == RecorderExitNothing:
		return "nothing to record"
	case r.Recorded == 0:
		return "not recorded"
	case r.TestExit == 0 && r.RecordExit == 0:
		return "passed"
	case r.TestExit == 0:
		return "passed (some cases failed to record)"
	case r.TestExit < 0:
		return "not tested"
	default:
		return "DRIFT"
	}
}

// ReadResults reads results.jsonl.
func ReadResults(path string) ([]Result, error) {
	f, err := os.Open(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	defer func() { _ = f.Close() }()
	var out []Result
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 1<<20)
	for n := 1; sc.Scan(); n++ {
		line := bytes.TrimSpace(sc.Bytes())
		if len(line) == 0 {
			continue
		}
		var r Result
		if err := json.Unmarshal(line, &r); err != nil {
			return nil, fmt.Errorf("%s:%d: %v", path, n, err)
		}
		out = append(out, r)
	}
	return out, sc.Err()
}

// driftLines picks the lines of a go test -v log worth showing: everything
// but the run/pass bookkeeping and live mode's skipped-golden notes.
func driftLines(log string, max int) []string {
	var out []string
	for _, line := range strings.Split(log, "\n") {
		t := strings.TrimSpace(line)
		switch {
		case t == "", t == "PASS", t == "FAIL",
			strings.HasPrefix(t, "=== RUN"), strings.HasPrefix(t, "=== PAUSE"), strings.HasPrefix(t, "=== CONT"),
			strings.HasPrefix(t, "--- PASS"), strings.HasPrefix(t, "--- SKIP"), strings.HasPrefix(t, "ok "),
			strings.Contains(t, "live mode, no golden"):
			continue
		}
		// A fence in the log would end the code block it is shown in.
		out = append(out, strings.ReplaceAll(strings.TrimRight(line, " \t"), "```", "'''"))
		if len(out) == max {
			out = append(out, "... (truncated; see the log in the artifact)")
			break
		}
	}
	return out
}

func readLog(base, rel string) string {
	if rel == "" {
		return ""
	}
	raw, err := os.ReadFile(filepath.Join(base, filepath.FromSlash(rel)))
	if err != nil {
		return ""
	}
	return string(raw)
}

// RenderResults is the recording half of the job summary: one row per
// recording, then the drift each failing live test reported.
func RenderResults(results []Result, base string) string {
	var b strings.Builder
	w := func(format string, args ...any) { fmt.Fprintf(&b, format+"\n", args...) }
	for _, kind := range []string{"new", "newest"} {
		var rs []Result
		for _, r := range results {
			if r.Kind == kind {
				rs = append(rs, r)
			}
		}
		if len(rs) == 0 {
			continue
		}
		if kind == "new" {
			w("## New models recorded from their templates (%d)\n", len(rs))
			w("| provider | new model | template | responses | recorder | live contract test |")
			w("|---|---|---|---:|---|---|")
		} else {
			w("## Newest models re-recorded (%d)\n", len(rs))
			w("| provider | model | | responses | recorder | live contract test |")
			w("|---|---|---|---:|---|---|")
		}
		for _, r := range rs {
			w("| %s | `%s` | %s | %d | %s | %s |", r.Provider, r.Model, codeOr(r.Template, ""), r.Recorded,
				yesNo(r.RecordExit == 0, "ok", fmt.Sprintf("exit %d", r.RecordExit)), r.Outcome())
		}
		w("")
		for _, r := range rs {
			if r.Outcome() == "passed" {
				continue
			}
			var lines []string
			if r.TestExit > 0 {
				lines = driftLines(readLog(base, r.TestLog), 40)
			}
			if r.RecordExit != 0 {
				lines = append(lines, driftLines(lastLines(readLog(base, r.RecordLog), 15), 15)...)
			}
			if len(lines) == 0 {
				continue
			}
			w("<details><summary>%s <code>%s</code>: %s</summary>\n", r.Provider, r.Model, r.Outcome())
			w("```")
			for _, l := range lines {
				w("%s", l)
			}
			w("```\n</details>\n")
		}
	}
	return b.String()
}

func lastLines(s string, n int) string {
	lines := strings.Split(strings.TrimRight(s, "\n"), "\n")
	if len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	return strings.Join(lines, "\n")
}

// Issue is one tracking issue the workflow opens or updates.
type Issue struct {
	Title string `json:"title"`
	Body  string `json:"body"`
}

// IssueTitle is the tracking issue's title for a NEW model. The workflow
// dedupes on it, so it must not change between runs.
func IssueTitle(provider, model string) string {
	return fmt.Sprintf("[model-watch] New %s model not under contract: %s", provider, model)
}

// IssueOptions fill in the links an issue body carries.
type IssueOptions struct {
	RunURL   string // this workflow run
	Artifact string // the artifact holding the recordings
	Date     string // YYYY-MM-DD
}

// RenderIssues builds one issue per NEW model in plan, with the latest
// recording result when there is one.
func RenderIssues(plan []PlanEntry, results []Result, base string, opts IssueOptions) []Issue {
	byModel := map[string]Result{}
	for _, r := range results {
		if r.Kind == "new" {
			byModel[r.Provider+"\x00"+r.Model] = r
		}
	}
	var out []Issue
	for _, e := range plan {
		r, recorded := byModel[e.Provider+"\x00"+e.NewModel]
		out = append(out, Issue{Title: IssueTitle(e.Provider, e.NewModel), Body: issueBody(e, r, recorded, base, opts)})
	}
	return out
}

func issueBody(e PlanEntry, r Result, recorded bool, base string, opts IssueOptions) string {
	var b strings.Builder
	w := func(format string, args ...any) { fmt.Fprintf(&b, format+"\n", args...) }
	w("<!-- model-watch: %s %s -->", e.Provider, e.NewModel)
	w("The %s API lists `%s`, but `agentcc-gateway/%s` has no entry for it, so no contract test pins what the gateway does with its responses. "+
		"Opened and kept up to date by the daily `AgentCC Provider New Models` workflow; this body is rewritten on every run.\n",
		e.Provider, e.NewModel, registryPath)
	w("| | |")
	w("|---|---|")
	w("| Provider | %s |", e.Provider)
	w("| Model | `%s` |", e.NewModel)
	if e.TemplateModel != "" {
		w("| Closest covered model | `%s`: %s |", e.TemplateModel, cell(e.Reason))
	} else {
		w("| Closest covered model | none: %s |", cell(e.Reason))
	}
	for _, warn := range e.Warnings {
		w("| Warning | %s |", cell(warn))
	}
	if e.AliasOf != "" {
		w("| Possibly an alias | it differs from `%s` only by snapshot date or geography |", e.AliasOf)
	}
	if e.Metadata != nil && len(e.Metadata.Variants) > 0 {
		w("| Same model, other geographies | `%s` (they count as aliases once this one has an entry) |", strings.Join(e.Metadata.Variants, "`, `"))
	}
	w("| Pricing | %s |", yesNo(e.InModelDB, "internal/modeldb/litellm.json has an entry", "**no entry in internal/modeldb/litellm.json: requests to it are not costed**"))
	if md := metadataLine(e.Metadata); md != "" {
		w("| Upstream metadata | %s |", cell(md))
	}
	switch {
	case recorded:
		w("| Last run | %s: %d responses recorded from the template's cases; live contract test **%s** ([run](%s), artifact `%s`, `%s`) |",
			opts.Date, r.Recorded, r.Outcome(), opts.RunURL, opts.Artifact, r.Dir)
	case e.Record:
		w("| Last run | %s: planned for recording, but no result came back ([run](%s)) |", opts.Date, opts.RunURL)
	default:
		w("| Last run | %s: not recorded this run (over the daily cap, or no template) ([run](%s)) |", opts.Date, opts.RunURL)
	}
	w("")
	if recorded && r.Outcome() != "passed" {
		lines := driftLines(readLog(base, r.TestLog), 40)
		if r.RecordExit != 0 {
			lines = append(lines, driftLines(lastLines(readLog(base, r.RecordLog), 15), 15)...)
		}
		if len(lines) > 0 {
			w("### What the last run reported\n")
			w("```")
			for _, l := range lines {
				w("%s", l)
			}
			w("```\n")
		}
	}
	w("### Bring it under contract\n")
	w("From `agentcc-gateway/`, with the provider's key in the environment:\n")
	if e.Recorder != nil {
		w("1. Record the template's cases against the new model, in place:")
		w("   ```sh")
		w("   go run ./cmd/contract-recorder -provider %s -from '%s' -as '%s'", e.Recorder.Provider, e.Recorder.From, e.Recorder.As)
		w("   ```")
		w("   Check that the new `meta.json` has `\"cloned_from\": \"%s\"` (add it if the recorder did not): it makes the contract test diff every case's shape against the template's. "+
			"Add cases by copying a case directory and recording it in place for whatever this model does differently.", e.Recorder.From)
		w("2. Write the goldens: `go test ./internal/providers/%s/ -run Contract -update`, then run again without `-update` and review the whole diff (fixtures, goldens, `meta.json`).", e.Recorder.Provider)
	} else {
		w("1. Write fixtures for it by hand (`internal/providers/contracttest/README.md`, \"Adding a model version\").")
		w("2. Write the goldens with `-update`, then run again without it and review the diff.")
	}
	w("3. Triage every failure: translate an unknown field or shape in the adapter (then `-update` again), or allowlist it in the new `meta.json` with a reason (`known gap ...` when content is lost); delete allowlist entries the stale check names; fix the parser for an unknown event type or finish reason.")
	w("4. Add it to `%s` as `{\"id\": \"%s\", \"status\": \"covered\"}` and run `go test ./internal/providers/contractwatch/`.", registryPath, e.NewModel)
	w("")
	w("If it should not be under contract, add it as `alias` (with `alias_of` a covered snapshot) or `ignored` with the reason instead. Either way, put `Closes #<this issue>` in the PR. Closing this issue without a registry entry does not stick: the next run reopens it.")
	return b.String()
}

const registryPath = "internal/providers/contractwatch/" + RegistryFile

func metadataLine(m *Model) string {
	if m == nil {
		return ""
	}
	var parts []string
	if m.DisplayName != "" {
		parts = append(parts, m.DisplayName)
	}
	if m.Created != nil {
		parts = append(parts, "created "+m.Created.Format("2006-01-02"))
	}
	if m.OwnedBy != "" {
		parts = append(parts, "owned by "+m.OwnedBy)
	}
	if m.Thinking != nil {
		parts = append(parts, fmt.Sprintf("thinking %v", *m.Thinking))
	}
	if m.Line != "" {
		parts = append(parts, "line "+m.Line)
	}
	if m.Lifecycle != "" {
		parts = append(parts, "lifecycle "+m.Lifecycle)
	}
	if m.ContextWindow > 0 {
		parts = append(parts, fmt.Sprintf("context %d", m.ContextWindow))
	}
	if m.MaxOutput > 0 {
		parts = append(parts, fmt.Sprintf("max output %d", m.MaxOutput))
	}
	if len(m.Capabilities) > 0 {
		caps := append([]string(nil), m.Capabilities...)
		sort.Strings(caps)
		parts = append(parts, strings.Join(caps, ", "))
	}
	return strings.Join(parts, "; ")
}

// cell makes s safe inside a Markdown table cell.
func cell(s string) string {
	s = strings.ReplaceAll(s, "|", "\\|")
	return strings.ReplaceAll(s, "\n", " ")
}

func codeOr(s, fallback string) string {
	if s == "" {
		return fallback
	}
	return "`" + s + "`"
}

func yesNo(b bool, yes, no string) string {
	if b {
		return yes
	}
	return no
}

func firstNonEmpty(v ...string) string {
	for _, s := range v {
		if s != "" {
			return s
		}
	}
	return ""
}
