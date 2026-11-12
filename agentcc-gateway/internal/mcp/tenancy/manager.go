package tenancy

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Options configure a Manager. Only Deps is required.
type Options struct {
	// Store holds the pushed org configs; nil serves no org.
	Store *tenant.Store
	// MCP is the operator's mcp config: the static YAML servers, static.org_id,
	// the stdio settings and managed_servers.
	MCP config.MCPConfig
	// Redactor is the operator's log redactor, for static stdio stderr.
	Redactor *privacy.Redactor
	// Catalog is the connector catalog the servers added from it (their
	// catalog_ref) take their declared credential parts from; nil
	// refuses such a server's shared credential, never sends it in part.
	Catalog credtemplate.Catalog
	// Deps returns the MCP server's dependencies; read each time a client is
	// built, so the factory and dialer installed by later wiring steps apply.
	Deps func() *mcp.Deps
	// Adopt returns the static clients connected before the manager existed
	// (mcp.Server.StaticClients), adopted on the static pool's first build.
	Adopt func() map[string]mcp.UpstreamClient
	// Publish hands a static server's current client (nil: removed) to the
	// MCP server (mcp.Server.SetStaticClient) and reports whether the server
	// now owns it: an owned client is closed by the server's own Shutdown,
	// so the manager's Close leaves it.
	Publish func(serverID string, c mcp.UpstreamClient) (owned bool)

	RetryInitial time.Duration // first retry delay of a failed connect; default 1 s
	RetryMax     time.Duration // longest retry delay; default 5 min
	ListTimeout  time.Duration // bound on one listing; default 30 s
	// InitialWait bounds how long a request waits for a just-configured
	// server's first connect; default 3 s.
	InitialWait time.Duration
	Clock       func() time.Time
}

// Manager keeps one Pool per org and the static pool (mcp.Tenancy).
type Manager struct {
	opts Options

	ctx    context.Context
	cancel context.CancelFunc

	mu         sync.Mutex
	pools      map[string]*Pool
	static     *Pool
	empty      *Pool
	staticView *staticOrgView
	staticCfg  config.MCPConfig
	staticInit bool

	closing     closeTracker // clients being closed by retired entries
	unsubscribe func()
}

var _ mcp.Tenancy = (*Manager)(nil)

// New returns a Manager. Nothing connects until an org is first resolved.
func New(opts Options) *Manager {
	if opts.RetryInitial <= 0 {
		opts.RetryInitial = time.Second
	}
	if opts.RetryMax <= 0 {
		opts.RetryMax = 5 * time.Minute
	}
	if opts.ListTimeout <= 0 {
		opts.ListTimeout = 30 * time.Second
	}
	if opts.InitialWait < 0 {
		opts.InitialWait = 0
	} else if opts.InitialWait == 0 {
		opts.InitialWait = 3 * time.Second
	}
	if opts.Clock == nil {
		opts.Clock = time.Now
	}
	ctx, cancel := context.WithCancel(context.Background())
	m := &Manager{opts: opts, ctx: ctx, cancel: cancel, pools: map[string]*Pool{}, staticCfg: opts.MCP}
	m.static = newPool(m, "")
	m.empty = newPool(m, "")
	if opts.Store != nil {
		m.unsubscribe = opts.Store.ObserveChanges(m.invalidateCredentials)
	}
	return m
}

func (m *Manager) now() time.Time { return m.opts.Clock() }

// emptyPool is the stable no-org view used when static.org_id binds the YAML
// pool to one explicit organization.
func (m *Manager) emptyPool() *Pool { return m.empty }

// StaticOrgID is the org the static YAML pool serves, "" for none.
func (m *Manager) StaticOrgID() string {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.staticCfg.Static.OrgID
}

// Known reports whether orgID has a stored config or is the static org.
func (m *Manager) Known(orgID string) bool {
	if orgID == "" {
		return false
	}
	if orgID == m.StaticOrgID() {
		return true
	}
	return m.opts.Store != nil && m.opts.Store.Get(orgID) != nil
}

// Resolve returns the org's policy snapshot and pool (mcp.Tenancy). A
// principal with no org gets the static pool only when no static org is
// configured; once static.org_id is set, an unbound principal gets no YAML
// servers. The static org gets its own pool together with the static pool;
// every other org gets only its own. The org's stored config is compared on
// access with the one its pool last evaluated, and a changed one is applied
// before returning.
func (m *Manager) Resolve(orgID string) (*tenant.OrgConfig, mcp.TenantPool, error) {
	if orgID == "" {
		if m.StaticOrgID() != "" {
			return nil, m.emptyPool(), nil
		}
		return nil, m.staticPool(), nil
	}
	p := m.orgPool(orgID)
	applied, err := m.sync(p)
	if err != nil {
		return nil, nil, err
	}
	if applied {
		// The request that applies a new version waits, bounded, for the
		// first connect of the servers it added; later requests never wait.
		p.waitFirst(m.opts.InitialWait)
	}
	snapshot := p.snapshot.Load()
	if orgID == m.StaticOrgID() {
		m.staticPool()
		return snapshot, m.staticView, nil
	}
	return snapshot, p, nil
}

// ResolveStatus is the admin-only variant of Resolve. It returns the pool
// even when the first config is invalid, allowing status to expose its load
// errors while call-chain resolution continues to fail closed.
func (m *Manager) ResolveStatus(orgID string) (*tenant.OrgConfig, mcp.TenantPool, error) {
	if orgID == "" {
		if m.StaticOrgID() != "" {
			return nil, m.emptyPool(), nil
		}
		return nil, m.staticPool(), nil
	}
	p := m.orgPool(orgID)
	applied, err := m.sync(p)
	if applied {
		p.waitFirst(m.opts.InitialWait)
	}
	snapshot := p.snapshot.Load()
	if orgID == m.StaticOrgID() {
		m.staticPool()
		return snapshot, m.staticView, err
	}
	return snapshot, p, err
}

func (m *Manager) orgPool(orgID string) *Pool {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.orgPoolLocked(orgID)
}

// orgPoolLocked is orgPool with m.mu held.
func (m *Manager) orgPoolLocked(orgID string) *Pool {
	p := m.pools[orgID]
	if p == nil {
		p = newPool(m, orgID)
		m.pools[orgID] = p
	}
	return p
}

// sync applies the org's stored config when it is not the one the pool last
// evaluated (the store replaces a config's pointer on every write, and a new
// version always arrives as a new pointer, so this also catches a change of
// meta.version).
func (m *Manager) sync(p *Pool) (applied bool, err error) {
	// Keep the store read, apply and seen update in one per-pool critical
	// section. Reading cfg before the lock lets an older Resolve overwrite a
	// newer config after the newer call has already applied it.
	p.syncMu.Lock()
	defer p.syncMu.Unlock()
	var cfg *tenant.OrgConfig
	if m.opts.Store != nil {
		cfg = m.opts.Store.Get(p.org)
	}
	if p.seen.Load() == cfg && (p.accepted.Load() || cfg == nil) {
		return false, nil
	}
	// Deployment capabilities are immutable for this manager. Restart to change;
	// a static YAML reload cannot re-enable cached catalogue connections.
	specs, errs := orgSpecsForRelease(cfg, m.opts.Catalog, m.opts.MCP.CatalogEnabled)
	if cfg != nil && cfg.MCP != nil && cfg.MCP.Enabled {
		m.mu.Lock()
		operator := m.staticCfg.ManagedServers
		m.mu.Unlock()
		for id, sp := range managedSpecs(m.deps().ManagedServers.ForOrg(p.org), operator) {
			bound, bindErr := bindManagedCredential(sp, cfg)
			if bindErr != nil {
				errs[id] = bindErr
				continue
			}
			specs[id] = bound
		}
	}
	err = p.apply(specs, errs, cfg, nil)
	p.seen.Store(cfg)
	return err == nil, err
}

// staticPool builds the static pool on first use, adopting the clients the
// route setup connected (they predate the wiring's dependencies); every
// later build goes through Deps.ClientFactory.
func (m *Manager) staticPool() *Pool {
	m.mu.Lock()
	if m.staticInit {
		m.mu.Unlock()
		return m.static
	}
	m.staticInit = true
	cfg := m.staticCfg
	m.staticView = &staticOrgView{static: m.static}
	if org := cfg.Static.OrgID; org != "" {
		m.staticView.orgPool.Store(m.orgPoolLocked(org))
	}
	m.mu.Unlock()
	var adopt map[string]mcp.UpstreamClient
	if m.opts.Adopt != nil {
		adopt = m.opts.Adopt()
	}
	specs, errs := staticSpecs(cfg, m.opts.Redactor)
	m.applyStatic(specs, errs, adopt)
	m.static.waitFirst(m.opts.InitialWait)
	return m.static
}

// applyStatic applies YAML specs; a refused server is reported, the others
// serve (the operator's config was validated at load).
func (m *Manager) applyStatic(specs map[string]spec, errs map[string]error, adopt map[string]mcp.UpstreamClient) {
	if err := m.static.apply(specs, nil, nil, adopt); err != nil {
		return
	}
	if len(errs) > 0 {
		msgs := make(map[string]string, len(errs))
		for id, err := range errs {
			msgs[id] = "config_invalid: " + err.Error()
		}
		m.static.loadErrs.Store(&msgs)
		m.static.touch()
	}
}

// ReloadStatic rebuilds the static pool from a reloaded operator config
// (/-/reload): only the YAML servers whose fingerprint changed reconnect.
func (m *Manager) ReloadStatic(cfg config.MCPConfig) {
	m.staticPool()
	m.mu.Lock()
	m.staticCfg = cfg
	m.mu.Unlock()
	if org := cfg.Static.OrgID; org != "" {
		m.staticView.orgPool.Store(m.orgPool(org))
	}
	specs, errs := staticSpecs(cfg, m.opts.Redactor)
	m.applyStatic(specs, errs, nil)
}

// build makes a client for sp, a server of org, through the server's current
// dependencies: a built-in server from Deps.Builtins, anything else from
// Deps.ClientFactory. auth, when set, is the credential the client is built
// with (a per-user client); a shared OAuth server's client takes org's
// signed-in account from Deps.SharedCredentials instead. A customer credential
// bound to a built-in gets the same admission, lifetime and completion guards
// as one sent to a remote server.
func (m *Manager) build(ctx context.Context, org string, sp spec, auth *mcp.AuthOverride, authorize func() error) (mcp.UpstreamClient, error) {
	deps := m.deps()
	var builtin mcp.UpstreamClient
	if sp.builtin != "" {
		for _, id := range sp.builtinIDs {
			if srv, ok := deps.Builtins.Get(id); ok {
				// The registry owns srv and shares it across orgs. Use a
				// per-entry adapter so retiring one org, or ending one org's
				// grant, cannot close srv for every other org using it.
				builtin = &builtinClient{UpstreamClient: srv}
				break
			}
		}
		if builtin == nil {
			return nil, fmt.Errorf("built-in server %q is %w", sp.builtin, errBuiltinUnregistered)
		}
	}
	cfg, shared, cleanup, err := constructionAuth(ctx, org, sp, auth, deps)
	if err != nil {
		cleanup()
		return nil, err
	}
	if shared != nil {
		auth = shared
	}
	if deps.ClientAdmission != nil {
		cfg.Auth, auth = guardedAuth(cfg.Auth, auth, authorize)
	}
	owned := false
	defer func() {
		if !owned {
			cleanup()
		}
	}()
	if builtin == nil && deps.ClientFactory == nil {
		return nil, errors.New("no MCP client factory")
	}
	if cfg.Lifetime.Err() != nil {
		return nil, mcp.CredentialRequired(sp.id, "superseded")
	}
	c := builtin
	if c == nil {
		c, err = deps.ClientFactory(cfg)
		if err != nil {
			if c != nil {
				_ = c.Close()
			}
			return nil, err
		}
		if c == nil {
			return nil, fmt.Errorf("mcp server %q: the client factory returned no client", sp.id)
		}
	}
	if cfg.Lifetime.Err() != nil {
		_ = c.Close()
		return nil, mcp.CredentialRequired(sp.id, "superseded")
	}
	if auth != nil {
		guarded := &timeoutClient{UpstreamClient: c, grant: auth, life: cfg.Lifetime, cleanup: cleanup, server: sp.id}
		if err := guarded.CheckCredential(ctx); err != nil {
			_ = c.Close()
			return nil, err
		}
		raw := c
		stop := context.AfterFunc(cfg.Lifetime, func() { _ = raw.Close() })
		guarded.cleanup = func() { stop(); cleanup() }
		c = guarded
	}
	owned = true
	return c, nil
}

func (m *Manager) deps() *mcp.Deps {
	if m.opts.Deps != nil {
		if d := m.opts.Deps(); d != nil {
			return d
		}
	}
	return &mcp.Deps{ClientFactory: mcp.DefaultClientFactory, Builtins: mcp.NewBuiltinRegistry(), ManagedServers: noManaged{}}
}

type noManaged struct{}

func (noManaged) ForOrg(string) []mcp.ClientConfig { return nil }

// Close closes every pool and waits for their clients to close, bounded by
// ctx; a static client the MCP server owns is left to its Shutdown.
func (m *Manager) Close(ctx context.Context) error {
	if m.unsubscribe != nil {
		m.unsubscribe()
	}
	m.cancel()
	m.mu.Lock()
	pools := make([]*Pool, 0, len(m.pools)+1)
	for _, p := range m.pools {
		pools = append(pools, p)
	}
	pools = append(pools, m.static)
	m.mu.Unlock()
	for _, p := range pools {
		p.close()
	}
	return m.closing.wait(ctx)
}
