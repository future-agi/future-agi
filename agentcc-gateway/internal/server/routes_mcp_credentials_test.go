package server

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// purgeRecorder is a CredentialResolver that keeps users' tokens: it
// records each purge and answers with n dropped, or err.
type purgeRecorder struct {
	mu     sync.Mutex
	purges [][3]string
	n      int
	err    error
}

func (p *purgeRecorder) Resolve(*mcp.CallContext) (*mcp.AuthOverride, *mcp.Denial) { return nil, nil }

func (p *purgeRecorder) PurgeCredentials(_ context.Context, org, user, server string) (int, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.purges = append(p.purges, [3]string{org, user, server})
	return p.n, p.err
}

// POST /-/mcp/credentials/purge (MC.7) takes the admin token only, names
// its org and server, and drops what it names: one user's tokens, or every
// user's on that server. With no per-user credentials there is nothing to
// drop; a purge that fails says so, so Django retries it.
func TestPurgeRouteDropsCachedCredentials(t *testing.T) {
	srv := newMCPTestServer(t, true, nil)
	const path = "/-/mcp/credentials/purge"

	if w := serve(srv, "POST", path, `{"org_id":"org-a","server_id":"issues"}`, ""); w.Code != http.StatusUnauthorized {
		t.Fatalf("without the admin token = %d", w.Code)
	}
	if w := serve(srv, "POST", path, `{"org_id":"org-a","server_id":"issues"}`, "Bearer not-the-admin-token"); w.Code != http.StatusUnauthorized {
		t.Fatalf("with another token = %d", w.Code)
	}
	if w := serve(srv, "POST", path, `{"org_id":"org-a","server_id":"issues"}`, "Bearer mcp-admin"); w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"purged":0`) {
		t.Fatalf("with no per-user credentials = %d %s, want 200 purged 0", w.Code, w.Body.String())
	}

	rec := &purgeRecorder{n: 2}
	srv.MCP().SetDeps(mcp.Deps{CredentialResolver: rec})
	for body, code := range map[string]string{
		`{"server_id":"issues"}`: "missing_org_id",
		`{"org_id":"org-a"}`:     "missing_server_id",
		`not json`:               "invalid_json",
	} {
		if w := serve(srv, "POST", path, body, "Bearer mcp-admin"); w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), code) {
			t.Fatalf("%s = %d %s, want 400 %s", body, w.Code, w.Body.String(), code)
		}
	}
	w := serve(srv, "POST", path, `{"org_id":"org-a","user_id":"u1","server_id":"issues"}`, "Bearer mcp-admin")
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"purged":2`) || w.Header().Get("Cache-Control") != "no-store" {
		t.Fatalf("one user's purge = %d %s", w.Code, w.Body.String())
	}
	if w := serve(srv, "POST", path, `{"org_id":"org-a","server_id":"issues"}`, "Bearer mcp-admin"); w.Code != http.StatusOK {
		t.Fatalf("every user's purge = %d %s", w.Code, w.Body.String())
	}
	if len(rec.purges) != 2 || rec.purges[0] != [3]string{"org-a", "u1", "issues"} || rec.purges[1] != [3]string{"org-a", "", "issues"} {
		t.Fatalf("purges %v", rec.purges)
	}

	rec.err = mcp.ErrInvalidCredentialKey
	if w := serve(srv, "POST", path, `{"org_id":"org-*","server_id":"issues"}`, "Bearer mcp-admin"); w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "invalid_id") {
		t.Fatalf("an id that names no credential = %d %s, want 400 invalid_id", w.Code, w.Body.String())
	}
	rec.err = errors.New("redis down")
	if w := serve(srv, "POST", path, `{"org_id":"org-a","server_id":"issues"}`, "Bearer mcp-admin"); w.Code != http.StatusServiceUnavailable {
		t.Fatalf("a failing purge = %d, want 503", w.Code)
	}
}

// A legacy cache purger cannot acknowledge a revision fence it never wrote.
// Keep the obligation in Django until a capable replica confirms it.
func TestPurgeRouteRevisionRequiresRevoker(t *testing.T) {
	for _, mode := range []string{"default", "legacy-only", "no-mcp"} {
		t.Run(mode, func(t *testing.T) {
			srv := newMCPTestServer(t, true, nil)
			rec := &purgeRecorder{}
			if mode == "legacy-only" {
				srv.MCP().SetDeps(mcp.Deps{CredentialResolver: rec})
			} else if mode == "no-mcp" {
				srv.mcp.Close()
				srv.mcp = nil
			}
			w := serve(srv, "POST", "/-/mcp/credentials/purge", `{"org_id":"org-a","server_id":"issues","revision":3}`, "Bearer mcp-admin")
			if w.Code != http.StatusNotImplemented || !strings.Contains(w.Body.String(), "revision_revocation_unsupported") {
				t.Fatalf("unsupported revision eviction status=%d; want 501 revision_revocation_unsupported", w.Code)
			}
			if len(rec.purges) != 0 {
				t.Fatal("revision eviction fell back to a legacy purge")
			}
		})
	}
}

// revokeRecorder also fences pushed grants (mcp.PushedGrantRevoker).
type revokeRecorder struct {
	purgeRecorder
	revocations [][2]string
	through     []int64
	revokeErr   error
}

func (p *revokeRecorder) RevokePushedGrants(_ context.Context, org, server string, through int64) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.revocations = append(p.revocations, [2]string{org, server})
	p.through = append(p.through, through)
	return p.revokeErr
}

// A shared static credential's revoke names its grant revision: the route
// revokes the org's pushed grants of that server through it, and only that:
// no person's tokens are purged. A failure answers 503 so Django retries it.
func TestPurgeRouteRevokesPushedGrantsThroughARevision(t *testing.T) {
	srv := newMCPTestServer(t, true, nil)
	const path = "/-/mcp/credentials/purge"
	rec := &revokeRecorder{}
	srv.MCP().SetDeps(mcp.Deps{CredentialResolver: rec})
	for _, body := range []string{
		`{"org_id":"org-a","server_id":"issues","revision":-1}`,
		`{"org_id":"org-a","user_id":"u1","server_id":"issues","revision":3}`,
	} {
		if w := serve(srv, "POST", path, body, "Bearer mcp-admin"); w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "invalid_revision") {
			t.Fatalf("%s = %d %s, want 400 invalid_revision", body, w.Code, w.Body.String())
		}
	}
	if w := serve(srv, "POST", path, `{"org_id":"org-a","server_id":"issues","revision":3}`, "Bearer mcp-admin"); w.Code != http.StatusOK {
		t.Fatalf("a revision eviction = %d %s", w.Code, w.Body.String())
	}
	if len(rec.revocations) != 1 || rec.revocations[0] != [2]string{"org-a", "issues"} || rec.through[0] != 3 || len(rec.purges) != 0 {
		t.Fatalf("revocations %v through %v purges %v", rec.revocations, rec.through, rec.purges)
	}
	rec.revokeErr = mcp.ErrInvalidCredentialKey
	if w := serve(srv, "POST", path, `{"org_id":"org-*","server_id":"issues","revision":3}`, "Bearer mcp-admin"); w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "invalid_id") {
		t.Fatalf("an id that names no credential = %d %s, want 400 invalid_id", w.Code, w.Body.String())
	}
	rec.revokeErr = errors.New("redis down")
	if w := serve(srv, "POST", path, `{"org_id":"org-a","server_id":"issues","revision":3}`, "Bearer mcp-admin"); w.Code != http.StatusServiceUnavailable {
		t.Fatalf("a failing revocation = %d, want 503", w.Code)
	}
}
