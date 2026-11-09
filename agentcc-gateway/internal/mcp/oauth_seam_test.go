package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The resource server seam (M23 on M8): the 401 names the metadata of the
// address called, and a token's scope bounds the methods it may call.

// m23PRM is the endpoint's protected-resource metadata URL the wiring sets.
const m23PRM = "https://gw.example.test/.well-known/oauth-protected-resource/mcp"

// fakeTokenVerifier accepts the tokens it maps to principals, refuses any
// other "oauth-" token, and leaves everything else to key authentication.
type fakeTokenVerifier map[string]Principal

func (f fakeTokenVerifier) Verify(_ context.Context, token, _ string) (*Principal, error) {
	if p, ok := f[token]; ok {
		return &p, nil
	}
	if strings.HasPrefix(token, "oauth-") {
		return nil, errors.New("refused")
	}
	return nil, nil
}

// postMCPTo posts msg to path, addressed to the virtual server vsID ("" for
// the endpoint itself), as the virtual-server route hands it over.
func postMCPTo(t *testing.T, s *Server, path, vsID string, msg *Message, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	body, err := json.Marshal(msg)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest("POST", path, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	if vsID != "" {
		req = WithVirtualServer(req, vsID)
	}
	w := httptest.NewRecorder()
	s.HandlePost(w, req)
	return w
}

// Every MCP 401 sends the client to the metadata of the resource it called:
// the endpoint's for the endpoint, and each virtual server's own for that
// virtual server, whether the credential was missing or refused.
func TestChallengeCarriesResourceMetadata(t *testing.T) {
	s, _ := newIdentityServer(t, false)
	s.SetDeps(Deps{ResourceMetadataURL: m23PRM, BearerVerifier: fakeTokenVerifier{}})

	cases := []struct {
		path, vs, want string
	}{
		{"/mcp", "", m23PRM},
		{"/mcp/v/vs_B", "vs_B", m23PRM + "/v/vs_B"},
		{"/mcp/v/vs_01M4ZEB9E0J3AADR85Z4T4428X", "vs_01M4ZEB9E0J3AADR85Z4T4428X", m23PRM + "/v/vs_01M4ZEB9E0J3AADR85Z4T4428X"},
	}
	for _, tc := range cases {
		for label, headers := range map[string]map[string]string{
			"no credential":   nil,
			"refused token":   bearer("oauth-refused"),
			"invalid api key": bearer("not-a-key"),
		} {
			w := postMCPTo(t, s, tc.path, tc.vs, toolsListMsg(), headers)
			requireUnauthorized(t, w, tc.path+" with "+label)
			if got, want := w.Header().Get("WWW-Authenticate"), models.BearerChallenge+`, resource_metadata="`+tc.want+`"`; got != want {
				t.Fatalf("%s with %s: challenge %q, want %q", tc.path, label, got, want)
			}
		}
	}

	// Without a metadata URL (OAuth off) the challenge stays the plain one,
	// on a virtual server too.
	s.SetDeps(Deps{})
	w := postMCPTo(t, s, "/mcp/v/vs_B", "vs_B", toolsListMsg(), nil)
	if got := w.Header().Get("WWW-Authenticate"); got != models.BearerChallenge {
		t.Fatalf("challenge with no metadata URL %q, want exactly %q", got, models.BearerChallenge)
	}
}

// requireInsufficientScope checks a 403 for a method the token's scope does
// not grant: the Bearer challenge names the error, the scope needed and the
// resource's metadata, and the JSON-RPC error's data says the same.
func requireInsufficientScope(t *testing.T, w *httptest.ResponseRecorder, scope, what string) {
	t.Helper()
	if w.Code != http.StatusForbidden {
		t.Fatalf("%s: status %d %s, want 403", what, w.Code, w.Body.String())
	}
	want := models.BearerChallenge + `, error="insufficient_scope", scope="` + scope + `", resource_metadata="` + m23PRM + `"`
	if got := w.Header().Get("WWW-Authenticate"); got != want {
		t.Fatalf("%s: challenge %q, want %q", what, got, want)
	}
	var msg struct {
		Error *struct {
			Code int `json:"code"`
			Data struct {
				Code  string `json:"code"`
				Scope string `json:"scope"`
			} `json:"data"`
		} `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &msg); err != nil || msg.Error == nil ||
		msg.Error.Data.Code != "insufficient_scope" || msg.Error.Data.Scope != scope {
		t.Fatalf("%s: body %s, want a JSON-RPC error with data.code insufficient_scope and scope %s", what, w.Body.String(), scope)
	}
}

// An access token grants only the method families its scope names: tools/*
// need mcp:tools, resources/* mcp:resources and prompts/* mcp:prompts. A key
// and a callback token carry no scope and are not checked.
func TestInsufficientScope403(t *testing.T) {
	s, _ := newIdentityServer(t, false)
	s.SetDeps(Deps{ResourceMetadataURL: m23PRM, BearerVerifier: fakeTokenVerifier{
		"oauth-prompts": {Kind: PrincipalOAuth, OrgID: m8Org, UserID: "user-1", ClientID: "client-1", Scopes: []string{"mcp:prompts"}, TokenID: "t1"},
		"oauth-tools":   {Kind: PrincipalOAuth, OrgID: m8Org, UserID: "user-1", ClientID: "client-1", Scopes: []string{"mcp:tools"}, TokenID: "t2"},
		// A callback token's claims carry no scope (mcp-access-token-claims-v1,
		// CallbackClaims): its allowed_tools bound it instead (M10, M34).
		"oauth-callback": {Kind: PrincipalCallback, OrgID: m8Org, KeyID: "key-1", TokenID: "t3"},
	}})

	promptsOnly := initializeSessionWith(t, s, bearer("oauth-prompts"))
	h := withHeaders(bearer("oauth-prompts"), "MCP-Session-Id", promptsOnly)
	requireInsufficientScope(t, postMCP(t, s, toolsListMsg(), h), "mcp:tools", "tools/list with mcp:prompts")
	call := &Message{JSONRPC: "2.0", ID: json.RawMessage(`3`), Method: MethodToolsCall, Params: json.RawMessage(`{"name":"gh_t1","arguments":{}}`)}
	requireInsufficientScope(t, postMCP(t, s, call, h), "mcp:tools", "tools/call with mcp:prompts")
	resources := &Message{JSONRPC: "2.0", ID: json.RawMessage(`4`), Method: MethodResourcesList}
	requireInsufficientScope(t, postMCP(t, s, resources, h), "mcp:resources", "resources/list with mcp:prompts")
	prompts := &Message{JSONRPC: "2.0", ID: json.RawMessage(`5`), Method: MethodPromptsList}
	if w := postMCP(t, s, prompts, h); w.Code != http.StatusOK || decodeResponse(t, w).Error != nil {
		t.Fatalf("prompts/list with mcp:prompts = %d %s, want served", w.Code, w.Body.String())
	}

	toolsOnly := initializeSessionWith(t, s, bearer("oauth-tools"))
	w := postMCP(t, s, toolsListMsg(), withHeaders(bearer("oauth-tools"), "MCP-Session-Id", toolsOnly))
	if resp := decodeResponse(t, w); w.Code != http.StatusOK || resp.Error != nil {
		t.Fatalf("tools/list with mcp:tools = %d %+v, want served", w.Code, resp.Error)
	}

	callbackSession := initializeSessionWith(t, s, bearer("oauth-callback"))
	w = postMCP(t, s, toolsListMsg(), withHeaders(bearer("oauth-callback"), "MCP-Session-Id", callbackSession))
	if w.Code == http.StatusForbidden {
		t.Fatalf("tools/list with a callback token = 403 %s, want no scope check: its claims carry no scope", w.Body.String())
	}

	keySession := initializeSessionWith(t, s, bearer(m8KeyShared))
	w = postMCP(t, s, toolsListMsg(), withHeaders(bearer(m8KeyShared), "MCP-Session-Id", keySession))
	if resp := decodeResponse(t, w); w.Code != http.StatusOK || resp.Error != nil {
		t.Fatalf("tools/list with a key = %d %+v, want served as before", w.Code, resp.Error)
	}
}
