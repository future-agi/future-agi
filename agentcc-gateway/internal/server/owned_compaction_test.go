package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/redis/go-redis/v9"
)

// These regressions use existing APIs at 21712f4e67, so their red must be an
// assertion, not a missing-symbol build failure. All upstreams are loopback;
// Redis and execution belong to the guarded shared kit, never the local host.
const ownedCompactPrimary = "openai/o1-pro"
const ownedCompactFinal = "google/gemini-3.5-flash"

// Intercept the real billing boundary, not Reserve/Begin's implementation. A
// simulated dead caller returns an ownership refusal after another worker has
// recovered its journal; it cannot dispatch or mutate that terminal record.
type ownedCompactBoundary struct {
	*creditsplugin.Plugin
	before, after func(*models.RequestContext, string) error
}

func (b *ownedCompactBoundary) BeginInclusive(rc *models.RequestContext, id string) error {
	if b.before != nil {
		if err := b.before(rc, id); err != nil {
			return err
		}
	}
	if err := b.Plugin.BeginInclusive(rc, id); err != nil {
		return err
	}
	if b.after != nil {
		return b.after(rc, id)
	}
	return nil
}

type ownedCompactResult struct {
	money         ownedR6Money
	log           []models.Attempt
	calls, status int
	crashed       bool
	crashID       string
	crashErr      error
}

// Eighteen clean failures precede the last retry. With a fallback all nineteen
// primary calls fail and the twentieth dispatch uses a different model/card.
func ownedCompactRun(t *testing.T, surface string, stream, fallback, unknown bool, crash string) ownedCompactResult {
	t.Helper()
	g, store, client, prefix := ownedR4Gateway(t)
	cfg := managedOptIn()
	cfg.Routing = &tenant.RoutingConfig{Retry: &tenant.RetryConfig{Enabled: true, MaxRetries: 18, InitialDelay: "1ms", MaxDelay: "1ms", Multiplier: 1, OnStatusCodes: []int{500, 502, 503}}}
	if fallback {
		cfg.Routing.FallbackEnabled = true
		cfg.Routing.ModelFallbacks = map[string][]string{ownedCompactPrimary: {ownedCompactFinal}}
	}
	g.tenants.Set("org-m", cfg)
	var result ownedCompactResult
	var capture bool
	g.srv.handlers.hookObserver = func(hook, _ string, rc *models.RequestContext) {
		if hook == hookBeginAttempt && !capture {
			capture = true
			rc.AddFinalizer(func() { result.log = rc.AttemptsSnapshot() })
		}
	}
	boundary := &ownedCompactBoundary{Plugin: g.credits}
	crashAt := 19
	if fallback {
		crashAt = 20
	}
	recoverAtBoundary := func(rc *models.RequestContext, id string) error {
		if id != models.AttemptID(rc.RequestID, crashAt) {
			return nil
		}
		result.crashed, result.crashID = true, id
		// The reservation must exist independently of the not-yet-added log row.
		raw, err := client.Redis().Get(context.Background(), prefix+"mc:{org-m}:owned:"+rc.RequestID).Bytes()
		var journal billing.OwnedRequest
		if err == nil {
			err = json.Unmarshal(raw, &journal)
		}
		if a, ok := journal.Attempts[id]; err == nil && (!ok || a.Dispatched != (crash == "after-begin")) {
			err = fmt.Errorf("crash financial identity missing or wrong dispatch state: exists=%t dispatched=%t", ok, a.Dispatched)
		}
		if err == nil {
			err = client.Redis().ZAdd(context.Background(), prefix+"mc:{org-m}:recovery", redis.Z{Score: 0, Member: rc.RequestID}).Err()
		}
		if err == nil {
			_, err = store.RecoverInclusive(context.Background(), "org-m", rc.RequestID)
		}
		result.crashErr = err
		return billing.ErrOwnership
	}
	if crash == "before-begin" {
		boundary.before = recoverAtBoundary
	} else if crash == "after-begin" {
		boundary.after = recoverAtBoundary
	}
	g.srv.handlers.SetBilling(boundary)
	var calls atomic.Int32
	answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
		n := calls.Add(1)
		if model == ownedCompactPrimary && (fallback || n < 19) {
			ownedR6Rejected(w)
			return
		}
		if unknown {
			panic(http.ErrAbortHandler)
		}
		r601Response("0.00005", stream)(w, r, model)
	})
	g.managed.responseWith.Store(&answer)
	g.managed.streamWith.Store(&answer)
	result.status = sendBillabilityBody(t, g, surface, ownedR6Body(ownedCompactPrimary, stream))
	result.calls = int(calls.Load())
	result.money = ownedR6Read(t, prefix)
	return result
}

func ownedCompactAssertMoney(t *testing.T, r ownedCompactResult, want int64, reservations, dispatched int) {
	t.Helper()
	m := r.money
	if m.spent != want || m.held != 0 || len(m.rows) != 1 || m.journal.Terminal == "" {
		t.Fatalf("owned compaction did not settle exactly: spent=%d want=%d held=%d rows=%d terminal=%t", m.spent, want, m.held, len(m.rows), m.journal.Terminal != "")
	}
	if fmt.Sprint(m.rows[0].Values["billed_micros"]) != fmt.Sprint(want) || m.journal.Record.BilledMicros != want {
		t.Fatal("financial outbox and retained receipt disagree")
	}
	if len(m.journal.Attempts) != reservations || len(m.journal.Record.Attempts) != dispatched {
		t.Fatalf("incomplete durable evidence: reservations=%d want=%d summaries=%d want=%d", len(m.journal.Attempts), reservations, len(m.journal.Record.Attempts), dispatched)
	}
	seen := map[string]bool{}
	var sum int64
	for _, s := range m.journal.Record.Attempts {
		if seen[s.AttemptID] || s.AttemptID == "" {
			t.Fatal("duplicate or empty financial identity")
		}
		seen[s.AttemptID] = true
		a, ok := m.journal.Attempts[s.AttemptID]
		if !ok || !a.Dispatched || s.Model != a.Model || s.CredentialType != "managed" {
			t.Fatal("summary is not bound to its immutable financial identity")
		}
		sum += s.BilledMicros
	}
	if sum != want {
		t.Fatalf("complete evidence sum=%d want=%d", sum, want)
	}
	for n := 1; n <= reservations; n++ {
		id := models.AttemptID(m.journal.Record.RequestID, n)
		a, ok := m.journal.Attempts[id]
		if !ok || a.Dispatched != (n <= dispatched) || (a.Dispatched && !seen[id]) {
			t.Fatalf("missing immutable dispatch sequence %d", n)
		}
	}
}

func ownedCompactAssertLog(t *testing.T, r ownedCompactResult) {
	t.Helper()
	if len(r.log) != models.MaxAttempts {
		t.Fatalf("public log length=%d want=%d", len(r.log), models.MaxAttempts)
	}
	seen := map[string]bool{}
	for i, a := range r.log {
		if a.Index != i+1 || a.AttemptID != models.AttemptID(r.money.journal.Record.RequestID, i+1) {
			t.Fatal("public log is not bounded and contiguous")
		}
		// Inspect the wire contract without requiring a new Go symbol at red.
		data, err := json.Marshal(a)
		var wire struct {
			FinancialID string `json:"financial_id"`
		}
		if err != nil || json.Unmarshal(data, &wire) != nil || wire.FinancialID == "" || seen[wire.FinancialID] {
			t.Fatal("public record lost its immutable financial_id")
		}
		seen[wire.FinancialID] = true
		j, ok := r.money.journal.Attempts[wire.FinancialID]
		if !ok || j.Model != a.Model || !j.Dispatched {
			t.Fatal("compacted record points at another model's journal identity")
		}
	}
	last := r.log[len(r.log)-1]
	data, _ := json.Marshal(last)
	var wire struct {
		FinancialID string `json:"financial_id"`
	}
	_ = json.Unmarshal(data, &wire)
	if wire.FinancialID != models.AttemptID(r.money.journal.Record.RequestID, r.calls) {
		t.Fatal("serving financial identity was renumbered")
	}
}

func TestOwnedCompactionMoreThan16SettlesExactly(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", surface, stream), func(t *testing.T) {
				r := ownedCompactRun(t, surface, stream, false, false, "")
				if r.status != http.StatusOK || r.calls != 19 {
					t.Fatalf("status=%d calls=%d", r.status, r.calls)
				}
				ownedCompactAssertMoney(t, r, 53, 19, 19)
				ownedCompactAssertLog(t, r)
			})
		}
	}
}

func TestOwnedCompactionDifferentModelsNeverConflict(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", surface, stream), func(t *testing.T) {
				r := ownedCompactRun(t, surface, stream, true, false, "")
				if r.status != http.StatusOK || r.calls != 20 {
					t.Fatalf("differing-model dispatch refused: status=%d calls=%d", r.status, r.calls)
				}
				ownedCompactAssertMoney(t, r, 53, 20, 20)
				ownedCompactAssertLog(t, r)
				for id, a := range r.money.journal.Attempts {
					want := ownedCompactPrimary
					if id == models.AttemptID(r.money.journal.Record.RequestID, 20) {
						want = ownedCompactFinal
					}
					if a.Model != want {
						t.Fatalf("financial id changed model: got=%s want=%s", a.Model, want)
					}
				}
			})
		}
	}
}

func TestOwnedCompactionRecoveryParityBothModes(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", surface, stream), func(t *testing.T) {
				live := ownedCompactRun(t, surface, stream, false, true, "")
				crashed := ownedCompactRun(t, surface, stream, false, true, "after-begin")
				if !crashed.crashed || crashed.crashErr != nil || crashed.calls != 18 || live.calls != 19 {
					t.Fatalf("recovery boundary missed: hit=%t err=%v calls=%d/%d", crashed.crashed, crashed.crashErr, crashed.calls, live.calls)
				}
				id := models.AttemptID(live.money.journal.Record.RequestID, 19)
				want := live.money.journal.Attempts[id].Micros
				if want <= 0 {
					t.Fatal("missing last dispatch hold")
				}
				ownedCompactAssertMoney(t, live, want, 19, 19)
				ownedCompactAssertMoney(t, crashed, want, 19, 19)
			})
		}
	}
}

func TestOwnedCompactionCrashBetweenReserveAndRecord(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", surface, stream), func(t *testing.T) {
				r := ownedCompactRun(t, surface, stream, true, false, "before-begin")
				if !r.crashed || r.crashErr != nil || r.calls != 19 {
					t.Fatalf("reserve/record crash boundary missed: hit=%t err=%v calls=%d", r.crashed, r.crashErr, r.calls)
				}
				ownedCompactAssertMoney(t, r, 0, 20, 19)
				a, ok := r.money.journal.Attempts[r.crashID]
				if !ok || a.Dispatched || a.Model != ownedCompactFinal || a.Micros <= 0 {
					t.Fatal("undispatched financial reservation was renamed, lost or billed")
				}
			})
		}
	}
}
