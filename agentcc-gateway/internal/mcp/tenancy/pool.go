package tenancy

import (
	"errors"
	"fmt"
	"maps"
	"slices"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// ErrNoLastKnownGood: the org's config cannot be loaded and no earlier
// version was, so its policy cannot be evaluated (policy_unavailable).
var ErrNoLastKnownGood = errors.New("mcp config cannot be loaded and there is no last-known-good version")

// Pool is one org's servers (mcp.TenantPool). Its entries map and policy
// snapshot are replaced whole, so readers never lock.
type Pool struct {
	org string
	m   *Manager

	mu       sync.Mutex // serializes apply, ready and close
	syncMu   sync.Mutex // serializes store reads with apply and seen updates
	entries  atomic.Pointer[map[string]*entry]
	loadErrs atomic.Pointer[map[string]string]
	snapshot atomic.Pointer[tenant.OrgConfig]
	// seen is the store's config pointer this pool last evaluated, and
	// accepted whether any version has loaded (last-known-good exists).
	seen     atomic.Pointer[tenant.OrgConfig]
	accepted atomic.Bool
	closed   bool

	gen   atomic.Uint64 // bumped on every change the list cache must see
	cache listCache
}

func newPool(m *Manager, org string) *Pool {
	p := &Pool{org: org, m: m}
	empty := map[string]*entry{}
	p.entries.Store(&empty)
	return p
}

// OrgID is the org the pool serves.
func (p *Pool) OrgID() string { return p.org }

func (p *Pool) touch() { p.gen.Add(1) }

func (p *Pool) current() map[string]*entry { return *p.entries.Load() }

// apply makes the pool serve specs. A config with load errors is refused
// whole: the pool keeps its last-known-good version and reports the errors,
// or, with no earlier version, returns ErrNoLastKnownGood. Otherwise only the
// servers whose upstream fingerprint changed, or that were added or removed,
// are rebuilt; the rest keep their clients and take the new spec, and the
// policy snapshot is swapped atomically.
func (p *Pool) apply(specs map[string]spec, errs map[string]error, snapshot *tenant.OrgConfig, adopt map[string]mcp.UpstreamClient) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.closed {
		return errors.New("pool closed")
	}
	if len(errs) > 0 {
		msgs := make(map[string]string, len(errs))
		for id, err := range errs {
			msgs[id] = "config_invalid: " + err.Error()
		}
		p.loadErrs.Store(&msgs)
		p.touch()
		if !p.accepted.Load() {
			return fmt.Errorf("%w: %v", ErrNoLastKnownGood, errors.Join(slices.Collect(maps.Values(errs))...))
		}
		return nil
	}
	cur := p.current()
	next := make(map[string]*entry, len(specs))
	start := make(map[*entry]mcp.UpstreamClient)
	for id, sp := range specs {
		old := cur[id]
		switch {
		case old == nil:
			e := newEntry(p, sp)
			next[id] = e
			start[e] = adopt[id]
		case old.isRetired() || old.spec().credentialFP != sp.credentialFP:
			e := newEntry(p, sp)
			// Keep the tool inventory so existing sessions receive auth_required,
			// not tool-not-found, while a new credential is being connected.
			old.mu.Lock()
			e.mu.Lock()
			e.tools, e.resources, e.prompts = slices.Clone(old.tools), slices.Clone(old.resources), slices.Clone(old.prompts)
			e.mu.Unlock()
			old.mu.Unlock()
			next[id] = e
			p.retireLocked(old, false)
			start[e] = nil
		case old.spec().fp == sp.fp:
			old.sp.Store(&sp)
			old.dropPendingUnless(sp.fp)
			next[id] = old
		case !old.connected():
			e := newEntry(p, sp)
			next[id] = e
			p.retireLocked(old, false)
			start[e] = nil
		default:
			old.dropPendingUnless(sp.fp)
			if old.pendingFP() != sp.fp {
				pending := newEntry(p, sp)
				old.setPending(pending)
				start[pending] = nil
			}
			next[id] = old
		}
	}
	for id, old := range cur {
		if _, kept := next[id]; !kept {
			p.retireLocked(old, true)
		}
	}
	p.entries.Store(&next)
	p.loadErrs.Store(nil)
	p.snapshot.Store(snapshot)
	p.accepted.Store(true)
	p.touch()
	// Register every new entry and pending replacement before a factory can
	// authenticate. A prior revoke is caught by run's authorization; a later
	// revoke can find and cancel the entry through invalidateCredentials.
	for e, adopted := range start {
		go e.run(adopted)
	}
	return nil
}

// ready is an entry's report that it connected and listed its tools: a
// current entry is published; a pending replacement takes its server's place
// and the entry it replaces is closed; a stale one is closed.
func (p *Pool) ready(e *entry) {
	p.mu.Lock()
	defer p.mu.Unlock()
	cur := p.current()
	old := cur[e.id]
	switch {
	case old == e && !p.closed && !e.isRetired():
		p.publish(e)
	case old != nil && old.isPending(e) && !p.closed && !e.isRetired():
		next := maps.Clone(cur)
		next[e.id] = e
		old.setPending(nil)
		p.entries.Store(&next)
		p.publish(e)
		p.retireLocked(old, false)
	default:
		e.retire()
	}
	p.touch()
}

// publish hands a static server's current client to the MCP server.
func (p *Pool) publish(e *entry) {
	if e.spec().source != SourceStatic || p.m.opts.Publish == nil {
		return
	}
	if c, err := e.sharedClient(); err == nil {
		e.serverOwned.Store(p.m.opts.Publish(e.id, c))
	}
}

// retireLocked closes an entry; removed says the server left the pool, so a
// static server is unpublished.
func (p *Pool) retireLocked(e *entry, removed bool) {
	if removed && e.spec().source == SourceStatic && p.m.opts.Publish != nil {
		p.m.opts.Publish(e.id, nil)
	}
	e.retire()
	p.touch()
}

// waitFirst waits, up to d, for every server's first connect attempt, so a
// request right after a push sees the servers it just configured.
func (p *Pool) waitFirst(d time.Duration) {
	if d <= 0 {
		return
	}
	timer := time.NewTimer(d)
	defer timer.Stop()
	for _, e := range p.current() {
		select {
		case <-e.firstDone:
		case <-timer.C:
			return
		}
	}
}

// close retires every entry at shutdown; a shared client the MCP server
// owns is left open for the server's Shutdown, which closes it and waits.
func (p *Pool) close() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.closed = true
	for _, e := range p.current() {
		if e.serverOwned.Load() {
			e.detachShared()
		}
		p.retireLocked(e, false)
	}
	empty := map[string]*entry{}
	p.entries.Store(&empty)
}

// Servers returns every server, sorted by id, with a row for each server
// the current config refused (its last_error is the load error).
func (p *Pool) Servers() []mcp.PoolServer {
	cur := p.current()
	var errs map[string]string
	if e := p.loadErrs.Load(); e != nil {
		errs = *e
	}
	out := make([]mcp.PoolServer, 0, len(cur)+len(errs))
	for _, e := range cur {
		ps := e.status()
		if msg, ok := errs[ps.ID]; ok {
			ps.LastError = msg
		}
		out = append(out, ps)
	}
	for id, msg := range errs {
		if _, ok := cur[id]; !ok {
			out = append(out, mcp.PoolServer{ID: id, Source: SourceOrg, LastError: msg})
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// Server returns one server by id.
func (p *Pool) Server(id string) (mcp.PoolServer, bool) {
	e := p.current()[id]
	if e == nil {
		return mcp.PoolServer{}, false
	}
	return e.status(), true
}

// Client returns serverID's client for one credential identity (see
// mcp.TenantPool). Calls through it default to the server's timeout_ms.
func (p *Pool) Client(serverID, credKey string, auth mcp.AuthOverride) (mcp.UpstreamClient, error) {
	e := p.current()[serverID]
	if e == nil {
		return nil, fmt.Errorf("mcp server %q is not in org %q's pool", serverID, p.org)
	}
	if err := e.authorize(); err != nil {
		return nil, err
	}
	sp := e.spec()
	var (
		c    mcp.UpstreamClient
		err  error
		user bool
	)
	switch {
	case sp.builtin != "":
		c, err = e.sharedClient() // a built-in server has no per-user credential
	case sp.perUser && credKey == "":
		return nil, mcp.CredentialRequired(serverID, "no_user_identity")
	case sp.perUser && authFromOverride(auth).Type == "":
		// A verified person id selects a credential; it is not one. Check the
		// effective credential before building or consulting cached clients.
		return nil, mcp.CredentialRequired(serverID, "no_token")
	case credKey == "" && len(auth.Header) == 0:
		c, err = e.sharedClient()
	default:
		user = true
		c, err = e.userClientFor(credKey, auth)
		if err == nil && sp.perUser {
			e.listThrough(c)
		}
	}
	if err != nil {
		if sp.credentialRequired {
			return nil, mcp.CredentialRequired(serverID, "needs_reauth")
		}
		return nil, err
	}
	if err := e.authorize(); err != nil {
		return nil, err
	}
	return &timeoutClient{UpstreamClient: c, timeout: time.Duration(sp.timeoutMs) * time.Millisecond, entry: e, userKey: credKey, user: user}, nil
}

// CachedList returns the cached list for key (mcp.TenantPool).
func (p *Pool) CachedList(key mcp.ListCacheKey, build func() []mcp.Tool) []mcp.Tool {
	return p.cache.get([2]uint64{p.gen.Load(), 0}, key, build)
}

// --- small entry helpers used by apply and ready ---

func (e *entry) isRetired() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.retired
}

func (e *entry) connected() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.client != nil || len(e.users) > 0 || e.spec().perUser
}

func (e *entry) setPending(pending *entry) {
	e.mu.Lock()
	e.pending = pending
	e.mu.Unlock()
}

func (e *entry) isPending(other *entry) bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.pending == other
}

func (e *entry) pendingFP() string {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.pending == nil {
		return ""
	}
	return e.pending.spec().fp
}

// dropPendingUnless retires a pending replacement built for another
// fingerprint than fp.
func (e *entry) dropPendingUnless(fp string) {
	e.mu.Lock()
	pending := e.pending
	if pending != nil && pending.spec().fp != fp {
		e.pending = nil
	} else {
		pending = nil
	}
	e.mu.Unlock()
	if pending != nil {
		pending.retire()
	}
}
