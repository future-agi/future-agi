// Command model-watch notices when a provider ships a model the provider
// contract tests do not cover, and plans how to record it.
//
// It lists each provider's models through the provider's own list-models API
// (with the credentials cmd/contract-recorder uses), diffs them against the
// committed registry internal/providers/contractwatch/known_models.json, and
// for each NEW model picks the covered model whose cases the recorder should
// clone (contract-recorder -from <template> -as <new>).
//
// Run it from agentcc-gateway/:
//
//	go run ./cmd/model-watch -offline                       # registry vs fixtures, no network
//	go run ./cmd/model-watch -check -plan-out plan.json     # list, diff, plan; exit 1 on NEW models
//	go run ./cmd/model-watch -newest -plan-out newest.json  # newest covered model per family
//	go run ./cmd/model-watch -update-registry               # add NEW models as ignored, for triage
//	go run ./cmd/model-watch -fmt                           # rewrite the registry in canonical form
//	go run ./cmd/model-watch -report -plan plan.json -results results.jsonl -summary-out s.md -issues-out issues.json
//
// See cmd/model-watch/README.md.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/providers/contractwatch"
)

type options struct {
	Check, Offline, Newest, UpdateRegistry, Fmt, Report bool

	Registry     string
	ProvidersDir string
	Providers    []string
	PlanOut      string
	SummaryOut   string
	MaxRecord    int
	MaxCases     int
	Rotate       int
	Timeout      time.Duration

	// -report inputs and outputs.
	Plan, Results, IssuesOut, RunURL, Artifact string
}

// deps is everything run touches outside its arguments, so tests can
// replace it.
type deps struct {
	stdout, stderr io.Writer
	env            func(string) string
	client         *http.Client
	now            func() time.Time
	listers        map[string]contractwatch.Lister
}

func main() {
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelWarn})))
	os.Exit(run(os.Args[1:], deps{
		stdout: os.Stdout,
		stderr: os.Stderr,
		env:    os.Getenv,
		client: &http.Client{Timeout: 2 * time.Minute, CheckRedirect: noRedirects},
		now:    time.Now,
	}))
}

// noRedirects keeps credentials on the host they were meant for.
func noRedirects(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }

// Exit codes.
const (
	exitOK       = 0
	exitFindings = 1 // NEW models, a failed listing, stale coverage, placeholders
	exitUsage    = 2 // bad flags, unreadable or invalid registry, I/O errors
)

func run(args []string, d deps) int {
	o, err := parseFlags(args, d.stderr)
	if err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return exitOK
		}
		_, _ = fmt.Fprintf(d.stderr, "model-watch: %v\n", err)
		return exitUsage
	}
	if o.Report {
		return report(o, d)
	}
	reg, err := contractwatch.LoadRegistry(o.Registry)
	if err != nil {
		_, _ = fmt.Fprintf(d.stderr, "model-watch: %v\n", err)
		return exitUsage
	}
	for _, p := range o.Providers {
		if reg.Providers[p] == nil {
			_, _ = fmt.Fprintf(d.stderr, "model-watch: -provider %q is not in %s (known: %s)\n", p, o.Registry, strings.Join(reg.Names(), ", "))
			return exitUsage
		}
	}
	switch {
	case o.Fmt:
		return writeRegistry(o, d, reg, "formatted")
	case o.Offline:
		return offline(o, d, reg)
	case o.Newest:
		return newest(o, d, reg)
	default:
		return check(o, d, reg)
	}
}

func parseFlags(args []string, stderr io.Writer) (options, error) {
	fs := flag.NewFlagSet("model-watch", flag.ContinueOnError)
	fs.SetOutput(stderr)
	var o options
	var providers string
	fs.BoolVar(&o.Check, "check", false, "list every provider with credentials, diff against the registry, write the plan; exit 1 if any model is NEW")
	fs.BoolVar(&o.Offline, "offline", false, "only check the registry against the fixture directories; no network")
	fs.BoolVar(&o.Newest, "newest", false, "plan a re-record of the newest covered model of every family (no network)")
	fs.BoolVar(&o.UpdateRegistry, "update-registry", false, "like -check, then add every NEW model to the registry as ignored with a placeholder reason, for local triage")
	fs.BoolVar(&o.Fmt, "fmt", false, "rewrite the registry in its canonical form")
	fs.BoolVar(&o.Report, "report", false, "render the recording summary and issue bodies from -plan and -results (no network)")
	fs.StringVar(&o.Registry, "registry", filepath.Join("internal", "providers", "contractwatch", contractwatch.RegistryFile), "the registry file")
	fs.StringVar(&o.ProvidersDir, "providers-dir", filepath.Join("internal", "providers"), "the gateway's internal/providers directory")
	fs.StringVar(&providers, "provider", "", "comma-separated registry sections to check (default: all)")
	fs.StringVar(&o.PlanOut, "plan-out", "", "write the plan (-check: NEW models; -newest: re-records) as a JSON array to this file")
	fs.StringVar(&o.SummaryOut, "summary-out", "", "write a Markdown summary to this file")
	fs.IntVar(&o.MaxRecord, "max-record", 0, "mark at most this many plan entries to record, spread across providers (0: no cap)")
	fs.IntVar(&o.MaxCases, "max-cases", 0, "record at most this many cases per model (0: all of the template's cases)")
	fs.IntVar(&o.Rotate, "rotate", 0, "-newest: shift the -max-record window by this many windows (pass the day number to cycle through every family)")
	fs.DurationVar(&o.Timeout, "timeout", time.Minute, "per-provider listing timeout")
	fs.StringVar(&o.Plan, "plan", "", "-report: the -check plan file")
	fs.StringVar(&o.Results, "results", "", "-report: results.jsonl the workflow wrote; logs are relative to its directory")
	fs.StringVar(&o.IssuesOut, "issues-out", "", "-report: write [{title, body}] for every NEW model to this file")
	fs.StringVar(&o.RunURL, "run-url", "", "-report: link to this workflow run, for issue bodies")
	fs.StringVar(&o.Artifact, "artifact", "", "-report: name of the artifact holding the recordings, for issue bodies")
	fs.Usage = func() {
		_, _ = fmt.Fprintf(fs.Output(), "usage: go run ./cmd/model-watch -check|-offline|-newest|-update-registry|-fmt|-report [flags]\n\n"+
			"Lists each provider's models, diffs them against %s,\n"+
			"and plans contract-recorder runs for new ones. Providers whose credentials are not\n"+
			"set are skipped. Credentials: ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY,\n"+
			"COHERE_API_KEY, GROQ_API_KEY, PERPLEXITY_API_KEY, AWS_ACCESS_KEY_ID +\n"+
			"AWS_SECRET_ACCESS_KEY (+ AWS_SESSION_TOKEN, AWS_REGION); AZURE_OPENAI_KEY +\n"+
			"AZURE_OPENAI_ENDPOINT gate -newest for azure.\n\nflags:\n", contractwatch.RegistryFile)
		fs.PrintDefaults()
	}
	if err := fs.Parse(args); err != nil {
		return o, err
	}
	if fs.NArg() > 0 {
		return o, fmt.Errorf("unexpected arguments: %v", fs.Args())
	}
	modes := 0
	for _, m := range []bool{o.Check, o.Offline, o.Newest, o.UpdateRegistry, o.Fmt, o.Report} {
		if m {
			modes++
		}
	}
	switch {
	case modes != 1:
		return o, errors.New("pick exactly one of -check, -offline, -newest, -update-registry, -fmt, -report")
	case o.MaxRecord < 0 || o.MaxCases < 0 || o.Rotate < 0:
		return o, errors.New("-max-record, -max-cases and -rotate must not be negative")
	case o.Timeout <= 0:
		return o, errors.New("-timeout must be positive")
	case o.Report && o.Plan == "":
		return o, errors.New("-report needs -plan")
	case !o.Report && (o.Plan != "" || o.Results != "" || o.IssuesOut != "" || o.RunURL != "" || o.Artifact != ""):
		return o, errors.New("-plan, -results, -issues-out, -run-url and -artifact are -report flags")
	case o.PlanOut != "" && !o.Check && !o.Newest && !o.UpdateRegistry:
		return o, errors.New("-plan-out goes with -check, -newest or -update-registry")
	}
	for _, p := range strings.Split(providers, ",") {
		if p = strings.TrimSpace(p); p != "" {
			o.Providers = append(o.Providers, p)
		}
	}
	if !o.Report && !o.Fmt {
		if st, err := os.Stat(o.ProvidersDir); err != nil || !st.IsDir() {
			return o, fmt.Errorf("-providers-dir %q is not a directory; run from agentcc-gateway/ or pass -providers-dir", o.ProvidersDir)
		}
	}
	return o, nil
}

func (o options) planOptions() contractwatch.PlanOptions {
	return contractwatch.PlanOptions{ProvidersDir: o.ProvidersDir, MaxRecord: o.MaxRecord, MaxCases: o.MaxCases, Rotate: o.Rotate}
}

func offline(o options, d deps, reg *contractwatch.Registry) int {
	stale := contractwatch.CheckCoverage(reg, o.ProvidersDir)
	ph := reg.Placeholders()
	for _, s := range stale {
		_, _ = fmt.Fprintf(d.stdout, "STALE %s\n", s)
	}
	for _, p := range ph {
		_, _ = fmt.Fprintf(d.stdout, "TRIAGE %s still has the placeholder reason from -update-registry\n", p)
	}
	covered := 0
	for _, name := range reg.Names() {
		covered += len(reg.Providers[name].Covered())
	}
	if len(stale) > 0 || len(ph) > 0 {
		_, _ = fmt.Fprintf(d.stdout, "offline: %d stale, %d to triage\n", len(stale), len(ph))
		return exitFindings
	}
	_, _ = fmt.Fprintf(d.stdout, "offline: ok: %d covered models across %d providers match the fixture directories under %s\n",
		covered, len(reg.Providers), o.ProvidersDir)
	return exitOK
}

func check(o options, d deps, reg *contractwatch.Registry) int {
	rep := contractwatch.Check(context.Background(), reg, contractwatch.CheckOptions{
		Env:      d.env,
		Client:   d.client,
		Sections: o.Providers,
		Timeout:  o.Timeout,
		Now:      d.now,
		Listers:  d.listers,
		Plan:     o.planOptions(),
	})
	rep.WriteText(d.stdout)
	if err := writeJSON(o.PlanOut, nonNil(rep.Plan)); err != nil {
		_, _ = fmt.Fprintf(d.stderr, "model-watch: %v\n", err)
		return exitUsage
	}
	if err := writeFile(o.SummaryOut, []byte(rep.Markdown())); err != nil {
		_, _ = fmt.Fprintf(d.stderr, "model-watch: %v\n", err)
		return exitUsage
	}
	listed := 0
	for _, s := range rep.Sections {
		if s.State == contractwatch.StateListed {
			listed++
		}
	}
	_, _ = fmt.Fprintf(d.stdout, "check: %d providers listed, %d new models, %d stale\n", listed, rep.NewCount(), len(rep.Stale))

	if o.UpdateRegistry {
		if len(rep.Plan) == 0 {
			_, _ = fmt.Fprintln(d.stdout, "update-registry: nothing new; the registry is unchanged")
			return exitOK
		}
		date := d.now().UTC().Format("2006-01-02")
		for _, e := range rep.Plan {
			reason := fmt.Sprintf("%s: listed upstream on %s and not triaged. Record it (contract-recorder -from %s -as %s) and mark it covered, or replace this with the real reason it is ignored",
				contractwatch.PlaceholderReason, date, orNone(e.TemplateModel), e.NewModel)
			if err := reg.AddIgnored(e.Provider, e.NewModel, reason); err != nil {
				_, _ = fmt.Fprintf(d.stderr, "model-watch: %v\n", err)
				return exitUsage
			}
		}
		return writeRegistry(o, d, reg, fmt.Sprintf("added %d models as ignored with a placeholder reason (go test fails until each is triaged)", len(rep.Plan)))
	}
	if rep.Failed() {
		return exitFindings
	}
	return exitOK
}

func newest(o options, d deps, reg *contractwatch.Registry) int {
	have, skipped := contractwatch.CredentialedSections(reg, d.env, o.Providers)
	for _, name := range reg.Names() {
		if why, ok := skipped[name]; ok {
			_, _ = fmt.Fprintf(d.stdout, "%s: skipped: %s\n", name, why)
		}
	}
	entries := contractwatch.BuildNewest(reg, have, o.planOptions())
	marked := 0
	for _, e := range entries {
		mark := "  "
		if e.Record {
			mark = "* "
			marked++
		}
		_, _ = fmt.Fprintf(d.stdout, "%s%s %s: %s (%d cases)\n", mark, e.Provider, e.Model, e.Family, len(e.Recorder.Cases))
	}
	_, _ = fmt.Fprintf(d.stdout, "newest: %d families, %d marked (*) to re-record this run\n", len(entries), marked)
	if err := writeJSON(o.PlanOut, nonNil(entries)); err != nil {
		_, _ = fmt.Fprintf(d.stderr, "model-watch: %v\n", err)
		return exitUsage
	}
	return exitOK
}

func report(o options, d deps) int {
	var plan []contractwatch.PlanEntry
	if err := readJSON(o.Plan, &plan); err != nil {
		_, _ = fmt.Fprintf(d.stderr, "model-watch: %v\n", err)
		return exitUsage
	}
	var results []contractwatch.Result
	base := "."
	if o.Results != "" {
		var err error
		if results, err = contractwatch.ReadResults(o.Results); err != nil {
			_, _ = fmt.Fprintf(d.stderr, "model-watch: %v\n", err)
			return exitUsage
		}
		base = filepath.Dir(o.Results)
	}
	if err := writeFile(o.SummaryOut, []byte(contractwatch.RenderResults(results, base))); err != nil {
		_, _ = fmt.Fprintf(d.stderr, "model-watch: %v\n", err)
		return exitUsage
	}
	issues := contractwatch.RenderIssues(plan, results, base, contractwatch.IssueOptions{
		RunURL: o.RunURL, Artifact: o.Artifact, Date: d.now().UTC().Format("2006-01-02"),
	})
	if err := writeJSON(o.IssuesOut, nonNil(issues)); err != nil {
		_, _ = fmt.Fprintf(d.stderr, "model-watch: %v\n", err)
		return exitUsage
	}
	failed := 0
	for _, r := range results {
		if r.Outcome() != "passed" {
			failed++
		}
	}
	_, _ = fmt.Fprintf(d.stdout, "report: %d new models, %d recordings, %d not passing\n", len(plan), len(results), failed)
	return exitOK
}

func writeRegistry(o options, d deps, reg *contractwatch.Registry, what string) int {
	if err := os.WriteFile(o.Registry, reg.Encode(), 0o644); err != nil {
		_, _ = fmt.Fprintf(d.stderr, "model-watch: %v\n", err)
		return exitUsage
	}
	_, _ = fmt.Fprintf(d.stdout, "%s: %s\n", o.Registry, what)
	return exitOK
}

func orNone(s string) string {
	if s == "" {
		return "<template>"
	}
	return s
}

// nonNil turns a nil slice into an empty one, so a plan with nothing in it
// is [] rather than null.
func nonNil[T any](s []T) []T {
	if s == nil {
		return []T{}
	}
	return s
}

func writeJSON(path string, v any) error {
	if path == "" {
		return nil
	}
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(v); err != nil {
		return err
	}
	return writeFile(path, b.Bytes())
}

func writeFile(path string, data []byte) error {
	if path == "" {
		return nil
	}
	if dir := filepath.Dir(path); dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return err
		}
	}
	return os.WriteFile(path, data, 0o644)
}

func readJSON(path string, v any) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if err := json.Unmarshal(raw, v); err != nil {
		return fmt.Errorf("%s: %v", path, err)
	}
	return nil
}
