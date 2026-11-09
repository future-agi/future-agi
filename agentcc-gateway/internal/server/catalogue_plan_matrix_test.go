package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"maps"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	cat2MatrixGem   = "google/gemini-3.5-flash"
	cat2MatrixPeer  = "google/gemini-4-argon"
	cat2MatrixMini  = "openai/gpt-5.4-mini"
	cat2MatrixToken = "openai/o1-pro"
	cat2MatrixOR    = "openrouter/"
)

// The plan sources a request's model fallback plan can come from.
const (
	cat2SourceRequest = "request" // the body's models[] (G26)
	cat2SourceOrg     = "org"     // the org's routing.model_fallbacks
	cat2SourceNode    = "node"    // the node's config.yaml routing.model_fallbacks
)

type cat2PlanCell struct {
	source                                   string
	primary, runner, request, key, inherited string
	on, stream, fail                         bool
	// configured are the keyed chains the cell installs: on the node for the
	// node source, else on the org. declared is the request's models[]; it is
	// nil unless the source is the request.
	configured map[string][]string
	declared   []string
}

func cat2MatrixUpstream(name string) string { return strings.TrimPrefix(name, cat2MatrixOR) }
func cat2MatrixBare(name string) string {
	_, bare, _ := strings.Cut(cat2MatrixUpstream(name), "/")
	return bare
}
func cat2MatrixInclusive(name string) bool { return strings.Contains(name, "gemini") }

// cat2MatrixKeyed is the configured chain {identity named by key: plan}.
// explicit-empty exercises presence, not length: the requested entry is
// present and empty, and a conflicting chain sits under the upstream. For
// auto the requested entry is the chosen model's key.
func cat2MatrixKeyed(primary, runner, request, key string, plan []string) map[string][]string {
	keys := map[string]string{
		"requested": request, "upstream": cat2MatrixUpstream(primary),
		"prefixed-upstream": primary, "bare": cat2MatrixBare(primary),
		"runner": runner, "runner-upstream": cat2MatrixUpstream(runner),
	}
	if key == "explicit-empty" {
		name := request
		if routing.IsAutoModel(name) {
			name = primary
		}
		out := map[string][]string{cat2MatrixUpstream(primary): plan}
		out[name] = []string{}
		return out
	}
	k, found := keys[key]
	if !found {
		k = strings.TrimPrefix(key, "auto-key=")
	}
	return map[string][]string{k: plan}
}

// cat2MatrixConflict is the other member of inherited's pair: a configured
// chain the request's own models[] must replace.
func cat2MatrixConflict(same, crossing, inherited string) string {
	if inherited == same {
		return crossing
	}
	return same
}

// newCat2PlanCell builds one cell of source. The org and node sources install
// the keyed chain {key: [inherited]}. The request source declares
// models:[inherited] and installs a conflicting org chain under the same key,
// which the declaration replaces. Its explicit-empty form declares only the
// request's own identity (the primary candidate for auto): the declaration
// filters to an empty plan, and an inheritable org chain under the upstream
// must still not run.
func newCat2PlanCell(source, primary, runner, request, key, inherited, same, crossing string, on, stream, fail bool) cat2PlanCell {
	c := cat2PlanCell{source: source, primary: primary, runner: runner, request: request, key: key, inherited: inherited, on: on, stream: stream, fail: fail}
	switch {
	case source != cat2SourceRequest:
		c.configured = cat2MatrixKeyed(primary, runner, request, key, []string{inherited})
	case key == "explicit-empty":
		self := request
		if routing.IsAutoModel(request) {
			self = primary
		}
		c.declared = []string{self}
		c.configured = map[string][]string{cat2MatrixUpstream(primary): {inherited}}
	default:
		c.declared = []string{inherited}
		c.configured = cat2MatrixKeyed(primary, runner, request, key, []string{cat2MatrixConflict(same, crossing, inherited)})
	}
	return c
}

type cat2PlanExpect struct {
	status        int
	code, chosen  string
	chain, sent   []string
	autoFallbacks string
}

// expected is an independent dispatch oracle, not a call to admission, auto
// ranking or a production fallback lookup. Fixture costs put primary first.
// A request's plan has one source: its models[] (without its own identity),
// else the auto runners-up, else the configured chain by presence under the
// chosen identity, then under the upstream it is sent as. Auto aliases never
// identify a dispatched hop; an auto-selected plan overrides the configured
// chain, even when its only runner-up was filtered out.
func (c cat2PlanCell) expected() (e cat2PlanExpect) {
	e.chosen = c.request
	upstream := cat2MatrixUpstream(c.primary)
	lookup := func(name, upstream string) []string {
		if plan, present := c.configured[name]; present {
			return plan
		}
		return c.configured[upstream]
	}
	crosses := func(candidate string, plan []string) bool {
		for _, next := range plan {
			if cat2MatrixInclusive(next) != cat2MatrixInclusive(candidate) {
				return true
			}
		}
		return false
	}
	if routing.IsAutoModel(c.request) {
		var pool []string
		for _, candidate := range []string{c.primary, c.runner} {
			// Pool admission judges only the caller's declared plan, and
			// only while the mixed-catalogue fence is on.
			if (!c.on && cat2MatrixInclusive(candidate)) || (c.on && crosses(candidate, c.declared)) {
				continue
			}
			pool = append(pool, candidate)
		}
		if len(pool) == 0 {
			e.status, e.code = http.StatusBadRequest, "no_auto_candidate"
			// Omega aliases have an earlier empty-pool contract. A pool
			// rejected by the declared-plan fence still uses auto's code.
			if !c.on && cat2MatrixInclusive(c.primary) && cat2MatrixInclusive(c.runner) && routing.IsOmegaAlias(c.request) {
				e.code = "omega_default_unroutable"
			}
			e.chosen = ""
			return e
		}
		e.chosen, upstream = pool[0], cat2MatrixUpstream(pool[0])
		if len(pool) > 1 {
			var runners []string
			for _, candidate := range pool[1:] {
				if cat2MatrixInclusive(candidate) == cat2MatrixInclusive(e.chosen) {
					runners = append(runners, candidate)
				}
			}
			e.chain = runners
			e.autoFallbacks = strings.Join(runners, ",")
		} else {
			e.chain = lookup(e.chosen, upstream)
		}
	} else {
		e.chain = lookup(e.chosen, upstream)
	}
	if c.declared != nil {
		e.chain = nil
		for _, next := range c.declared {
			if next != e.chosen && next != upstream {
				e.chain = append(e.chain, next)
			}
		}
	}
	if !c.on && cat2MatrixInclusive(e.chosen) {
		e.status, e.code = http.StatusForbidden, "permission_denied"
		return e
	}
	for _, next := range e.chain {
		if c.on && cat2MatrixInclusive(next) != cat2MatrixInclusive(e.chosen) {
			e.status, e.code = http.StatusForbidden, "model_not_permitted"
			return e
		}
	}
	e.status, e.sent = http.StatusOK, []string{upstream}
	if !c.fail {
		return e
	}
	for _, next := range e.chain {
		if !c.on && cat2MatrixInclusive(next) {
			continue
		}
		e.sent = append(e.sent, cat2MatrixUpstream(next))
		return e
	}
	e.status, e.code = http.StatusBadGateway, "provider_503"
	return e
}

// The structural invariant is: the mixed-catalogue plan judged at admission is
// exactly the chain dispatch would run, whichever source it came from. One
// table crosses the plan source (request models[], org chains, node chains)
// with all request identities, chain-key identities, streaming modes, primary
// outcomes, activation states and pool families. Both price-family directions
// and both inherited-chain families are included. Every AutoModelNames entry
// is used, including omega-default, rather than a hand-maintained alias subset.
// A mixed plan must be refused before any upstream call, hold or charge: every
// refused cell, streaming or not, leaves the org's charge ledger exactly as it
// found it (cat2ChargeLedger). The provider-failover cells cross the same plan
// sources and streaming modes with a first target that fails over to another
// target with its own model_override before the plan is resolved
// (catalogue_plan_failover_test.go).
func TestCAT2PlanDispatchMatrix(t *testing.T) {
	var cells []cat2PlanCell
	keys := []string{"requested", "upstream", "prefixed-upstream", "bare", "runner", "runner-upstream", "explicit-empty"}
	for _, alias := range routing.AutoModelNames {
		keys = append(keys, "auto-key="+alias)
	}
	for _, family := range []struct{ primary, same, crossing string }{
		{cat2MatrixGem, cat2MatrixPeer, cat2MatrixToken},
		{cat2MatrixMini, cat2MatrixToken, cat2MatrixGem},
	} {
		primary := cat2MatrixOR + family.primary
		forms := []string{family.primary, primary, cat2MatrixBare(family.primary)}
		forms = append(forms, routing.AutoModelNames...)
		for _, source := range []string{cat2SourceRequest, cat2SourceOrg, cat2SourceNode} {
			for _, runner := range []string{family.same, family.crossing} {
				for _, inherited := range []string{family.same, family.crossing} {
					for _, request := range forms {
						for _, key := range keys {
							for _, on := range []bool{false, true} {
								for _, stream := range []bool{false, true} {
									for _, fail := range []bool{false, true} {
										cells = append(cells, newCat2PlanCell(source, primary, cat2MatrixOR+runner, request, key, inherited, family.same, family.crossing, on, stream, fail))
									}
								}
							}
						}
					}
				}
			}
		}
		// Skipping configured lookup at pool admission must not skip the
		// caller's own declared mixed plan, even with an explicit-empty org
		// entry.
		for _, request := range forms {
			for _, stream := range []bool{false, true} {
				for _, fail := range []bool{false, true} {
					c := newCat2PlanCell(cat2SourceOrg, primary, cat2MatrixOR+family.same, request, "explicit-empty", family.crossing, family.same, family.crossing, true, stream, fail)
					c.source, c.declared = cat2SourceRequest, []string{family.crossing}
					cells = append(cells, c)
				}
			}
		}
	}
	// How many refused cells checked their charge ledger, by streaming mode
	// (index 1 streams). The parent's cleanup runs after every parallel cell.
	var refusedChecked [2]atomic.Int64
	var failoverChecked atomic.Int64
	t.Cleanup(func() {
		t.Logf("charge ledger checked unchanged on refused cells: stream=false %d, stream=true %d", refusedChecked[0].Load(), refusedChecked[1].Load())
		t.Logf("charge ledger checked unchanged on provider-failover cells: %d", failoverChecked.Load())
	})
	for _, c := range cells {
		name := fmt.Sprintf("source=%s/on=%t/primary=%s/runner=%s/req=%s/key=%s/inherited=%s/stream=%t/fail=%t/declared=%s", c.source, c.on, c.primary, c.runner, c.request, c.key, c.inherited, c.stream, c.fail, strings.Join(c.declared, "+"))
		t.Run(name, func(t *testing.T) {
			// Every cell owns its config, catalogue, ledger and loopback
			// servers. Use the runner's bounded -parallel budget so the full
			// matrix remains practical under the race detector on cloud VMs.
			t.Parallel()
			if c.check(t) {
				mode := 0
				if c.stream {
					mode = 1
				}
				refusedChecked[mode].Add(1)
			}
		})
	}
	for _, c := range cat2FailoverCells() {
		t.Run(c.name(), func(t *testing.T) {
			t.Parallel()
			c.check(t)
			failoverChecked.Add(1)
		})
	}
}

// cat2ChargeLedger is what a cell's charge ledger holds for its org: the
// account (granted and its version, spent, held), every settlement entry, and
// the outbox's pending count and push flags.
type cat2ChargeLedger struct {
	account              billing.Account
	entries              []map[string]string
	pending              int64
	needsPush, outboxOrg bool
}

func cat2ChargeLedgerOf(t *testing.T, owner *r601Owner) cat2ChargeLedger {
	t.Helper()
	ctx := context.Background()
	account, err := owner.Account(ctx, "org-m")
	if err != nil {
		t.Fatal(err)
	}
	pending, err := owner.OutboxPending(ctx, "org-m")
	if err != nil {
		t.Fatal(err)
	}
	return cat2ChargeLedger{account: account, entries: owner.Outbox("org-m"), pending: pending, needsPush: owner.NeedsPush("org-m"), outboxOrg: owner.HasOutboxOrg("org-m")}
}

func (l cat2ChargeLedger) equal(o cat2ChargeLedger) bool {
	return l.account == o.account && l.pending == o.pending && l.needsPush == o.needsPush && l.outboxOrg == o.outboxOrg &&
		slices.EqualFunc(l.entries, o.entries, func(a, b map[string]string) bool { return maps.Equal(a, b) })
}

// releaseCat2Gateway ends one cell's gateway. The matrix runs every cell's
// gateway in one test binary, and two things kept each finished cell's whole
// gateway (catalogue and model database, about 3 MB, several times that under
// the race detector) reachable:
//   - goroutines that outlive the cell: Server.Shutdown stops the async
//     worker pool, but not the batch processor's cleanup loop, stopped here
//     (an end-of-run goroutine dump of the 4,928-cell node slice found one
//     loop per cell);
//   - the testing package, which keeps every finished parallel subtest, and
//     the cleanups it ran, until the matrix ends: an upstream's Close cleanup
//     reaches its handler, the handler the upstream, and the upstream the
//     scripted answer, which captures g. Dropping the answers cuts that path
//     (the plain run's memory fell only when the matrix ended).
//
// check defers this rather than registering a t.Cleanup, so no finished
// subtest holds a closure over g.
func releaseCat2Gateway(t *testing.T, g *managedTestGateway) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := g.srv.Shutdown(ctx); err != nil {
		t.Errorf("gateway shutdown: %v", err)
	}
	g.srv.batchProcessor.Stop()
	g.managed.responseWith.Store(nil)
	g.managed.streamWith.Store(nil)
	g.operator.responseWith.Store(nil)
	g.operator.streamWith.Store(nil)
}

// HTTP and ledger assertions are based on the r11 independent verifier probe2
// inputs. Unlike that diagnostic probe, the matrix pins the chosen primary and
// exact calls, so a silently flipped crossing pool cannot pass by recomputing
// its expectation from whichever candidate the server happened to choose.
// check reports whether the cell was refused, and so checked its charge ledger.
func (c cat2PlanCell) check(t *testing.T) (refused bool) {
	t.Helper()
	g, owner, _ := cat2OperatorGateway(t, c.on)
	defer releaseCat2Gateway(t, g)
	cfg := managedOptIn()
	cfg.Routing = &tenant.RoutingConfig{
		FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false},
		Auto: &tenant.AutoRoutingConfig{Preference: "cost", Candidates: []string{c.primary, c.runner}},
	}
	if c.source == cat2SourceNode {
		g.srv.handlers.modelFallbacks.Store(routing.NewModelFallbacks(maps.Clone(c.configured)))
	} else {
		cfg.Routing.ModelFallbacks = maps.Clone(c.configured)
	}
	g.tenants.Set("org-m", cfg)
	answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
		if c.fail && g.managed.calls() == 1 {
			w.WriteHeader(http.StatusServiceUnavailable)
			fmt.Fprint(w, `{"error":{"message":"synthetic primary failure","code":"provider_503"}}`)
			return
		}
		r601Response("0.0000095", c.stream)(w, r, model)
	})
	g.managed.responseWith.Store(&answer)
	g.managed.streamWith.Store(&answer)
	snaps := make(chan *models.TraceSnapshot, 1)
	g.srv.handlers.SetGenerationRecorder(func(s *models.TraceSnapshot) { snaps <- s })
	extra := ""
	if c.declared != nil {
		declared, err := json.Marshal(c.declared)
		if err != nil {
			t.Fatal(err)
		}
		extra = fmt.Sprintf(`,"models":%s`, declared)
	}
	body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"synthetic"}]%s}`, c.request, c.stream, extra)
	ts := httptest.NewServer(g.srv.httpServer.Handler)
	defer ts.Close()
	req, err := http.NewRequest(http.MethodPost, ts.URL+"/v1/chat/completions", strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+managedOrgKey)
	req.Header.Set("Content-Type", "application/json")
	before := cat2ChargeLedgerOf(t, owner)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil {
		t.Fatal(err)
	}
	var failure models.ErrorResponse
	if resp.StatusCode != http.StatusOK {
		if err := json.Unmarshal(raw, &failure); err != nil {
			t.Fatalf("invalid error envelope: %v", err)
		}
	}
	spent, held := settledAccount(t, g)
	g.managed.mu.Lock()
	sent := append([]string(nil), g.managed.models...)
	g.managed.mu.Unlock()
	e := c.expected()
	// Refusal before work: a refused plan made no upstream call, took no
	// hold and charged nothing; every served request drained its holds.
	if held != 0 || (len(e.sent) == 0 && spent != 0) {
		t.Errorf("money on refusal or hold not drained: spent=%d held=%d", spent, held)
	}
	// No customer charge for a call the gateway itself refuses: a refused
	// cell, streaming or not, leaves the whole charge ledger as it was before
	// the request (account, entries, outbox), not just spent and held at zero,
	// and reached no upstream at all.
	refused = len(e.sent) == 0
	if refused {
		after := cat2ChargeLedgerOf(t, owner)
		if !after.equal(before) {
			t.Errorf("charge ledger changed on refusal: stream=%t entries %d->%d spent %d->%d held %d->%d granted %d->%d version %d->%d pending %d->%d needs_push %t->%t",
				c.stream, len(before.entries), len(after.entries),
				before.account.SpentMicros, after.account.SpentMicros, before.account.HeldMicros, after.account.HeldMicros,
				before.account.GrantedMicros, after.account.GrantedMicros, before.account.GrantedVersion, after.account.GrantedVersion,
				before.pending, after.pending, before.needsPush, after.needsPush)
		}
		if n := g.operator.calls(); n != 0 {
			t.Errorf("upstream call on refusal: stream=%t operator calls=%d", c.stream, n)
		}
	}
	if resp.StatusCode != e.status || failure.Error.Code != e.code || !slices.Equal(sent, e.sent) {
		t.Fatalf("admission != dispatch: status=%d code=%q sent=%v; want status=%d code=%q sent=%v chosen=%q chain=%v spent=%d held=%d", resp.StatusCode, failure.Error.Code, sent, e.status, e.code, e.sent, e.chosen, e.chain, spent, held)
	}
	if e.status == http.StatusOK && routing.IsAutoModel(c.request) {
		select {
		case snap := <-snaps:
			// The decision records auto's own runners-up, not an inherited
			// single-candidate chain or the caller's models[].
			if snap.Metadata["auto_model"] != e.chosen || snap.Metadata["auto_fallbacks"] != e.autoFallbacks {
				t.Fatalf("auto decision model=%q fallbacks=%q; want %q %q", snap.Metadata["auto_model"], snap.Metadata["auto_fallbacks"], e.chosen, e.autoFallbacks)
			}
		case <-time.After(5 * time.Second):
			t.Fatal("no generation record")
		}
	}
	return refused
}
