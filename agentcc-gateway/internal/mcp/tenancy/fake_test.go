package tenancy

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"sort"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// fakeUpstream is one upstream, named by its URL: the tools it lists and the
// calls it received.
type fakeUpstream struct {
	tools []string
	mu    sync.Mutex
	calls []string // tool names called on it
	auths []mcp.AuthConfig
}

func (u *fakeUpstream) called() []string {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]string(nil), u.calls...)
}

// fakeClient is a connected client of one fakeUpstream.
type fakeClient struct {
	cfg       mcp.ClientConfig
	up        *fakeUpstream
	closed    atomic.Bool
	closeCall atomic.Int32
	opts      []mcp.CallOptions
	mu        sync.Mutex
}

func (c *fakeClient) ListTools(context.Context) ([]json.RawMessage, error) {
	out := make([]json.RawMessage, 0, len(c.up.tools))
	for _, name := range c.up.tools {
		out = append(out, json.RawMessage(fmt.Sprintf(`{"name":%q,"inputSchema":{"type":"object"}}`, name)))
	}
	return out, nil
}

func (c *fakeClient) CallTool(_ context.Context, name string, _ json.RawMessage, opts mcp.CallOptions) (json.RawMessage, error) {
	c.up.mu.Lock()
	c.up.calls = append(c.up.calls, name)
	c.up.mu.Unlock()
	c.mu.Lock()
	c.opts = append(c.opts, opts)
	c.mu.Unlock()
	return json.RawMessage(fmt.Sprintf(`{"content":[{"type":"text","text":%q}]}`, c.cfg.URL+"#"+name)), nil
}

func (c *fakeClient) ListResources(context.Context) ([]json.RawMessage, error) { return nil, nil }
func (c *fakeClient) ReadResource(context.Context, string, mcp.CallOptions) (json.RawMessage, error) {
	return nil, errors.New("none")
}
func (c *fakeClient) ListPrompts(context.Context) ([]json.RawMessage, error) { return nil, nil }
func (c *fakeClient) GetPrompt(context.Context, string, json.RawMessage, mcp.CallOptions) (json.RawMessage, error) {
	return nil, errors.New("none")
}
func (c *fakeClient) Healthy() bool { return !c.closed.Load() }
func (c *fakeClient) Close() error {
	c.closeCall.Add(1)
	c.closed.Store(true)
	return nil
}

// fakeFactory is a Deps.ClientFactory over fake upstreams, counting builds
// per server id and failing a URL a set number of times first.
type fakeFactory struct {
	mu        sync.Mutex
	upstreams map[string]*fakeUpstream // by URL
	failLeft  map[string]int           // by URL
	builds    map[string]int           // by server id
	clients   []*fakeClient
	dialer    *countingDialer
}

func newFactory() *fakeFactory {
	return &fakeFactory{upstreams: map[string]*fakeUpstream{}, failLeft: map[string]int{}, builds: map[string]int{}}
}

func (f *fakeFactory) upstream(url string, tools ...string) *fakeUpstream {
	f.mu.Lock()
	defer f.mu.Unlock()
	u := &fakeUpstream{tools: tools}
	f.upstreams[url] = u
	return u
}

func (f *fakeFactory) build(cfg mcp.ClientConfig) (mcp.UpstreamClient, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.builds[cfg.ServerID]++
	if f.dialer != nil {
		f.dialer.n.Add(1) // a real factory dials through Deps.Dialer
	}
	if f.failLeft[cfg.URL] > 0 {
		f.failLeft[cfg.URL]--
		return nil, fmt.Errorf("connect %s: connection refused", cfg.URL)
	}
	u := f.upstreams[cfg.URL]
	if u == nil {
		return nil, fmt.Errorf("connect %s: no such upstream", cfg.URL)
	}
	u.mu.Lock()
	u.auths = append(u.auths, cfg.Auth)
	u.mu.Unlock()
	c := &fakeClient{cfg: cfg, up: u}
	f.clients = append(f.clients, c)
	return c, nil
}

func (f *fakeFactory) buildsOf(id string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.builds[id]
}

func (f *fakeFactory) totalBuilds() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := 0
	for _, b := range f.builds {
		n += b
	}
	return n
}

// countingDialer is a Deps.Dialer that counts dials.
type countingDialer struct{ n atomic.Int64 }

func (d *countingDialer) DialContext(context.Context, string, string) (net.Conn, error) {
	d.n.Add(1)
	return nil, errors.New("no network in tests")
}

// fixedManaged is a Deps.ManagedServers that offers each org its list.
type fixedManaged map[string][]mcp.ClientConfig

func (m fixedManaged) ForOrg(org string) []mcp.ClientConfig { return m[org] }

// rig is a manager over a store and a fake factory.
type rig struct {
	t       *testing.T
	store   *tenant.Store
	factory *fakeFactory
	deps    atomic.Pointer[mcp.Deps]
	m       *Manager
}

func newRig(t *testing.T, opts Options) *rig {
	t.Helper()
	r := &rig{t: t, store: tenant.NewStore(), factory: newFactory()}
	r.deps.Store(&mcp.Deps{ClientFactory: r.factory.build, Builtins: mcp.NewBuiltinRegistry(), ManagedServers: fixedManaged{}})
	opts.Store = r.store
	opts.Deps = func() *mcp.Deps { return r.deps.Load() }
	if opts.RetryInitial == 0 {
		opts.RetryInitial = 10 * time.Millisecond
	}
	if opts.RetryMax == 0 {
		opts.RetryMax = 40 * time.Millisecond
	}
	if opts.InitialWait == 0 {
		opts.InitialWait = 2 * time.Second
	}
	r.m = New(opts)
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := r.m.Close(ctx); err != nil {
			t.Errorf("manager close: %v", err)
		}
	})
	return r
}

// push stores an org config built from a JSON mcp block and a version.
func (r *rig) push(org string, version int, mcpJSON string) {
	r.t.Helper()
	var cfg tenant.OrgConfig
	raw := fmt.Sprintf(`{"meta":{"version":%d},"mcp":%s}`, version, mcpJSON)
	if err := json.Unmarshal([]byte(raw), &cfg); err != nil {
		r.t.Fatalf("org config: %v", err)
	}
	r.store.Set(org, &cfg)
}

// resolve resolves org and fails the test on an error.
func (r *rig) resolve(org string) (*tenant.OrgConfig, mcp.TenantPool) {
	r.t.Helper()
	snap, pool, err := r.m.Resolve(org)
	if err != nil {
		r.t.Fatalf("Resolve(%q): %v", org, err)
	}
	return snap, pool
}

// eventually polls cond for up to 3 s.
func eventually(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", what)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

// toolNames lists "<server>/<tool>" for every tool of the pool, sorted.
func toolNames(p mcp.TenantPool) []string {
	var out []string
	for _, s := range p.Servers() {
		for _, tool := range s.Tools {
			out = append(out, s.ID+"/"+tool.Name)
		}
	}
	sort.Strings(out)
	return out
}

func serverIDs(p mcp.TenantPool) []string {
	var out []string
	for _, s := range p.Servers() {
		out = append(out, s.ID)
	}
	return out
}
