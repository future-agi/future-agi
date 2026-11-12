package budget

import (
	"context"
	"math"
	"net/http/httptest"
	"os"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// These private fixtures qualify state transitions, never a provider protocol,
// identity verifier, enforced provider ceiling or production price source.
type capNativeBody struct{ raw []byte }

func (b *capNativeBody) Body() []byte { return append([]byte(nil), b.raw...) }
func (*capNativeBody) ChatRequest(context.Context) (*models.ChatCompletionRequest, string) {
	return nil, ""
}
func (*capNativeBody) ApplyChecks() error { return nil }

type capNativePolicy func(context.Context, *models.RequestContext) *models.APIError

func (f capNativePolicy) PrepareNative(ctx context.Context, rc *models.RequestContext) *models.APIError {
	return f(ctx, rc)
}

type capForeignAttempt struct{ NativeAttempt }

func capNativeFixture(t *testing.T, stream bool) (*models.RequestContext, *nativeControl, *Plugin, *capNativeBody) {
	t.Helper()
	rc := models.AcquireRequestContext()
	rc.RequestID, rc.Provider, rc.Model, rc.EndpointType = "synthetic", "synthetic-provider", "synthetic-model", "responses"
	rc.Metadata["org_id"], rc.Metadata["auth_key_id"], rc.Metadata["billing_mode"] = "org-test", "key-test", models.BillingModeBYOK
	rc.IsStream = stream
	now := time.Now().UTC()
	a := &Admission{org: "org-test", key: "key-test", nonce: "synthetic-server-nonce", deadline: now.Add(time.Minute), period: now.Truncate(24 * time.Hour), agentLimit: -1, keyLimit: -1, runLimit: -1, revision: 1}
	BindAdmission(rc, a)
	c := &nativeControl{admission: a, protocol: models.NativeResponses, authority: func() bool { return true }}
	c.qualify = func(_ *models.RequestContext, _ models.NativeRouteBinding, _ []byte) (nativeBound, bool) {
		return nativeBound{capMicros: 100, revision: "synthetic-prices-v1", kind: NativeCumulativeUsage, current: func() bool { return true }, meter: capSyntheticMeter}, true
	}
	bindNativeControl(rc, c)
	b := &capNativeBody{raw: []byte(`{"synthetic":true}`)}
	binding := models.NativeRouteBinding{CanonicalModel: rc.Model, SentModel: rc.Model, ModelRevision: "m1", ProviderID: rc.Provider, ProviderRevision: "p1", ProviderRouteID: "r", RouteRevision: "r1", EndpointType: "responses", EndpointID: "endpoint", UpstreamProvider: "synthetic-upstream", CredentialType: models.CredentialBYOK, Protocol: models.NativeResponses, Action: "create", Stream: stream, CapabilityRevision: "c1"}
	rc.Billing.EndpointID = binding.EndpointID
	rc.Routing.ComputerUseBinding = binding
	if err := AttachNative(rc, b, binding); err != nil {
		t.Fatal(err.Code)
	}
	p := &Plugin{}
	p.SetNativePolicyPreparer(capNativePolicy(func(context.Context, *models.RequestContext) *models.APIError { return nil }))
	t.Cleanup(rc.Release)
	return rc, c, p, b
}

func capSyntheticMeter(previous *models.Usage, e NativeUsageEvent) (*models.Usage, bool) {
	if e.Kind != NativeCumulativeUsage {
		return nil, false
	}
	return models.MergeUsage(previous, e.Usage), e.Complete && e.Usage != nil && e.Usage.HasPromptTokens() && e.Usage.HasCompletionTokens()
}

func capNativeAttemptFixture(t *testing.T, stream bool) (*models.RequestContext, *nativeControl, NativeAttempt, *int) {
	rc, c, p, b := capNativeFixture(t, stream)
	if e := p.reserveCaps(context.Background(), rc); e != nil {
		t.Fatal(e.Code)
	}
	ctx, cancel := context.WithDeadline(context.Background(), c.admission.deadline)
	t.Cleanup(cancel)
	if e := PrepareNativeDispatch(ctx, rc, b, c.binding); e != nil {
		t.Fatal(e.Code)
	}
	if e := ClaimPreparedNative(rc, rc.Provider, rc.Model); e != nil {
		t.Fatal(e.Code)
	}
	a := rc.AddAttempt(models.Attempt{Provider: rc.Provider, Model: rc.Model, CredentialType: models.CredentialBYOK, InFlight: true})
	calls := new(int)
	h, e := BindNativeAttempt(ctx, rc, a.FinancialID, func(u *models.Usage, g models.GenerationEvidence, failure *models.APIError) {
		*calls++
		rc.UpdateAttempt(a.AttemptID, func(a *models.Attempt) {
			a.InFlight = false
			a.Usage = u
			a.Generation = g
			a.CostSource = models.CostSourceCatalog
			a.UpstreamCostMicros = 10
		})
		cancel()
	})
	if e != nil {
		t.Fatal(e.Code)
	}
	return rc, c, h, calls
}
func capNativeEvent(a NativeAttempt, n int, complete bool) NativeUsageEvent {
	return NativeUsageEvent{FinancialID: a.FinancialID(), Generation: models.GenerationEvidence{ID: "synthetic-generation", ServedModel: "synthetic-model", Provider: "synthetic-upstream"}, Usage: models.MeasuredUsage(n, n), Kind: NativeCumulativeUsage, Sequence: 1, Complete: complete}
}

func TestNativeC1ClosedDependencies(t *testing.T) {
	for _, protocol := range []models.NativeProtocol{models.NativeMessages, models.NativeResponses, models.NativeGenAI} {
		rc := models.AcquireRequestContext()
		cap := 0.01
		rc.Agent.MaxCostUSD = &cap
		r := httptest.NewRequest("POST", "/v1/messages", nil)
		if e := BindNativeAdmission(r, rc, nil, protocol); e == nil || e.Code != "budget_state_unavailable" {
			t.Fatal("caller-only native escaped missing verifier")
		}
		if RequestAdmission(rc) != nil || rc.Request != nil {
			t.Fatal("manufactured authority or chat request")
		}
		rc.Release()
	}
}

func TestNativeC1QuoteOrderAndMutation(t *testing.T) {
	rc, c, p, b := capNativeFixture(t, false)
	p.SetNativePolicyPreparer(capNativePolicy(func(context.Context, *models.RequestContext) *models.APIError {
		b.raw = []byte(`{"filtered":true}`)
		return nil
	}))
	if e := p.reserveCaps(context.Background(), rc); e != nil {
		t.Fatal(e.Code)
	}
	q, ok := NativeQuoteFor(rc)
	if !ok {
		t.Fatal("missing quote")
	}
	if amount, ready := q.CapMicros(); !ready || amount != 100 {
		t.Fatal("nil-request quote became free")
	}
	if string(c.quote.body) != string(b.raw) {
		t.Fatal("quoted before policy")
	}
	b.raw[2] = 'X'
	if _, ok := NativeQuoteFor(rc); ok {
		t.Fatal("late mutation reused quote")
	}
	if e := ClaimPreparedNative(rc, rc.Provider, rc.Model); e == nil {
		t.Fatal("unprepared claim")
	}
}

func TestNativeC1MissingQualificationAndRange(t *testing.T) {
	for _, n := range []int64{-1, 0, 1_000_000_000_000_001, math.MaxInt64} {
		t.Run(time.Duration(n).String(), func(t *testing.T) {
			rc, c, p, _ := capNativeFixture(t, false)
			c.qualify = func(*models.RequestContext, models.NativeRouteBinding, []byte) (nativeBound, bool) {
				return nativeBound{capMicros: n, revision: "synthetic", kind: NativeCumulativeUsage, current: func() bool { return true }, meter: capSyntheticMeter}, true
			}
			if e := p.reserveCaps(context.Background(), rc); e == nil {
				t.Fatal("invalid/free-unqualified bound admitted")
			}
		})
	}
	rc, c, p, _ := capNativeFixture(t, false)
	c.qualify = nil
	if e := p.reserveCaps(context.Background(), rc); e == nil {
		t.Fatal("missing qualification admitted")
	}
}

func TestNativeC1ClaimBarriers(t *testing.T) {
	for _, missing := range []string{"policy", "authority", "run", "deadline", "day", "price", "mode", "nonce"} {
		t.Run(missing, func(t *testing.T) {
			rc, c, p, b := capNativeFixture(t, false)
			if missing == "policy" {
				p.SetNativePolicyPreparer(nil)
				if p.reserveCaps(context.Background(), rc) == nil {
					t.Fatal("missing policy")
				}
				return
			}
			if e := p.reserveCaps(context.Background(), rc); e != nil {
				t.Fatal(e.Code)
			}
			ctx, cancel := context.WithDeadline(context.Background(), c.admission.deadline)
			defer cancel()
			if e := PrepareNativeDispatch(ctx, rc, b, c.binding); e != nil {
				t.Fatal(e.Code)
			}
			switch missing {
			case "authority":
				c.authority = func() bool { return false }
			case "run":
				c.runRequired = true
			case "deadline":
				c.admission.deadline = time.Now().Add(-time.Second)
			case "day":
				c.admission.period = c.admission.period.Add(-24 * time.Hour)
			case "price":
				rc.Billing.EndpointSet = []models.EndpointRef{{ID: "changed"}}
			case "mode":
				rc.Metadata["billing_mode"] = models.BillingModeManaged
			case "nonce":
				c.admission.nonce = "changed"
			}
			if e := ClaimPreparedNative(rc, rc.Provider, rc.Model); e == nil {
				t.Fatal("missing barrier admitted")
			}
			if c.admission.attempted.Load() {
				t.Fatal("claimed after mismatch")
			}
		})
	}
}

func TestNativeC1SingleClaimAndOwnedCopies(t *testing.T) {
	rc, c, p, b := capNativeFixture(t, true)
	if e := p.reserveCaps(context.Background(), rc); e != nil {
		t.Fatal(e.Code)
	}
	ctx, cancel := context.WithDeadline(context.Background(), c.admission.deadline)
	defer cancel()
	if e := PrepareNativeDispatch(ctx, rc, b, c.binding); e != nil {
		t.Fatal(e.Code)
	}
	var wg sync.WaitGroup
	var mu sync.Mutex
	admitted := 0
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if ClaimPreparedNative(rc, rc.Provider, rc.Model) == nil {
				mu.Lock()
				admitted++
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	if admitted != 1 {
		t.Fatal("claim is not exclusive")
	}
}

func TestNativeC1FinalOnlyAndBillingGate(t *testing.T) {
	rc, c, a, finishes := capNativeAttemptFixture(t, true)
	e := capNativeEvent(a, 2, false)
	if err := ObserveNative(a, e); err != nil {
		t.Fatal(err.Code)
	}
	if *finishes != 0 || a.Context().Err() != nil {
		t.Fatal("first chunk ended stream")
	}
	if _, known := c.admission.ActualSpend(rc); known {
		t.Fatal("partial usage settled")
	}
	e.Sequence, e.Complete = 2, true
	if err := ObserveNative(a, e); err != nil {
		t.Fatal(err.Code)
	}
	e.Usage.PromptTokens = 900
	if err := EndNative(a, NativeCompleted); err != nil {
		t.Fatal(err.Code)
	}
	if *finishes != 1 {
		t.Fatal("finish count")
	}
	if _, known := c.admission.ActualSpend(rc); known {
		t.Fatal("settled before AfterBilling")
	}
	p := &Plugin{}
	if result := p.FinishNativeCaps(context.Background(), rc); result.State != NativeSettled {
		t.Fatal(result.State)
	}
	if amount, known := c.admission.ActualSpend(rc); !known || amount != 10 {
		t.Fatal("final evidence lost")
	}
	_ = EndNative(a, NativeCompleted)
	if *finishes != 1 {
		t.Fatal("duplicate finish")
	}
}

func TestNativeC1TerminalRefusals(t *testing.T) {
	for _, variant := range []string{"no-terminal", "foreign", "generation", "estimated", "negative", "decrease", "delta", "after-end", "over-bound", "cache"} {
		t.Run(variant, func(t *testing.T) {
			rc, c, a, _ := capNativeAttemptFixture(t, true)
			e := capNativeEvent(a, 2, true)
			switch variant {
			case "no-terminal":
				e.Complete = false
			case "foreign":
				e.FinancialID = "foreign"
			case "generation":
				e.Generation.ServedModel = "foreign"
			case "estimated":
				e.Usage.Estimated = true
			case "negative":
				e.Usage.PromptTokens = -1
			case "decrease":
				first := capNativeEvent(a, 3, false)
				_ = ObserveNative(a, first)
				e.Sequence = 2
			case "delta":
				e.Kind = NativeDeltaUsage
			}
			_ = ObserveNative(a, e)
			_ = EndNative(a, NativeCompleted)
			if variant == "after-end" {
				_ = ObserveNative(a, e)
			}
			if variant == "over-bound" {
				for _, at := range rc.AttemptsSnapshot() {
					rc.UpdateAttempt(at.AttemptID, func(a *models.Attempt) { a.UpstreamCostMicros = 101 })
				}
			}
			if variant == "cache" {
				rc.Flags.CacheHit = true
			}
			_ = (&Plugin{}).FinishNativeCaps(context.Background(), rc)
			if _, known := c.admission.ActualSpend(rc); known {
				t.Fatal("unsafe actual accepted")
			}
		})
	}
}

func TestNativeC1DailyPendingAndPostGate(t *testing.T) {
	if os.Getenv("TEST_REDIS_ADDR") == "" {
		t.Fatal("isolated Redis required")
	}
	db, _ := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	client, err := redisstate.NewClient(redisstate.Config{Address: os.Getenv("TEST_REDIS_ADDR"), DB: db, Timeout: time.Second})
	if err != nil {
		t.Fatal("Redis unavailable")
	}
	defer client.Close()
	for _, uncertain := range []bool{false, true} {
		rc, c, p, _ := capNativeFixture(t, false)
		a := c.admission
		a.keyLimit, a.keyActivation = 100, a.period.Format(time.RFC3339)
		p.SetCapStore(redisstate.NewCapHoldStore(client, "test:c1:"+strconv.FormatInt(time.Now().UnixNano(), 10)+":"))
		if e := p.reserveCaps(context.Background(), rc); e != nil {
			t.Fatal(e.Code)
		}
		v, ok := p.capStates.Load(rc)
		if !ok {
			t.Fatal("reserve lacked registered cleanup")
		}
		st := v.(*capHold)
		a.nonce = "changed-after-reserve" // cleanup must use the original durable binding
		p.finishCaps(rc)
		if st.done.Load() {
			t.Fatal("post completed before billing gate")
		}
		if uncertain {
			st.nativeResolved = false
			InvalidateNative(rc)
		}
		result := p.FinishNativeCaps(context.Background(), rc)
		if uncertain && result.State != NativeFenced {
			t.Fatal("unresolved reserve refunded")
		}
		if !uncertain && result.State != NativeSettled {
			t.Fatal("known undispatched cleanup failed")
		}
		if uncertain {
			res, err := p.capStore.Reserve(context.Background(), a.org, nativeDay(a.period), "next-nonce", a.revision, st.buckets, a.deadline.Add(time.Hour))
			if err == nil && res.Status == "ok" {
				t.Fatal("unknown hold released room")
			}
		}
		rc.RunFinalizers()
	}
}

func TestNativeC1ForeignHandlesAndRelease(t *testing.T) {
	rc, _, a, calls := capNativeAttemptFixture(t, false)
	if ObserveNative(capForeignAttempt{a}, capNativeEvent(a, 1, true)) == nil {
		t.Fatal("embedded handle accepted")
	}
	copy := a.Body()
	copy[0] = 'X'
	if a.Body()[0] == 'X' {
		t.Fatal("body alias")
	}
	rc.RunFinalizers()
	if *calls != 1 {
		t.Fatal("fallback did not end attempt")
	}
	if a.Context() != nil || a.Body() != nil || a.FinancialID() != "" {
		t.Fatal("handle retains released request")
	}
	if EndNative(a, NativeCompleted) == nil {
		t.Fatal("released handle accepted")
	}
}
