package vendors_test

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/restmcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/mcp/vendors"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// hostedFixture is the K2 hosted runtime wiring under test: a real
// agentcc-connectors runtime (restmcp over the embedded catalog) whose Gmail
// and Microsoft Graph origins are replayed from a loopback fake that records
// each bearer it receives; a plain per_user MCP server added by URL
// (agentcc-mcpmock); the credential resolver over a mocked mcp-upstream-token-v1
// control plane; and the gateway's MCP server with the selected credential
// step (hostedStep). Everything is loopback and synthetic.
type hostedFixture struct {
	srv      *mcp.Server
	cp       *controlPlane
	resolver *credentials.Resolver
	catalog  *manifest.Catalog
	store    *tenant.Store
	runtime  string
	vendor   *vendorAPI
	plain    *mcpmock.Mock
	plainURL string
	step     mcp.CredentialResolver
}

// vendorAPI answers the two read tools the tests call, as Gmail's and
// Graph's REST APIs would, and records the bearer of each request (hashed).
type vendorAPI struct {
	mu      sync.Mutex
	bearers []string
	paths   []string
}

func (v *vendorAPI) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	sum := sha256.Sum256([]byte(r.Header.Get("Authorization")))
	v.mu.Lock()
	v.bearers = append(v.bearers, hex.EncodeToString(sum[:]))
	v.paths = append(v.paths, r.URL.Path)
	v.mu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	switch {
	case r.Method == http.MethodGet && r.URL.Path == "/gmail/v1/users/me/profile":
		_, _ = io.WriteString(w, `{"emailAddress":"member@example.test","messagesTotal":3,"threadsTotal":2}`)
	case r.Method == http.MethodGet && r.URL.Path == "/v1.0/me/mailFolders":
		_, _ = io.WriteString(w, `{"value":[{"id":"inbox","displayName":"Inbox","unreadItemCount":1}]}`)
	default:
		w.WriteHeader(http.StatusNotFound)
		_, _ = io.WriteString(w, `{"error":{"code":404,"message":"Not Found"}}`)
	}
}

func (v *vendorAPI) seen() []string {
	v.mu.Lock()
	defer v.mu.Unlock()
	return append([]string(nil), v.bearers...)
}

// pilotEntry is a pushed server added from a K2 pilot provider.
func pilotEntry(url, app, version string) map[string]any {
	return map[string]any{"url": url, "catalog_ref": app + "/futureagi-rest@" + version, "credential": map[string]any{"mode": "per_user"}}
}

// newHostedFixture builds the fixture. servers gives each org's pushed MCP
// servers once the runtime and the plain server have their URLs.
func newHostedFixture(t *testing.T, servers func(f *hostedFixture) map[string]any) *hostedFixture {
	t.Helper()
	rdb, prefix := testRedis(t)
	f := &hostedFixture{vendor: &vendorAPI{}, store: tenant.NewStore()}
	cacheCodec, err := mcp.NewStateCodec([]string{stateSecret}, false)
	if err != nil {
		t.Fatal(err)
	}
	links, err := credentials.ConnectCodec(channelSecret)
	if err != nil {
		t.Fatal(err)
	}
	f.cp = newControlPlane(t)
	tc, err := credentials.NewTokenClient(credentials.TokenClientOptions{BaseURL: f.cp.url, Secret: channelSecret})
	if err != nil {
		t.Fatal(err)
	}
	cache := credentials.NewTokenCache(rdb, cacheCodec, credentials.CacheOptions{Prefix: prefix})
	f.resolver, err = credentials.NewResolver(credentials.ResolverOptions{Tokens: tc, Cache: cache, Codec: cacheCodec, ConnectCodec: links, DjangoBaseURL: f.cp.url})
	if err != nil {
		t.Fatal(err)
	}
	cat, err := connectors.Load("")
	if err != nil {
		t.Fatal(err)
	}
	f.catalog = withClientSources(t, cat.Records, []string{"org"})
	apps, err := restmcp.Compile(cat.Records)
	if err != nil {
		t.Fatal(err)
	}
	api := httptest.NewServer(f.vendor)
	t.Cleanup(api.Close)
	quiet := slog.New(slog.NewTextHandler(io.Discard, nil))
	k2, err := restmcp.New(apps, restmcp.Options{Logger: quiet,
		Replay: map[string]string{"https://gmail.googleapis.com": api.URL, "https://graph.microsoft.com": api.URL}})
	if err != nil {
		t.Fatal(err)
	}
	runtime := httptest.NewServer(k2)
	t.Cleanup(runtime.Close)
	f.runtime = runtime.URL
	fix, err := mcpmock.LoadFixtures("../../../scripts/scenarios/fixtures/mcp/common")
	if err != nil {
		t.Fatal(err)
	}
	era, err := mcpmock.ParseEra("2025-06-18")
	if err != nil {
		t.Fatal(err)
	}
	f.plain, err = mcpmock.New(mcpmock.Config{Era: era, Auth: "oauth", Path: "/mcp", ServerName: "k2-plain"}, fix)
	if err != nil {
		t.Fatal(err)
	}
	plain := httptest.NewServer(f.plain.Handler())
	t.Cleanup(plain.Close)
	f.plain.SetPublicURL(plain.URL)
	f.plainURL = plain.URL + "/mcp"
	tools := map[string][]mcp.Tool{}
	for id, raw := range servers(f) {
		entry := raw.(map[string]any)
		tools[id] = f.listTools(t, entry["url"].(string), fix)
	}
	for _, org := range []string{"org-a", "org-b"} {
		body, _ := json.Marshal(map[string]any{"meta": map[string]any{"version": 1}, "mcp": map[string]any{
			"enabled": true, "access": map[string]any{"default": "allow"}, "servers": servers(f)}})
		var oc tenant.OrgConfig
		if err := json.Unmarshal(body, &oc); err != nil {
			t.Fatal(err)
		}
		f.store.Set(org, &oc)
	}
	f.step = hostedStep(t, f)
	admission, err := vendors.NewClientAdmission(f.catalog, f.runtime)
	if err != nil {
		t.Fatal(err)
	}
	// The release builds catalog_ref servers (every K2 pilot entry) only with
	// mcp.catalog_enabled on (28c5d0ecd4), its default.
	mgr := tenancy.New(tenancy.Options{Store: f.store, MCP: config.MCPConfig{CatalogEnabled: true}, InitialWait: time.Second, Deps: func() *mcp.Deps {
		return &mcp.Deps{ClientFactory: mcp.DefaultClientFactory, Builtins: mcp.NewBuiltinRegistry(), ManagedServers: noManaged{}, ClientAdmission: admission}
	}})
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = mgr.Close(ctx)
	})
	ten := &serverTools{Tenancy: mgr, tools: tools}
	f.srv = mcp.NewServer(mcp.ServerConfig{MaxAgentDepth: 10, ToolCallTimeout: 5 * time.Second, SessionTTL: time.Minute, Separator: "_"})
	t.Cleanup(f.srv.Close)
	f.srv.SetIdentity(mcp.Identity{Keys: auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Name: "shared", Key: sharedCaller, Owner: "u1", Metadata: map[string]string{"org_id": "org-a"}}}})})
	money := &ledger{}
	f.srv.SetDeps(mcp.Deps{BearerVerifier: callers{}, CredentialResolver: f.step, Credits: money, SpendGuard: money, KeyLimits: money, RunBudget: &ledger{}, ToolBudget: &ledger{}})
	f.srv.SetTenancy(ten)
	t.Cleanup(func() { f.srv.SetTenancy(nil) })
	sep := f.srv.Registry().Separator()
	f.srv.Registry().SetOrgSource(func(org string) mcp.MemberSource {
		_, pool, err := ten.Resolve(org)
		if err != nil {
			return nil
		}
		return mcp.PoolMembers(pool, sep)
	})
	return f
}

// listTools is a server's tool list, as a person's client would list it:
// the runtime's own tools/list for a server on it (a listing reaches no
// vendor), else the plain mock's fixtures.
func (f *hostedFixture) listTools(t *testing.T, url string, fix *mcpmock.Fixtures) []mcp.Tool {
	t.Helper()
	var defs []json.RawMessage
	if strings.HasPrefix(url, f.runtime+"/") {
		resp, err := rpc(url, "k2-synthetic-listing-bearer", "tools/list", map[string]any{})
		if err != nil || len(resp.Tools) == 0 {
			t.Fatalf("the K2 runtime did not list %s", url)
		}
		defs = resp.Tools
	} else {
		for _, item := range fix.Tools {
			defs = append(defs, item.Def)
		}
	}
	tools := make([]mcp.Tool, 0, len(defs))
	for _, def := range defs {
		var tool mcp.Tool
		if err := json.Unmarshal(def, &tool); err != nil {
			t.Fatal(err)
		}
		tool.Raw = def
		tools = append(tools, tool)
	}
	return tools
}

type listed struct {
	Tools []json.RawMessage `json:"tools"`
}

// rpc is one stateless JSON-RPC request to a K2 runtime path.
func rpc(url, bearer, method string, params any) (*listed, error) {
	body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	req.Header.Set("MCP-Protocol-Version", "2025-11-25")
	req.Header.Set("Authorization", "Bearer "+bearer)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	var envelope struct {
		Result *listed `json:"result"`
	}
	if resp.StatusCode != http.StatusOK || json.NewDecoder(resp.Body).Decode(&envelope) != nil || envelope.Result == nil {
		return nil, io.ErrUnexpectedEOF
	}
	return envelope.Result, nil
}

// serverTools lists each pool server with its own tools, as its first
// person's client would have listed them.
type serverTools struct {
	mcp.Tenancy
	tools map[string][]mcp.Tool
}

func (s *serverTools) Resolve(org string) (*tenant.OrgConfig, mcp.TenantPool, error) {
	c, p, e := s.Tenancy.Resolve(org)
	if e != nil || p == nil {
		return c, p, e
	}
	return c, &serverToolsPool{TenantPool: p, tools: s.tools}, nil
}

type serverToolsPool struct {
	mcp.TenantPool
	tools map[string][]mcp.Tool
}

func (p *serverToolsPool) with(s mcp.PoolServer) mcp.PoolServer {
	if tools := p.tools[s.ID]; len(tools) > 0 {
		s.Tools = tools // inventory-only fixture; Client and admission remain production
	}
	return s
}
func (p *serverToolsPool) Server(id string) (mcp.PoolServer, bool) {
	s, ok := p.TenantPool.Server(id)
	return p.with(s), ok
}
func (p *serverToolsPool) Servers() []mcp.PoolServer {
	s := p.TenantPool.Servers()
	for i := range s {
		s[i] = p.with(s[i])
	}
	return s
}

// callTool initializes a session as bearer and calls one tool by its
// gateway name, decoding the result or the typed in-band refusal.
func (f *hostedFixture) callTool(t *testing.T, bearer, name string) answer {
	t.Helper()
	post := func(session, method string, params any) *httptest.ResponseRecorder {
		raw, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
		r := httptest.NewRequest("POST", "/mcp", bytes.NewReader(raw))
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("Accept", "application/json, text/event-stream")
		r.Header.Set("Authorization", "Bearer "+bearer)
		if session != "" {
			r.Header.Set("MCP-Session-Id", session)
		}
		w := httptest.NewRecorder()
		f.srv.HandlePost(w, r)
		return w
	}
	w := post("", "initialize", map[string]any{"protocolVersion": mcp.ProtocolVersion, "capabilities": map[string]any{}, "clientInfo": map[string]any{"name": "k2", "version": "1"}})
	session := w.Header().Get("MCP-Session-Id")
	if session == "" {
		t.Fatalf("initialize: status=%d", w.Code)
	}
	args := map[string]any{}
	if strings.HasSuffix(name, "__echo") { // the plain mock's echo requires its text
		args["text"] = "k2"
	}
	w = post(session, "tools/call", map[string]any{"name": name, "arguments": args})
	var envelope struct {
		Result json.RawMessage `json:"result"`
		Error  json.RawMessage `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &envelope); err != nil || len(envelope.Error) > 0 {
		t.Fatalf("tools/call %s: status=%d, protocol failure=%s", name, w.Code, envelope.Error)
	}
	var out answer
	if err := json.Unmarshal(envelope.Result, &out); err != nil {
		t.Fatal(err)
	}
	out.Raw = envelope.Result
	if raw := out.Meta[mcp.MetaKeyError]; len(raw) > 0 {
		if err := json.Unmarshal(raw, &out.Denial); err != nil {
			t.Fatal(err)
		}
	}
	return out
}

func (p *serverToolsPool) ClientAdmission(id string) *mcp.Denial {
	return p.TenantPool.(mcp.ClientAdmissionPool).ClientAdmission(id)
}
