package tenancy

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

func userExpired(life context.Context, expires, now time.Time) bool {
	return (life != nil && life.Err() != nil) || (!expires.IsZero() && !now.Before(expires))
}

// userClientFor singleflights one credential's construction without holding the
// entry lock across upstream I/O. Replacement withdraws the old handle first;
// a late completion can neither replace a newer client nor outlive its grant.
func (e *entry) userClientFor(key string, auth mcp.AuthOverride) (mcp.UpstreamClient, error) {
	// Empty overrides are not credentials, including maps with empty values.
	// Refuse before looking up a cached client or invoking the factory.
	present := false
	for _, values := range auth.Header {
		for _, value := range values {
			present = present || strings.TrimSpace(value) != ""
		}
	}
	if !present {
		return nil, mcp.CredentialRequired(e.id, "no_token")
	}
	if auth.Check != nil {
		if err := auth.Check(e.ctx); err != nil {
			return nil, err
		}
	}
	fp := fingerprint([]any{auth.Header, auth.Version})
	if err := e.authorize(); err != nil {
		return nil, err
	}
	e.mu.Lock()
	if e.retired || userExpired(auth.Lifetime, auth.ExpiresAt, e.pool.m.now()) {
		e.mu.Unlock()
		return nil, mcp.CredentialRequired(e.id, "needs_reauth")
	}
	if uc := e.users[key]; uc != nil && uc.authFP == fp && uc.life == auth.Lifetime && !userExpired(uc.life, uc.expires, e.pool.m.now()) && uc.client.Healthy() {
		c := uc.client
		e.mu.Unlock()
		return c, nil
	}
	if build := e.userBuilds[key]; build != nil && build.fp == fp && build.life == auth.Lifetime {
		e.mu.Unlock()
		select {
		case <-e.ctx.Done():
			// A transport policy refusal retires the entry before the builder
			// can publish its error. Preserve that current Coming soon denial.
			if refusal := e.authorize(); refusal != nil {
				return nil, refusal
			}
			return nil, mcp.CredentialRequired(e.id, "superseded")
		case <-build.done:
		}
		if refusal := e.authorize(); refusal != nil {
			return nil, refusal
		}
		return build.client, build.err
	}
	old := e.users[key]
	delete(e.users, key)
	if old != nil {
		old.cancel()
	}
	// Bind the grant before calling a factory: initialization and discovery are
	// authenticated operations, not merely preparation for publication.
	ctx, cancel := context.WithCancel(e.ctx)
	if auth.Lifetime != nil {
		stop := context.AfterFunc(auth.Lifetime, cancel)
		baseCancel := cancel
		cancel = func() { stop(); baseCancel() }
		if auth.Lifetime.Err() != nil {
			cancel()
		}
	}
	if previous := e.userBuilds[key]; previous != nil {
		previous.cancel()
	}
	build := &userBuild{fp: fp, life: auth.Lifetime, done: make(chan struct{}), cancel: cancel}
	e.userBuilds[key] = build
	sp := e.spec()
	e.mu.Unlock()
	if old != nil {
		_ = old.client.Close()
	}
	buildAuth := auth
	buildAuth.Lifetime = ctx
	c, err := e.pool.m.build(ctx, e.pool.org, sp, &buildAuth, e.authorize)
	// Cancellation caused by a transport refusal must not replace its typed
	// Coming soon denial with a generic credential/lifetime error.
	if refusal := e.authorize(); refusal != nil {
		err = refusal
	}
	if err == nil && auth.Check != nil {
		err = auth.Check(ctx)
	}
	e.mu.Lock()
	if err == nil && (e.retired || e.userBuilds[key] != build || userExpired(auth.Lifetime, auth.ExpiresAt, e.pool.m.now())) {
		err = mcp.CredentialRequired(e.id, "superseded")
	}
	if e.userBuilds[key] == build {
		delete(e.userBuilds, key)
	}
	if err != nil {
		cancel()
		build.err = mcp.CredentialRequired(e.id, "needs_reauth")
		var refusal *mcp.Denial
		if errors.As(err, &refusal) {
			build.err = refusal
		}
		close(build.done)
		e.mu.Unlock()
		if c != nil {
			_ = c.Close()
		}
		return nil, build.err
	}
	uc := &userClient{client: c, authFP: fp, ctx: ctx, cancel: cancel, life: auth.Lifetime, expires: auth.ExpiresAt, check: auth.Check}
	e.users[key] = uc
	// The callback closes idle clients as well as cancelling active operations.
	// A replaced client's callback names its exact identity, never its successor.
	if auth.Lifetime != nil {
		stop := context.AfterFunc(auth.Lifetime, func() { e.dropUser(key, uc) })
		uc.cancel = func() { stop(); cancel() }
	}
	build.client = c
	close(build.done)
	e.mu.Unlock()
	return c, nil
}

func (e *entry) dropUser(key string, expected *userClient) {
	e.mu.Lock()
	if e.users[key] != expected {
		e.mu.Unlock()
		return
	}
	delete(e.users, key)
	expected.cancel()
	e.pool.m.closing.add()
	e.mu.Unlock()
	defer e.pool.m.closing.done()
	_ = expected.client.Close()
}
