package credentials

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// list opens a session as bearer and returns the names tools/list shows it.
func (f *e2e) list(t *testing.T, bearer string) []string {
	t.Helper()
	post := func(session string, msg map[string]any) *httptest.ResponseRecorder {
		body, _ := json.Marshal(msg)
		req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		req.Header.Set("Authorization", "Bearer "+bearer)
		if session != "" {
			req.Header.Set("MCP-Session-Id", session)
		}
		w := httptest.NewRecorder()
		f.srv.HandlePost(w, req)
		return w
	}
	w := post("", map[string]any{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": map[string]any{
		"protocolVersion": mcp.ProtocolVersion, "capabilities": map[string]any{}, "clientInfo": map[string]any{"name": "m25-list", "version": "1"}}})
	session := w.Header().Get("MCP-Session-Id")
	if session == "" {
		t.Fatalf("initialize as %s: %d %s", bearer, w.Code, w.Body.String())
	}
	w = post(session, map[string]any{"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
	var resp struct {
		Result struct {
			Tools []struct {
				Name string `json:"name"`
			} `json:"tools"`
		} `json:"result"`
		Error json.RawMessage `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil || len(resp.Error) > 0 {
		t.Fatalf("tools/list as %s: %d %s", bearer, w.Code, w.Body.String())
	}
	names := make([]string, 0, len(resp.Result.Tools))
	for _, tl := range resp.Result.Tools {
		names = append(names, tl.Name)
	}
	return names
}

func perUserTools(names []string) []string {
	var out []string
	for _, n := range names {
		if strings.HasPrefix(n, "issues") {
			out = append(out, n)
		}
	}
	return out
}

// A per_user server has no account of the organization's own, so the pool
// learns its tools only through a member's own account: the first member
// with an account who opens the organization's MCP endpoint lists the server
// with that account and nothing else. That listing is
// the member's own: another member with no account sees none of it, is asked
// to connect when they call one of its tools and nothing is sent upstream for
// them, and another organization's members see nothing of that listing.
func TestPerUserServerListsThroughAMembersOwnAccount(t *testing.T) {
	f := newE2EPool(t, false)
	exp := f.clock.now().Add(time.Hour)
	f.django.give(keyA1, "m25-test-token-a-u1", exp, 1)

	first := perUserTools(f.list(t, callerA1))
	if !slices.Contains(first, "issues__echo") {
		t.Fatalf("the first member with an account sees %v of the per_user server, want its tools (issues__echo among them)", first)
	}
	// The shared server "wiki" lists with its own pushed credential; every
	// other listing the upstream got is the per_user server's.
	shared := map[string]bool{}
	for _, org := range []string{"org-a", "org-b"} {
		v := "m25-test-shared-wiki-" + org
		shared[bearerHash(v)] = true
		sum := sha256.Sum256([]byte(v))
		shared[hex.EncodeToString(sum[:])] = true
	}
	perUserLists := 0
	for i, r := range f.mock.State().ReceivedLast(0, "tools/list") {
		if shared[r.Auth.SHA256] {
			continue
		}
		perUserLists++
		if r.Auth.SHA256 != bearerHash("m25-test-token-a-u1") {
			t.Fatalf("upstream tools/list %d carried another credential than the listing member's own", i)
		}
	}
	if perUserLists == 0 {
		t.Fatal("the per_user server was never listed upstream")
	}

	if got := perUserTools(f.list(t, callerA2)); len(got) != 0 {
		t.Fatalf("another member of the organization with no account sees %v of the first member's listing, want none", got)
	}
	_, den := f.call(t, callerA2, "issues__echo")
	if den == nil || den["code"] != mcp.CodeAuthRequired || metaOf(den)["reason"] != mcp.AuthReasonNoToken {
		t.Fatalf("a member with no account calling issues__echo: %v, want auth_required no_token", den)
	}
	if _, den := f.call(t, callerA1, "issues__echo"); den != nil {
		t.Fatalf("the member with an account calling issues__echo: %v", den)
	}
	if got := f.upstreamAuth(); len(got) != 1 || got[0] != bearerHash("m25-test-token-a-u1") {
		t.Fatalf("the upstream got %d tools/call, want only the connected member's, with their own token", len(got))
	}

	if got := perUserTools(f.list(t, callerB1)); len(got) != 0 {
		t.Fatalf("a member of another organization with no account sees %v of org-a's listing, want none", got)
	}
}
