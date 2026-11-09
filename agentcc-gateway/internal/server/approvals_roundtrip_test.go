package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/oklog/ulid/v2"
)

type w5Keys struct{ org string }

func (k w5Keys) AuthenticateKey(raw string) (mcp.KeyGrant, bool) {
	switch raw {
	case "w5-owner":
		return mcp.KeyGrant{KeyID: "owner", OrgID: k.org}, true
	case "w5-other":
		return mcp.KeyGrant{KeyID: "other", OrgID: k.org}, true
	}
	return mcp.KeyGrant{}, false
}

type w5Bearer struct{ org string }

func (v w5Bearer) Verify(_ context.Context, token, audience string) (*mcp.Principal, error) {
	if token != "w5-oauth" && token != "w5-oauth-other" {
		return nil, nil
	}
	if audience != "/mcp/v/vs_01M4ZEB9E0J3AADR85Z4T4428X" {
		return nil, errors.New("wrong audience")
	}
	user := "owner"
	if token == "w5-oauth-other" {
		user = "other"
	}
	return &mcp.Principal{Kind: mcp.PrincipalOAuth, OrgID: v.org, UserID: user, ClientID: "client", Scopes: []string{"mcp:tools"}}, nil
}

type w5HTTPFixture struct {
	s       *Server
	up      *mcpmock.Mock
	upURL   string
	org     string
	manager *mcp.ApprovalManager
}

func w5Gateway(t *testing.T) *w5HTTPFixture {
	t.Helper()
	return w5GatewayForOrg(t, "")
}

func w5GatewayForOrg(t *testing.T, org string) *w5HTTPFixture {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("TEST_REDIS_ADDR required")
	}
	rc, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: 14})
	if err != nil {
		t.Fatal(err)
	}
	if org == "" {
		org = "w5-http-" + ulid.Make().String()
	}
	t.Cleanup(func() {
		keys, _ := rc.Redis().Keys(context.Background(), "mapr:{"+org+"}:*").Result()
		if len(keys) > 0 {
			rc.Redis().Del(context.Background(), keys...)
		}
		rc.Close()
	})
	up, upURL := w5MockUpstream(t)
	s := newTenancyServer(t, nil)
	// Match executable tenancy wiring: the admin pool alone does not install
	// the registry source used to compose client-facing virtual server tools.
	s.MCP().Registry().SetOrgSource(func(orgID string) mcp.MemberSource {
		_, pool, err := s.MCP().Tenancy().Resolve(orgID)
		if err != nil {
			return nil
		}
		return mcp.PoolMembers(pool, s.MCP().Registry().Separator())
	})
	codec, err := mcp.NewStateCodec([]string{"synthetic-w5-state-material-not-a-real-secret"}, false)
	if err != nil {
		t.Fatal(err)
	}
	manager := mcp.NewApprovalManager(redisstate.NewMCPApprovalStore(rc), codec, nil)
	deps := *s.MCP().Deps()
	// The tools/call path unwraps a real MCP client; an admin-only fake
	// implements listings but cannot exercise transport dispatch.
	deps.ClientFactory = func(cfg mcp.ClientConfig) (mcp.UpstreamClient, error) {
		if cfg.URL != upURL {
			return nil, errors.New("only the synthetic loopback MCP upstream is allowed")
		}
		return mcp.DefaultClientFactory(cfg)
	}
	deps.Approvals = manager
	deps.BearerVerifier = w5Bearer{org}
	deps.Pins = mcp.OrgPins{}
	s.MCP().SetDeps(deps)
	s.MCP().SetKeyAuth(w5Keys{org})
	f := &w5HTTPFixture{s: s, up: up, upURL: upURL, org: org, manager: manager}
	f.configure(t, "")
	deadline := time.Now().Add(3 * time.Second)
	for {
		w := serve(s, "GET", "/-/mcp/tools?org_id="+org, "", "Bearer mcp-admin")
		if strings.Contains(w.Body.String(), "delete_repo") {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("pool not ready: %s", w.Body.String())
		}
		time.Sleep(10 * time.Millisecond)
	}
	return f
}
func (f *w5HTTPFixture) configure(t *testing.T, extra string) {
	// extra replaces named fields via a map, never duplicate JSON properties.
	raw := `{"mcp":{"enabled":true,"access":{"default":"allow"},"servers":{"mock":{"url":"http://synthetic.example"}},"virtual_servers":{"review":{"id":"vs_01M4ZEB9E0J3AADR85Z4T4428X","members":["mock"],"access":{"default_for_org":true},"approval":{"require":"listed","tools":["mock/*"]}},"other":{"id":"vs_01M4ZEB9E0J3AADR85Z4T4428Y","members":["mock"]}}}}`
	var obj map[string]any
	_ = json.Unmarshal([]byte(raw), &obj)
	mc := obj["mcp"].(map[string]any)
	mc["servers"].(map[string]any)["mock"].(map[string]any)["url"] = f.upURL
	vs := mc["virtual_servers"].(map[string]any)["review"].(map[string]any)
	switch extra {
	case "agent":
		vs["access"] = map[string]any{"agents": []string{"approval-agent"}}
	case "deny":
		vs["access"] = map[string]any{"default_for_org": true, "rules": []any{map[string]any{"deny": []string{"mock/*"}}}}
	case "hidden":
		vs["tools"] = map[string]any{"exclude": []string{"mock/*"}}
	case "quarantine":
		mc["pinning"] = map[string]any{"pins": map[string]string{"mock/delete_repo": strings.Repeat("0", 64)}}
	}
	b, _ := json.Marshal(obj)
	putOrg(t, f.s, f.org, string(b))
}
func (f *w5HTTPFixture) count() int { return len(f.up.State().ReceivedLast(0, "tools/call")) }
func w5Request(s *Server, method, path, body, token, session string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("Accept", "application/json, text/event-stream")
	r.Header.Set("MCP-Protocol-Version", "2025-06-18")
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	if session != "" {
		r.Header.Set("MCP-Session-Id", session)
	}
	w := httptest.NewRecorder()
	s.httpServer.Handler.ServeHTTP(w, r)
	return w
}
func (f *w5HTTPFixture) session(t *testing.T, path, token string) string {
	t.Helper()
	w := w5Request(f.s, "POST", path, `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"synthetic","version":"1"}}}`, token, "")
	id := w.Header().Get("Mcp-Session-Id")
	if w.Code != 200 || id == "" {
		t.Fatalf("initialize=%d %s", w.Code, w.Body.String())
	}
	return id
}
func (f *w5HTTPFixture) call(path, token, session, id string) *httptest.ResponseRecorder {
	params := map[string]any{"name": "mock__delete_repo", "arguments": map[string]any{"repo": "synthetic"}}
	if id != "" {
		params["_meta"] = map[string]any{"io.futureagi/approval_id": id}
	}
	b, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params})
	return w5Request(f.s, "POST", path, string(b), token, session)
}
func w5ApprovalID(t *testing.T, w *httptest.ResponseRecorder) string {
	t.Helper()
	var body struct {
		Result struct {
			IsError bool                       `json:"isError"`
			Meta    map[string]json.RawMessage `json:"_meta"`
		} `json:"result"`
	}
	if json.Unmarshal(w.Body.Bytes(), &body) != nil || w.Code != 200 || !body.Result.IsError {
		t.Fatalf("pending=%d %s", w.Code, w.Body.String())
	}
	var e struct {
		Code     string         `json:"code"`
		Metadata map[string]any `json:"metadata"`
	}
	_ = json.Unmarshal(body.Result.Meta["io.futureagi/error"], &e)
	if e.Code != "approval_required" || len(e.Metadata) != 5 || e.Metadata["status"] != "pending" {
		t.Fatalf("pending metadata=%s", w.Body.String())
	}
	return e.Metadata["approval_id"].(string)
}
func w5Schema(t *testing.T, w *httptest.ResponseRecorder) {
	t.Helper()
	var rec any
	if err := json.Unmarshal(w.Body.Bytes(), &rec); err != nil {
		t.Fatal(err)
	}
	if errs := schematest.ValidateDef(t, "../agentcc-admin.schema.json", "MCPApproval", rec); len(errs) > 0 {
		t.Fatalf("record schema: %v; %s", errs, w.Body.String())
	}
}
func TestW5ApprovalDecisionPollRetryOnce(t *testing.T) {
	f := w5Gateway(t)
	path := "/mcp/v/vs_01M4ZEB9E0J3AADR85Z4T4428X"
	session := f.session(t, path, "w5-owner")
	pending := f.call(path, "w5-owner", session, "")
	id := w5ApprovalID(t, pending)
	if f.count() != 0 {
		t.Fatal("pending reached upstream")
	}
	body := fmt.Sprintf(`{"org_id":%q,"decision":"approve","actor":"synthetic-admin"}`, f.org)
	w := serve(f.s, "POST", "/-/mcp/approvals/"+id+"/decision", body, "Bearer mcp-admin")
	if w.Code != 200 {
		t.Fatalf("decision=%d %s", w.Code, w.Body.String())
	}
	w5Schema(t, w)
	w = w5Request(f.s, "GET", path+"/approvals/"+id, "", "w5-owner", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"status":"approved"`) {
		t.Fatalf("poll=%d %s", w.Code, w.Body.String())
	}
	w5Schema(t, w)
	w = f.call(path, "w5-owner", session, id)
	if w.Code != 200 || strings.Contains(w.Body.String(), `"isError":true`) || f.count() != 1 {
		t.Fatalf("retry=%d count=%d %s", w.Code, f.count(), w.Body.String())
	}
	w = f.call(path, "w5-owner", session, id)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"isError":true`) || f.count() != 1 {
		t.Fatalf("replay=%d count=%d %s", w.Code, f.count(), w.Body.String())
	}
}
func TestW5ApprovalPollAudience(t *testing.T) {
	for _, token := range []string{"w5-owner", "w5-oauth"} {
		t.Run(token, func(t *testing.T) {
			f := w5Gateway(t)
			path := "/mcp/v/vs_01M4ZEB9E0J3AADR85Z4T4428X"
			session := f.session(t, path, token)
			id := w5ApprovalID(t, f.call(path, token, session, ""))
			for _, tc := range []struct {
				path, token string
				code        int
			}{
				{path, token, 200}, {path, "w5-other", 404}, {path, "w5-oauth-other", 404}, {path, "", 401}, {"/mcp/v/vs_01M4ZEB9E0J3AADR85Z4T4428Y", token, 404},
			} {
				want := tc.code
				if token == "w5-oauth" && tc.path != path {
					want = 401
				}
				w := w5Request(f.s, "GET", tc.path+"/approvals/"+id, "", tc.token, "")
				if w.Code != want {
					t.Fatalf("poll %s as %s=%d %s want %d", tc.path, tc.token, w.Code, w.Body.String(), want)
				}
				if w.Code == 200 {
					w5Schema(t, w)
					if strings.Contains(w.Body.String(), `"arguments"`) || strings.Contains(w.Body.String(), "credential") {
						t.Fatal("poll leaked private material")
					}
				}
			}
		})
	}
}
func TestW5ApprovalDecisionOrgAndAdminAuth(t *testing.T) {
	f := w5Gateway(t)
	path := "/mcp/v/vs_01M4ZEB9E0J3AADR85Z4T4428X"
	session := f.session(t, path, "w5-owner")
	id := w5ApprovalID(t, f.call(path, "w5-owner", session, ""))
	route := "/-/mcp/approvals/" + id + "/decision"
	valid := fmt.Sprintf(`{"org_id":%q,"decision":"approve","actor":"admin"}`, f.org)
	for _, token := range []string{"", "Bearer wrong"} {
		if w := serve(f.s, "POST", route, valid, token); w.Code != 401 {
			t.Fatalf("admin auth=%d", w.Code)
		}
	}
	if w := serve(f.s, "POST", route, `{"decision":"approve","actor":"admin"}`, "Bearer mcp-admin"); w.Code != 400 {
		t.Fatalf("missing org=%d", w.Code)
	}
	wrong := serve(f.s, "POST", route, `{"org_id":"other","decision":"approve","actor":"admin"}`, "Bearer mcp-admin")
	unknown := serve(f.s, "POST", "/-/mcp/approvals/unknown/decision", valid, "Bearer mcp-admin")
	if wrong.Code != 404 || unknown.Code != 404 || wrong.Body.String() != unknown.Body.String() {
		t.Fatalf("not-found oracle: %s vs %s", wrong.Body.String(), unknown.Body.String())
	}
	w := serve(f.s, "GET", "/-/mcp/approvals?org_id="+f.org+"&status=pending", "", "Bearer mcp-admin")
	var list struct {
		Approvals []mcp.ApprovalRecord `json:"approvals"`
	}
	_ = json.Unmarshal(w.Body.Bytes(), &list)
	if w.Code != 200 || len(list.Approvals) != 1 || list.Approvals[0].ID != id {
		t.Fatalf("list=%s", w.Body.String())
	}
	if errs := schematest.ValidateDef(t, "../agentcc-admin.schema.json", "MCPApproval", list.Approvals[0]); len(errs) > 0 {
		t.Fatal(errs)
	}
	denied := strings.Replace(valid, `"approve"`, `"deny"`, 1)
	w = serve(f.s, "POST", route, denied, "Bearer mcp-admin")
	if w.Code != 200 {
		t.Fatalf("deny=%s", w.Body.String())
	}
	if w = f.call(path, "w5-owner", session, id); !strings.Contains(w.Body.String(), `"isError":true`) || f.count() != 0 {
		t.Fatal("denied approval executed")
	}
}
func TestW5ApprovalNoAuthorityWidening(t *testing.T) {
	for _, mode := range []string{"deny", "hidden", "quarantine", "callback"} {
		t.Run(mode, func(t *testing.T) {
			f := w5Gateway(t)
			path := "/mcp/v/vs_01M4ZEB9E0J3AADR85Z4T4428X"
			owner := mcp.Principal{Kind: mcp.PrincipalKey, KeyID: "owner", OrgID: f.org}
			if mode == "callback" {
				deps := *f.s.MCP().Deps()
				deps.BearerVerifier = w5Callback{f.org, []string{"mock/delete_repo"}}
				f.s.MCP().SetDeps(deps)
				owner.Kind, owner.TokenID = mcp.PrincipalCallback, "callback"
			}
			session := f.session(t, path, "w5-owner")
			id := w5ApprovalID(t, f.call(path, "w5-owner", session, ""))
			if _, err := f.manager.Decide(context.Background(), id, mcp.ApprovalDecision{OrgID: f.org, Decision: "approve", Actor: "admin"}); err != nil {
				t.Fatal(err)
			}
			if mode == "callback" {
				deps := *f.s.MCP().Deps()
				deps.BearerVerifier = w5Callback{f.org, []string{"mock/other"}}
				f.s.MCP().SetDeps(deps)
				session = f.session(t, path, "w5-owner")
			} else {
				f.configure(t, mode)
			}
			w := f.call(path, "w5-owner", session, id)
			if f.count() != 0 || (!strings.Contains(w.Body.String(), `"isError":true`) && !strings.Contains(w.Body.String(), `"error":`)) {
				t.Fatalf("authority widened: %s", w.Body.String())
			}
			if mode == "callback" && !strings.Contains(w.Body.String(), `"tool_denied"`) {
				t.Fatalf("callback denial was not enforced before approvals: %s", w.Body.String())
			}
			rec, err := f.manager.Poll(context.Background(), id, owner, "vs_01M4ZEB9E0J3AADR85Z4T4428X")
			if err != nil || rec.Status != "approved" {
				t.Fatalf("earlier deny consumed approval: %+v %v", rec, err)
			}
		})
	}
}

type w5Callback struct {
	org   string
	tools []string
}

func (v w5Callback) Verify(context.Context, string, string) (*mcp.Principal, error) {
	return &mcp.Principal{Kind: mcp.PrincipalCallback, OrgID: v.org, KeyID: "owner", TokenID: "callback", AllowedTools: v.tools}, nil
}
