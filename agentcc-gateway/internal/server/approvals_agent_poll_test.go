package server

import (
	"context"
	"errors"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Synthetic verifier follows the normal ClaimsVerifier seam; no live issuer.
// The audience stays the MCP resource for both the call and its poll route.
type w5RuntimeClaims struct{ org string }

func (v w5RuntimeClaims) Verify(ctx context.Context, token, audience string) (*mcp.Principal, error) {
	p, _, err := v.VerifyClaims(ctx, token, audience)
	return p, err
}
func (v w5RuntimeClaims) VerifyClaims(_ context.Context, token, audience string) (*mcp.Principal, *models.RuntimeIdentity, error) {
	if audience != "/mcp/v/vs_01M4ZEB9E0J3AADR85Z4T4428X" {
		return nil, nil, errors.New("wrong audience")
	}
	if token != "w5-runtime-owner" && token != "w5-runtime-other" {
		return nil, nil, errors.New("unknown synthetic token")
	}
	p := &mcp.Principal{Kind: mcp.PrincipalRuntime, OrgID: v.org, TokenID: token}
	claims := &models.RuntimeIdentity{TokenID: token, OrgID: v.org, AgentID: "approval-agent", RootRunID: "approval-run"}
	return p, claims, nil
}

func TestW5ApprovalPollAgentClaims(t *testing.T) {
	f := w5Gateway(t)
	deps := *f.s.MCP().Deps()
	deps.BearerVerifier = w5RuntimeClaims{f.org}
	f.s.MCP().SetDeps(deps)
	f.configure(t, "agent")
	path := "/mcp/v/vs_01M4ZEB9E0J3AADR85Z4T4428X"
	session := f.session(t, path, "w5-runtime-owner")
	id := w5ApprovalID(t, f.call(path, "w5-runtime-owner", session, ""))
	if f.count() != 0 {
		t.Fatal("pending runtime call reached upstream")
	}
	poll := path + "/approvals/" + id
	w := w5Request(f.s, "GET", poll, "", "w5-runtime-owner", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"status":"pending"`) {
		t.Fatalf("agent-restricted poll=%d %s", w.Code, w.Body.String())
	}
	w5Schema(t, w)
	if w = w5Request(f.s, "GET", poll, "", "w5-runtime-other", ""); w.Code != 404 {
		t.Fatalf("another runtime principal polled the row: %d %s", w.Code, w.Body.String())
	}
	// A conflict with the authenticated agent cannot select another identity.
	r := httptest.NewRequest("GET", poll, nil)
	r.Header.Set("Authorization", "Bearer w5-runtime-owner")
	r.Header.Set("X-Agentcc-Agent-Id", "another-agent")
	w = httptest.NewRecorder()
	f.s.httpServer.Handler.ServeHTTP(w, r)
	if w.Code != 404 {
		t.Fatalf("conflicting agent claim accepted: %d %s", w.Code, w.Body.String())
	}
}
