// Command contract-specs keeps the gateway's provider spec subsets in step
// with the providers' official machine-readable specs.
//
// It fetches each provider's spec (OpenAPI, Google discovery or Smithy) at
// the latest commit (resolved with git ls-remote, then fetched raw at that
// commit) or at a pinned one, flattens the roots the gateway parses
// (internal/providers/contractspec), and either writes the subsets and
// their provenance (-update) or diffs them against the committed ones
// (-check, exit 1 on drift). No credentials are needed and no model is
// called.
//
// Run it from agentcc-gateway/:
//
//	go run ./cmd/contract-specs -fetch -check                        # drift check, every provider
//	go run ./cmd/contract-specs -fetch -update -provider anthropic   # re-pin one provider at latest
//	go run ./cmd/contract-specs -fetch -ref <sha> -update -provider openai
//	go run ./cmd/contract-specs -spec openapi.json -provider openai -check   # offline, a local copy
//
// See internal/providers/contractspec/README.md.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/providers/contractspec"
)

// Exit codes.
const (
	exitOK    = 0
	exitDrift = 1
	exitError = 2
)

type options struct {
	providers []string
	fetch     bool
	ref       string
	specFile  string
	update    bool
	check     bool
	dir       string
	cache     string
	report    string
	timeout   time.Duration

	// The registry; main uses contractspec's, tests their own.
	sources     []contractspec.Source
	unfetchable []contractspec.Unfetchable
}

func main() {
	var o options
	var providers string
	fs := flag.NewFlagSet("contract-specs", flag.ContinueOnError)
	fs.StringVar(&providers, "provider", "", "comma-separated providers (default: all)")
	fs.BoolVar(&o.fetch, "fetch", false, "download each spec from upstream (latest commit, or -ref)")
	fs.StringVar(&o.ref, "ref", "", "with -fetch and one -provider: the commit (or branch/tag) to fetch instead of the latest")
	fs.StringVar(&o.specFile, "spec", "", "with one -provider: read this local, already-decoded spec instead of fetching")
	fs.BoolVar(&o.update, "update", false, "write the subsets and provenance (and the unfetchable manifest) to -dir")
	fs.BoolVar(&o.check, "check", false, "diff the extracted subsets against the committed ones; exit 1 on drift")
	fs.StringVar(&o.dir, "dir", contractspec.Dir, "the subsets directory")
	fs.StringVar(&o.cache, "cache", "", "save each fetched spec under this directory (for a CI artifact)")
	fs.StringVar(&o.report, "report", "", "with -check: write <provider>.md for each drifted provider, and summary.md, under this directory")
	fs.DurationVar(&o.timeout, "timeout", 3*time.Minute, "per download")
	if err := fs.Parse(os.Args[1:]); err != nil {
		os.Exit(exitError)
	}
	if providers != "" {
		o.providers = strings.Split(providers, ",")
	}
	o.sources, o.unfetchable = contractspec.Sources(), contractspec.UnfetchableProviders()
	ctx := context.Background()
	os.Exit(run(ctx, o, newNetFetcher(o.timeout), os.Stdout, os.Stderr, time.Now().UTC()))
}

// providerResult is what happened to one provider.
type providerResult struct {
	provider string
	status   string // ok, drift, error, updated, extracted
	diffs    []contractspec.Diff
	signal   string // the change-signal line for an unfetchable provider
	err      error
	from, to string // committed and fresh provenance, for reports
}

func run(ctx context.Context, o options, f fetcher, stdout, stderr io.Writer, now time.Time) int {
	sources, unfetch, err := selectProviders(o.providers, o.sources, o.unfetchable)
	if err != nil {
		_, _ = fmt.Fprintln(stderr, "contract-specs:", err)
		return exitError
	}
	if err := validate(o, sources, unfetch); err != nil {
		_, _ = fmt.Fprintln(stderr, "contract-specs:", err)
		return exitError
	}

	var results []providerResult
	for _, src := range sources {
		results = append(results, runSource(ctx, o, f, src, stdout, now))
	}
	if len(unfetch) > 0 {
		results = append(results, runSignals(ctx, o, f, unfetch, stdout, now)...)
	}

	code := exitOK
	_, _ = fmt.Fprintln(stdout, "\nsummary:")
	for _, r := range results {
		line := fmt.Sprintf("  %-11s %s", r.provider, r.status)
		if r.err != nil {
			line += ": " + r.err.Error()
		}
		_, _ = fmt.Fprintln(stdout, line)
		switch {
		case r.status == "error":
			code = exitError
		case r.status == "drift" && code == exitOK:
			code = exitDrift
		}
	}
	if o.report != "" && o.check {
		if err := writeReports(o.report, results); err != nil {
			_, _ = fmt.Fprintln(stderr, "contract-specs: writing reports:", err)
			return exitError
		}
	}
	return code
}

func selectProviders(names []string, all []contractspec.Source, allU []contractspec.Unfetchable) ([]contractspec.Source, []contractspec.Unfetchable, error) {
	if len(names) == 0 {
		return all, allU, nil
	}
	var srcs []contractspec.Source
	var unf []contractspec.Unfetchable
	for _, n := range names {
		n = strings.TrimSpace(n)
		found := false
		for _, s := range all {
			if s.Provider == n {
				srcs = append(srcs, s)
				found = true
			}
		}
		for _, u := range allU {
			if u.Provider == n {
				unf = append(unf, u)
				found = true
			}
		}
		if !found {
			return nil, nil, fmt.Errorf("unknown provider %q", n)
		}
	}
	return srcs, unf, nil
}

func validate(o options, sources []contractspec.Source, unfetch []contractspec.Unfetchable) error {
	single := len(sources) == 1 && len(unfetch) == 0
	switch {
	case o.fetch && o.specFile != "":
		return errors.New("-fetch and -spec are exclusive")
	case !o.fetch && o.specFile == "":
		return errors.New("nothing to read: pass -fetch, or -spec with one -provider")
	case o.specFile != "" && !single:
		return errors.New("-spec needs exactly one -provider with a fetchable spec")
	case o.ref != "" && !single:
		return errors.New("-ref needs exactly one -provider with a fetchable spec")
	case o.report != "" && !o.check:
		return errors.New("-report needs -check")
	}
	return nil
}

func runSource(ctx context.Context, o options, f fetcher, src contractspec.Source, stdout io.Writer, now time.Time) providerResult {
	res := providerResult{provider: src.Provider}
	fail := func(err error) providerResult {
		res.status, res.err = "error", err
		return res
	}
	var got *fetched
	if o.specFile != "" {
		spec, err := os.ReadFile(o.specFile)
		if err != nil {
			return fail(err)
		}
		got = &fetched{url: "file:" + filepath.Base(o.specFile), commit: o.ref, raw: spec, spec: spec, at: now}
	} else {
		dctx, cancel := context.WithTimeout(ctx, o.timeout)
		defer cancel()
		var err error
		if got, err = fetchSource(dctx, f, src, o.ref, now); err != nil {
			return fail(err)
		}
	}
	if o.cache != "" {
		name := filepath.Join(o.cache, src.Provider, filepath.Base(firstNonEmpty(src.Path, "discovery.json")))
		if err := os.MkdirAll(filepath.Dir(name), 0o755); err != nil {
			return fail(err)
		}
		if err := os.WriteFile(name, got.raw, 0o644); err != nil {
			return fail(err)
		}
	}
	subsets, meta, failed, err := contractspec.ExtractEach(src, got.spec)
	if err != nil {
		return fail(err)
	}
	var failedRoots []string
	for _, r := range src.Roots {
		if ferr := failed[r.Name]; ferr != nil {
			failedRoots = append(failedRoots, r.Name)
			_, _ = fmt.Fprintf(stdout, "   %s/%s: cannot extract: %v\n", src.Provider, r.Name, ferr)
		}
	}
	prov := provenance(src, got, meta, subsets)
	res.to = describeProvenance(prov)
	res.status = "extracted"

	if o.check {
		committed, err := contractspec.LoadFS(os.DirFS(o.dir), src.Provider)
		if err != nil {
			return fail(err)
		}
		if old, err := contractspec.LoadProvenance(os.DirFS(o.dir), src.Provider); err == nil {
			res.from = describeProvenance(old)
		}
		_, _ = fmt.Fprintf(stdout, "== %s\n   committed: %s\n   latest:    %s\n", src.Provider, firstNonEmpty(res.from, "(none)"), res.to)
		res.diffs = contractspec.DiffProvider(committed, subsets)
		// A root the spec no longer yields (its operation or schema is gone
		// or renamed) is drift too; the committed subset is left in place.
		for _, name := range failedRoots {
			id, found := src.Provider+"/"+name, false
			unextractable := contractspec.Diff{ID: id, Changes: []contractspec.Change{{
				Kind: contractspec.ChangeUnextractable, Path: "$", Detail: failed[name].Error()}}}
			for i, d := range res.diffs {
				if d.ID == id {
					res.diffs[i], found = unextractable, true
				}
			}
			if !found {
				res.diffs = append(res.diffs, unextractable)
			}
		}
		drift := 0
		for _, d := range res.diffs {
			if len(d.Changes) > 0 {
				d.Format(stdout)
			}
			drift += d.Failing()
		}
		res.status = "ok"
		if drift > 0 {
			res.status = "drift"
		}
		_, _ = fmt.Fprintf(stdout, "   %s: %d failing change(s)\n", src.Provider, drift)
	}
	if o.update {
		if err := contractspec.WriteProvider(o.dir, prov, subsets, failedRoots...); err != nil {
			return fail(err)
		}
		if !o.check {
			res.status = "updated"
		}
		_, _ = fmt.Fprintf(stdout, "wrote %s (%s)\n", filepath.Join(o.dir, src.Provider), res.to)
	}
	if len(failedRoots) > 0 && !o.check {
		// Writing what could be extracted keeps the rest current; the failed
		// roots' committed files stay as they were.
		res.status, res.err = "error", fmt.Errorf("cannot extract %s", strings.Join(failedRoots, ", "))
	}
	if !o.check && !o.update {
		for _, s := range subsets {
			_, _ = fmt.Fprintf(stdout, "%-36s %5d paths  %3d with enums  %3d events\n", s.ID(), len(s.Paths), countEnums(s), len(s.Events))
		}
	}
	return res
}

func countEnums(s *contractspec.Subset) int {
	n := 0
	for _, p := range s.Paths {
		if len(p.Enum) > 0 {
			n++
		}
	}
	return n
}

func provenance(src contractspec.Source, got *fetched, meta map[string]string, subsets []*contractspec.Subset) *contractspec.Provenance {
	p := &contractspec.Provenance{
		Provider: src.Provider, Format: src.Format, URL: got.url,
		Repo: src.Repo, Path: src.Path, Commit: got.commit, Decode: src.Decode,
		SpecVersion: meta, SHA256: contractspec.SHA256Hex(got.raw),
		FetchedAt: got.at.UTC().Format(time.RFC3339), License: src.License,
	}
	if src.Decode != "" {
		p.DecodedSHA256 = contractspec.SHA256Hex(got.spec)
	}
	for _, s := range subsets {
		p.Roots = append(p.Roots, s.Root)
	}
	sort.Strings(p.Roots)
	return p
}

func describeProvenance(p *contractspec.Provenance) string {
	var parts []string
	if p.Repo != "" {
		parts = append(parts, p.Repo+"@"+contractspec.ShortRef(firstNonEmpty(p.Commit, "?")))
	} else {
		parts = append(parts, p.URL)
	}
	if v := p.SpecVersion["revision"]; v != "" {
		parts = append(parts, "revision "+v)
	}
	parts = append(parts, "sha256 "+contractspec.ShortRef(p.SHA256), "fetched "+p.FetchedAt)
	return strings.Join(parts, ", ")
}

// runSignals checks (or records) the change signals of the providers
// without a fetchable spec.
func runSignals(ctx context.Context, o options, f fetcher, unfetch []contractspec.Unfetchable, stdout io.Writer, now time.Time) []providerResult {
	manifest, err := contractspec.LoadManifest(os.DirFS(o.dir))
	if err != nil {
		if !o.update || !errors.Is(err, os.ErrNotExist) {
			return []providerResult{{provider: "unfetchable", status: "error", err: err}}
		}
		manifest = &contractspec.Manifest{}
	}
	var results []providerResult
	_, _ = fmt.Fprintln(stdout, "== providers without a fetchable spec")
	for _, u := range unfetch {
		res := providerResult{provider: u.Provider, status: "ok"}
		entry := manifest.Entry(u.Provider)
		var obs *contractspec.Observed
		if u.Signal != nil && o.fetch {
			dctx, cancel := context.WithTimeout(ctx, o.timeout)
			obs, err = observeSignal(dctx, f, u.Signal, now)
			cancel()
			if err != nil {
				results = append(results, providerResult{provider: u.Provider, status: "error", err: err})
				continue
			}
		}
		switch {
		case u.Signal == nil:
			res.signal = "no spec and no change signal: " + u.Reason
		case obs == nil:
			res.signal = "signal not read"
		case entry == nil || entry.Observed == nil:
			res.signal = fmt.Sprintf("%s = %s (no committed value)", u.Signal.What, obs.Value)
			if o.check {
				res.status = "drift"
			}
		case entry.Observed.Value != obs.Value:
			res.signal = fmt.Sprintf("FAIL  change signal moved: %s %s %s: %s -> %s (%s@%s); the spec changed but cannot be fetched, so review the provider's changelog",
				u.Signal.Repo, u.Signal.Path, firstNonEmpty(u.Signal.Key, "sha256"), entry.Observed.Value, obs.Value, u.Signal.Repo, contractspec.ShortRef(obs.Commit))
			res.from = fmt.Sprintf("%s@%s %s", u.Signal.Repo, contractspec.ShortRef(entry.Observed.Commit), entry.Observed.Value)
			res.to = fmt.Sprintf("%s@%s %s", u.Signal.Repo, contractspec.ShortRef(obs.Commit), obs.Value)
			if o.check {
				res.status = "drift"
			}
		default:
			res.signal = fmt.Sprintf("unchanged: %s = %s", firstNonEmpty(u.Signal.Key, "sha256"), obs.Value)
		}
		_, _ = fmt.Fprintf(stdout, "  %-11s %s\n", u.Provider, res.signal)
		if o.update {
			ne := contractspec.ManifestEntry{Unfetchable: u}
			if entry != nil {
				ne.Observed = entry.Observed
			}
			if obs != nil {
				ne.Observed = obs
			}
			if entry != nil {
				*entry = ne
			} else {
				manifest.Providers = append(manifest.Providers, ne)
			}
			if !o.check {
				res.status = "updated"
			}
		}
		results = append(results, res)
	}
	if o.update {
		manifest.Schema = contractspec.SchemaVersion
		manifest.Note = "Providers the gateway serves whose official spec cannot be fetched anonymously, why, and (where one exists) a public value that changes when their spec does. Written by cmd/contract-specs -update; -check fails when an observed value moves."
		sort.Slice(manifest.Providers, func(i, j int) bool { return manifest.Providers[i].Provider < manifest.Providers[j].Provider })
		if err := contractspec.WriteManifest(o.dir, manifest); err != nil {
			results = append(results, providerResult{provider: "unfetchable", status: "error", err: err})
		}
	}
	return results
}
