package batch

import (
	"context"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// KeyHeader is retired and is never accepted as a batch credential.
const KeyHeader = "x-agentcc-batch-key"
const ErrCodeBatchKeyRequired = "service_envelope_rejected"

var forwardedHeaders = []string{"X-Forwarded-For", "X-Real-IP"}

// Caller stores only non-secret audit IDs publicly. The principal and grant
// live on its unexported context, never on Batch or in JSON or log metadata.
type Caller struct {
	KeyID      string
	OrgID      string
	principal  context.Context
	remoteAddr string
	forwarded  http.Header
}
type callerKey struct{}

func withCaller(ctx context.Context, c Caller) context.Context {
	if c.principal != nil {
		ctx = auth.WithDelegationFrom(ctx, c.principal)
	}
	return context.WithValue(ctx, callerKey{}, c)
}
func callerFrom(ctx context.Context) (Caller, bool) {
	c, ok := ctx.Value(callerKey{}).(Caller)
	return c, ok
}

type KeyCheck struct{ keys *auth.KeyStore }
type Option func(*Processor)

// WithKeyStore supplies the membership/revocation snapshot for item checks.
// The legacy required argument is retained for source compatibility only:
// HTTP submits ALWAYS require a delegated principal, even with API auth off.
func WithKeyStore(keys *auth.KeyStore, _ bool) Option {
	return func(p *Processor) { p.keyCheck = &KeyCheck{keys: keys} }
}

func (p *Processor) CallerFor(r *http.Request) (Caller, *models.APIError) {
	key := auth.DelegatedPrincipal(r.Context())
	_, claims, ok := auth.DelegatedGrant(r.Context())
	if key == nil || !ok || p.keyCheck == nil || !p.keyCheck.keys.DelegatedEligible(claims, time.Now()) {
		err := models.ErrUnauthorized("service_envelope_rejected")
		err.Code = "service_envelope_rejected"
		return Caller{}, err
	}
	c := Caller{KeyID: key.ID, OrgID: claims.Org, principal: auth.WithDelegationFrom(context.Background(), r.Context()), remoteAddr: r.RemoteAddr}
	for _, h := range forwardedHeaders {
		if v := r.Header.Values(h); len(v) > 0 {
			if c.forwarded == nil {
				c.forwarded = make(http.Header)
			}
			c.forwarded[h] = append([]string(nil), v...)
		}
	}
	return c, nil
}
func (p *Processor) callerRejection(ctx context.Context) auth.DelegationRejection {
	_, claims, delegated := auth.DelegatedGrant(ctx)
	// Submit/SubmitWithTraceID remain an internal API for non-HTTP jobs.
	if !delegated {
		return ""
	}
	if p.keyCheck == nil {
		return auth.DelegationUnavailable
	}
	return p.keyCheck.keys.DelegationRejection(claims, time.Now())
}
