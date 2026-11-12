package budget

import (
	"context"
	"net/http"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Native authority and qualification have no production installer. Scalars on
// rc (including Runtime) only establish that a barrier is REQUIRED, never that
// it passed. Owner-verified identity/revocation and native price/metering inputs
// must be integrated before BindNativeAdmission can issue a positive control.
type nativeControl struct {
	mu                            sync.Mutex
	rc                            *models.RequestContext
	admission                     *Admission
	protocol                      models.NativeProtocol
	authority                     func() bool
	qualify                       func(*models.RequestContext, models.NativeRouteBinding, []byte) (nativeBound, bool)
	scope                         string
	runRequired                   bool
	view                          models.NativeBody
	binding                       models.NativeRouteBinding
	quote                         *nativeQuoteState
	prepared, claimed, spendReady bool
	attempt                       *nativeAttemptState
	billing, closing, failed      bool
}

type nativeQuoteState struct {
	owner     *nativeControl
	body, pin []byte
	bound     nativeBound
	// Clone all internal-only fields too; JSON omits price qualifications and
	// routing restrictions and therefore cannot be their sole mutation guard.
	agent     models.AgentContext
	runtime   *models.RuntimeIdentity
	routing   models.RoutingOptions
	price     *models.PriceCard
	endpoints []models.EndpointRef
}

type nativeAttemptState struct {
	owner                   *nativeControl
	ctx                     context.Context
	financialID             string
	finish                  func(*models.Usage, models.GenerationEvidence, *models.APIError)
	usage                   *models.Usage
	generation              models.GenerationEvidence
	last                    NativeUsageEvent
	sequence                uint64
	terminal, ending, ended bool
	end                     NativeEnd
}

var nativeRequests sync.Map // request-scoped only; every binding registers removal

func nativeFor(rc *models.RequestContext) *nativeControl {
	v, _ := nativeRequests.Load(rc)
	c, _ := v.(*nativeControl)
	return c
}

// NativeControlled reports only package-bound state, not caller assertions.
func NativeControlled(rc *models.RequestContext) bool { return rc != nil && nativeFor(rc) != nil }

// NativeCapsRequired is a refusal/consumption gate, not an authority accessor.
// An unbound caller-only cap must not reach a nil-chat priced-zero estimator.
func NativeCapsRequired(rc *models.RequestContext) bool {
	if rc == nil {
		return false
	}
	if NativeControlled(rc) {
		return true
	}
	if rc.Request != nil {
		return false
	}
	switch rc.EndpointType {
	case "anthropic_messages", "responses", "genai_generate", "genai_stream":
	default:
		return false
	}
	a := rc.Agent
	return RequestAdmission(rc) != nil || a.MaxCostUSD != nil || a.BudgetUSD != nil || a.BudgetRemainingUSD != nil || a.RunID != "" || a.BudgetScope != "" || rc.Runtime != nil || rc.Routing.ComputerUse.Required
}

func BindNativeAdmission(r *http.Request, rc *models.RequestContext, cfg *tenant.OrgConfig, protocol models.NativeProtocol) *models.APIError {
	if r == nil || rc == nil || rc.Request != nil || protocol < models.NativeMessages || protocol > models.NativeGenAI {
		return capStateUnavailable()
	}
	// The caller-only check also applies before a handler has set EndpointType.
	caller := rc.Agent.MaxCostUSD != nil || rc.Agent.BudgetUSD != nil || rc.Agent.BudgetRemainingUSD != nil || rc.Agent.RunID != "" || rc.Agent.BudgetScope != "" || rc.Runtime != nil
	policyRequired := cfg.ManagedPolicy().MaxCallCostUSD != nil || capAgents(cfg).MaxRunUSD != nil
	if !CapsEnabled(cfg) && !caller && !policyRequired && !NativeCapsRequired(rc) {
		return nil
	}
	if a, checked := AdmissionFromContext(r.Context()); checked && a != nil {
		if rc.Metadata["org_id"] != a.org || (rc.Metadata["auth_key_id"] != "" && rc.Metadata["auth_key_id"] != a.key) {
			return models.ErrForbidden("native admission identity mismatch")
		}
	}
	// No accepted identity-owner native accessor exists at this boundary. Do
	// not manufacture a caller-only Admission or treat a nil checked chat
	// admission as a verified principal/workspace/run bridge.
	return capStateUnavailable()
}

// bindNativeControl is private lifecycle plumbing, not a trust constructor.
// Its only positive caller until owner integration is a package-local fixture.
func bindNativeControl(rc *models.RequestContext, c *nativeControl) {
	c.rc = rc
	if _, loaded := nativeRequests.LoadOrStore(rc, c); loaded {
		return
	}
	rc.AddFinalizer(func() {
		c.mu.Lock()
		c.closing = true
		a := c.attempt
		c.mu.Unlock()
		if a != nil {
			_ = EndNative(a, NativeAborted)
		}
		c.mu.Lock()
		defer c.mu.Unlock()
		nativeRequests.Delete(rc)
		c.rc, c.view, c.authority, c.qualify = nil, nil, nil, nil
		if c.quote != nil {
			c.quote.body, c.quote.pin = nil, nil
			c.quote.bound = nativeBound{}
		}
		if a != nil {
			a.ctx, a.finish, a.usage = nil, nil, nil
			a.last = NativeUsageEvent{}
			a.financialID = ""
		}
	})
}

func AttachNative(rc *models.RequestContext, view models.NativeBody, binding models.NativeRouteBinding) *models.APIError {
	c := nativeFor(rc)
	if c == nil || view == nil {
		return capStateUnavailable()
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.view != nil || c.quote != nil || c.closing || rc.Request != nil || !nativeBindingValid(binding, c.protocol) {
		return capStateUnavailable()
	}
	c.view, c.binding = view, binding
	return nil
}

func PrepareNativeDispatch(ctx context.Context, rc *models.RequestContext, view models.NativeBody, binding models.NativeRouteBinding) *models.APIError {
	c := nativeFor(rc)
	if c == nil || ctx == nil {
		return capStateUnavailable()
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	deadline, ok := ctx.Deadline()
	if !ok || ctx.Err() != nil || deadline.After(c.admission.deadline) || c.prepared || c.claimed || binding != c.binding || !nativeSameView(view, c.view) || !nativeQuoteMatches(c) {
		return capStateUnavailable()
	}
	c.prepared = true
	return nil
}

func ClaimPreparedNative(rc *models.RequestContext, providerID, model string) *models.APIError {
	c := nativeFor(rc)
	if c == nil {
		return capStateUnavailable()
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.prepared || c.claimed || !nativeQuoteMatches(c) || providerID != c.binding.ProviderID || model != c.binding.SentModel {
		return capStateUnavailable()
	}
	a := c.admission
	if !a.qualified.Load() || ((c.runRequired || c.scope != "" || a.runLimit >= 0) && !a.runReady.Load()) {
		return capStateUnavailable()
	}
	if c.binding.CredentialType == models.CredentialManaged {
		q := c.quote.bound.managedMicros
		if !c.spendReady || !rc.Billing.Reserved || !rc.Billing.OwnedRequest || rc.Billing.OwnedAttemptID == "" || rc.Billing.AttemptHoldMicros != q || rc.Billing.HoldMicros != q {
			return capStateUnavailable()
		}
	}
	if !a.ClaimAttempt() {
		return CapUnenforceable("native admission permits one dispatch only")
	}
	c.claimed = true
	return nil
}

func MarkNativeSpendGuardReady(rc *models.RequestContext, managedMicros int64) *models.APIError {
	c := nativeFor(rc)
	if c == nil {
		return capStateUnavailable()
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if !nativeQuoteMatches(c) || c.claimed || c.spendReady || c.binding.CredentialType != models.CredentialManaged || managedMicros != c.quote.bound.managedMicros {
		return capStateUnavailable()
	}
	c.spendReady = true
	return nil
}

func BindNativeAttempt(ctx context.Context, rc *models.RequestContext, financialID string, finish func(*models.Usage, models.GenerationEvidence, *models.APIError)) (NativeAttempt, *models.APIError) {
	c := nativeFor(rc)
	if c == nil || ctx == nil || finish == nil || financialID == "" {
		return nil, capStateUnavailable()
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	deadline, bounded := ctx.Deadline()
	if !bounded || deadline.After(c.admission.deadline) || ctx.Err() != nil || !c.claimed || !c.admission.attempted.Load() || c.attempt != nil || c.closing || c.failed {
		return nil, capStateUnavailable()
	}
	attempts := rc.AttemptsSnapshot()
	if len(attempts) != 1 {
		c.failed = true
		return nil, capStateUnavailable()
	}
	a := attempts[0]
	if a.FinancialID != financialID || !a.InFlight || a.Provider != c.binding.ProviderID || a.Model != c.binding.SentModel || a.CredentialType != c.binding.CredentialType {
		c.failed = true
		return nil, capStateUnavailable()
	}
	h := &nativeAttemptState{owner: c, ctx: ctx, financialID: financialID, finish: finish}
	c.attempt = h
	return h, nil
}

func (*nativeAttemptState) nativeAttempt() {}
func (a *nativeAttemptState) Context() context.Context {
	if a == nil || a.owner == nil {
		return nil
	}
	c := a.owner
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.rc == nil || c.attempt != a || c.closing {
		return nil
	}
	return a.ctx
}
func (a *nativeAttemptState) Body() []byte {
	if a == nil || a.owner == nil {
		return nil
	}
	c := a.owner
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.rc == nil || c.attempt != a || c.closing || a.ended || c.quote == nil {
		return nil
	}
	return append([]byte(nil), c.quote.body...)
}
func (a *nativeAttemptState) FinancialID() string {
	if a == nil || a.owner == nil {
		return ""
	}
	c := a.owner
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.rc == nil || c.attempt != a || c.closing {
		return ""
	}
	return a.financialID
}

// NativeRunScope returns only the accepted private scope; never rc.Agent's
// claimed scope. A required run with no accepted scope remains unavailable.
func NativeRunScope(rc *models.RequestContext) (string, bool) {
	c := nativeFor(rc)
	if c == nil {
		return "", false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.rc == nil || c.closing || c.authority == nil || !c.authority() || ((c.runRequired || c.admission.runLimit >= 0) && c.scope == "") {
		return "", false
	}
	return c.scope, true
}

func NativeBillingComplete(rc *models.RequestContext) bool {
	c := nativeFor(rc)
	if c == nil {
		return false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.billing && !c.closing
}

func nativeDay(t time.Time) string { return t.UTC().Format("2006-01-02") }
