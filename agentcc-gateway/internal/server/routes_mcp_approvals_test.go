package server

import (
	"net/http"
	"testing"
)

// Baseline-seam red: the approval route must exist and enforce admin auth,
// rather than falling through to the router's 404.
func TestW5ApprovalAdminRouteExists(t *testing.T) {
	s := newTenancyServer(t, nil)
	w := serve(s, "GET", "/-/mcp/approvals?org_id=synthetic-org", "", "")
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("approval admin route = %d %s, want 401", w.Code, w.Body.String())
	}
}
