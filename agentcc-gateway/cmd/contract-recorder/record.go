package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
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

type recorder struct {
	opts options
	out  io.Writer
	env  func(string) string
	now  func() time.Time
	// client calls upstream; nil uses a clone of http.DefaultTransport.
	client *http.Client

	selected, recorded, failed, skipped int
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
			return 1
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

	sel := selection{Models: r.opts.Models, Cases: r.opts.Cases, From: r.opts.From, As: r.opts.As, Out: r.opts.Out}
	for _, name := range names {
		r.provider(ctx, name, sel, explicit)
	}

	switch {
	case r.opts.DryRun:
		r.printf("dry run: %d cases selected; nothing was sent upstream or written", r.selected)
	default:
		r.printf("done: %d recorded, %d failed; %d upstream(s) skipped for missing credentials", r.recorded, r.failed, r.skipped)
	}
	if r.selected == 0 && r.failed == 0 {
		r.printf("error: no case matched the selection")
		return 1
	}
	if r.failed > 0 {
		return 1
	}
	return 0
}

func (r *recorder) provider(ctx context.Context, name string, sel selection, explicit bool) {
	pl, err := planProvider(r.opts.ProvidersDir, name, sel)
	if err != nil {
		r.fail("%s: FAILED: %v", name, err)
		return
	}
	if len(pl.Jobs) == 0 {
		if explicit {
			r.printf("%s: no case matches the selection", name)
		}
		return
	}
	r.selected += len(pl.Jobs)

	if r.opts.DryRun {
		r.printPlan(pl)
		return
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

// upstreamGroup is a plan's jobs that share an upstream.
type upstreamGroup struct {
	Name string // see upstreamFor
	Jobs []int  // indexes into plan.Jobs
}

// groups splits the plan's jobs by upstream, in a stable order.
func (p *plan) groups() []upstreamGroup {
	var gs []upstreamGroup
	at := map[string]int{}
	for i, j := range p.Jobs {
		k, ok := at[j.Upstream]
		if !ok {
			k = len(gs)
			at[j.Upstream] = k
			gs = append(gs, upstreamGroup{Name: j.Upstream})
		}
		gs[k].Jobs = append(gs[k].Jobs, i)
	}
	sort.Slice(gs, func(a, b int) bool { return gs[a].Name < gs[b].Name })
	return gs
}

// label names a group in output: "openai", or "openai via groq".
func (p *plan) label(g upstreamGroup) string {
	if g.Name == p.Provider {
		return p.Provider
	}
	return p.Provider + " via " + g.Name
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

// record runs one case and saves the body. It reports whether it did.
func (r *recorder) record(ctx context.Context, adapter providers.Provider, proxy *teeProxy, u upstream, j job) bool {
	id := fmt.Sprintf("%s %s/%s", j.Provider, j.Target, j.Name)
	if j.Patch != nil && u.signedBody {
		r.fail("%s: FAILED: %s cannot be applied: %s signs the request body (SigV4)", id, PatchFile, j.Provider)
		return false
	}
	var req models.ChatCompletionRequest
	if err := json.Unmarshal(j.Body, &req); err != nil {
		r.fail("%s: FAILED: request.json: %v", id, err)
		return false
	}

	proxy.setPatch(j.Patch)
	ctx, cancel := context.WithTimeout(ctx, r.opts.Timeout)
	defer cancel()
	ctx, shapes := common.WithShapeRecorder(ctx)
	adapterErr := drive(ctx, adapter, &req)
	exchanges := proxy.take()

	if len(exchanges) == 0 {
		r.fail("%s: FAILED: the adapter sent nothing upstream: %v", id, adapterErr)
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
	body, leaked := redact(ex.Body, u.secrets)
	if ex.Status < 200 || ex.Status > 299 {
		r.fail("%s: FAILED: upstream answered %d %s (not saved): %s", id, ex.Status, ex.ContentType, snippet(body))
		return false
	}
	if leaked {
		warnings = append(warnings, "the response contained a credential; it was replaced with REDACTED")
	}
	file := j.ResponseFile()
	if file == contracttest.ResponseJSONFile && !json.Valid(body) {
		r.fail("%s: FAILED: upstream answered %d %s but the body is not JSON (not saved): %s", id, ex.Status, ex.ContentType, snippet(body))
		return false
	}
	if want := contentTypes[file]; !strings.Contains(strings.ToLower(ex.ContentType), want) {
		warnings = append(warnings, fmt.Sprintf("Content-Type is %q, but %s replays as %s", ex.ContentType, file, want))
	}
	if adapterErr != nil {
		warnings = append(warnings, fmt.Sprintf("the adapter failed on this %d response (saved; the contract test will fail on it): %v", ex.Status, adapterErr))
	}
	for _, s := range shapes.Shapes() {
		warnings = append(warnings, fmt.Sprintf("the adapter reported an unknown shape: %s %q", s.Kind, s.Value))
	}

	dst, removed, err := writeRecording(j, body)
	if err != nil {
		r.fail("%s: FAILED: writing: %v", id, err)
		return false
	}
	for _, f := range removed {
		warnings = append(warnings, fmt.Sprintf("removed %s: it described the replaced fixture", f))
	}
	r.recorded++
	r.printf("%s: recorded %d %s (%d bytes) -> %s", id, ex.Status, ex.ContentType, len(body), dst)
	for _, w := range warnings {
		r.printf("  warning: %s", w)
	}
	return true
}

var contentTypes = map[string]string{
	contracttest.ResponseJSONFile:        "json",
	contracttest.ResponseSSEFile:         "text/event-stream",
	contracttest.ResponseEventStreamFile: "application/vnd.amazon.eventstream",
}

// drive sends req through the adapter and consumes the whole answer.
func drive(ctx context.Context, p providers.Provider, req *models.ChatCompletionRequest) error {
	if !req.Stream {
		_, err := p.ChatCompletion(ctx, req)
		return err
	}
	chunks, errs := p.StreamChatCompletion(ctx, req)
	for open := true; open; {
		select {
		case _, open = <-chunks:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	select {
	case err := <-errs:
		return err
	case <-time.After(10 * time.Second):
		return errors.New("the adapter closed its chunk channel but never its error channel")
	}
}

// snippet shortens a body for a one-line error message.
func snippet(b []byte) string {
	const max = 512
	s := strings.Join(strings.Fields(string(b)), " ")
	if len(s) > max {
		s = s[:max] + "..."
	}
	return s
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
			continue
		}
		if mw.ClonedFrom == "" && n < mw.Cases {
			r.printf("%s %s: %s left unchanged: %d of its %d cases were recorded, and meta.json covers them all",
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
		if mw.ClonedFrom == "" {
			r.printf("  note: its notes may still describe synthetic bodies; review them")
		}
	}
}

func (r *recorder) printPlan(pl *plan) {
	r.printf("%s: %d cases", pl.Provider, len(pl.Jobs))
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
			what := j.ResponseFile()
			if j.WriteRequest {
				what = contracttest.RequestFile + " (model " + models.ExtractModelFromBody(j.Body) + ") + " + what
			}
			extra := ""
			if j.Patch != nil {
				extra = " [with " + PatchFile + "]"
				if signed {
					extra += " (will fail: SigV4-signed body)"
				}
			}
			r.printf("    %s/%s -> %s: %s%s", j.Target, j.Name, j.OutDir, what, extra)
		}
	}
	for _, mw := range pl.Metas {
		if mw.ClonedFrom != "" {
			r.printf("  meta: %s (new: model %s, allowlists from %s, source recorded)", mw.Path, mw.Model, mw.ClonedFrom)
		} else {
			r.printf("  meta: %s -> source recorded, if all %d of its cases record (%d selected)", mw.Path, mw.Cases, len(mw.Jobs))
		}
	}
}
