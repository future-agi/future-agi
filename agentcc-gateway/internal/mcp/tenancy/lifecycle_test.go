package tenancy

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

func twoServers(urlGithub, urlDocs string, extra string) string {
	return `{"enabled":true,"servers":{
		"github":{"url":"` + urlGithub + `"},
		"docs":{"url":"` + urlDocs + `"}}` + extra + `}`
}

// A version that changes one server's upstream rebuilds only that server;
// the old client serves until the new one has listed, then is closed.
func TestPoolRebuildOnUpstreamChangeOnly(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "a_tool")
	r.factory.upstream(urlB, "docs_tool")
	r.factory.upstream("http://mock-a2.example", "a2_tool")
	r.push("org-a", 1, twoServers(urlA, urlB, ""))
	_, pool := r.resolve("org-a")
	old, _ := pool.Client("github", "", mcp.AuthOverride{})

	r.push("org-a", 2, twoServers("http://mock-a2.example", urlB, ""))
	r.resolve("org-a")
	eventually(t, "github rebuilt on its new URL", func() bool {
		return slices.Contains(toolNames(pool), "github/a2_tool")
	})
	if r.factory.buildsOf("github") != 2 || r.factory.buildsOf("docs") != 1 {
		t.Fatalf("builds github=%d docs=%d, want 2 and 1", r.factory.buildsOf("github"), r.factory.buildsOf("docs"))
	}
	eventually(t, "the replaced client closed", func() bool { return unwrap(old).(*fakeClient).closed.Load() })
	now, _ := pool.Client("github", "", mcp.AuthOverride{})
	if unwrap(now).(*fakeClient).cfg.URL != "http://mock-a2.example" {
		t.Fatal("github still served by the old upstream")
	}
	// A server removed from the config is closed; one added connects.
	r.push("org-a", 3, `{"enabled":true,"servers":{"github":{"url":"http://mock-a2.example"},"new":{"url":"`+urlA+`"}}}`)
	r.resolve("org-a")
	eventually(t, "docs removed and new added", func() bool {
		return strings.Join(serverIDs(pool), ",") == "github,new"
	})
	if r.factory.buildsOf("github") != 2 {
		t.Fatalf("an unchanged server reconnected: %d builds", r.factory.buildsOf("github"))
	}

	// Until the replacement lists, the old client keeps serving.
	r.factory.mu.Lock()
	r.factory.failLeft["http://down.example"] = 1 << 30
	r.factory.mu.Unlock()
	r.push("org-a", 4, `{"enabled":true,"servers":{"github":{"url":"http://down.example"},"new":{"url":"`+urlA+`"}}}`)
	r.resolve("org-a")
	eventually(t, "the failing replacement reported", func() bool {
		s, _ := pool.Server("github")
		return strings.Contains(s.LastError, "connection refused")
	})
	if c, err := pool.Client("github", "", mcp.AuthOverride{}); err != nil || unwrap(c).(*fakeClient).cfg.URL != "http://mock-a2.example" {
		t.Fatalf("the old client stopped serving before its replacement listed: %v", err)
	}
}

// A version that edits only pinning and virtual servers swaps the policy
// snapshot and reconnects nothing.
func TestPolicyOnlyChangeKeepsClients(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "a_tool")
	r.factory.upstream(urlB, "docs_tool")
	r.push("org-a", 1, twoServers(urlA, urlB, ""))
	snap1, pool := r.resolve("org-a")
	before := r.factory.totalBuilds()

	r.push("org-a", 2, twoServers(urlA, urlB, `,
		"pinning":{"pins":{"github/a_tool":"`+strings.Repeat("ab", 32)+`"}},
		"virtual_servers":{"eng":{"id":"vs_01J0000000000000000000000","members":["github"]}},
		"managed_tools":{"enabled":false},"access":{"default":"allow"}`))
	snap2, pool2 := r.resolve("org-a")
	if pool2 != pool {
		t.Fatal("a policy-only change built a new pool")
	}
	settle() // a rebuild would run in the background
	if snap2 == snap1 || snap2.Meta.Version != 2 || len(snap2.MCP.VirtualServers) != 1 {
		t.Fatalf("the policy snapshot was not swapped: %+v", snap2.Meta)
	}
	if got := r.factory.totalBuilds(); got != before {
		t.Fatalf("connections %d -> %d after a policy-only change", before, got)
	}
	for _, c := range r.factory.clients {
		if c.closed.Load() {
			t.Fatalf("client of %s closed by a policy-only change", c.cfg.ServerID)
		}
	}
}

type blockingManagedServers struct {
	started chan struct{}
	release chan struct{}
	once    sync.Once
}

func (b *blockingManagedServers) ForOrg(string) []mcp.ClientConfig {
	b.once.Do(func() { close(b.started) })
	<-b.release
	return nil
}

// Concurrent resolves must not let an older config overwrite a newer one
// after the newer request has already observed it in the store.
func TestConcurrentConfigSyncKeepsNewestSnapshot(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "a_tool")
	r.push("org-a", 1, githubAt(urlA))
	r.resolve("org-a")

	blocked := &blockingManagedServers{started: make(chan struct{}), release: make(chan struct{})}
	d := *r.deps.Load()
	d.ManagedServers = blocked
	r.deps.Store(&d)
	configFor := func(access string) string {
		return `{"enabled":true,"access":{"default":"` + access + `"},"servers":{"github":{"url":"` + urlA + `"}}}`
	}
	r.push("org-a", 2, configFor("allow"))
	firstDone := make(chan error, 1)
	go func() {
		_, _, err := r.m.Resolve("org-a")
		firstDone <- err
	}()
	<-blocked.started

	r.push("org-a", 3, configFor("deny"))
	secondStarted := make(chan struct{})
	secondDone := make(chan error, 1)
	go func() {
		close(secondStarted)
		_, _, err := r.m.Resolve("org-a")
		secondDone <- err
	}()
	<-secondStarted
	time.Sleep(20 * time.Millisecond)
	close(blocked.release)
	if err := <-firstDone; err != nil {
		t.Fatalf("first resolve: %v", err)
	}
	if err := <-secondDone; err != nil {
		t.Fatalf("second resolve: %v", err)
	}

	snapshot, _ := r.resolve("org-a")
	if snapshot == nil || snapshot.Meta.Version != 3 || snapshot.MCP.Access.Default != "deny" {
		t.Fatalf("final snapshot = %+v, want version 3 deny", snapshot)
	}
}

// A server that fails to connect is reported with last_error and retried
// with backoff until it connects.
func TestFailedServerRetried(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "a_tool")
	r.factory.mu.Lock()
	r.factory.failLeft[urlA] = 3
	r.factory.mu.Unlock()
	r.push("org-a", 1, githubAt(urlA))
	_, pool := r.resolve("org-a")
	s, _ := pool.Server("github")
	if s.Healthy || !strings.Contains(s.LastError, "connection refused") {
		t.Fatalf("first attempt: %+v, want unhealthy with the connect error", s)
	}
	eventually(t, "the retry to connect", func() bool {
		s, _ := pool.Server("github")
		return s.Healthy && s.LastError == "" && len(s.Tools) == 1
	})
	if n := r.factory.buildsOf("github"); n != 4 {
		t.Fatalf("attempts = %d, want 3 failures and 1 success", n)
	}

	// The backoff doubles to its cap: 1 s to 5 min by default.
	if m := New(Options{}); m.opts.RetryInitial.Seconds() != 1 || m.opts.RetryMax.Minutes() != 5 {
		t.Fatalf("default backoff %v..%v, want 1s..5m", m.opts.RetryInitial, m.opts.RetryMax)
	}
}

// MCA E12: a config the pool cannot load keeps the previous pool and
// snapshot, and its errors are shown in status.
func TestLastKnownGoodOnBadConfig(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "a_tool")
	r.push("org-a", 1, githubAt(urlA))
	snap1, pool := r.resolve("org-a")

	for i, bad := range []string{
		`{"enabled":true,"servers":{"github":{"url":"` + urlA + `"},"local":{"url":"x","transport":"stdio"}}}`,
		`{"enabled":true,"servers":{"github":{"url":"ftp://nope"}}}`,
		`{"enabled":true,"servers":{"github":{"url":"` + urlA + `","forward_headers":["Authorization"]}}}`,
		`{"enabled":true,"servers":{"github":{"url":"` + urlA + `","era":"1999-01-01"}}}`,
	} {
		r.push("org-a", 2+i, bad)
		snap, p, err := r.m.Resolve("org-a")
		if err != nil || p != pool || snap != snap1 {
			t.Fatalf("bad config %d: snapshot %v, err %v; want the last-known-good v1", i, snap.Meta, err)
		}
		if got := toolNames(pool); !slices.Equal(got, []string{"github/a_tool"}) {
			t.Fatalf("bad config %d: tools %v, want v1's", i, got)
		}
		var shown bool
		for _, s := range pool.Servers() {
			shown = shown || strings.HasPrefix(s.LastError, "config_invalid: ")
		}
		if !shown {
			t.Fatalf("bad config %d: no config_invalid error in status %+v", i, pool.Servers())
		}
	}
	if r.factory.buildsOf("github") != 1 {
		t.Fatalf("bad configs reconnected github: %d builds", r.factory.buildsOf("github"))
	}

	// With no last-known-good version, the org's policy is unavailable.
	r.push("org-new", 1, `{"enabled":true,"servers":{"x":{"url":"ftp://nope"}}}`)
	if _, p, err := r.m.Resolve("org-new"); !errors.Is(err, ErrNoLastKnownGood) || p != nil {
		t.Fatalf("first config bad: pool %v, err %v; want ErrNoLastKnownGood", p, err)
	}
	// A good version afterwards loads.
	r.push("org-new", 2, githubAt(urlA))
	if _, _, err := r.m.Resolve("org-new"); err != nil {
		t.Fatalf("good config after a bad one: %v", err)
	}
}

// Admin status keeps the first invalid config visible even though calls fail
// closed with policy_unavailable.
func TestAdminStatusShowsFirstLoadErrors(t *testing.T) {
	r := newRig(t, Options{})
	r.push("org-bad", 1, `{"enabled":true,"servers":{"bad":{"url":"ftp://nope"}}}`)
	s := mcp.NewServer(mcp.ServerConfig{})
	defer s.Close()
	s.SetTenancy(r.m)
	status, err := s.AdminStatus("org-bad")
	if err != nil {
		t.Fatalf("AdminStatus: %v", err)
	}
	if status.OrgEnabled || len(status.Servers) != 1 || status.Servers[0].ServerID != "bad" || status.Servers[0].LastError == nil || *status.Servers[0].LastError == "" {
		t.Fatalf("status = %+v, want the refused server's load error", status)
	}
}

// Managed servers come from Deps.ManagedServers and join the org's pool.
func TestManagedServersMergedFromDeps(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "a_tool")
	r.factory.upstream("http://fagi-web.example", "search")
	d := *r.deps.Load()
	d.ManagedServers = fixedManaged{"org-a": {{ServerID: "fagi.web", URL: "http://fagi-web.example", TransportType: "http"}}}
	r.deps.Store(&d)
	r.push("org-a", 1, githubAt(urlA))
	r.push("org-b", 1, githubAt(urlA))

	_, poolA := r.resolve("org-a")
	if got := toolNames(poolA); !slices.Equal(got, []string{"fagi.web/search", "github/a_tool"}) {
		t.Fatalf("org-a tools = %v", got)
	}
	s, _ := poolA.Server("fagi.web")
	if !s.Managed || s.Source != SourceManaged {
		t.Fatalf("fagi.web = %+v, want a managed server", s)
	}
	if _, poolB := r.resolve("org-b"); slices.Contains(serverIDs(poolB), "fagi.web") {
		t.Fatal("org-b got a managed server ForOrg did not offer it")
	}
}

// Two orgs never share a managed upstream client, although the operator
// credential is the same.
func TestManagedClientsPerOrg(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream("http://fagi-web.example", "search")
	web := mcp.ClientConfig{ServerID: "fagi.web", URL: "http://fagi-web.example", Auth: mcp.AuthConfig{Type: "bearer", Token: "operator"}}
	d := *r.deps.Load()
	d.ManagedServers = fixedManaged{"org-a": {web}, "org-b": {web}}
	r.deps.Store(&d)
	r.push("org-a", 1, `{"enabled":true}`)
	r.push("org-b", 1, `{"enabled":true}`)
	_, a := r.resolve("org-a")
	_, b := r.resolve("org-b")
	ca, errA := a.Client("fagi.web", "", mcp.AuthOverride{})
	cb, errB := b.Client("fagi.web", "", mcp.AuthOverride{})
	if errA != nil || errB != nil {
		t.Fatalf("clients: %v %v", errA, errB)
	}
	if unwrap(ca) == unwrap(cb) || r.factory.buildsOf("fagi.web") != 2 {
		t.Fatalf("orgs share the managed client (%d builds)", r.factory.buildsOf("fagi.web"))
	}
}

// Two keys of one org with different access rules on the same virtual
// server never see each other's cached list; the same rules share one.
func TestListCacheKeyedByAccessHash(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "read", "write")
	r.push("org-a", 1, githubAt(urlA))
	_, pool := r.resolve("org-a")

	builds := 0
	listFor := func(rules any, keep string) []mcp.Tool {
		key := mcp.ListCacheKey{VirtualServer: "vs_1", AccessHash: mcp.AccessHash(rules)}
		return pool.CachedList(key, func() []mcp.Tool {
			builds++
			return []mcp.Tool{{Name: keep}}
		})
	}
	k1 := map[string]any{"key": "k1", "deny": []string{"github/write"}}
	k2 := map[string]any{"key": "k2", "deny": []string{}}
	if got := listFor(k1, "read"); got[0].Name != "read" {
		t.Fatal(got)
	}
	if got := listFor(k2, "read,write"); got[0].Name != "read,write" {
		t.Fatalf("k2 saw k1's cached list: %v", got)
	}
	if got := listFor(k1, "other"); got[0].Name != "read" || builds != 2 {
		t.Fatalf("k1's own list not cached: %v after %d builds", got, builds)
	}
	// Mutating a returned list changes nothing cached.
	listFor(k1, "x")[0].Name = "mutated"
	if got := listFor(k1, "x"); got[0].Name != "read" {
		t.Fatal("a caller's change reached the cache")
	}
	// A pool change empties the cache; another org's pool never shares it.
	r.push("org-a", 2, githubAt(urlA)+``)
	r.push("org-a", 3, `{"enabled":true,"servers":{"github":{"url":"`+urlA+`"}},"access":{"default":"allow"}}`)
	r.resolve("org-a")
	if got := listFor(k1, "fresh"); got[0].Name != "fresh" {
		t.Fatalf("cache kept across a config change: %v", got)
	}
	r.push("org-b", 1, githubAt(urlA))
	_, poolB := r.resolve("org-b")
	key := mcp.ListCacheKey{VirtualServer: "vs_1", AccessHash: mcp.AccessHash(k1)}
	if got := poolB.CachedList(key, func() []mcp.Tool { return []mcp.Tool{{Name: "b"}} }); got[0].Name != "b" {
		t.Fatalf("org-b saw org-a's cached list: %v", got)
	}
}

// Every client is built through the factory the server holds when the
// pool builds, never one captured earlier: a factory installed after the
// manager was created (SetDeps) is the one used.
func TestPoolBuildsThroughClientFactory(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "a_tool")
	early := 0
	r.deps.Store(&mcp.Deps{ClientFactory: func(mcp.ClientConfig) (mcp.UpstreamClient, error) {
		early++
		return nil, errors.New("the factory of an earlier wiring step")
	}, Builtins: mcp.NewBuiltinRegistry(), ManagedServers: fixedManaged{}})
	// The wiring's SetDeps runs after the manager exists.
	r.deps.Store(&mcp.Deps{ClientFactory: r.factory.build, Builtins: mcp.NewBuiltinRegistry(), ManagedServers: fixedManaged{}})
	r.push("org-a", 1, githubAt(urlA))
	_, pool := r.resolve("org-a")
	if s, _ := pool.Server("github"); !s.Healthy || early != 0 || r.factory.buildsOf("github") != 1 {
		t.Fatalf("healthy %v, early factory calls %d, late %d", s.Healthy, early, r.factory.buildsOf("github"))
	}

	// The static pool adopts the clients connected at route setup on its
	// first build, and every later build goes through the factory.
	adopted := &fakeClient{cfg: mcp.ClientConfig{ServerID: "ops", URL: "http://ops.example"}, up: &fakeUpstream{tools: []string{"restart"}}}
	var published []string
	static := config.MCPConfig{Servers: map[string]config.MCPServerConfig{
		"ops":  {URL: "http://ops.example", Transport: "http"},
		"late": {URL: urlA, Transport: "http"},
	}}
	r2 := newRig(t, Options{MCP: static,
		Adopt:   func() map[string]mcp.UpstreamClient { return map[string]mcp.UpstreamClient{"ops": adopted} },
		Publish: func(id string, c mcp.UpstreamClient) bool { published = append(published, id); return false }})
	r2.factory.upstream(urlA, "late_tool")
	r2.factory.upstream("http://ops2.example", "restart2")
	_, anon := r2.resolve("")
	if got := toolNames(anon); strings.Join(got, ",") != "late/late_tool,ops/restart" {
		t.Fatalf("static pool tools = %v", got)
	}
	if r2.factory.buildsOf("ops") != 0 || r2.factory.buildsOf("late") != 1 {
		t.Fatalf("builds ops=%d late=%d; want the adopted client kept and late built", r2.factory.buildsOf("ops"), r2.factory.buildsOf("late"))
	}
	static.Servers["ops"] = config.MCPServerConfig{URL: "http://ops2.example", Transport: "http"}
	r2.m.ReloadStatic(static)
	eventually(t, "reload rebuilt ops through the factory", func() bool {
		return slices.Contains(toolNames(anon), "ops/restart2")
	})
	if r2.factory.buildsOf("ops") != 1 || !adopted.closed.Load() {
		t.Fatalf("after reload: ops builds %d, adopted closed %v", r2.factory.buildsOf("ops"), adopted.closed.Load())
	}
	if !slices.Contains(published, "ops") {
		t.Fatalf("the rebuilt static client was not published: %v", published)
	}
}

// settle gives background rebuilds time to show; a negative check waits on it.
func settle() { time.Sleep(300 * time.Millisecond) }

// fakeBuiltin is an in-process server registered in Deps.Builtins.
type fakeBuiltin struct{ fakeClient }

// A kind: builtin managed server is served by its Deps.Builtins entry: it
// lists and calls through it, and neither the factory nor the dialer runs;
// an unregistered id shows last_error and never gets a remote client.
func TestBuiltinServerFromDepsNoUpstreamClient(t *testing.T) {
	operator := map[string]config.ManagedMCPServerConfig{
		"web":         {Kind: config.ManagedServerKindBuiltin},
		"fagi.ghost":  {Kind: config.ManagedServerKindBuiltin},
		"fagi.remote": {Kind: config.ManagedServerKindRemote, URL: "http://remote.example"},
	}
	r := newRig(t, Options{MCP: config.MCPConfig{ManagedServers: operator}})
	dialer := &countingDialer{}
	r.factory.dialer = dialer
	up := &fakeUpstream{tools: []string{"search"}}
	reg := mcp.NewBuiltinRegistry()
	if err := reg.Register("web", &fakeBuiltin{fakeClient{cfg: mcp.ClientConfig{ServerID: "fagi.web"}, up: up}}); err != nil {
		t.Fatal(err)
	}
	r.deps.Store(&mcp.Deps{ClientFactory: r.factory.build, Dialer: dialer, Builtins: reg,
		ManagedServers: fixedManaged{"org-a": {{ServerID: "fagi.web"}, {ServerID: "fagi.ghost"}}}})
	r.push("org-a", 1, `{"enabled":true}`)
	_, pool := r.resolve("org-a")

	web, _ := pool.Server("fagi.web")
	if !web.Builtin || !web.Healthy || len(web.Tools) != 1 || web.Tools[0].Name != "search" {
		t.Fatalf("fagi.web = %+v, want the built-in's tools", web)
	}
	c, err := pool.Client("fagi.web", "", mcp.AuthOverride{})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := c.CallTool(context.Background(), "search", json.RawMessage(`{"q":"x"}`), mcp.CallOptions{}); err != nil {
		t.Fatal(err)
	}
	if got := up.called(); !slices.Equal(got, []string{"search"}) {
		t.Fatalf("built-in got %v", got)
	}
	ghost, _ := pool.Server("fagi.ghost")
	if ghost.Healthy || !strings.Contains(ghost.LastError, "not registered") {
		t.Fatalf("fagi.ghost = %+v, want last_error for an unregistered built-in", ghost)
	}
	if _, err := pool.Client("fagi.ghost", "", mcp.AuthOverride{}); err == nil {
		t.Fatal("an unregistered built-in got a client")
	}
	if r.factory.totalBuilds() != 0 || dialer.n.Load() != 0 {
		t.Fatalf("factory builds %d, dials %d; want none for built-in servers", r.factory.totalBuilds(), dialer.n.Load())
	}
}

// Built-ins are registered process-wide, but every org entry owns an adapter
// whose Close is a no-op. Removing one org's server must not close the shared
// implementation or make another org's client unusable.
func TestBuiltinLifecycleIsolatedPerOrg(t *testing.T) {
	operator := map[string]config.ManagedMCPServerConfig{
		"web": {Kind: config.ManagedServerKindBuiltin},
	}
	r := newRig(t, Options{MCP: config.MCPConfig{ManagedServers: operator}})
	builtin := &fakeBuiltin{fakeClient{cfg: mcp.ClientConfig{ServerID: "fagi.web"}, up: &fakeUpstream{tools: []string{"search"}}}}
	reg := mcp.NewBuiltinRegistry()
	if err := reg.Register("web", builtin); err != nil {
		t.Fatal(err)
	}
	d := *r.deps.Load()
	d.Builtins = reg
	d.ManagedServers = fixedManaged{
		"org-a": {{ServerID: "fagi.web"}},
		"org-b": {{ServerID: "fagi.web"}},
	}
	r.deps.Store(&d)
	r.push("org-a", 1, `{"enabled":true}`)
	r.push("org-b", 1, `{"enabled":true}`)
	_, poolA := r.resolve("org-a")
	_, poolB := r.resolve("org-b")
	clientA, err := poolA.Client("fagi.web", "", mcp.AuthOverride{})
	if err != nil {
		t.Fatal(err)
	}
	clientB, err := poolB.Client("fagi.web", "", mcp.AuthOverride{})
	if err != nil {
		t.Fatal(err)
	}
	if clientA == clientB {
		t.Fatal("orgs received the same built-in lifecycle adapter")
	}

	r.push("org-a", 2, `{"enabled":false}`)
	r.resolve("org-a")
	eventually(t, "org-b built-in remains healthy after org-a retires", func() bool {
		server, ok := poolB.Server("fagi.web")
		return ok && server.Healthy
	})
	if builtin.closeCall.Load() != 0 {
		t.Fatalf("org-a retirement closed the process-wide built-in %d times", builtin.closeCall.Load())
	}
	if _, err := poolB.Client("fagi.web", "", mcp.AuthOverride{}); err != nil {
		t.Fatalf("org-b built-in after org-a retirement: %v", err)
	}
}

// At shutdown the manager closes every pool's clients except a static
// client the MCP server owns: the server's Shutdown closes that one and
// waits for it (a stdio child's ladder), so closing it here first would let
// Shutdown return early.
func TestCloseLeavesServerOwnedStaticClients(t *testing.T) {
	owned := &fakeClient{cfg: mcp.ClientConfig{ServerID: "ops"}, up: &fakeUpstream{tools: []string{"restart"}}}
	static := config.MCPConfig{Servers: map[string]config.MCPServerConfig{
		"ops":  {URL: "http://ops.example", Transport: "http"},
		"mine": {URL: urlB, Transport: "http"},
	}}
	r := newRig(t, Options{MCP: static,
		Adopt:   func() map[string]mcp.UpstreamClient { return map[string]mcp.UpstreamClient{"ops": owned} },
		Publish: func(id string, _ mcp.UpstreamClient) bool { return id == "ops" }})
	r.factory.upstream(urlB, "mine_tool")
	r.factory.upstream(urlA, "a_tool")
	r.push("org-a", 1, githubAt(urlA))
	_, anon := r.resolve("")
	_, poolA := r.resolve("org-a")
	eventually(t, "static and org servers up", func() bool { return len(toolNames(anon)) == 2 && len(toolNames(poolA)) == 1 })

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := r.m.Close(ctx); err != nil {
		t.Fatal(err)
	}
	if owned.closed.Load() {
		t.Fatal("the manager closed a client the server owns")
	}
	for _, c := range r.factory.clients {
		if !c.closed.Load() {
			t.Fatalf("client of %s left open at shutdown", c.cfg.ServerID)
		}
	}
}

// A slow per-user build must not hold the entry lock while the factory
// connects. Other credentials, status reads, and policy-only syncs remain
// available while the first credential is still connecting.
func TestSlowPerUserBuildDoesNotBlockEntry(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "whoami")
	r.factory.upstream(urlB, "shared_tool")
	release := make(chan struct{})
	entered := make(chan struct{}, 1)
	slowDone := make(chan struct{})
	d := *r.deps.Load()
	inner := d.ClientFactory
	d.ClientFactory = func(cfg mcp.ClientConfig) (mcp.UpstreamClient, error) {
		if cfg.Auth.Token == "slow" {
			select {
			case entered <- struct{}{}:
			default:
			}
			<-release
		}
		return inner(cfg)
	}
	r.deps.Store(&d)
	tok := func(v string) mcp.AuthOverride {
		return mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer " + v}}}
	}
	r.push("org-a", 1, `{"enabled":true,"servers":{
		"mine":{"url":"`+urlA+`","credential":{"mode":"per_user"}},
		"team":{"url":"`+urlB+`","credential":{"mode":"shared","secret":{"type":"bearer","value":"team-secret"}}}}}`)
	_, pool := r.resolve("org-a")
	eventually(t, "team connected", func() bool {
		s, _ := pool.Server("team")
		return s.Healthy
	})

	go func() {
		defer close(slowDone)
		_, _ = pool.Client("mine", "user-slow", tok("slow"))
	}()
	<-entered

	within := func(name string, f func()) {
		done := make(chan struct{})
		go func() { f(); close(done) }()
		select {
		case <-done:
		case <-time.After(300 * time.Millisecond):
			t.Errorf("%s remained blocked on another user's client build", name)
		}
	}
	within("Pool.Servers", func() { pool.Servers() })
	within("Pool.Server", func() { pool.Server("mine") })
	within("another user client", func() { _, _ = pool.Client("mine", "user-fast", tok("fast")) })
	r.push("org-a", 2, `{"enabled":true,"access":{"default":"deny"},"servers":{
		"mine":{"url":"`+urlA+`","credential":{"mode":"per_user"}},
		"team":{"url":"`+urlB+`","credential":{"mode":"shared","secret":{"type":"bearer","value":"team-secret"}}}}}`)
	within("policy-only Resolve", func() { _, _, _ = r.m.Resolve("org-a") })
	within("shared client", func() { _, _ = pool.Client("team", "", mcp.AuthOverride{}) })
	close(release)
	<-slowDone
}
