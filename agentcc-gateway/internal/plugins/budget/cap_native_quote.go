package budget

import (
	"bytes"
	"context"
	"encoding/json"
	"math"
	"reflect"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

const nativeMaxMicros int64 = 1_000_000_000_000_000

// nativeBound consumes an owner-qualified conservative bound, not an estimator.
// No production qualifier is installed: native enforced ceilings, complete price
// dimensions and terminal decoder contracts are external closed dependencies.
// The private callback validates all priced dimensions and returns a detached
// cumulative reading. Delta support requires its own qualified normalizer.
type nativeBound struct {
	capMicros, managedMicros int64
	free                     bool
	revision                 string
	kind                     NativeUsageKind
	current                  func() bool // owner snapshot/revocation check, no positive default
	meter                    func(*models.Usage, NativeUsageEvent) (*models.Usage, bool)
}

// SetNativePolicyPreparer is startup-only injection. It does not enable native
// admission or install identity, prices, schema or decoder qualifications.
func (p *Plugin) SetNativePolicyPreparer(preparer NativePolicyPreparer) { p.nativePolicy = preparer }

func (p *Plugin) quoteNative(ctx context.Context, rc *models.RequestContext) *models.APIError {
	c := nativeFor(rc)
	if c == nil || p.nativePolicy == nil || ctx == nil || ctx.Err() != nil {
		return capStateUnavailable()
	}
	c.mu.Lock()
	if c.quote != nil || c.view == nil || c.closing || c.authority == nil || !c.authority() {
		c.mu.Unlock()
		return capStateUnavailable()
	}
	c.mu.Unlock()
	// The policy may mutate the view. Nothing is sealed or reserved until it
	// returns; priority 60 must subsequently verify its idempotent snapshot.
	if err := p.nativePolicy.PrepareNative(ctx, rc); err != nil {
		return err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.qualify == nil || c.authority == nil || !c.authority() {
		return capStateUnavailable()
	}
	if rc.Request != nil || !nativeBindingValid(c.binding, c.protocol) {
		return CapUnenforceable("native request cannot be bounded")
	}
	body := c.view.Body()
	if len(body) == 0 {
		return CapUnenforceable("native request cannot be bounded")
	}
	bound, ok := c.qualify(rc, c.binding, append([]byte(nil), body...))
	if !ok || bound.revision == "" || bound.meter == nil || bound.current == nil || !bound.current() || (bound.kind != NativeCumulativeUsage && bound.kind != NativeDeltaUsage) {
		return CapUnenforceable("native bound qualification unavailable")
	}
	if bound.capMicros < 0 || bound.capMicros > nativeMaxMicros || bound.managedMicros < 0 || bound.managedMicros > nativeMaxMicros || (bound.capMicros == 0 && !bound.free) || (bound.free && (bound.capMicros != 0 || bound.managedMicros != 0)) {
		return CapUnenforceable("native bound is outside the qualified range")
	}
	managed := c.binding.CredentialType == models.CredentialManaged
	if (managed && bound.managedMicros != bound.capMicros) || (!managed && bound.managedMicros != 0) {
		return CapUnenforceable("native credential and bound mismatch")
	}
	if !nativeCallCapFits(rc, bound.capMicros) {
		return CapUnenforceable("native bound exceeds the call ceiling")
	}
	pin, err := nativeSnapshot(c)
	if err != nil {
		return capStateUnavailable()
	}
	c.quote = &nativeQuoteState{owner: c, body: append([]byte(nil), body...), pin: pin, bound: bound, agent: rc.Agent.Clone(), runtime: rc.Runtime.Clone(), routing: rc.Routing.Clone()}
	if rc.Billing.ReservePrice != nil {
		card := rc.Billing.ReservePrice.Clone()
		c.quote.price = &card
	}
	if rc.Billing.EndpointSet != nil {
		c.quote.endpoints = append([]models.EndpointRef{}, rc.Billing.EndpointSet...)
		for i := range c.quote.endpoints {
			c.quote.endpoints[i].Price = c.quote.endpoints[i].Price.Clone()
		}
	}
	if !nativeQuoteMatches(c) {
		c.quote = nil
		return capStateUnavailable()
	}
	a := c.admission
	a.quoteMicros, a.quoteReady, a.managed = bound.capMicros, true, managed
	a.provider, a.model, a.endpoint = c.binding.ProviderID, c.binding.SentModel, c.binding.EndpointID
	return nil
}

func nativeCallCapFits(rc *models.RequestContext, amount int64) bool {
	for _, ceiling := range []*float64{rc.Agent.MaxCostUSD, rc.Agent.BudgetUSD, rc.Agent.BudgetRemainingUSD, rc.Billing.CallCapUSD} {
		if ceiling == nil {
			continue
		}
		v := *ceiling
		if math.IsNaN(v) || math.IsInf(v, 0) || v < 0 || v > float64(nativeMaxMicros)/1e6 || float64(amount) > math.Floor(v*1e6) {
			return false
		}
	}
	return true
}

func nativeBindingValid(b models.NativeRouteBinding, protocol models.NativeProtocol) bool {
	if b.Protocol != protocol || b.CanonicalModel == "" || b.SentModel == "" || b.ModelRevision == "" || b.ProviderID == "" || b.ProviderRevision == "" || b.ProviderRouteID == "" || b.RouteRevision == "" || b.EndpointID == "" || b.UpstreamProvider == "" || b.CapabilityRevision == "" {
		return false
	}
	if b.CredentialType != models.CredentialManaged && b.CredentialType != models.CredentialBYOK {
		return false
	}
	switch protocol {
	case models.NativeMessages:
		return b.EndpointType == "anthropic_messages" && b.Action == "messages"
	case models.NativeResponses:
		return b.EndpointType == "responses" && b.Action == "create"
	case models.NativeGenAI:
		return (b.EndpointType == "genai_generate" && b.Action == "generateContent" && !b.Stream) || (b.EndpointType == "genai_stream" && b.Action == "streamGenerateContent" && b.Stream)
	}
	return false
}

func nativeSameView(a, b models.NativeBody) bool {
	if a == nil || b == nil {
		return false
	}
	av, bv := reflect.ValueOf(a), reflect.ValueOf(b)
	return av.Kind() == reflect.Pointer && bv.Kind() == reflect.Pointer && !av.IsNil() && !bv.IsNil() && av.Type() == bv.Type() && av.Pointer() == bv.Pointer()
}

// All snapshots are private memory, never a fingerprint, log or metadata value.
// Authority validation is separate: a snapshot of caller fields is not proof.
func nativeSnapshot(c *nativeControl) ([]byte, error) {
	rc, a := c.rc, c.admission
	return json.Marshal(struct {
		RequestID, Org, Key, Mode, Nonce, Day, AgentActivation, KeyActivation string
		Revision                                                              int
		AgentLimit, KeyLimit, RunLimit                                        int64
		Deadline                                                              time.Time
		Agent                                                                 models.AgentContext
		Runtime                                                               *models.RuntimeIdentity
		Routing                                                               models.RoutingOptions
		Price                                                                 *models.PriceCard
		Endpoints                                                             []models.EndpointRef
		CallCap                                                               *float64
		Scope, AdministrativeAgent                                            string
		RunRequired                                                           bool
	}{rc.RequestID, rc.Metadata["org_id"], rc.Metadata["auth_key_id"], rc.Metadata["billing_mode"], a.nonce, nativeDay(a.period), a.agentActivation, a.keyActivation, a.revision, a.agentLimit, a.keyLimit, a.runLimit, a.deadline, rc.Agent, rc.Runtime, rc.Routing, rc.Billing.ReservePrice, rc.Billing.EndpointSet, rc.Billing.CallCapUSD, c.scope, a.agent, c.runRequired})
}

// Caller holds c.mu. Fresh authority is required at every pre-dispatch read;
// revision/day changes abandon this admission rather than reprice its hold.
func nativeQuoteMatches(c *nativeControl) bool {
	if c == nil || c.rc == nil || c.quote == nil || c.quote.owner != c || c.closing || c.failed || c.authority == nil || !c.authority() {
		return false
	}
	rc, a, q := c.rc, c.admission, c.quote
	if r := rc.Routing.ComputerUse; r.Required && (r.Protocol != c.binding.Protocol || r.Action != c.binding.Action || r.Stream != c.binding.Stream || r.ToolProtocol == "" || r.VendorVersion == "" || r.ToolProtocol != c.binding.ToolProtocol || r.VendorVersion != c.binding.VendorVersion) {
		return false
	}
	if q.bound.current == nil || !q.bound.current() || !reflect.DeepEqual(q.agent, rc.Agent) || !reflect.DeepEqual(q.runtime, rc.Runtime) || !reflect.DeepEqual(q.routing, rc.Routing) || !reflect.DeepEqual(q.price, rc.Billing.ReservePrice) || !reflect.DeepEqual(q.endpoints, rc.Billing.EndpointSet) || rc.Routing.ComputerUseBinding != c.binding {
		return false
	}
	if a == nil || a.nonce == "" || !time.Now().Before(a.deadline) || nativeDay(time.Now()) != nativeDay(a.period) || rc.Request != nil || !nativeSameView(c.view, c.view) {
		return false
	}
	if rc.Metadata["org_id"] != a.org || rc.Metadata["auth_key_id"] != a.key || rc.Provider != c.binding.ProviderID || rc.Model != c.binding.SentModel || rc.Billing.EndpointID != c.binding.EndpointID || rc.IsStream != c.binding.Stream || rc.EndpointType != c.binding.EndpointType {
		return false
	}
	if (rc.Metadata["billing_mode"] == models.BillingModeManaged) != (c.binding.CredentialType == models.CredentialManaged) || !bytes.Equal(c.view.Body(), q.body) {
		return false
	}
	pin, err := nativeSnapshot(c)
	return err == nil && bytes.Equal(pin, q.pin) && nativeCallCapFits(rc, q.bound.capMicros)
}

func NativeQuoteFor(rc *models.RequestContext) (NativeQuote, bool) {
	c := nativeFor(rc)
	if c == nil {
		return nil, false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if !nativeQuoteMatches(c) {
		return nil, false
	}
	return c.quote, true
}
func (*nativeQuoteState) nativeQuote() {}
func (q *nativeQuoteState) CapMicros() (int64, bool) {
	if q == nil || q.owner == nil {
		return 0, false
	}
	c := q.owner
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.quote != q || !nativeQuoteMatches(c) {
		return 0, false
	}
	return q.bound.capMicros, true
}
func (q *nativeQuoteState) ManagedHoldMicros() (int64, bool) {
	if q == nil || q.owner == nil {
		return 0, false
	}
	c := q.owner
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.quote != q || !nativeQuoteMatches(c) {
		return 0, false
	}
	return q.bound.managedMicros, true
}
