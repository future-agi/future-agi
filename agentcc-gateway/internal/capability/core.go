package capability

import (
	"context"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Options configure a Core. Zero values take the defaults NewCore states.
type Options struct {
	Registry    *Registry   // required
	Cards       CardSource  // nil: EmptyCards
	Credentials Credentials // nil: no BYOK and no managed credential
	// Ledger is the managed-credit store (OrgLedger.Ledger()); nil fails
	// every managed call closed with 503 managed_billing_unavailable.
	Ledger billing.Ledger
	// MarkupBps is managed.tool_markup_percent in basis points
	// (ManagedConfig.ToolMarkupBps).
	MarkupBps int
	// ProcessingFeeBps is managed.processing_fee_bps, for landed cost.
	ProcessingFeeBps int
	// HoldTTL is how long a managed hold lives (managed.hold_ttl).
	HoldTTL time.Duration
	// AccountMissingRetryAfter is the 503 account_state_missing's
	// Retry-After.
	AccountMissingRetryAfter time.Duration
	// ReserveDefaultUSD is held for a managed call whose SKU has no list
	// price (tools.reserve_default_usd).
	ReserveDefaultUSD float64
	// CacheTTL is tools.cache_ttl_seconds; 0 caches nothing.
	CacheTTL time.Duration
	// Timeouts is capabilities.<cap>.timeout_ms (CapabilitiesConfig.Timeout);
	// nil: config.DefaultCapabilityTimeout.
	Timeouts func(capability string) time.Duration
	// Budget is the late-bound per-run limit; nil: NoBudget.
	Budget Budget
	// Now is the clock; nil: time.Now.
	Now func() time.Time
}

// Core is the capability core.
type Core struct {
	opts    Options
	reg     *Registry
	cards   CardSource
	creds   Credentials
	health  *healthBook
	cache   *resultCache
	accrued *accrual
}

// NewCore returns a core over opts.
func NewCore(opts Options) *Core {
	if opts.Registry == nil {
		opts.Registry = NewRegistry()
	}
	if opts.Cards == nil {
		opts.Cards = EmptyCards{}
	}
	if opts.Credentials == nil {
		opts.Credentials = ConfigCredentials{}
	}
	if opts.Budget == nil {
		opts.Budget = NoBudget{}
	}
	if opts.Now == nil {
		opts.Now = time.Now
	}
	if opts.HoldTTL <= 0 {
		opts.HoldTTL = 15 * time.Minute
	}
	return &Core{opts: opts, reg: opts.Registry, cards: opts.Cards, creds: opts.Credentials,
		health: newHealthBook(opts.Now), cache: newResultCache(opts.CacheTTL, opts.Now), accrued: newAccrual(opts.Now)}
}

// Registry is the core's adapter registry.
func (c *Core) Registry() *Registry { return c.reg }

// Serves reports whether capability is registered here.
func (c *Core) Serves(capability string) bool {
	_, ok := c.reg.Spec(capability)
	return ok
}

// EndpointType is the request endpoint type a capability is logged under.
func (c *Core) EndpointType(capability string) string {
	spec, _ := c.reg.Spec(capability)
	return spec.EndpointType
}

// Timeout bounds one call of capability.
func (c *Core) Timeout(capability string) time.Duration {
	if c.opts.Timeouts != nil {
		if d := c.opts.Timeouts(capability); d > 0 {
			return d
		}
	}
	return config.DefaultCapabilityTimeout
}

var installed atomic.Pointer[Core]

// Install makes core the one Do and WorstCaseMicros use; nil uninstalls.
// The wiring installs it once at startup.
func Install(core *Core) { installed.Store(core) }

// Installed is the installed core, or nil.
func Installed() *Core { return installed.Load() }

// Do runs a capability call on the installed core: the only path a vendor
// adapter is called through, from REST handlers and MCP built-ins alike.
func Do(ctx context.Context, rc *models.RequestContext, capability string, req Request) (Result, error) {
	c := Installed()
	if c == nil {
		return Result{}, errNotConfigured(capability)
	}
	return c.Do(ctx, rc, capability, req)
}

// orgOf is the org a request bills to.
func orgOf(rc *models.RequestContext) string {
	if id := rc.Metadata["org_id"]; id != "" {
		return id
	}
	return rc.Metadata["key_org_id"]
}

// Do runs one capability call: select the candidates, admit the call's worst
// case, serve a per-org cache hit, then try each candidate in order, holding
// a managed one first and falling back only as the capability's class and
// the selector's rules allow. Every attempt, BYOK or managed, refused,
// failed or served, is recorded on rc's call record.
//
// Do is not safe for concurrent use on one request context: it writes the
// request's provider, model and metadata. A caller that fans capability
// calls out runs each on its own request context, or one after another.
// Attempt indices (and the holds keyed by them) are allocated under the call
// record's lock all the same, so they never repeat on one request.
func (c *Core) Do(ctx context.Context, rc *models.RequestContext, capability string, req Request) (Result, error) {
	spec, ok := c.reg.Spec(capability)
	if !ok {
		return Result{}, errNotConfigured(capability)
	}
	call := attachCall(rc, capability, req)
	// Named before any refusal, so the request log records every call
	// (it skips a request with no model); a tried vendor refines it.
	rc.Model, rc.ResolvedModel = capability, capability
	cands, why, apiErr := c.selectCandidates(rc, capability, req)
	if apiErr != nil {
		return Result{}, apiErr
	}
	if len(cands) == 0 {
		return Result{}, noCandidate(capability, why)
	}
	if apiErr := c.opts.Budget.Admit(ctx, rc, capability, c.worstOf(cands, req)); apiErr != nil {
		return Result{}, apiErr
	}
	org := orgOf(rc)
	if res, ok := c.fromCache(rc, call, capability, cands[0], req, org); ok {
		return res, nil
	}

	var tried []models.Attempt
	var prev error
	for i, cand := range cands {
		a := newAttempt(rc, call.nextIndex(rc), i, prev, capability, cand, c.opts.Now())
		rc.Provider, rc.Model, rc.ResolvedModel = cand.vendor, a.Model, a.Model
		holdMicros := int64(0)
		if cand.managed() {
			// Every managed attempt holds before its vendor is called, even
			// at a worst case of 0 micros: the hold is what fails managed
			// billing closed (no ledger, no account, no credit left), and
			// whatever the vendor then bills settles against it.
			holdMicros = c.worstCase(cand, req)
			if refused := c.hold(ctx, org, a.AttemptID, holdMicros); refused != nil {
				refusedAttempt(&a, refused)
				call.addAttempt(a)
				call.tried(cand.card, callBilling{mode: BillingManaged, credentialType: models.CredentialManaged})
				return Result{}, refused
			}
		}
		if cand.card.NoPerfDisclosure {
			markNoPerfDisclosure(rc)
		}
		finished := c.voidOnExit(rc, org, a, cand.managed(), holdMicros)
		started := c.opts.Now()
		resp, err := cand.adapter.Call(ctx, VendorCall{Capability: capability, Vendor: cand.vendor, Card: cand.card,
			SKU: cand.sku, Credential: cand.cred, Request: req})
		latency := c.opts.Now().Sub(started)
		finished()
		if err == nil {
			c.health.success(capability, cand.vendor, latency)
			return c.served(rc, call, capability, cand, req, &a, resp, holdMicros, latency, org), nil
		}
		if transient(err) {
			c.health.failure(capability, cand.vendor) // an input's 4xx says nothing of the vendor
		}
		c.failed(ctx, rc, call, cand, req, &a, err, holdMicros, latency, org)
		tried = append(tried, a)
		prev = err
		if cand.managed() || cand.card.NoDivert || !spec.Class.fallsBack(ctx, err) || i == len(cands)-1 {
			return Result{}, c.failure(capability, err, tried)
		}
	}
	return Result{}, providerUnavailable(capability, tried)
}

// voidOnExit registers the request finalizer that voids an attempt's hold if
// the call never finishes it (an adapter panic): every hold is settled or
// voided on every exit path. The returned func marks the call
// finished, after which served or failed settles or voids the hold itself.
func (c *Core) voidOnExit(rc *models.RequestContext, org string, a models.Attempt, managed bool, holdMicros int64) (finished func()) {
	if !managed {
		return func() {}
	}
	var done atomic.Bool
	rc.AddFinalizer(func() {
		if !done.Load() {
			a.Outcome, a.ErrorOrigin = models.OutcomeCancelled, models.OriginGateway
			c.void(org, a.AttemptID, settlementOf(rc, org, &a, holdMicros, c.opts.MarkupBps))
		}
	})
	return func() { done.Store(true) }
}

// failure is the error a failed call returns: the vendor's own 4xx (the
// input's fault) as a provider error, anything else as 503
// capability_provider_unavailable with the attempts.
func (c *Core) failure(capability string, err error, tried []models.Attempt) *models.APIError {
	if ve, ok := vendorAnswered(err); ok && ve.Status >= 400 && ve.Status < 500 && ve.Status != 429 {
		return models.ErrUpstreamProvider(ve.Status, tried[len(tried)-1].ErrorMessage)
	}
	return providerUnavailable(capability, tried)
}

// nextIndex allocates the request's next attempt index (1-based): after
// every attempt already on the request, recorded on this call but not yet
// published, or handed out to an attempt still in flight. Two Do calls
// running at once on one request therefore never share an attempt id, nor
// the hold keyed by it.
func (c *Call) nextIndex(rc *models.RequestContext) int {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.next = max(c.next, rc.AttemptCount()+len(c.attempts)-c.published) + 1
	return c.next
}
