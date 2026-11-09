package server

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func presenceCases() []upInt2Case {
	cases := append([]upInt2Case(nil), upInt2Cases...)
	// Unknown native totals/charges are deliberately not treated as measured
	// input/output or invented provider charges. Only OpenAI/Azure support cost.
	cases = append(cases,
		upInt2Case{"native-total-only", `{"total_tokens":1500}`, map[string]string{
			"anthropic": `{"total_tokens":1500}`, "bedrock-invoke": `{"total_tokens":1500}`, "cohere": `{"tokens":{"total_tokens":1500}}`}},
		upInt2Case{"cache-only", `{"prompt_tokens_details":{"cached_tokens":2}}`, map[string]string{
			"azure": `{"prompt_tokens_details":{"cached_tokens":2}}`, "anthropic": `{"cache_read_input_tokens":2}`,
			"gemini": `{"cachedContentTokenCount":2}`, "cohere": `{"tokens":{"cached_tokens":0}}`,
			"bedrock-invoke": `{"cache_read_input_tokens":2}`, "bedrock-converse": `{"cacheReadInputTokens":2}`}},
		upInt2Case{"null-counters", `{"prompt_tokens":null,"completion_tokens":null}`, map[string]string{
			"azure": `{"prompt_tokens":null,"completion_tokens":null}`, "anthropic": `{"input_tokens":null,"output_tokens":null}`,
			"gemini": `{"promptTokenCount":null,"candidatesTokenCount":null}`, "cohere": `{"tokens":{"input_tokens":null,"output_tokens":null}}`,
			"bedrock-invoke": `{"input_tokens":null,"output_tokens":null}`, "bedrock-converse": `{"inputTokens":null,"outputTokens":null}`}},
		upInt2Case{"cohere-empty-with-billed", `{"prompt_tokens":10,"completion_tokens":5}`, map[string]string{"cohere": `{"tokens":{},"billed_units":{"input_tokens":10,"output_tokens":5}}`}},
		upInt2Case{"cohere-partial-with-billed", `{"prompt_tokens":10,"completion_tokens":5}`, map[string]string{"cohere": `{"tokens":{"input_tokens":10},"billed_units":{"input_tokens":99,"output_tokens":5}}`}},
		upInt2Case{"cohere-other-partial-with-billed", `{"prompt_tokens":10,"completion_tokens":5}`, map[string]string{"cohere": `{"tokens":{"output_tokens":5},"billed_units":{"input_tokens":10,"output_tokens":99}}`}},
		upInt2Case{"cohere-null-with-billed", `{"prompt_tokens":10,"completion_tokens":5}`, map[string]string{"cohere": `{"tokens":{"input_tokens":null,"output_tokens":null},"billed_units":{"input_tokens":10,"output_tokens":5}}`}},
		upInt2Case{"cohere-zero-overrides-billed", `{"prompt_tokens":0,"completion_tokens":0}`, map[string]string{"cohere": `{"tokens":{"input_tokens":0,"output_tokens":0},"billed_units":{"input_tokens":10,"output_tokens":5}}`}},
		upInt2Case{"reported-charge", `{"cost":0.001}`, map[string]string{"azure": `{"cost":0.001}`}},
		upInt2Case{"reported-zero-charge", `{"cost":0}`, map[string]string{"azure": `{"cost":0}`}},
		upInt2Case{"unsupported-native-charge", `{}`, map[string]string{
			"anthropic": `{"cost":0.001}`, "gemini": `{"cost":0.001}`, "cohere": `{"cost":0.001}`,
			"bedrock-invoke": `{"cost":0.001}`, "bedrock-converse": `{"cost":0.001}`}},
	)
	return cases
}

// Compare credit settlement and BYOK post-cost usage on the real adapter path.
// Managed Messages is also covered; BYOK Messages can be a byte passthrough,
// rather than the canonical adapter whose accounting this matrix exercises.
func TestUsagePresenceNativeMatrix(t *testing.T) {
	for _, managed := range []bool{false, true} {
		surfaces := []string{"/v1/chat/completions"}
		if managed {
			surfaces = managedSurfaces
		}
		for _, surface := range surfaces {
			for _, stream := range []bool{false, true} {
				for _, c := range presenceCases() {
					t.Run(fmt.Sprintf("managed=%t/%s/stream=%t/%s", managed, upInt2SurfaceName(surface), stream, c.name), func(t *testing.T) {
						oracle, oracleCost := presenceRun(t, upInt2Adapters[0], surface, stream, managed, c.oracle)
						if managed {
							upInt2Served(t, "oracle", oracle)
						}
						for _, a := range upInt2Adapters[1:] {
							usage, ok := c.native[a.name]
							if !ok {
								continue
							}
							t.Run(a.name, func(t *testing.T) {
								want, wantCost := oracle, oracleCost
								if c.name == "cache-only" && (a.name == "cohere" || a.name == "bedrock-converse") {
									want, wantCost = presenceRun(t, upInt2Adapters[0], surface, stream, managed, `{}`)
								}
								got, gotCost := presenceRun(t, a, surface, stream, managed, usage)
								if managed {
									upInt2Compare(t, got, want)
									g, w := gotCost.usage, wantCost.usage
									if g == nil || w == nil || g.PromptTokens != w.PromptTokens || g.CompletionTokens != w.CompletionTokens || g.HasPromptTokens() != w.HasPromptTokens() || g.HasCompletionTokens() != w.HasCompletionTokens() {
										t.Errorf("native estimate changed token evidence: native=%+v oracle=%+v", g, w)
									}
									return
								}
								if got.status != 200 || got.calls != 1 || got.mode != "byok" || got.spent != 0 || got.held != 0 || len(got.records) != 0 {
									t.Fatalf("BYOK dispatch control: %+v", got)
								}
								// Cost and estimation must match the same evidence on OpenAI, and
								// BYOK must never spend the organization's managed-credit balance.
								if gotCost.cost != wantCost.cost || gotCost.usageEstimated != wantCost.usageEstimated {
									t.Errorf("BYOK cost=%q estimate=%q; oracle cost=%q estimate=%q", gotCost.cost, gotCost.usageEstimated, wantCost.cost, wantCost.usageEstimated)
								}
								g, w := gotCost.usage, wantCost.usage
								if g.HasPromptTokens() != w.HasPromptTokens() || g.HasCompletionTokens() != w.HasCompletionTokens() || g.HasReportedCost() != w.HasReportedCost() {
									t.Errorf("BYOK invented or dropped metering evidence: native=%+v oracle=%+v", g, w)
								}
							})
						}
					})
				}
			}
		}
	}
}

func presenceRun(t *testing.T, a upInt2Adapter, surface string, stream, managed bool, usage string) (upInt2Result, costRun) {
	t.Helper()
	var calls atomic.Int32
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := a.path
		if stream && a.name == "gemini" {
			path = ":streamGenerateContent"
		}
		if r.Method != http.MethodPost || !strings.Contains(r.URL.Path, path) {
			w.WriteHeader(404)
			return
		}
		_, _ = io.Copy(io.Discard, r.Body)
		calls.Add(1)
		if stream {
			presenceStream(a, usage)(w)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, upInt2Body(a, usage))
	}))
	defer up.Close()
	probe := &costProbe{}
	g := upInt2Gateway(t, a, up.URL, probe)
	if !managed {
		// Inherit the operator's synthetic endpoint. An org-supplied loopback
		// URL is correctly rejected by production's SSRF policy.
		pc := &tenant.ProviderConfig{Enabled: true, APIKey: "synthetic-byok", APIFormat: a.format, Models: []string{a.model}}
		if a.format == "bedrock" {
			pc.APIKey = ""
			pc.AWSAccessKeyID = "SYNTHETIC"
			pc.AWSSecretAccessKey = "synthetic"
			pc.AWSRegion = "us-east-1"
		}
		g.tenants.Set("org-m", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{upInt2Provider: pc}})
	}
	status, body, header := sendManaged(t, g, surface, a.model, stream, false, nil)
	spent, held := settledAccount(t, g)
	result := upInt2Result{status: status, calls: int(calls.Load()), native: a.format != "openai" && a.format != "azure", mode: header.Get("x-agentcc-billing-mode"), body: body,
		spent: spent, held: held, records: g.outbox("org-m")}
	if status != http.StatusOK || result.calls != 1 {
		t.Fatalf("dispatch control failed: %+v", result)
	}
	return result, probe.last(t, 1)
}
