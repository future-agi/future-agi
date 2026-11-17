package mcp

import (
	"encoding/json"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
)

// Exercise the ordinary HTTP tools/call chain and its typed in-band refusal,
// not just the admission utility. The observer supplies a synthetic tenant
// snapshot at the same seam the approval-kernel regression uses.
func TestW5bHTTPRefusalHasZeroUpstreamCalls(t *testing.T) {
	for _, row := range []struct{ budget, code string }{
		// A configured zero is a permanent block, not a rate window.
		{`{"rpm_per_key":0}`, "tool_denied"},
		{`{"per_tool":{"mock/*":{"rpm":0}}}`, "tool_denied"},
		{`{"max_tool_calls_per_run":0}`, "tool_budget_exhausted"},
	} {
		t.Run(row.budget, func(t *testing.T) {
			limits, org := w5bLimits(t)
			c := newChainServer(t)
			deps := *c.Deps()
			deps.RateLimiter = limits
			deps.ToolBudget = limits
			c.SetDeps(deps)
			calls := 0
			c.hookObserver = func(h string, cc *CallContext) {
				if h == hookCheckRateLimit {
					fixture := w5bCall(org, row.budget)
					cc.OrgConfig = fixture.OrgConfig
					cc.VirtualServer = fixture.VirtualServer
					cc.Principal = fixture.Principal
					cc.Agent = fixture.Agent
				}
				if h == hookCallUpstream {
					calls++
				}
			}
			w := c.callTool(t)
			if w.Code != http.StatusOK || calls != 0 || len(c.up.received()) != 0 {
				t.Fatalf("HTTP %d upstream %d", w.Code, calls)
			}
			var response struct {
				Result struct {
					IsError bool                       `json:"isError"`
					Meta    map[string]json.RawMessage `json:"_meta"`
				} `json:"result"`
			}
			if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil {
				t.Fatal(err)
			}
			var refusal struct {
				Code     string         `json:"code"`
				Metadata map[string]any `json:"metadata"`
			}
			if !response.Result.IsError || json.Unmarshal(response.Result.Meta["io.futureagi/error"], &refusal) != nil || refusal.Code != row.code {
				t.Fatalf("missing typed refusal: %s", response.Result.Meta["io.futureagi/error"])
			}
			if _, retry := refusal.Metadata["retry_after_s"]; retry {
				t.Fatal("a configured zero offered a retry")
			}
			var wire map[string]any
			if err := json.Unmarshal(w.Body.Bytes(), &wire); err != nil {
				t.Fatal(err)
			}
			if errs := schematest.ValidateDef(t, "mcp-denial-v1.schema.json", "Denial", wire["result"]); len(errs) > 0 {
				t.Fatalf("denial contract: %v", errs)
			}
			var evidence struct {
				Executed bool `json:"executed"`
			}
			if err := json.Unmarshal(response.Result.Meta[MetaKeyToolCall], &evidence); err != nil || evidence.Executed {
				t.Fatal("incorrect dispatch evidence", err)
			}
		})
	}
}
