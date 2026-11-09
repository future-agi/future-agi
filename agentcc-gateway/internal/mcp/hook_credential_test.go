package mcp

import (
	"errors"
	"net/http"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Step 13 (M25) on the real tools/call chain and the real callClient: a
// per_user server's call goes through the calling user's own upstream
// client, built with that user's credential, and never runs without one.

// userPool is org-a's pool: "issues" is per_user and "wiki" shared, both on
// one fake upstream. Client keeps one real client per server, credential key
// and credential, built with the bearer it was asked with (as tenancy's
// per-user clients are), and records what it was asked for.
type userPool struct {
	t     *testing.T
	up    *fakeUpstream
	tools []Tool

	mu      sync.Mutex
	asked   []poolAsk
	clients map[poolAsk]UpstreamClient
}

type poolAsk struct{ server, credKey, authorization string }

func (p *userPool) OrgID() string { return "org-a" }
func (p *userPool) Servers() []PoolServer {
	return []PoolServer{
		{ID: "issues", Source: "org", PerUser: true, Healthy: true, Tools: p.tools},
		{ID: "wiki", Source: "org", Healthy: true, Tools: p.tools},
	}
}
func (p *userPool) Server(id string) (PoolServer, bool) {
	for _, s := range p.Servers() {
		if s.ID == id {
			return s, true
		}
	}
	return PoolServer{}, false
}
func (p *userPool) Client(id, credKey string, auth AuthOverride) (UpstreamClient, error) {
	ask := poolAsk{id, credKey, auth.Header.Get("Authorization")}
	p.mu.Lock()
	defer p.mu.Unlock()
	p.asked = append(p.asked, ask)
	if id == "issues" && credKey == "" {
		return nil, errors.New("a per_user server needs the caller's credential identity")
	}
	if c := p.clients[ask]; c != nil {
		return c, nil
	}
	cfg := ClientConfig{ServerID: id}
	if tok, ok := strings.CutPrefix(ask.authorization, "Bearer "); ok {
		cfg.Auth = AuthConfig{Type: "bearer", Token: tok}
	}
	c := AdaptClient(connectFake(p.t, p.up, cfg))
	p.clients[ask] = c
	return c, nil
}
func (p *userPool) CachedList(_ ListCacheKey, build func() []Tool) []Tool { return build() }

func (p *userPool) askedFor(server string) []poolAsk {
	p.mu.Lock()
	defer p.mu.Unlock()
	var out []poolAsk
	for _, a := range p.asked {
		if a.server == server {
			out = append(out, a)
		}
	}
	return out
}

// userTokens stands in for M25's resolver: each user it knows has a token,
// returned for any server; anyone else is auth_required.
type userTokens map[string]string

func (u userTokens) Resolve(cc *CallContext) (*AuthOverride, *Denial) {
	if tok, ok := u[cc.Principal.UserID]; ok {
		return &AuthOverride{Header: http.Header{"Authorization": {"Bearer " + tok}}}, nil
	}
	return nil, AuthRequired(cc.Upstream.ServerID, AuthReasonNoToken, "")
}

// newCredentialFixture is org-a with userPool, served through its virtual
// servers' composition, with resolver as Deps.CredentialResolver (the
// default when nil).
func newCredentialFixture(t *testing.T, resolver CredentialResolver) (*w3bOrgFixture, *userPool, *fakeUpstream) {
	t.Helper()
	up := newFakeUpstream(t, "/mcp", nil)
	pool := &userPool{t: t, up: up, tools: connectFake(t, up, ClientConfig{ServerID: "probe"}).Tools(), clients: map[poolAsk]UpstreamClient{}}
	cfg := orgConfig(t, `{"mcp":{"enabled":true,"access":{"default":"allow"},"servers":{`+
		`"issues":{"url":"https://issues.example/mcp","credential":{"mode":"per_user"}},`+
		`"wiki":{"url":"https://wiki.example/mcp"}}}}`)
	ten := &w3bTenancy{cfgs: map[string]*tenant.OrgConfig{"org-a": cfg}, pools: map[string]TenantPool{"org-a": pool, "": &w3bPool{}}}
	f := newW3BOrgFixture(t, ten, newFakeUpstream(t, "/mcp", nil))
	if resolver != nil {
		f.SetDeps(Deps{CredentialResolver: resolver})
	}
	return f, pool, up
}

// upstreamAuthorizations is the Authorization each tools/call carried.
func upstreamAuthorizations(up *fakeUpstream) []string {
	var out []string
	for _, r := range up.requests(MethodToolsCall) {
		out = append(out, r.Header.Get("Authorization"))
	}
	return out
}

func oauthPrincipal(user string) Principal {
	return Principal{Kind: PrincipalOAuth, OrgID: "org-a", UserID: user}
}

// A per_user call goes through the caller's own client, built with the
// caller's credential; a user the resolver refuses reaches nothing; users
// never share a client or a credential.
func TestPerUserCallBindsTheCallersCredential(t *testing.T) {
	f, pool, up := newCredentialFixture(t, userTokens{"u1": "m25-test-token-u1", "u3": "m25-test-token-u3"})

	f.principal = oauthPrincipal("u1")
	if resp := f.call(t, "issues__echo"); resp.Error != nil || denial(t, resp) != nil {
		t.Fatalf("u1: %+v %s", resp.Error, resp.Result)
	}
	f.principal = oauthPrincipal("u2")
	resp := f.call(t, "issues__echo")
	if obj := requireDenialSchema(t, resp); obj["code"] != CodeAuthRequired {
		t.Fatalf("u2 with no token: %v, want auth_required", obj)
	}
	f.principal = oauthPrincipal("u3")
	if resp := f.call(t, "issues__echo"); resp.Error != nil || denial(t, resp) != nil {
		t.Fatalf("u3: %+v %s", resp.Error, resp.Result)
	}
	f.principal = oauthPrincipal("u1")
	if resp := f.call(t, "issues__echo"); resp.Error != nil || denial(t, resp) != nil {
		t.Fatalf("u1 again: %+v %s", resp.Error, resp.Result)
	}

	want := []string{"Bearer m25-test-token-u1", "Bearer m25-test-token-u3", "Bearer m25-test-token-u1"}
	if got := upstreamAuthorizations(up); strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("the upstream's calls carried %q, want %q", got, want)
	}
	for _, a := range pool.askedFor("issues") {
		owner := map[string]string{"m25-test-token-u1": "u1", "m25-test-token-u3": "u3"}[strings.TrimPrefix(a.authorization, "Bearer ")]
		if a.credKey == "" || a.credKey != owner {
			t.Fatalf("the pool was asked for %q's client with %q's credential", a.credKey, owner)
		}
	}
}

// A per_user call with no credential resolved is refused before anything is
// sent: with no per-user credential service (the default resolver) it is
// policy_unavailable, and a caller with no user is auth_required
// no_user_identity. A shared server is unaffected.
func TestPerUserCallNeverRunsWithoutCredential(t *testing.T) {
	f, pool, up := newCredentialFixture(t, nil)

	f.principal = oauthPrincipal("u1")
	if obj := requireDenialSchema(t, f.call(t, "issues__echo")); obj["code"] != CodePolicyUnavailable {
		t.Fatalf("a per_user call with no credential service: %v, want policy_unavailable", obj)
	}
	f.principal = Principal{Kind: PrincipalKey, KeyID: "k-shared", OrgID: "org-a"}
	obj := requireDenialSchema(t, f.call(t, "issues__echo"))
	if meta, _ := obj["metadata"].(map[string]any); obj["code"] != CodeAuthRequired || meta["reason"] != AuthReasonNoUserIdentity {
		t.Fatalf("a shared key on a per_user server: %v, want auth_required no_user_identity", obj)
	}
	if n := len(pool.askedFor("issues")); n != 0 {
		t.Fatalf("the pool was asked %d times for a per_user client with no credential", n)
	}
	if resp := f.call(t, "wiki__echo"); resp.Error != nil || denial(t, resp) != nil {
		t.Fatalf("the shared server: %+v %s", resp.Error, resp.Result)
	}
	if got := upstreamAuthorizations(up); len(got) != 1 {
		t.Fatalf("the upstream got %d calls, want only the shared server's", len(got))
	}
}

// A shared server keeps its configured credential even when a resolver
// offers the caller's: its shared client is used, never a per-user one.
func TestSharedServerKeepsItsCredential(t *testing.T) {
	f, pool, up := newCredentialFixture(t, userTokens{"u1": "m25-test-token-u1"})
	f.principal = oauthPrincipal("u1")
	if resp := f.call(t, "wiki__echo"); resp.Error != nil || denial(t, resp) != nil {
		t.Fatalf("u1 on the shared server: %+v %s", resp.Error, resp.Result)
	}
	if asks := pool.askedFor("wiki"); len(asks) != 1 || asks[0] != (poolAsk{"wiki", "", ""}) {
		t.Fatalf("the pool was asked %+v for the shared server, want its shared client", asks)
	}
	if got := upstreamAuthorizations(up); len(got) != 1 || got[0] != "" {
		t.Fatalf("the shared call carried %q", got)
	}
}

// auth_required and the credential refusal are the objects mcp-denial-v1
// states, for every reason, with and without a connect link.
func TestAuthRequiredMatchesContract(t *testing.T) {
	for _, reason := range []string{AuthReasonNoToken, AuthReasonExpired, AuthReasonNeedsReauth, AuthReasonNoUserIdentity} {
		for _, link := range []string{"", "https://connect.example.test/agentcc/mcp/connect/issues/start/?binding=x"} {
			d := AuthRequired("issues", reason, link)
			if !d.InBand() || d.Message == "" {
				t.Fatalf("%s: %+v", reason, d)
			}
			if errs := schematest.ValidateDef(t, "mcp-denial-v1.schema.json", "Error", d.Object()); len(errs) > 0 {
				t.Fatalf("auth_required %s (link %q) breaks mcp-denial-v1: %v", reason, link, errs)
			}
		}
	}
	if errs := schematest.ValidateDef(t, "mcp-denial-v1.schema.json", "Error", CredentialUnavailable().Object()); len(errs) > 0 {
		t.Fatalf("the credential refusal breaks mcp-denial-v1: %v", errs)
	}
}
