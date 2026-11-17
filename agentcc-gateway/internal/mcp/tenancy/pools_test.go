package tenancy

import (
	"context"
	"encoding/json"
	"net/http"
	"slices"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

const (
	urlA = "http://mock-a.example"
	urlB = "http://mock-b.example"
)

func githubAt(url string) string {
	return `{"enabled":true,"servers":{"github":{"url":"` + url + `"}}}`
}

// Bug 5: two orgs that both configure a server named github each get their
// own pool, list only their own tools and reach only their own upstream.
func TestOrgPoolsIsolated(t *testing.T) {
	r := newRig(t, Options{})
	a := r.factory.upstream(urlA, "a_search", "a_issue")
	b := r.factory.upstream(urlB, "b_only")
	r.push("org-a", 1, githubAt(urlA))
	r.push("org-b", 1, githubAt(urlB))

	_, poolA := r.resolve("org-a")
	_, poolB := r.resolve("org-b")
	if got := toolNames(poolA); strings.Join(got, ",") != "github/a_issue,github/a_search" {
		t.Fatalf("org-a tools = %v", got)
	}
	if got := toolNames(poolB); strings.Join(got, ",") != "github/b_only" {
		t.Fatalf("org-b tools = %v", got)
	}

	ca, err := poolA.Client("github", "", mcp.AuthOverride{})
	if err != nil {
		t.Fatalf("org-a client: %v", err)
	}
	if _, err := ca.CallTool(context.Background(), "a_search", nil, mcp.CallOptions{}); err != nil {
		t.Fatalf("call: %v", err)
	}
	if got := a.called(); !slices.Equal(got, []string{"a_search"}) {
		t.Fatalf("org-a upstream got %v", got)
	}
	if got := b.called(); len(got) != 0 {
		t.Fatalf("org-b upstream got org-a's call: %v", got)
	}
	if poolA == poolB || poolA.OrgID() != "org-a" || poolB.OrgID() != "org-b" {
		t.Fatal("the orgs share a pool")
	}
	if r.factory.buildsOf("github") != 2 {
		t.Fatalf("github clients built = %d, want one per org", r.factory.buildsOf("github"))
	}
}

// The operator's YAML pool serves only principals of mcp.static.org_id; its
// servers never appear in another org's list or an unbound principal's view.
func TestStaticPoolNotInOrgLists(t *testing.T) {
	static := config.MCPConfig{
		Servers: map[string]config.MCPServerConfig{"ops": {URL: "http://ops.example", Transport: "http"}},
		Static:  config.MCPStaticConfig{OrgID: "org-ops"},
	}
	r := newRig(t, Options{MCP: static})
	r.factory.upstream("http://ops.example", "restart")
	r.factory.upstream(urlA, "a_search")
	r.push("org-a", 1, githubAt(urlA))

	_, poolA := r.resolve("org-a")
	if got := serverIDs(poolA); !slices.Equal(got, []string{"github"}) {
		t.Fatalf("org-a servers = %v, want only its own github", got)
	}
	if _, err := poolA.Client("ops", "", mcp.AuthOverride{}); err == nil {
		t.Fatal("org-a got a client of the operator's YAML server")
	}

	_, poolOps := r.resolve("org-ops")
	if got := toolNames(poolOps); !slices.Equal(got, []string{"ops/restart"}) {
		t.Fatalf("static org tools = %v", got)
	}
	_, anon := r.resolve("")
	if got := toolNames(anon); len(got) != 0 {
		t.Fatalf("no-org principal tools = %v, want no static YAML tools", got)
	}

	// With no static.org_id, the YAML pool serves no org-config principal.
	r2 := newRig(t, Options{MCP: config.MCPConfig{Servers: static.Servers}})
	r2.factory.upstream("http://ops.example", "restart")
	r2.push("org-ops", 1, `{"enabled":true}`)
	if _, p := r2.resolve("org-ops"); len(p.Servers()) != 0 {
		t.Fatalf("with no static.org_id, org-ops sees %v", serverIDs(p))
	}
}

// MCA I12: a per_user server gets one client per user, keyed by the user,
// built with that user's credential; a shared server has one client.
func TestPerUserServerGetsOwnClient(t *testing.T) {
	r := newRig(t, Options{})
	up := r.factory.upstream(urlA, "whoami")
	r.factory.upstream(urlB, "shared_tool")
	r.push("org-a", 1, `{"enabled":true,"servers":{
		"mine":{"url":"`+urlA+`","credential":{"mode":"per_user"}},
		"team":{"url":"`+urlB+`","credential":{"mode":"shared","secret":{"type":"bearer","value":"team-secret"}}}}}`)
	_, pool := r.resolve("org-a")

	if n := r.factory.buildsOf("mine"); n != 0 {
		t.Fatalf("a per_user server connected %d times before any user called", n)
	}
	tok := func(v string) mcp.AuthOverride {
		return mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer " + v}}}
	}
	u1, err := pool.Client("mine", "user-1", tok("t1"))
	if err != nil {
		t.Fatal(err)
	}
	u1again, _ := pool.Client("mine", "user-1", tok("t1"))
	u2, _ := pool.Client("mine", "user-2", tok("t2"))
	if _, err := pool.Client("mine", "", mcp.AuthOverride{}); err == nil {
		t.Fatal("a per_user server served a call with no credential identity")
	}
	if r.factory.buildsOf("mine") != 2 {
		t.Fatalf("per_user clients built = %d, want one per user", r.factory.buildsOf("mine"))
	}
	if unwrap(u1) != unwrap(u1again) || unwrap(u1) == unwrap(u2) {
		t.Fatal("per_user clients are not keyed by user")
	}
	if got := up.auths; len(got) != 2 || got[0].Token != "t1" || got[1].Token != "t2" {
		t.Fatalf("per_user clients built with %+v, want each user's own token", got)
	}
	// A refreshed token rebuilds that user's client only.
	if _, err := pool.Client("mine", "user-1", tok("t1b")); err != nil || r.factory.buildsOf("mine") != 3 {
		t.Fatalf("token refresh: err %v, builds %d", err, r.factory.buildsOf("mine"))
	}

	s1, _ := pool.Client("team", "", mcp.AuthOverride{})
	s2, _ := pool.Client("team", "", mcp.AuthOverride{})
	if unwrap(s1) != unwrap(s2) || r.factory.buildsOf("team") != 1 {
		t.Fatalf("shared server: %d clients", r.factory.buildsOf("team"))
	}
	if c := unwrap(s1).(*fakeClient); c.cfg.Auth.Type != "bearer" || c.cfg.Auth.Token != "team-secret" {
		t.Fatalf("shared server credential = %+v, want the pushed secret", c.cfg.Auth)
	}
}

func unwrap(c mcp.UpstreamClient) mcp.UpstreamClient {
	if tc, ok := c.(*timeoutClient); ok {
		return tc.UpstreamClient
	}
	return c
}

// The timeout_ms of org config reaches every call made through the pool.
func TestPoolClientAppliesTimeoutMs(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "slow")
	r.push("org-a", 1, `{"enabled":true,"servers":{"github":{"url":"`+urlA+`","timeout_ms":120000}}}`)
	_, pool := r.resolve("org-a")
	c, err := pool.Client("github", "", mcp.AuthOverride{})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := c.CallTool(context.Background(), "slow", json.RawMessage(`{}`), mcp.CallOptions{}); err != nil {
		t.Fatal(err)
	}
	fc := unwrap(c).(*fakeClient)
	if len(fc.opts) != 1 || fc.opts[0].Timeout.Milliseconds() != 120000 {
		t.Fatalf("call options = %+v, want the server's 120 s timeout", fc.opts)
	}
}

// Concurrent requests right after a push build each server once, and a
// caller filtering the lists it read changes nothing in the pool.
func TestConcurrentResolveBuildsOnce(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "a_tool", "b_tool")
	r.push("org-a", 1, githubAt(urlA))
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, _, err := r.m.Resolve("org-a"); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	_, pool := r.resolve("org-a")
	eventually(t, "github connected", func() bool { s, _ := pool.Server("github"); return s.Healthy })
	if n := r.factory.buildsOf("github"); n != 1 {
		t.Fatalf("github built %d times by concurrent requests", n)
	}
	s, _ := pool.Server("github")
	s.Tools[0].Name = "mutated"
	if got := toolNames(pool); strings.Join(got, ",") != "github/a_tool,github/b_tool" {
		t.Fatalf("a caller's change reached the pool: %v", got)
	}
}
