package server

import (
	"fmt"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// PROVIDER-CONCURRENCY-1, money rows. provider_busy is refused before
// dispatch: the request is charged nothing, writes no spend record and
// releases the hold it took (the zero-amount void record is that release),
// and a busy entry never moves a request to the other billing mode. Built,
// like provider_slots_test.go, against the gateway before the change too.

// heldModel is the managed model whose upstream answer the money tests hold,
// so its request keeps the provider entry's one slot.
const heldModel = "openai/gpt-6-sol"

// writeRecordedChat is recordingUpstream's own non-streamed answer, cost
// included, for a responseWith hook that answers it itself.
func writeRecordedChat(w http.ResponseWriter, model string) {
	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"id":"c1","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500,"cost":%g}}`, model, upstreamCost)
}

// holdRecordedUpstream makes u hold every non-streamed request for model until the
// returned release is called, and report each one's arrival on arrived.
func holdRecordedUpstream(t *testing.T, u *recordingUpstream, model string) (arrived <-chan struct{}, release func()) {
	t.Helper()
	got := make(chan struct{}, 16)
	gate := make(chan struct{})
	var once sync.Once
	release = func() { once.Do(func() { close(gate) }) }
	t.Cleanup(release)
	hook := upstreamStream(func(w http.ResponseWriter, _ *http.Request, m string) {
		if m == model {
			got <- struct{}{}
			<-gate
		}
		writeRecordedChat(w, m)
	})
	u.responseWith.Store(&hook)
	return got, release
}

// oneSlot gives provider id one upstream slot and a 150 ms queue wait, and
// the server a 10 s request deadline, so the held request keeps its slot
// through the test.
func oneSlot(t *testing.T, id string) func(*config.Config) {
	return func(cfg *config.Config) {
		cfg.Server.DefaultRequestTimeout = 10 * time.Second
		pc := cfg.Providers[id]
		yamlInto(t, &pc, "max_concurrent: 1\nqueue_timeout: 150ms\n")
		cfg.Providers[id] = pc
	}
}

// waitArrived waits for one held request to reach the upstream.
func waitArrived(t *testing.T, arrived <-chan struct{}) {
	t.Helper()
	select {
	case <-arrived:
	case <-time.After(5 * time.Second):
		t.Fatal("the held request never reached the upstream")
	}
}

// TestProviderBusyChargesNothing: with the managed entry's one slot held by
// a request, a managed request on chat and Messages, streamed or not, is
// refused 503 provider_busy at once. Once the held request ends, the org has
// spent only what that request cost, holds nothing, and its outbox has one
// spend record and, per refused request, a void record of zero amounts.
func TestProviderBusyChargesNothing(t *testing.T) {
	g := newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{configure: oneSlot(t, "openrouter")})
	g.ledger.SetMicros("org-m", 1_000_000)
	arrived, release := holdRecordedUpstream(t, g.managed, heldModel)

	heldDone := make(chan int, 1)
	go func() {
		status, _, _ := sendManaged(t, g, "/v1/chat/completions", heldModel, false, false, nil)
		heldDone <- status
	}()
	waitArrived(t, arrived)

	refused := 0
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			start := time.Now()
			status, body, header := sendManaged(t, g, surface, decoratedModel, stream, false, nil)
			elapsed := time.Since(start)
			if status != http.StatusServiceUnavailable || !strings.Contains(body, "provider_busy") || header.Get("Retry-After") != "1" {
				t.Errorf("%s stream=%v: status %d after %v, Retry-After %q, body %s; want 503 provider_busy, Retry-After 1",
					surface, stream, status, elapsed, header.Get("Retry-After"), body)
				continue
			}
			if elapsed > 1500*time.Millisecond {
				t.Errorf("%s stream=%v: refused after %v; the queue wait is 150ms", surface, stream, elapsed)
			}
			refused++
		}
	}
	if n := g.managed.calls(); n != 1 {
		t.Fatalf("the managed upstream got %d calls; want only the held one", n)
	}

	release()
	if status := <-heldDone; status != http.StatusOK {
		t.Fatalf("the held request ended %d", status)
	}
	spent, held := settledAccount(t, g)
	recs := g.outbox("org-m")
	var spends, voids int
	var billed string
	for _, rec := range recs {
		switch rec["kind"] {
		case "spend":
			spends++
			billed = rec["billed_micros"]
		case "void":
			voids++
			if rec["billed_micros"] != "0" || rec["upstream_micros"] != "0" || rec["markup_micros"] != "0" {
				t.Errorf("void record with an amount: %v", rec)
			}
		default:
			t.Errorf("unexpected record: %v", rec)
		}
	}
	if held != 0 || spends != 1 || billed != fmt.Sprint(spent) || voids != refused || refused != 4 {
		t.Fatalf("spent %d held %d; %d spend record(s) billing %s, %d void(s) for %d refusals; records %v",
			spent, held, spends, billed, voids, refused, recs)
	}
}

// busyOrgConfig is an org whose own provider "operator" serves org-model,
// with a model fallback from org-model to fallback and failover on 503.
func busyOrgConfig(fallback string, extra map[string]*tenant.ProviderConfig) *tenant.OrgConfig {
	cfg := managedOptIn()
	cfg.Providers = map[string]*tenant.ProviderConfig{
		"operator": {APIKey: "org-operator-key", APIFormat: "openai", Models: []string{"org-model"}, Enabled: true},
	}
	for id, pc := range extra {
		cfg.Providers[id] = pc
	}
	cfg.Routing = &tenant.RoutingConfig{
		ModelFallbacks: map[string][]string{"org-model": {fallback}},
		Failover:       &tenant.FailoverConfig{Enabled: true, MaxAttempts: 2, OnStatusCodes: []int{502, 503}},
		Retry:          &tenant.RetryConfig{Enabled: true, MaxRetries: 1, InitialDelay: "1ms", MaxDelay: "2ms", OnStatusCodes: []int{502, 503}},
	}
	return cfg
}

// TestProviderBusyNeverChangesBillingMode: the org's own (BYOK) provider is
// busy, and its model's fallback is a managed model. The 503 that fails over
// on any other upstream failure must not move the request onto Future AGI
// credits: it is refused provider_busy, the managed upstream is never
// called, and the org's credit is untouched.
func TestProviderBusyNeverChangesBillingMode(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			g := newManagedTestGatewayWith(t, busyOrgConfig(decoratedModel, nil), managedGatewayOptions{configure: oneSlot(t, "operator")})
			g.ledger.SetMicros("org-m", auth.USDToMicros(1))
			arrived, release := holdRecordedUpstream(t, g.operator, "org-model")
			heldDone := make(chan int, 1)
			go func() {
				status, _, _ := sendManaged(t, g, "/v1/chat/completions", "org-model", false, false, nil)
				heldDone <- status
			}()
			waitArrived(t, arrived)

			start := time.Now()
			status, body, _ := sendManaged(t, g, "/v1/chat/completions", "org-model", stream, false, nil)
			if status != http.StatusServiceUnavailable || !strings.Contains(body, "provider_busy") || time.Since(start) > 1500*time.Millisecond {
				t.Errorf("status %d after %v, body %s; want 503 provider_busy at once", status, time.Since(start), body)
			}
			if n := g.managed.calls(); n != 0 {
				t.Errorf("the busy BYOK request fell back to the managed upstream (%d calls)", n)
			}
			release()
			<-heldDone
			if bal := g.ledger.BalanceUSD("org-m"); bal != 1 {
				t.Fatalf("balance %.6f, want the untouched 1.000000", bal)
			}
		})
	}
}

// TestProviderBusyFallsBackInTheSameMode: the busy BYOK provider's fallback
// is another of the org's own providers (also BYOK). Fallback semantics stay:
// the request is served there.
func TestProviderBusyFallsBackInTheSameMode(t *testing.T) {
	spare := map[string]*tenant.ProviderConfig{
		"spare": {APIKey: "org-spare-key", APIFormat: "openai", Models: []string{"spare-model"}, Enabled: true},
	}
	configure := func(cfg *config.Config) {
		oneSlot(t, "operator")(cfg)
		off := false
		cfg.Providers["spare"] = config.ProviderConfig{BaseURL: cfg.Providers["operator"].BaseURL, APIKey: "fagi-spare-key",
			APIFormat: "openai", AutoDiscover: &off}
	}
	g := newManagedTestGatewayWith(t, busyOrgConfig("spare-model", spare), managedGatewayOptions{configure: configure})
	arrived, release := holdRecordedUpstream(t, g.operator, "org-model")
	heldDone := make(chan int, 1)
	go func() {
		status, _, _ := sendManaged(t, g, "/v1/chat/completions", "org-model", false, false, nil)
		heldDone <- status
	}()
	waitArrived(t, arrived)

	status, body, _ := sendManaged(t, g, "/v1/chat/completions", "org-model", false, false, nil)
	if status != http.StatusOK || g.operator.lastModel() != "spare-model" {
		t.Errorf("status %d, last upstream model %q, body %s; want 200 from the same-mode fallback spare-model",
			status, g.operator.lastModel(), body)
	}
	release()
	<-heldDone
}
