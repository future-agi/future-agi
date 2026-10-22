package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"slices"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The org's own price for a model
// (cost_tracking.custom_pricing) on an own-key "<provider>/<id>" request.
// The org's provider is sent <id>, the id the org registered, so the attempt
// records <id>; a real host answers with a dated id (Anthropic
// "claude-haiku-4-5-20251001", OpenAI "gpt-4o-mini-2024-07-18"), unlike
// okHost, which echoes the id it was sent. The org's price applies whether
// it is keyed by the asked id, which the request log records as model, or by
// the registered id; when both are keyed, the asked id's price wins. A price
// keyed "<provider>/<model>" also prices a bare <model> that provider serves.
// A model fallback hop is never priced at the price of the model asked for:
// with no org price for its own model, it takes its own listed price, or is
// left unpriced when the catalog does not list its model.

// okDatedIDs are the ids datedHost knows, each with the dated id a real host
// answers with. claude-custom-x and my-finetune are ids the catalog does not
// list: an org's own deployment or fine-tune.
var okDatedIDs = map[string]string{
	"claude-haiku-4-5": "claude-haiku-4-5-20251001",
	"gpt-4o-mini":      "gpt-4o-mini-2024-07-18",
	"claude-custom-x":  "claude-custom-x-20251001",
	"my-finetune":      "my-finetune-2024-07-18",
}

// okDatedDown is an Anthropic id datedHost knows but fails (502).
const okDatedDown = "claude-down"

// datedHost answers chat (OpenAI format) and messages (Anthropic format)
// naming the dated id for the id it was sent, fails okDatedDown and any id in
// down with 502 and refuses any other id. It keeps the id each call named.
// With echo, it answers naming the id it was sent instead, as okHost does, so
// the serving attempt's model is the request's resolved one.
type datedHost struct {
	*httptest.Server
	mu   sync.Mutex
	seen []string
}

func startDatedHost(t *testing.T, echo bool, down ...string) *datedHost {
	t.Helper()
	h := &datedHost{}
	h.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.Method != http.MethodPost {
			io.WriteString(w, `{"object":"list","data":[]}`)
			return
		}
		var body struct {
			Model string `json:"model"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		h.mu.Lock()
		h.seen = append(h.seen, r.URL.Path+" "+body.Model)
		h.mu.Unlock()
		if body.Model == okDatedDown || slices.Contains(down, body.Model) {
			w.WriteHeader(http.StatusBadGateway)
			io.WriteString(w, `{"type":"error","error":{"type":"overloaded_error","message":"overloaded"}}`)
			return
		}
		dated, ok := okDatedIDs[body.Model]
		if !ok {
			w.WriteHeader(http.StatusNotFound)
			fmt.Fprintf(w, `{"error":{"message":"The model %q does not exist","type":"invalid_request_error","code":"model_not_found"}}`, body.Model)
			return
		}
		if echo {
			dated = body.Model
		}
		switch r.URL.Path {
		case "/v1/messages":
			fmt.Fprintf(w, `{"id":"msg_1","type":"message","role":"assistant","model":%q,"content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","stop_sequence":null,"usage":{"input_tokens":1000,"output_tokens":1000}}`, dated)
		case "/v1/chat/completions":
			fmt.Fprintf(w, `{"id":"c1","object":"chat.completion","created":1,"model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],%s}`, dated, okUsage)
		default:
			w.WriteHeader(http.StatusNotFound)
			io.WriteString(w, `{"error":{"message":"no such path","type":"invalid_request_error"}}`)
		}
	}))
	t.Cleanup(h.Close)
	return h
}

func (h *datedHost) calls() []string {
	h.mu.Lock()
	defer h.mu.Unlock()
	return append([]string(nil), h.seen...)
}

// okPrices is an org's custom_pricing: each model at price a million tokens,
// input and output alike.
func okPrices(prices map[string]float64) map[string]*tenant.CustomPricing {
	m := make(map[string]*tenant.CustomPricing, len(prices))
	for k, v := range prices {
		m[k] = &tenant.CustomPricing{InputPerMTok: v, OutputPerMTok: v}
	}
	return m
}

func TestOwnKeyCustomPricingAppliesToTheAskedAndTheRegisteredID(t *testing.T) {
	failover := &tenant.FailoverConfig{Enabled: true, OnStatusCodes: []int{http.StatusBadGateway}}
	for _, c := range []struct {
		name, host, asked string
		echo              bool     // the host answers naming the id it was sent
		down              []string // ids the host fails, besides okDatedDown
		extra             []string // ids the org's provider registers for a fallback chain, besides okDatedDown
		prices            map[string]float64
		fallbacks         map[string][]string // the org's model fallback chain
		want              []string            // the calls the host gets
		cost, source      string              // 1,000 tokens in and 1,000 out
	}{
		{name: "anthropic, priced on the asked id", host: "anthropic", asked: "anthropic/claude-haiku-4-5",
			prices: map[string]float64{"anthropic/claude-haiku-4-5": 100},
			want:   []string{"/v1/messages claude-haiku-4-5"}, cost: "0.200000", source: "org_custom"},
		{name: "anthropic, priced on the registered id", host: "anthropic", asked: "anthropic/claude-haiku-4-5",
			prices: map[string]float64{"claude-haiku-4-5": 100},
			want:   []string{"/v1/messages claude-haiku-4-5"}, cost: "0.200000", source: "org_custom"},
		{name: "anthropic, both priced: the asked id's price", host: "anthropic", asked: "anthropic/claude-haiku-4-5",
			prices: map[string]float64{"anthropic/claude-haiku-4-5": 100, "claude-haiku-4-5": 50},
			want:   []string{"/v1/messages claude-haiku-4-5"}, cost: "0.200000", source: "org_custom"},
		{name: "anthropic bare ask, both priced: the bare id's price", host: "anthropic", asked: "claude-haiku-4-5",
			prices: map[string]float64{"anthropic/claude-haiku-4-5": 100, "claude-haiku-4-5": 50},
			want:   []string{"/v1/messages claude-haiku-4-5"}, cost: "0.100000", source: "org_custom"},
		{name: "openai, priced on the asked id", host: "openai", asked: "openai/gpt-4o-mini",
			prices: map[string]float64{"openai/gpt-4o-mini": 100},
			want:   []string{"/v1/chat/completions gpt-4o-mini"}, cost: "0.200000", source: "org_custom"},
		{name: "openai, priced on the registered id", host: "openai", asked: "openai/gpt-4o-mini",
			prices: map[string]float64{"gpt-4o-mini": 100},
			want:   []string{"/v1/chat/completions gpt-4o-mini"}, cost: "0.200000", source: "org_custom"},
		{name: "anthropic fallback hop, priced on its own asked id", host: "anthropic", asked: "anthropic/" + okDatedDown,
			prices:    map[string]float64{"anthropic/claude-haiku-4-5": 100, "anthropic/" + okDatedDown: 50},
			fallbacks: map[string][]string{"anthropic/" + okDatedDown: {"anthropic/claude-haiku-4-5"}},
			want:      []string{"/v1/messages " + okDatedDown, "/v1/messages claude-haiku-4-5"}, cost: "0.200000", source: "org_custom"},
		{name: "anthropic fallback hop with no org price of its own: its listed price, not the primary's org price", host: "anthropic", asked: "anthropic/" + okDatedDown,
			prices:    map[string]float64{"anthropic/" + okDatedDown: 50},
			fallbacks: map[string][]string{"anthropic/" + okDatedDown: {"anthropic/claude-haiku-4-5"}},
			want:      []string{"/v1/messages " + okDatedDown, "/v1/messages claude-haiku-4-5"},
			cost:      "0.006000", source: "modeldb"}, // claude-haiku-4-5: $1 and $5 a million tokens
		// A host that answers naming the id it was sent makes the hop's model
		// the request's resolved one; the hop is still another model than the
		// one asked for, so the primary's org price never covers it.
		{name: "anthropic fallback hop with no org price of its own, echoing host: its listed price, not the primary's org price", host: "anthropic", asked: "anthropic/" + okDatedDown, echo: true,
			prices:    map[string]float64{"anthropic/" + okDatedDown: 50},
			fallbacks: map[string][]string{"anthropic/" + okDatedDown: {"anthropic/claude-haiku-4-5"}},
			want:      []string{"/v1/messages " + okDatedDown, "/v1/messages claude-haiku-4-5"},
			cost:      "0.006000", source: "modeldb"},
		{name: "anthropic bare fallback hop with no org price of its own, echoing host: its listed price, not the primary's org price", host: "anthropic", asked: okDatedDown, echo: true,
			prices:    map[string]float64{okDatedDown: 50},
			fallbacks: map[string][]string{okDatedDown: {"claude-haiku-4-5"}},
			want:      []string{"/v1/messages " + okDatedDown, "/v1/messages claude-haiku-4-5"},
			cost:      "0.006000", source: "modeldb"},
		{name: "anthropic fallback hop priced on its own id, echoing host", host: "anthropic", asked: "anthropic/" + okDatedDown, echo: true,
			prices:    map[string]float64{"anthropic/" + okDatedDown: 50, "claude-haiku-4-5": 100},
			fallbacks: map[string][]string{"anthropic/" + okDatedDown: {"anthropic/claude-haiku-4-5"}},
			want:      []string{"/v1/messages " + okDatedDown, "/v1/messages claude-haiku-4-5"},
			cost:      "0.200000", source: "org_custom"},
		// A price keyed "<provider>/<model>" names that model on that
		// provider: it prices a bare ask the provider serves, and never one
		// another of the org's providers' keys names (custom_pricing wins over
		// the listed price).
		{name: "anthropic bare ask, priced on \"anthropic/<id>\" only: the org's price", host: "anthropic", asked: "claude-haiku-4-5",
			prices: map[string]float64{"anthropic/claude-haiku-4-5": 100},
			want:   []string{"/v1/messages claude-haiku-4-5"}, cost: "0.200000", source: "org_custom"},
		{name: "openai bare ask, priced on \"openai/<id>\" only: the org's price", host: "openai", asked: "gpt-4o-mini",
			prices: map[string]float64{"openai/gpt-4o-mini": 100},
			want:   []string{"/v1/chat/completions gpt-4o-mini"}, cost: "0.200000", source: "org_custom"},
		{name: "anthropic bare ask, priced on another org provider's \"<provider>/<id>\": its listed price", host: "anthropic", asked: "claude-haiku-4-5",
			prices: map[string]float64{"myrouter/claude-haiku-4-5": 100},
			want:   []string{"/v1/messages claude-haiku-4-5"}, cost: "0.006000", source: "modeldb"},
		{name: "anthropic prefixed ask, priced on another org provider's \"<provider>/<id>\": its listed price", host: "anthropic", asked: "anthropic/claude-haiku-4-5",
			prices: map[string]float64{"myrouter/claude-haiku-4-5": 100},
			want:   []string{"/v1/messages claude-haiku-4-5"}, cost: "0.006000", source: "modeldb"},
		{name: "openai bare ask, priced on another org provider's \"<provider>/<id>\": its listed price", host: "openai", asked: "gpt-4o-mini",
			prices: map[string]float64{"groq/gpt-4o-mini": 100},
			want:   []string{"/v1/chat/completions gpt-4o-mini"}, cost: "0.000750", source: "modeldb"}, // gpt-4o-mini: $0.15 and $0.60 a million tokens
		{name: "openai prefixed ask, priced on another org provider's \"<provider>/<id>\": its listed price", host: "openai", asked: "openai/gpt-4o-mini",
			prices: map[string]float64{"groq/gpt-4o-mini": 100},
			want:   []string{"/v1/chat/completions gpt-4o-mini"}, cost: "0.000750", source: "modeldb"},
		// With no org price, a hop onto a model the catalog does not list is
		// left unpriced, never priced at the listed price of the model asked
		// for (claude-haiku-4-5 at $0.006000, gpt-4o-mini at $0.000750), even
		// when its host answers naming the id it was sent.
		{name: "anthropic fallback hop on a model the catalog does not list, no org price, echoing host: unpriced, not the primary's listed price", host: "anthropic", asked: "anthropic/claude-haiku-4-5", echo: true,
			down: []string{"claude-haiku-4-5"}, extra: []string{"claude-custom-x"},
			fallbacks: map[string][]string{"anthropic/claude-haiku-4-5": {"anthropic/claude-custom-x"}},
			want:      []string{"/v1/messages claude-haiku-4-5", "/v1/messages claude-custom-x"}, cost: "", source: "unpriced"},
		{name: "anthropic bare fallback hop on a model the catalog does not list, no org price, echoing host: unpriced, not the primary's listed price", host: "anthropic", asked: "claude-haiku-4-5", echo: true,
			down: []string{"claude-haiku-4-5"}, extra: []string{"claude-custom-x"},
			fallbacks: map[string][]string{"claude-haiku-4-5": {"claude-custom-x"}},
			want:      []string{"/v1/messages claude-haiku-4-5", "/v1/messages claude-custom-x"}, cost: "", source: "unpriced"},
		{name: "openai fallback hop on a model the catalog does not list, no org price, echoing host: unpriced, not the primary's listed price", host: "openai", asked: "openai/gpt-4o-mini", echo: true,
			down: []string{"gpt-4o-mini"}, extra: []string{"my-finetune"},
			fallbacks: map[string][]string{"openai/gpt-4o-mini": {"openai/my-finetune"}},
			want:      []string{"/v1/chat/completions gpt-4o-mini", "/v1/chat/completions my-finetune"}, cost: "", source: "unpriced"},
		{name: "openai bare fallback hop on a model the catalog does not list, no org price, echoing host: unpriced, not the primary's listed price", host: "openai", asked: "gpt-4o-mini", echo: true,
			down: []string{"gpt-4o-mini"}, extra: []string{"my-finetune"},
			fallbacks: map[string][]string{"gpt-4o-mini": {"my-finetune"}},
			want:      []string{"/v1/chat/completions gpt-4o-mini", "/v1/chat/completions my-finetune"}, cost: "", source: "unpriced"},
		// Control: a host that answers with a dated id leaves such a hop
		// unpriced too.
		{name: "anthropic fallback hop on a model the catalog does not list, no org price, dated host: unpriced", host: "anthropic", asked: "anthropic/claude-haiku-4-5",
			down: []string{"claude-haiku-4-5"}, extra: []string{"claude-custom-x"},
			fallbacks: map[string][]string{"anthropic/claude-haiku-4-5": {"anthropic/claude-custom-x"}},
			want:      []string{"/v1/messages claude-haiku-4-5", "/v1/messages claude-custom-x"}, cost: "", source: "unpriced"},
	} {
		t.Run(c.name, func(t *testing.T) {
			host := startDatedHost(t, c.echo, c.down...)
			g := newOKGateway(t, func(cfg *config.Config, org *tenant.OrgConfig) {
				pc := cfg.Providers[c.host]
				pc.BaseURL = host.URL
				cfg.Providers[c.host] = pc
				if c.fallbacks != nil {
					ids := append(append([]string(nil), org.Providers[c.host].Models...), okDatedDown)
					org.Providers[c.host].Models = append(ids, c.extra...)
					org.Routing = &tenant.RoutingConfig{Failover: failover, ModelFallbacks: c.fallbacks}
				}
				org.CostTracking = &tenant.CostTrackingConfig{Enabled: true, CustomPricing: okPrices(c.prices)}
			})
			ct, body := okJSON(okChat)(t, c.asked)
			w := g.send(t, "/v1/chat/completions", ct, body)
			seen := host.calls()
			if w.Code != http.StatusOK {
				t.Fatalf("status = %d %s; the host received %q", w.Code, w.Body.String(), seen)
			}
			if fmt.Sprint(seen) != fmt.Sprint(c.want) {
				t.Errorf("the host received %q, want %q", seen, c.want)
			}
			g.probe.mu.Lock()
			source, attempts := g.probe.meta["cost_source"], g.probe.attempts
			g.probe.mu.Unlock()
			if got := w.Header().Get("x-agentcc-cost"); got != c.cost || source != c.source {
				t.Errorf("x-agentcc-cost = %q from %q, want %s from %s (attempts %q)", got, source, c.cost, c.source, attempts)
			}
		})
	}
}
