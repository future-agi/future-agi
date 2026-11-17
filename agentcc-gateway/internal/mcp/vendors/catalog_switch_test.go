package vendors_test

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// TestW4bCatalogSwitchOffServesNoCatalogServer pins the release switch the
// rigs turn on. With mcp.catalog_enabled off (tenancy/spec.go
// orgSpecsForRelease, 28c5d0ecd4), a server added from the catalog (its
// catalog_ref set) is never built: a person holding a valid token gets the
// plain unknown-tool refusal, and no token is fetched.
func TestW4bCatalogSwitchOffServesNoCatalogServer(t *testing.T) {
	f := newFixture(t, "google", "gmail", func(f *fixture) { f.catalogOff = true })
	f.give(t, "org-a", "u1")
	_, result, rpcErr := f.callEnvelope(t, userCaller("org-a", "u1"))
	var refusal struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
	}
	if err := json.Unmarshal(rpcErr, &refusal); err != nil || len(result) != 0 {
		t.Fatalf("switch off: result %s, error %s; want the unknown-tool refusal", result, rpcErr)
	}
	if refusal.Code != mcp.ErrCodeInvalidParams || !strings.Contains(refusal.Message, "Unknown tool: service__echo") {
		t.Fatalf("switch off: error %d %q; want %d Unknown tool: service__echo", refusal.Code, refusal.Message, mcp.ErrCodeInvalidParams)
	}
	equal(t, len(f.cp.fetched()), 0)
}
