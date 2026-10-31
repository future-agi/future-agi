package main

import (
	"context"
	"fmt"
	"io"
	"maps"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// Exit codes.
const (
	exitOK = 0
	// exitFailed: a case failed (upstream error, unexpected status, write
	// error, bad fixture).
	exitFailed = 1
	// exitUsage: bad flags.
	exitUsage = 2
	// exitNothing: no recordable case matched the selection, so nothing was
	// sent. A capped workflow pass over endpoints a provider may not have
	// treats it as success.
	exitNothing = 3
)

type recorder struct {
	opts options
	out  io.Writer
	env  func(string) string
	now  func() time.Time
	// client calls upstream; nil uses a clone of http.DefaultTransport.
	client *http.Client

	selected, recorded, failed, skipped int
	notRecordable, capped, copied       int
	// byEndpoint counts the selected cases per endpoint.
	byEndpoint map[string]int
}

func (r *recorder) printf(format string, args ...any) {
	_, _ = fmt.Fprintf(r.out, format+"\n", args...)
}

func (r *recorder) fail(format string, args ...any) {
	r.failed++
	r.printf(format, args...)
}

func (r *recorder) run(ctx context.Context) int {
	names := r.opts.Providers
	explicit := len(names) > 0
	if !explicit {
		found, err := providersWithFixtures(r.opts.ProvidersDir)
		if err != nil {
			r.fail("error: %v", err)
			return exitFailed
		}
		for _, p := range found {
			if _, ok := providerSpecs[p]; !ok {
				r.printf("%s: skipped: the recorder has no upstream configuration for it (known: %s)",
					p, strings.Join(knownProviders(), ", "))
				continue
			}
			names = append(names, p)
		}
	}

	sel := selection{
		Models: r.opts.Models, Cases: r.opts.Cases,
		Endpoints: r.opts.Endpoints, Exclude: r.opts.ExcludeEndpoints,
		MaxCases: r.opts.MaxCases, MaxModels: r.opts.MaxModels, Rotate: r.opts.Rotate,
		From: r.opts.From, As: r.opts.As, Out: r.opts.Out,
	}
	r.byEndpoint = map[string]int{}
	for _, name := range names {
		r.provider(ctx, name, sel, explicit)
	}

	tail := fmt.Sprintf("%d not recordable (skipped), %d over the caps", r.notRecordable, r.capped)
	switch {
	case r.opts.DryRun:
		r.printf("dry run: %d cases selected (%s); %s; nothing was sent upstream or written", r.selected, endpointCounts(r.byEndpoint), tail)
	default:
		r.printf("done: %d recorded, %d failed, %d copied as synthetic; %d upstream(s) skipped for missing credentials; %s",
			r.recorded, r.failed, r.copied, r.skipped, tail)
	}
	if r.failed > 0 {
		return exitFailed
	}
	if r.selected == 0 {
		r.printf("nothing to record: no recordable case matched the selection")
		return exitNothing
	}
	return exitOK
}

// endpointCounts is "chat 12, embeddings 2", in contracttest.Endpoints order.
func endpointCounts(counts map[string]int) string {
	var parts []string
	for _, ep := range contracttest.Endpoints {
		if n := counts[ep]; n > 0 {
			parts = append(parts, fmt.Sprintf("%s %d", ep, n))
		}
	}
	if len(parts) == 0 {
		return "none"
	}
	return strings.Join(parts, ", ")
}

func (r *recorder) provider(ctx context.Context, name string, sel selection, explicit bool) {
	pl, err := planProvider(r.opts.ProvidersDir, name, sel)
	if err != nil {
		r.fail("%s: FAILED: %v", name, err)
		return
	}
	r.notRecordable += len(pl.NotRecordable)
	r.capped += len(pl.Capped)
	if len(pl.Jobs) == 0 && len(pl.NotRecordable) == 0 && len(pl.Capped) == 0 {
		if explicit {
			r.printf("%s: no case matches the selection", name)
		}
		return
	}
	r.selected += len(pl.Jobs)
	for _, j := range pl.Jobs {
		r.byEndpoint[j.Endpoint]++
	}

	if r.opts.DryRun {
		r.printPlan(pl)
		return
	}
	for _, c := range pl.NotRecordable {
		r.printf("%s %s/%s: skipped: case.json says \"recordable\": false (status %d); not sent, its synthetic body stays",
			c.Provider, c.Model, c.Name, c.wantStatus())
	}
	if len(pl.Capped) > 0 {
		r.printf("%s: %d cases left out by -max-cases/-max-models: %s", name, len(pl.Capped), caseList(pl.Capped))
	}
	recorded := make([]bool, len(pl.Jobs))
	for _, g := range pl.groups() {
		r.recordGroup(ctx, pl, g, recorded)
	}
	r.writeMetas(pl, recorded)
	if r.opts.Out == "" && slices.Contains(recorded, true) {
		r.printf("%s: next: go test ./internal/providers/%s/ -run Contract -update, then review the golden and fixture diff", name, name)
	}
}

func caseList(cs []caseRef) string {
	ids := make([]string, len(cs))
	for i, c := range cs {
		ids[i] = c.Model + "/" + c.Name
	}
	return strings.Join(ids, ", ")
}

// upstreamGroup is a plan's jobs that share an upstream and an adapter
// configuration.
type upstreamGroup struct {
	Name       string // see upstreamFor
	APIVersion string // see apiVersionOf
	Jobs       []int  // indexes into plan.Jobs
}

// groups splits the plan's jobs by upstream and api-version, in a stable
// order.
func (p *plan) groups() []upstreamGroup {
	var gs []upstreamGroup
	at := map[[2]string]int{}
	for i, j := range p.Jobs {
		key := [2]string{j.Upstream, j.APIVersion}
		k, ok := at[key]
		if !ok {
			k = len(gs)
			at[key] = k
			gs = append(gs, upstreamGroup{Name: j.Upstream, APIVersion: j.APIVersion})
		}
		gs[k].Jobs = append(gs[k].Jobs, i)
	}
	sort.Slice(gs, func(a, b int) bool {
		if gs[a].Name != gs[b].Name {
			return gs[a].Name < gs[b].Name
		}
		return gs[a].APIVersion < gs[b].APIVersion
	})
	return gs
}

// label names a group in output: "openai", "openai via groq", or
// "azure at api-version 2025-04-01-preview".
func (p *plan) label(g upstreamGroup) string {
	l := p.Provider
	if g.Name != p.Provider {
		l += " via " + g.Name
	}
	if g.APIVersion != "" {
		l += " at api-version " + g.APIVersion
	}
	return l
}

// recordGroup records one upstream's jobs through one adapter and proxy.
func (r *recorder) recordGroup(ctx context.Context, pl *plan, g upstreamGroup, recorded []bool) {
	label := pl.label(g)
	spec := upstreamSpec(pl.Provider, g.Name)
	if missing := spec.missingEnv(r.env); len(missing) > 0 {
		r.skipped++
		msg := fmt.Sprintf("%s: skipped %d cases: %s not set", label, len(g.Jobs), strings.Join(missing, ", "))
		if spec.help != "" {
			msg += " (" + spec.help + ")"
		}
		r.printf("%s", msg)
		return
	}
	failAll := func(format string, args ...any) {
		r.failed += len(g.Jobs)
		r.printf("%s: FAILED (%d cases): %s", label, len(g.Jobs), fmt.Sprintf(format, args...))
	}

	u := spec.build(r.env)
	if r.opts.BaseURL != "" {
		u.baseURL = r.opts.BaseURL
	}
	if g.APIVersion != "" && u.cfg.Headers["api-version"] == "" {
		// The version the case's golden pins, unless the environment set one.
		h := maps.Clone(u.cfg.Headers)
		if h == nil {
			h = map[string]string{}
		}
		h["api-version"] = g.APIVersion
		u.cfg.Headers = h
	}
	target, err := parseBaseURL(u.baseURL)
	if err != nil {
		failAll("%v", err)
		return
	}
	proxy := newTeeProxy(target, r.client, r.opts.Timeout)
	defer func() { _ = proxy.Close() }()

	// The gateway's own registry builds the adapter, presets and all, exactly
	// as it would for config.yaml.
	reg, err := providers.NewRegistry(&config.Config{
		Providers: map[string]config.ProviderConfig{pl.Provider: u.adapterConfig(target, proxy.Dial)},
	})
	if err != nil {
		failAll("building the adapter: %v", err)
		return
	}
	defer func() { _ = reg.Close() }()
	adapter, ok := reg.GetProvider(pl.Provider)
	if !ok {
		failAll("the registry did not register the adapter")
		return
	}
	for _, i := range g.Jobs {
		recorded[i] = r.record(ctx, adapter, proxy, u, pl.Jobs[i])
	}
}

// record runs one case and saves what decide says to. It reports whether it
// saved anything.
func (r *recorder) record(ctx context.Context, adapter providers.Provider, proxy *teeProxy, u upstream, j job) bool {
	id := j.ID()
	switch {
	case j.Bad != nil:
		r.fail("%s: FAILED: cannot record it: %v", id, j.Bad)
		return false
	case j.Patch != nil && u.signedBody:
		r.fail("%s: FAILED: %s cannot be applied: %s signs the request body (SigV4)", id, PatchFile, j.Provider)
		return false
	case j.Patch != nil && contracttest.MultipartEndpoint(j.Endpoint):
		r.fail("%s: FAILED: %s cannot be applied: a %s request is a multipart form, not JSON", id, PatchFile, j.Endpoint)
		return false
	}

	proxy.setPatch(j.Patch)
	ctx, cancel := context.WithTimeout(ctx, r.opts.Timeout)
	defer cancel()
	ctx, shapes := common.WithShapeRecorder(ctx)
	adapterErr := drive(ctx, adapter, j.call())
	exchanges := proxy.take()

	if len(exchanges) == 0 {
		r.fail("%s: FAILED: the adapter sent nothing upstream for endpoint %s: %v", id, j.Endpoint, adapterErr)
		return false
	}
	var warnings []string
	if len(exchanges) > 1 {
		warnings = append(warnings, fmt.Sprintf("the adapter made %d upstream calls; recorded the last", len(exchanges)))
	}
	ex := exchanges[len(exchanges)-1]
	if ex.Err != nil {
		r.fail("%s: FAILED: %v", id, ex.Err)
		return false
	}
	rec, err := decide(j, ex, u.secrets)
	if err != nil {
		r.fail("%s: FAILED: %v", id, err)
		return false
	}
	warnings = append(warnings, rec.Warnings...)
	if adapterErr != nil && !j.isError() {
		warnings = append(warnings, fmt.Sprintf("the adapter failed on this %d response (saved; the contract test will fail on it): %v", ex.Status, adapterErr))
	}
	for _, s := range shapes.Shapes() {
		warnings = append(warnings, fmt.Sprintf("the adapter reported an unknown shape: %s %q", s.Kind, s.Value))
	}

	dst, removed, err := writeRecording(j, rec)
	if err != nil {
		r.fail("%s: FAILED: writing: %v", id, err)
		return false
	}
	for _, f := range removed {
		warnings = append(warnings, fmt.Sprintf("removed %s: it described the replaced fixture", f))
	}
	r.recorded++
	r.printf("%s: recorded %d %s (%d bytes) -> %s", id, ex.Status, ex.ContentType, len(rec.Body), dst)
	if rec.Spec != nil {
		what := "copied"
		if len(rec.Changes) > 0 {
			what = strings.Join(rec.Changes, "; ")
		}
		r.printf("  %s: %s", filepath.Join(j.OutDir, contracttest.CaseFile), what)
		if j.isError() && !j.Live && !j.WriteRequest {
			r.printf("  note: its notes may still describe a synthetic body; review them")
		}
	}
	for _, w := range warnings {
		r.printf("  warning: %s", w)
	}
	return true
}

func (r *recorder) writeMetas(pl *plan, recorded []bool) {
	date := r.now().UTC().Format("2006-01-02")
	for _, mw := range pl.Metas {
		n := 0
		for _, i := range mw.Jobs {
			if recorded[i] {
				n++
			}
		}
		if n == 0 {
			if mw.ClonedFrom != "" && len(pl.Copies) > 0 {
				r.printf("%s %s: nothing was recorded, so its %d not-recordable cases were not copied either", pl.Provider, mw.Model, len(pl.Copies))
			}
			continue
		}
		if mw.ClonedFrom == "" && n < mw.Cases {
			r.printf("%s %s: %s left unchanged: %d of its %d recordable cases were recorded, and meta.json covers them all",
				pl.Provider, mw.Model, mw.Path, n, mw.Cases)
			continue
		}
		data, err := markRecorded(mw, date)
		if err == nil {
			err = os.MkdirAll(filepath.Dir(mw.Path), 0o755)
		}
		if err == nil {
			err = os.WriteFile(mw.Path, data, 0o644)
		}
		if err != nil {
			r.fail("%s %s: FAILED: writing %s: %v", pl.Provider, mw.Model, mw.Path, err)
			continue
		}
		r.printf("%s %s: wrote %s (source recorded, recorded_at %s)", pl.Provider, mw.Model, mw.Path, date)
		switch {
		case mw.ClonedFrom != "":
			r.printf("  cloned_from %s: the contract test diffs each case's shape against %s's", mw.ClonedFrom, mw.ClonedFrom)
			for _, j := range pl.Copies {
				dst, err := writeCopy(j, mw.ClonedFrom, date)
				if err != nil {
					r.fail("%s: FAILED: copying the not-recordable case: %v", j.ID(), err)
					continue
				}
				r.copied++
				r.printf("%s: copied the synthetic body (not recordable) -> %s; its case.json notes say so", j.ID(), dst)
			}
		default:
			r.printf("  note: its notes may still describe synthetic bodies; review them")
			if mw.Synthetic > 0 {
				r.printf("  note: %d not-recordable cases stay synthetic; their case.json says so", mw.Synthetic)
			}
		}
	}
}

func (r *recorder) printPlan(pl *plan) {
	byEP := map[string]int{}
	for _, j := range pl.Jobs {
		byEP[j.Endpoint]++
	}
	r.printf("%s: %d cases to record (%s); %d not recordable, %d over the caps",
		pl.Provider, len(pl.Jobs), endpointCounts(byEP), len(pl.NotRecordable), len(pl.Capped))
	for _, g := range pl.groups() {
		spec := upstreamSpec(pl.Provider, g.Name)
		head := fmt.Sprintf("  %s: %d cases", pl.label(g), len(g.Jobs))
		if missing := spec.missingEnv(r.env); len(missing) > 0 {
			head += fmt.Sprintf("; %s not set, so a real run skips them", strings.Join(missing, ", "))
		}
		r.printf("%s", head)
		signed := spec.build(func(string) string { return "" }).signedBody
		for _, i := range g.Jobs {
			j := pl.Jobs[i]
			bad := ""
			if j.Bad != nil {
				bad = " (will fail: " + j.Bad.Error() + ")"
			}
			r.printf("    %s/%s%s -> %s: %s%s%s", j.Target, j.Name, jobTags(j), j.OutDir, jobFiles(j), patchNote(j, signed), bad)
		}
	}
	if len(pl.NotRecordable) > 0 {
		r.printf("  not recordable (case.json \"recordable\": false), not sent:")
		for _, c := range pl.NotRecordable {
			r.printf("    %s/%s [%s, %d]", c.Model, c.Name, c.Endpoint, c.wantStatus())
		}
	}
	if len(pl.Copies) > 0 {
		r.printf("  not recordable, copied as synthetic if the clone records anything:")
		for _, j := range pl.Copies {
			r.printf("    %s/%s [%s, %d] -> %s", j.Target, j.Name, j.Endpoint, j.wantStatus(), j.OutDir)
		}
	}
	if len(pl.Capped) > 0 {
		r.printf("  over -max-cases/-max-models, not sent: %s", caseList(pl.Capped))
	}
	for _, mw := range pl.Metas {
		if mw.ClonedFrom != "" {
			r.printf("  meta: %s (new: model %s, cloned_from %s with its allowlists, source recorded)", mw.Path, mw.Model, mw.ClonedFrom)
		} else {
			r.printf("  meta: %s -> source recorded, if all %d of its recordable cases record (%d selected)", mw.Path, mw.Cases, len(mw.Jobs))
		}
	}
}

// jobTags is " [endpoint, stream, expects N]" for what is not a plain 200
// chat case.
func jobTags(j job) string {
	var tags []string
	if j.Endpoint != contracttest.EndpointChat {
		tags = append(tags, j.Endpoint)
	}
	if j.isError() {
		tags = append(tags, fmt.Sprintf("expects %d", j.wantStatus()))
	}
	if len(tags) == 0 {
		return ""
	}
	return " [" + strings.Join(tags, ", ") + "]"
}

// jobFiles says what a job writes.
func jobFiles(j job) string {
	what := j.RespFile
	if what == "" {
		what = "response.* (by Content-Type)"
	}
	if j.WriteRequest {
		what = contracttest.RequestFile + " (model " + models.ExtractModelFromBody(j.Body) + ") + " + what
	}
	switch {
	case j.isError():
		what += " + " + contracttest.CaseFile + " (status checked, observed headers)"
	case j.WriteRequest && j.HasSpec:
		what += " + " + contracttest.CaseFile
	}
	return what
}

func patchNote(j job, signed bool) string {
	if j.Patch == nil {
		return ""
	}
	s := " [with " + PatchFile + "]"
	if signed {
		s += " (will fail: SigV4-signed body)"
	}
	return s
}
