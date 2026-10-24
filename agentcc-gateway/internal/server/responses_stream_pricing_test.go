package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A /v1/responses stream is relayed to the client as the provider sends it,
// and the cost plugin prices it after the stream, from rc.Response's usage.
// The stream's usage arrives in its last event (response.completed, or
// response.incomplete or response.failed for a response that stopped early),
// which carries the whole response object, usage included. A stream must be
// priced from it, as a non-streamed response is from its body. A request
// paid from prepaid credit (a managed key's balance here; org managed
// credits do not reach /v1/responses) whose usage the gateway
// cannot read is refused before the provider is called: a background
// response's answer and usage never pass through the gateway.

const (
	respHugeMarker   = "huge final event"        // the stream's last event is over the usage line limit
	respCutOffMarker = "cut off"                 // the stream ends before its last event
	respManagedKey   = "sk-resp-managed-key-001" // key_type managed: a prepaid balance
	respByokKey      = "sk-resp-byok-key-001"
	respOrg          = "org-resp"
	respUsage        = `{"input_tokens":1000,"output_tokens":500,"total_tokens":1500}`
)

// responsesUpstream is an OpenAI-format upstream that serves the Responses
// API: JSON, a stream ending with response.completed, or a background
// response (queued, and for a stream, events that end before it completes).
type responsesUpstream struct {
	*httptest.Server
	calls atomic.Int32
}

func startResponsesUpstream(t *testing.T) *responsesUpstream {
	t.Helper()
	u := &responsesUpstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/responses" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		u.calls.Add(1)
		var req struct {
			Model      string `json:"model"`
			Stream     bool   `json:"stream"`
			Background bool   `json:"background"`
		}
		raw, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(raw, &req)
		message := `{"type":"message","id":"msg_1","status":"completed","role":"assistant","content":[{"type":"output_text","text":"hi","annotations":[]}]}`
		// A response whose last event is over the line limit the gateway
		// reads its usage within (as a response.completed carrying base64
		// images can be), or a stream cut off before its last event.
		huge := bytes.Contains(raw, []byte(respHugeMarker))
		cutOff := bytes.Contains(raw, []byte(respCutOffMarker))
		if huge {
			message = `{"type":"message","id":"msg_1","status":"completed","role":"assistant","content":[{"type":"output_text","text":"` +
				strings.Repeat("A", maxResponsesUsageLineBytes+1024) + `","annotations":[]}]}`
		}
		switch {
		case !req.Stream && req.Background:
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprintf(w, `{"id":"resp_bg","object":"response","status":"queued","background":true,"model":%q,"output":[],"usage":null}`, req.Model)
		case !req.Stream:
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprintf(w, `{"id":"resp_1","object":"response","status":"completed","model":%q,"output":[%s],"usage":%s}`, req.Model, message, respUsage)
		default:
			w.Header().Set("Content-Type", "text/event-stream")
			flusher := w.(http.Flusher)
			events := []string{
				fmt.Sprintf(`event: response.created`+"\n"+`data: {"type":"response.created","sequence_number":0,"response":{"id":"resp_1","object":"response","status":"in_progress","model":%q,"output":[],"usage":null}}`, req.Model),
				`event: response.output_text.delta` + "\n" + `data: {"type":"response.output_text.delta","sequence_number":1,"item_id":"msg_1","output_index":0,"content_index":0,"delta":"hi"}`,
			}
			if !req.Background && !cutOff {
				// A background response goes on after its stream; the
				// gateway never sees how it ends.
				events = append(events, fmt.Sprintf(`event: response.completed`+"\n"+`data: {"type":"response.completed","sequence_number":2,"response":{"id":"resp_1","object":"response","status":"completed","model":%q,"output":[%s],"usage":%s}}`, req.Model, message, respUsage))
			}
			for _, ev := range events {
				fmt.Fprint(w, ev+"\n\n")
				flusher.Flush()
			}
		}
	}))
	t.Cleanup(u.Close)
	return u
}

// costProbe records each run's cost metadata and usage after the cost plugin
// priced it.
type costProbe struct {
	mu   sync.Mutex
	runs []costRun
}

type costRun struct {
	cost, resolvedModel string
	usage               *models.Usage
	// usage_estimated and usage_unreadable, as the run's metadata held
	// them after pricing.
	usageEstimated, usageUnreadable string
}

func (p *costProbe) Name() string  { return "cost-probe" }
func (p *costProbe) Priority() int { return 990 }
func (p *costProbe) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *costProbe) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	run := costRun{cost: rc.Metadata["cost"], resolvedModel: rc.ResolvedModel,
		usageEstimated: rc.Metadata["usage_estimated"], usageUnreadable: rc.Metadata["usage_unreadable"]}
	if rc.Response != nil && rc.Response.Usage != nil {
		u := *rc.Response.Usage
		run.usage = &u
	}
	p.mu.Lock()
	p.runs = append(p.runs, run)
	p.mu.Unlock()
	return pipeline.ResultContinue()
}

func (p *costProbe) last(t *testing.T, n int) costRun {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for {
		p.mu.Lock()
		got := len(p.runs)
		var run costRun
		if got > 0 {
			run = p.runs[got-1]
		}
		p.mu.Unlock()
		if got >= n {
			return run
		}
		if time.Now().After(deadline) {
			t.Fatalf("the pipeline ran %d times, want %d", got, n)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

type responsesBillingGateway struct {
	srv      *Server
	keyStore *auth.KeyStore
	up       *responsesUpstream
	probe    *costProbe
}

// newResponsesBillingGateway serves org respOrg's two keys from the org's
// own openai credentials (the gateway's openai provider, with the org's
// key): a managed key with a $1 prepaid balance, and a BYOK key. The engine
// prices and debits as cmd/agentcc wires it: auth, cost, credits.
func newResponsesBillingGateway(t *testing.T) *responsesBillingGateway {
	t.Helper()
	up := startResponsesUpstream(t)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "managed", Key: respManagedKey, Owner: "o", KeyType: "managed", CreditBalance: 1,
			Metadata: map[string]string{"org_id": respOrg}},
		{Name: "byok", Key: respByokKey, Owner: "o", KeyType: "byok", Metadata: map[string]string{"org_id": respOrg}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	tenants.Set(respOrg, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {Enabled: true, APIKey: "org-key", Models: []string{"gpt-4o"}},
	}})
	ks := auth.NewKeyStore(cfg.Auth)
	mdb := testModelDBPtr()
	probe := &costProbe{}
	engine := pipeline.NewEngine(authplugin.New(ks, true), costplugin.New(true, mdb.Load, tenants, nil),
		creditsplugin.New(true, ks), probe)
	srv := New(cfg, "", registry, engine, ks, nil, nil, nil, mdb, tenants, nil)
	srv.ready.Store(true)
	return &responsesBillingGateway{srv: srv, keyStore: ks, up: up, probe: probe}
}

// send posts a /v1/responses request with key and returns its status and
// body.
func (g *responsesBillingGateway) send(t *testing.T, key string, stream, background bool) (int, string) {
	t.Helper()
	body := fmt.Sprintf(`{"model":"gpt-4o","input":"hi","stream":%t`, stream)
	if background {
		body += `,"background":true`
	}
	body += "}"
	req := httptest.NewRequest(http.MethodPost, "/v1/responses", bytes.NewBufferString(body))
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", "application/json")
	ctx, cancel := context.WithTimeout(req.Context(), 5*time.Second)
	defer cancel()
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req.WithContext(ctx))
	b, _ := io.ReadAll(w.Result().Body)
	return w.Code, string(b)
}

// balance is the managed key's prepaid balance, in micro-dollars.
func (g *responsesBillingGateway) balance(t *testing.T) int64 {
	t.Helper()
	for _, k := range g.keyStore.List() {
		if k.Name == "managed" {
			return k.BalanceMicros()
		}
	}
	t.Fatal("the managed key is not in the store")
	return 0
}

// A /v1/responses stream is priced from its last event's usage, exactly as
// the same response is priced unstreamed: the managed key is debited the same
// amount, and the run's cost and usage are the response's.
func TestResponsesStream_IsPricedFromItsFinalUsage(t *testing.T) {
	g := newResponsesBillingGateway(t)

	start := g.balance(t)
	if code, body := g.send(t, respManagedKey, false, false); code != http.StatusOK {
		t.Fatalf("the unstreamed request got %d: %s", code, body)
	}
	unstreamed := g.probe.last(t, 1)
	unstreamedDebit := start - g.balance(t)
	if unstreamedDebit <= 0 || unstreamed.cost == "" {
		t.Fatalf("control: the unstreamed response debited %d micros (cost %q); it should be priced", unstreamedDebit, unstreamed.cost)
	}

	before := g.balance(t)
	code, body := g.send(t, respManagedKey, true, false)
	if code != http.StatusOK || !strings.Contains(body, "response.completed") {
		t.Fatalf("the stream got %d: %s", code, body)
	}
	streamed := g.probe.last(t, 2)
	streamDebit := before - g.balance(t)
	t.Logf("unstreamed: debit %d micros, cost %q, usage %+v; streamed: debit %d micros, cost %q, usage %+v",
		unstreamedDebit, unstreamed.cost, unstreamed.usage, streamDebit, streamed.cost, streamed.usage)
	if streamed.usage == nil || streamed.usage.PromptTokens != 1000 || streamed.usage.CompletionTokens != 500 || streamed.usage.TotalTokens != 1500 {
		t.Errorf("the stream was priced on usage %+v; its response.completed reported 1000 in, 500 out, 1500 total", streamed.usage)
	}
	if streamed.cost != unstreamed.cost {
		t.Errorf("the stream cost %q; the same response unstreamed costs %q", streamed.cost, unstreamed.cost)
	}
	if streamDebit != unstreamedDebit {
		t.Errorf("the managed key was debited %d micros for the stream; the same response unstreamed debits %d", streamDebit, unstreamedDebit)
	}
	if streamed.resolvedModel != "gpt-4o" {
		t.Errorf("the stream's resolved model is %q, want the model its last event names", streamed.resolvedModel)
	}

	// A BYOK key's stream is priced too: its cost is what the org's budget
	// and the request log count.
	if code, body := g.send(t, respByokKey, true, false); code != http.StatusOK {
		t.Fatalf("the BYOK stream got %d: %s", code, body)
	}
	if byok := g.probe.last(t, 3); byok.cost != unstreamed.cost {
		t.Errorf("the BYOK key's stream cost %q, want %q", byok.cost, unstreamed.cost)
	}
}

// A background response's usage never passes through the gateway: the
// response goes on after its stream (or after the queued answer), and its
// usage is read back from the provider, never here. So a background request
// paid from prepaid credit is refused before the provider is called, streamed
// or not. A BYOK key's background request is still sent.
func TestResponsesBackground_PaidFromPrepaidCreditIsRefusedBeforeTheProvider(t *testing.T) {
	g := newResponsesBillingGateway(t)
	for _, stream := range []bool{true, false} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			calls, before := g.up.calls.Load(), g.balance(t)
			code, body := g.send(t, respManagedKey, stream, true)
			t.Logf("managed key, background: %d %s", code, strings.TrimSpace(body))
			if n := g.up.calls.Load() - calls; n != 0 {
				t.Errorf("the provider was called %d time(s) for a managed key's background request, which nothing can price", n)
			}
			if code != http.StatusBadRequest || !strings.Contains(body, "usage_unavailable") {
				t.Errorf("the managed key's background request got %d %s; want 400 usage_unavailable", code, body)
			}
			if debit := before - g.balance(t); debit != 0 {
				t.Errorf("a refused request debited %d micros", debit)
			}

			calls = g.up.calls.Load()
			if code, body := g.send(t, respByokKey, stream, true); code != http.StatusOK {
				t.Errorf("control: the BYOK key's background request got %d: %s", code, body)
			}
			if n := g.up.calls.Load() - calls; n != 1 {
				t.Errorf("control: the BYOK key's background request reached the provider %d time(s), want 1", n)
			}
		})
	}
}
