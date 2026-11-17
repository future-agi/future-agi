package tenancy

import (
	"context"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Credential identity is independent of connection health and policy loading.
// In particular an invalid sibling server cannot preserve a superseded secret
// through the pool's last-known-good policy behavior.
func credentialFingerprint(s tenant.MCPServerSettings) string {
	return fingerprint([]any{s.Credential, s.Auth})
}

func credentialReason(state, expiry string, now time.Time) string {
	if state != "" && state != "active" {
		return state
	}
	if expiry != "" {
		deadline, err := time.Parse(time.RFC3339Nano, expiry)
		if err != nil || !now.Before(deadline) {
			return "expired"
		}
	}
	return ""
}

func (e *entry) credentialReason() string {
	sp := e.spec()
	if reason := credentialReason(sp.credentialState, sp.credentialExpiry, e.pool.m.now()); reason != "" {
		return reason
	}
	if sp.source == SourceStatic || e.pool.m.opts.Store == nil {
		return ""
	}
	return e.reasonIn(e.pool.m.opts.Store.Get(e.pool.org))
}

// reasonIn reads only the supplied snapshot, including inside a store write.
func (e *entry) reasonIn(cfg *tenant.OrgConfig) string {
	sp := e.spec()
	if cfg == nil || cfg.MCP == nil || !cfg.MCP.Enabled {
		return "disabled"
	}
	if sp.source == SourceManaged {
		c := managedCredential(cfg, e.id)
		if c != nil {
			if reason := credentialReason(c.State, c.ExpiresAt, e.pool.m.now()); reason != "" {
				return reason
			}
		}
		if fingerprint(c) != sp.credentialFP {
			return "superseded"
		}
		return ""
	}
	s, ok := cfg.MCPServer(e.id)
	if !ok || !s.Enabled {
		return "disabled"
	}
	if reason := credentialReason(s.CredentialState, s.Credential.ExpiresAt, e.pool.m.now()); reason != "" {
		return reason
	}
	if credentialFingerprint(s) != sp.credentialFP {
		return "superseded"
	}
	return ""
}

// authorize runs before every send and every client construction. The
// admission policy is asked first, every time, about the server's current
// binding in the store (see admission.go); its refusal, not a superseded
// credential, is what the caller sees.
func (e *entry) authorize() error {
	cfg, known := e.pool.storeConfig()
	if refusal := e.refusalFor(cfg, known); refusal != nil {
		return refusal
	}
	if reason := e.credentialReason(); reason != "" {
		e.retire()
		return mcp.CredentialRequired(e.id, reason)
	}
	e.mu.Lock()
	retired := e.retired
	e.mu.Unlock()
	if retired {
		return mcp.CredentialRequired(e.id, "superseded")
	}
	return nil
}

// Invalidation is ordered with store writes and happens before they return.
// It does not read the store or wait for an upstream close. Dispatch also
// checks current state, including entries whose build raced this callback.
func (m *Manager) invalidateCredentials(org string, cfg *tenant.OrgConfig) {
	m.mu.Lock()
	p := m.pools[org]
	m.mu.Unlock()
	if p == nil {
		return
	}
	for _, e := range p.current() {
		e.mu.Lock()
		pending := e.pending
		e.mu.Unlock()
		if e.reasonIn(cfg) != "" {
			e.retire()
		}
		// A binding the admission policy now refuses (a changed catalog_ref or
		// URL included) ends the entry too, synchronously with the write.
		_ = e.refusalFor(cfg, true)
		if pending != nil {
			if pending.reasonIn(cfg) != "" {
				pending.retire()
			}
			_ = pending.refusalFor(cfg, true)
		}
	}
}

// Expiry does not require a follow-up push or call. The deadline check on each
// call remains authoritative if a timer is delayed by scheduling.
func (e *entry) watchExpiry() {
	expiry := e.spec().credentialExpiry
	if expiry == "" {
		return
	}
	deadline, err := time.Parse(time.RFC3339Nano, expiry)
	if err != nil {
		e.retire()
		return
	}
	timer := time.NewTimer(deadline.Sub(e.pool.m.now()))
	defer timer.Stop()
	select {
	case <-e.ctx.Done():
	case <-timer.C:
		e.retire()
	}
}

// begin binds an operation to the lifetime of the exact client handed out by
// Client. A retained handle never follows a replacement implicitly. Revocation
// cancels calls already in flight; bytes sent before it cannot be recalled.
func (t *timeoutClient) begin(ctx context.Context) (context.Context, func(), error) {
	if t.entry == nil {
		if t.grant == nil {
			return ctx, func() {}, nil
		}
		if err := t.finish(ctx, nil); err != nil {
			return nil, nil, err
		}
		call, cancel := context.WithCancel(ctx)
		stop := context.AfterFunc(t.life, cancel)
		if t.life.Err() != nil {
			cancel()
		}
		call = mcp.GuardUpstreamNotifications(call, func() error { return t.finish(call, nil) })
		return call, func() { stop(); cancel() }, nil
	}
	e := t.entry
	if err := e.authorize(); err != nil {
		return nil, nil, err
	}
	e.mu.Lock()
	current := e.client == t.UpstreamClient
	life := e.ctx
	var check func(context.Context) error
	var personal *userClient
	if t.user {
		uc := e.users[t.userKey]
		current = uc != nil && uc.client == t.UpstreamClient && !userExpired(uc.life, uc.expires, t.entry.pool.m.now())
		if current {
			life, check, personal = uc.ctx, uc.check, uc
		}
	}
	if e.retired || !current {
		e.mu.Unlock()
		return nil, nil, mcp.CredentialRequired(e.id, "superseded")
	}
	call, cancel := context.WithCancel(ctx)
	stop := context.AfterFunc(life, cancel)
	if life.Err() != nil {
		cancel()
	}
	e.mu.Unlock()
	if check != nil {
		if err := check(call); err != nil {
			stop()
			cancel()
			e.dropUser(t.userKey, personal)
			return nil, nil, err
		}
	}
	if life.Err() != nil {
		stop()
		cancel()
		return nil, nil, mcp.CredentialRequired(e.id, "superseded")
	}
	call = mcp.GuardUpstreamNotifications(call, func() error { return t.finish(call, nil) })
	return call, func() { stop(); cancel() }, nil
}

func (t *timeoutClient) finish(ctx context.Context, err error) error {
	if t.grant != nil {
		if t.life.Err() != nil {
			return mcp.CredentialRequired(t.server, "needs_reauth")
		}
		if t.grant.Check != nil {
			if refusal := t.grant.Check(ctx); refusal != nil {
				return refusal
			}
		}
		if t.life.Err() != nil {
			return mcp.CredentialRequired(t.server, "needs_reauth")
		}
	}
	if t.entry == nil {
		return err
	}
	if refusal := t.entry.authorize(); refusal != nil {
		return refusal
	}
	e := t.entry
	e.mu.Lock()
	current := !e.retired && e.client == t.UpstreamClient
	var personal *userClient
	if t.user {
		personal = e.users[t.userKey]
		current = !e.retired && personal != nil && personal.client == t.UpstreamClient && !userExpired(personal.life, personal.expires, e.pool.m.now())
	}
	e.mu.Unlock()
	if !current {
		return mcp.CredentialRequired(e.id, "superseded")
	}
	// Redis is outside the entry lock. Recheck the same local identity after
	// the distributed fence so replacement cannot make an old result current.
	if personal != nil && personal.check != nil {
		if refusal := personal.check(ctx); refusal != nil {
			return refusal
		}
	}
	if checked, ok := t.UpstreamClient.(interface{ CheckCredential(context.Context) error }); ok {
		if refusal := checked.CheckCredential(ctx); refusal != nil {
			return refusal
		}
	}
	e.mu.Lock()
	current = !e.retired && e.client == t.UpstreamClient
	if t.user {
		current = !e.retired && e.users[t.userKey] == personal && !userExpired(personal.life, personal.expires, e.pool.m.now())
	}
	e.mu.Unlock()
	if !current {
		return mcp.CredentialRequired(e.id, "superseded")
	}
	return err
}
