package batch

import (
	"context"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// KeyHeader names, on POST /-/batches, the API key the batch's items run as:
// each item is a chat completions request authenticated by that key, so it
// runs as the key's org (its providers, guardrails, budgets, credits and
// request log). The admin token also authorizes the route itself.
const KeyHeader = "x-agentcc-batch-key"

// ErrCodeBatchKeyRequired is the 400 code of a submit that names no batch key
// on a gateway with auth enabled.
const ErrCodeBatchKeyRequired = "batch_key_required"

// forwardedHeaders are the client-address headers an item copies from the
// submit request, so every IP rule (the org's ip_acl, the key's allowed_ips)
// sees an item come from where its batch came from.
var forwardedHeaders = []string{"X-Forwarded-For", "X-Real-IP"}

// Caller is who a batch runs as: the batch key's id and org, and where the
// submit came from. The raw key is unexported and lives only on the batch's
// context (never on the Batch, so it is never serialized or returned).
type Caller struct {
	KeyID string
	OrgID string

	key        string
	remoteAddr string
	forwarded  http.Header
}

type callerKey struct{}

func withCaller(ctx context.Context, c Caller) context.Context {
	return context.WithValue(ctx, callerKey{}, c)
}

func callerFrom(ctx context.Context) (Caller, bool) {
	c, ok := ctx.Value(callerKey{}).(Caller)
	return c, ok
}

// KeyCheck settles the batch key a submit names. Built with WithKeyStore.
type KeyCheck struct {
	keys     *auth.KeyStore
	required bool
}

// Option configures a Processor.
type Option func(*Processor)

// WithKeyStore makes the processor check each submit's batch key against
// keys. With required (the gateway's auth enabled) a submit must name one.
// Without required, a submit may name none (the batch then runs with no
// org); one it names is still checked, and scopes the batch to the key's org.
func WithKeyStore(keys *auth.KeyStore, required bool) Option {
	return func(p *Processor) {
		p.keyCheck = &KeyCheck{keys: keys, required: required}
	}
}

// CallerFor returns who a batch submitted by r runs as, or the error to
// answer: 400 batch_key_required when a key is required and r names none,
// 401 invalid_api_key when the key it names is unknown, revoked or expired.
// The key may be sent bare or as "Bearer <key>".
func (p *Processor) CallerFor(r *http.Request) (Caller, *models.APIError) {
	c := Caller{remoteAddr: r.RemoteAddr}
	for _, h := range forwardedHeaders {
		if v := r.Header.Values(h); len(v) > 0 {
			if c.forwarded == nil {
				c.forwarded = make(http.Header, len(forwardedHeaders))
			}
			c.forwarded[h] = append([]string(nil), v...)
		}
	}

	raw := strings.TrimSpace(r.Header.Get(KeyHeader))
	raw = strings.TrimSpace(strings.TrimPrefix(raw, "Bearer "))
	check := p.keyCheck
	if raw == "" {
		if check != nil && check.required {
			return Caller{}, models.ErrBadRequest(ErrCodeBatchKeyRequired,
				"Name the API key the batch's items run as in the "+KeyHeader+" header: with auth enabled, every item is a request authenticated by that key")
		}
		return c, nil
	}
	if check == nil || check.keys == nil {
		return Caller{}, models.ErrUnauthorized("This gateway holds no API keys to check the batch key against")
	}
	// Authenticate answers only a known key that is active and unexpired,
	// read under the store's lock.
	key := check.keys.Authenticate(raw)
	if key == nil {
		return Caller{}, models.ErrUnauthorized("The batch key is unknown, revoked or expired")
	}
	id := check.keys.Identity(key)
	c.KeyID, c.OrgID, c.key = id.KeyID, id.OrgID, raw
	return c, nil
}
