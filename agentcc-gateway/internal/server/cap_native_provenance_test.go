package server

// Permanent round-three native provenance reproducer. Both cap ledgers must
// retain incomplete native usage, while complete and explicit-zero controls settle.
import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	nativeanthropic "github.com/futureagi/agentcc-gateway/internal/providers/anthropic"
	nativecohere "github.com/futureagi/agentcc-gateway/internal/providers/cohere"
	nativegemini "github.com/futureagi/agentcc-gateway/internal/providers/gemini"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type opusNativeCase struct {
	usage string // "absent" omits the usage member entirely
	known bool   // complete, explicit native metering: settlement is allowed
}

func opusNativeBody(format, usage string) (path string, body string) {
	part := ""
	member := "usage"
	if format == "gemini" {
		member = "usageMetadata"
	}
	if usage != "absent" {
		part = `,"` + member + `":` + usage
	}
	switch format {
	case "anthropic":
		return "/v1/messages", `{"id":"native","type":"message","role":"assistant","model":"gpt-4o","content":[{"type":"text","text":"billable native work"}],"stop_reason":"end_turn"` + part + `}`
	case "gemini":
		return "generateContent", `{"candidates":[{"content":{"role":"model","parts":[{"text":"billable native work"}]},"finishReason":"STOP"}]` + part + `}`
	case "cohere":
		return "/v2/chat", `{"id":"native","finish_reason":"COMPLETE","message":{"role":"assistant","content":[{"type":"text","text":"billable native work"}]}` + part + `}`
	}
	panic(format)
}

var opusNativeCases = map[string][]opusNativeCase{
	"anthropic": {
		{"absent", false}, {`null`, false}, {`{}`, false},
		{`{"input_tokens":1000}`, false}, {`{"output_tokens":500}`, false},
		{`{"input_tokens":null,"output_tokens":7}`, false}, {`{"cache_read_input_tokens":100}`, false},
		{`{"input_tokens":0,"output_tokens":0}`, true}, {`{"input_tokens":10,"output_tokens":5}`, true},
	},
	"gemini": {
		{"absent", false}, {`null`, false}, {`{}`, false}, {`{"promptTokenCount":1000}`, false},
		{`{"candidatesTokenCount":500}`, false}, {`{"totalTokenCount":1500}`, false},
		{`{"promptTokenCount":10,"thoughtsTokenCount":5}`, false},
		{`{"promptTokenCount":10,"candidatesTokenCount":5,"totalTokenCount":15}`, true},
		{`{"promptTokenCount":0,"candidatesTokenCount":0}`, true},
		{`{"promptTokenCount":10,"totalTokenCount":10}`, true},
		{`{"promptTokenCount":10,"thoughtsTokenCount":5,"totalTokenCount":15}`, true},
	},
	"cohere": {
		{"absent", false}, {`null`, false}, {`{}`, false}, {`{"tokens":{}}`, false},
		{`{"billed_units":{"input_tokens":1000}}`, false}, {`{"tokens":{"output_tokens":5}}`, false},
		{`{"tokens":{"input_tokens":10,"output_tokens":5}}`, true},
		{`{"billed_units":{"input_tokens":0,"output_tokens":0}}`, true},
	},
}

func TestOpusR3NativeAdapterUsageProvenance(t *testing.T) {
	testNativeCapUsage(t, false)
}

func TestOpusR3ManagedNativeAdapterUsageProvenance(t *testing.T) {
	testNativeCapUsage(t, true)
}

// Exercise real native HTTP decoders through sync /v1/chat/completions, the
// supported capped surface. Managed and BYOK must share the same evidence rule.
func testNativeCapUsage(t *testing.T, managed bool) {
	for _, format := range []string{"anthropic", "gemini", "cohere"} {
		for _, runOnly := range []bool{false, true} {
			for _, c := range opusNativeCases[format] {
				t.Run(fmt.Sprintf("managed=%v/%s/run=%v/usage=%s", managed, format, runOnly, c.usage), func(t *testing.T) {
					g, client, prefix := capComposedGateway(t)
					cfg := g.tenants.Get("org-m")
					headers := map[string]string{}
					if runOnly {
						limit := 1.0
						cfg.Budgets.Agents, cfg.Budgets.KeyIDs = nil, nil
						cfg.Routing.Agents.MaxRunUSD = &limit
						headers["x-agentcc-run-id"] = "native-provenance"
					}
					var calls atomic.Int32
					wantPath, body := opusNativeBody(format, c.usage)
					up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						if !strings.Contains(r.URL.Path, wantPath) {
							http.NotFound(w, r)
							return
						}
						calls.Add(1)
						w.Header().Set("Content-Type", "application/json")
						fmt.Fprint(w, body)
					}))
					defer up.Close()
					model, mode := "gpt-4o", "byok"
					if managed {
						model, mode = "openai/gpt-6-sol", "managed"
						pc := config.ProviderConfig{BaseURL: up.URL, APIKey: "synthetic-platform-key", APIFormat: format}
						var p providers.Provider
						var err error
						switch format {
						case "anthropic":
							p, err = nativeanthropic.New("openrouter", pc)
						case "gemini":
							p, err = nativegemini.New("openrouter", pc)
						case "cohere":
							p, err = nativecohere.New("openrouter", pc)
						}
						if err != nil {
							t.Fatal(err)
						}
						g.srv.handlers.registry.RegisterProvider("openrouter", p, nil)
					} else {
						cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-native-org-key", APIFormat: format, Models: []string{model}, Enabled: true}}
						g.srv.handlers.orgProviderCache = providers.NewOrgProviderCache(map[string]config.ProviderConfig{"operator": {BaseURL: up.URL, APIFormat: format, Models: []string{model}}}, false)
					}
					g.tenants.Set("org-m", cfg)
					first := g.chat(t, chatBody(model), headers)
					if first.Code != http.StatusOK || calls.Load() != 1 || first.Header().Get("x-agentcc-billing-mode") != mode {
						t.Fatalf("positive control not dispatched: code=%d calls=%d mode=%q", first.Code, calls.Load(), first.Header().Get("x-agentcc-billing-mode"))
					}
					assertNativeCapEvidence(t, g, client, prefix, runOnly, c.known)
					second := g.chat(t, chatBody(model), headers)
					wantStatus, wantCalls := http.StatusServiceUnavailable, int32(1)
					if c.known {
						wantStatus, wantCalls = http.StatusOK, 2
					}
					if second.Code != wantStatus || calls.Load() != wantCalls {
						t.Errorf("native cap provenance: second=%d calls=%d want=%d/%d", second.Code, calls.Load(), wantStatus, wantCalls)
					}
				})
			}
		}
	}
}

func assertNativeCapEvidence(t *testing.T, g *managedTestGateway, client *redisstate.Client, prefix string, runOnly, known bool) {
	t.Helper()
	ctx := context.Background()
	rdb := client.Redis()
	wantHeld, wantTerminal := int64(1), int64(0)
	if known {
		wantHeld, wantTerminal = 0, 1
	}
	base := prefix + "bhold:{org-m}:" + time.Now().UTC().Format("2006-01-02") + ":"
	var uncertain int64
	if runOnly {
		base = prefix + "abud:{org-m}:native-provenance:"
		uncertain = rdb.SCard(ctx, base+"limit:uncertain").Val()
	} else {
		uncertain = rdb.HLen(ctx, base+"uncertain").Val()
		key := g.srv.keyStore.Authenticate(managedOrgKey)
		for _, level := range []string{"agent:worker", "key_id:" + key.ID} {
			got, err := rdb.HGet(ctx, base+"state", "uncertain:"+level).Int64()
			if !known && (err != nil || got != 1) {
				t.Errorf("incomplete usage did not fence %s", level)
			}
		}
	}
	if rdb.HLen(ctx, base+"holds").Val() != wantHeld || rdb.HLen(ctx, base+"terminal").Val() != wantTerminal || uncertain != wantHeld {
		t.Errorf("native ledger evidence: holds=%d terminals=%d uncertainty=%d want=%d/%d/%d", rdb.HLen(ctx, base+"holds").Val(), rdb.HLen(ctx, base+"terminal").Val(), uncertain, wantHeld, wantTerminal, wantHeld)
	}
}
