package tenancy

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"slices"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// errNotConnected is a server with no healthy client yet.
var errNotConnected = errors.New("not connected")

// errNotSignedIn is a shared OAuth server's status until its account is
// signed in.
var errNotSignedIn = errors.New("not connected: the organization's account is not signed in; an Owner or Admin signs it in")

// entry is one server of a pool: its spec, its shared client (or, for a
// per_user server, one client per user) and the lists it served last.
type entry struct {
	id   string
	pool *Pool
	sp   atomic.Pointer[spec]

	ctx    context.Context
	cancel context.CancelFunc

	firstDone chan struct{}
	firstOnce sync.Once

	mu                sync.Mutex
	client            mcp.UpstreamClient
	connectionChanged chan struct{}
	users             map[string]*userClient
	userBuilds        map[string]*userBuild
	tools             []mcp.Tool
	resources         []mcp.Resource
	prompts           []mcp.Prompt
	lastErr           string
	refreshedAt       time.Time
	// pending is the replacement being built after an upstream change; this
	// entry serves until pending has listed its tools (Pool.ready).
	pending *entry
	retired bool
	// serverOwned: the MCP server took the shared client (a published static
	// client), so its Shutdown closes it.
	serverOwned atomic.Bool
}

// userClient is a per-credential client and the fingerprint of the auth it
// was built with.
type userClient struct {
	client  mcp.UpstreamClient
	authFP  string
	ctx     context.Context
	cancel  context.CancelFunc
	life    context.Context
	expires time.Time
	check   func(context.Context) error
	// Discovery belongs to this exact credential version and lifetime, never
	// to the organization-wide entry. Replacing the client drops its catalog.
	// The separate lock serializes discovery without holding entry.mu over I/O.
	catalogMu sync.Mutex
	catalog   *personalCatalog
}

// userBuild is a per-credential construction already in progress. Its done
// channel lets callers wait without holding the entry lock while a factory
// connects to a possibly slow upstream.
type userBuild struct {
	cancel context.CancelFunc
	life   context.Context
	fp     string
	done   chan struct{}
	client mcp.UpstreamClient
	err    error
}

func newEntry(p *Pool, sp spec) *entry {
	ctx, cancel := context.WithCancel(p.m.ctx)
	e := &entry{id: sp.id, pool: p, ctx: ctx, cancel: cancel, firstDone: make(chan struct{}), connectionChanged: make(chan struct{}), users: map[string]*userClient{}, userBuilds: map[string]*userBuild{}}
	e.sp.Store(&sp)
	e.tools = slices.Clone(sp.inventory)
	go e.watchExpiry()
	return e
}

func (e *entry) spec() spec { return *e.sp.Load() }

// run connects the entry, retrying a failed connect with backoff (1 s
// doubling to 5 min by default) until it connects or is retired. adopted is
// a client connected before the pool existed (the static pool's first
// build), used instead of building one.
func (e *entry) run(adopted mcp.UpstreamClient) {
	sp := e.spec()
	if err := e.authorize(); err != nil {
		e.setErr(err)
		e.markFirst()
		return
	}
	// markFirst comes after the attempt's outcome is in the pool (ready's
	// publish and generation bump, or the entry's error), so a request that
	// waits for the first connect (Pool.waitFirst) never sees the pool change
	// under it afterwards: a list it caches then is not dropped by a late bump.
	if sp.perUser {
		e.pool.ready(e)
		e.markFirst()
		return
	}
	if sp.sharedOAuth && !sp.signedIn {
		// Nothing to connect with until an Owner or Admin signs the account
		// in; the push that names it replaces this entry (connected is false).
		e.setErr(errNotSignedIn)
		e.pool.ready(e)
		e.markFirst()
		return
	}
	backoff := e.pool.m.opts.RetryInitial
	for {
		err := e.connect(adopted)
		adopted = nil
		if err == nil {
			e.pool.ready(e)
			e.markFirst()
			if !e.refreshLoop() {
				return
			}
			backoff = e.pool.m.opts.RetryInitial
			continue
		}
		e.setErr(err)
		e.markFirst()
		if sp.builtin != "" && errors.Is(err, errBuiltinUnregistered) {
			slog.Warn("mcp built-in server not registered", "org", e.pool.org, "server", e.id, "error", err)
			return // a built-in id is registered at startup or never
		}
		slog.Warn("mcp upstream not connected; retrying", "org", e.pool.org, "server", e.id, "in", backoff, "error", err)
		select {
		case <-e.ctx.Done():
			return
		case <-time.After(backoff):
		}
		backoff = min(backoff*2, e.pool.m.opts.RetryMax)
	}
}

func (e *entry) markFirst() { e.firstOnce.Do(func() { close(e.firstDone) }) }

// connect builds the shared client (or takes the adopted one) and lists its
// tools, resources and prompts; a client that cannot list its tools is closed.
func (e *entry) connect(adopted mcp.UpstreamClient) error {
	if err := e.authorize(); err != nil {
		return err
	}
	sp := e.spec()
	c := adopted
	if c == nil {
		var err error
		if c, err = e.pool.m.build(e.ctx, e.pool.org, sp, nil, e.authorize); err != nil {
			return err
		}
	}
	if err := e.authorize(); err != nil {
		_ = c.Close()
		return err
	}
	if err := e.list(c); err != nil {
		_ = c.Close()
		return err
	}
	if checked, ok := c.(interface{ CheckCredential(context.Context) error }); ok {
		if err := checked.CheckCredential(e.ctx); err != nil {
			_ = c.Close()
			return err
		}
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.retired || !c.Healthy() {
		go c.Close()
		return mcp.CredentialRequired(e.id, "superseded")
	}
	e.client = c
	close(e.connectionChanged)
	e.connectionChanged = make(chan struct{})
	e.lastErr = ""
	return nil
}

// list reads the client's lists into the entry. Tools are required; a server
// without resources or prompts lists none.
func (e *entry) list(c mcp.UpstreamClient) error {
	ctx, cancel := context.WithTimeout(e.ctx, e.pool.m.opts.ListTimeout)
	defer cancel()
	rawTools, err := c.ListTools(ctx)
	if err != nil {
		return fmt.Errorf("tools/list: %w", err)
	}
	tools := decodeAll[mcp.Tool](rawTools)
	var resources []mcp.Resource
	if raw, err := c.ListResources(ctx); err == nil {
		resources = decodeAll[mcp.Resource](raw)
	}
	var prompts []mcp.Prompt
	if raw, err := c.ListPrompts(ctx); err == nil {
		prompts = decodeAll[mcp.Prompt](raw)
	}
	if err := e.authorize(); err != nil {
		return err
	}
	if checked, ok := c.(interface{ CheckCredential(context.Context) error }); ok {
		if err := checked.CheckCredential(ctx); err != nil {
			return err
		}
	}
	e.mu.Lock()
	if e.retired || ctx.Err() != nil || !c.Healthy() {
		e.mu.Unlock()
		return mcp.CredentialRequired(e.id, "superseded")
	}
	changed := !sameTools(e.tools, tools) || !sameResources(e.resources, resources) || !samePrompts(e.prompts, prompts)
	e.tools, e.resources, e.prompts = tools, resources, prompts
	e.refreshedAt = e.pool.m.now()
	e.mu.Unlock()
	if changed {
		e.pool.touch()
	}
	return nil
}

// refreshLoop re-reads the lists every tools_cache_ttl_seconds, so a
// client's refreshed lists reach the pool and its list cache.
func (e *entry) refreshLoop() bool {
	var ended <-chan struct{}
	e.mu.Lock()
	client := e.client
	e.mu.Unlock()
	if life, ok := client.(interface{ CredentialLifetime() context.Context }); ok && life.CredentialLifetime() != nil && e.spec().sharedOAuth {
		ended = life.CredentialLifetime().Done()
	}
	ttl := time.Duration(e.spec().cacheTTLSeconds) * time.Second
	var ticks <-chan time.Time
	if ttl > 0 {
		timer := time.NewTicker(ttl)
		defer timer.Stop()
		ticks = timer.C
	} else if ended == nil {
		return false
	}
	for {
		select {
		case <-e.ctx.Done():
			return false
		case <-ended:
			e.mu.Lock()
			if e.client == client {
				e.client = nil
			}
			e.mu.Unlock()
			_ = client.Close()
			e.pool.touch()
			return e.ctx.Err() == nil
		case <-ticks:
			e.mu.Lock()
			c := e.client
			e.mu.Unlock()
			if c != nil {
				if err := e.list(c); err != nil {
					slog.Debug("mcp tools refresh failed", "org", e.pool.org, "server", e.id, "error", err)
				}
			}
		}
	}
}

func (e *entry) setErr(err error) {
	e.mu.Lock()
	e.lastErr = err.Error()
	e.mu.Unlock()
}

// detachShared drops the shared client without closing it.
func (e *entry) detachShared() {
	e.mu.Lock()
	e.client = nil
	e.mu.Unlock()
}

// retire stops the entry and closes its clients in the background; the
// manager's Close waits for them.
func (e *entry) retire() {
	e.mu.Lock()
	if e.retired {
		e.mu.Unlock()
		return
	}
	e.pool.m.closing.add()
	e.retired = true
	e.cancel()
	clients := make([]mcp.UpstreamClient, 0, len(e.users)+1)
	if e.client != nil {
		clients = append(clients, e.client)
	}
	for _, uc := range e.users {
		uc.cancel()
		clients = append(clients, uc.client)
	}
	e.client, e.users = nil, map[string]*userClient{}
	pending := e.pending
	e.pending = nil
	e.mu.Unlock()
	if pending != nil {
		pending.retire()
	}
	e.markFirst()
	go func() {
		defer e.pool.m.closing.done()
		var wg sync.WaitGroup
		for _, c := range clients {
			wg.Add(1)
			go func(c mcp.UpstreamClient) { defer wg.Done(); _ = c.Close() }(c)
		}
		wg.Wait()
	}()
}

// status is the entry as a pool server.
func (e *entry) status() mcp.PoolServer {
	sp := e.spec()
	e.mu.Lock()
	defer e.mu.Unlock()
	ps := mcp.PoolServer{
		ID: e.id, Source: sp.source, Managed: sp.source == SourceManaged, Builtin: sp.builtin != "",
		PerUser: sp.perUser, SharedOAuth: sp.sharedOAuth, Era: sp.era, Transport: sp.transport, TimeoutMs: sp.timeoutMs,
		ToolsCacheTTLSeconds: sp.cacheTTLSeconds, ForwardHeaders: sp.forwardHeaders,
		PropagateDepth: sp.propagateDepth, TrustedAnnotations: sp.trustedAnnotations,
		CredentialState: sp.credentialState,
		LastError:       e.lastErr, LastRefreshedAt: e.refreshedAt,
		// Copies: a hook may filter the lists it reads in place.
		Tools: slices.Clone(e.tools), Resources: slices.Clone(e.resources), Prompts: slices.Clone(e.prompts),
	}
	ps.Healthy = e.client != nil && e.client.Healthy()
	for _, uc := range e.users {
		ps.Healthy = ps.Healthy || uc.client.Healthy()
	}
	if p := e.pending; p != nil {
		p.mu.Lock()
		if p.lastErr != "" {
			ps.LastError = p.lastErr
		}
		p.mu.Unlock()
	}
	return ps
}
