package server

import (
	"context"
	"maps"
	"net/http"
	"reflect"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/plugins/agentbudget"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Installation is startup-only. There is intentionally no positive production
// factory: the verified identity, provider/credential revision, native pricing
// and qualified decoder owners have not supplied an accepted vertical slice.
func (h *Handlers) SetNativeCapHooks(hooks NativeCapHooks) { h.nativeCaps = hooks }

type nativeCapFacade struct {
	handlers *Handlers
	daily    *capbudget.Plugin
	run      *agentbudget.Plugin
	states   sync.Map // request-local snapshots, cleared synchronously on finalization
}

type nativeFacadeState struct {
	provider           providers.Provider
	view               models.NativeBody
	binding            models.NativeRouteBinding
	headers            map[string]string
	claimHeaders       map[string]string // actual options, retained only through synchronous claim
	recheck            func() (models.NativeRouteBinding, *models.APIError)
	prepared, consumed bool
}

var _ NativeCapHooks = (*nativeCapFacade)(nil)

func nativeHookUnavailable() *models.APIError {
	return &models.APIError{Status: 503, Type: models.ErrTypeInvalidRequest, Code: "budget_state_unavailable", Message: "native dispatch authority unavailable"}
}

func (f *nativeCapFacade) Bind(r *http.Request, rc *models.RequestContext, cfg *tenant.OrgConfig, protocol models.NativeProtocol) *models.APIError {
	if r == nil || r.URL == nil || r.Method != http.MethodPost || r.URL.RawPath != "" || r.URL.RawQuery != "" {
		return nativeHookUnavailable()
	}
	if e := capbudget.BindNativeAdmission(r, rc, cfg, protocol); e != nil {
		return e
	}
	if !capbudget.NativeControlled(rc) {
		return nativeHookUnavailable()
	}
	if _, loaded := f.states.LoadOrStore(rc, &nativeFacadeState{}); loaded {
		return nativeHookUnavailable()
	}
	rc.AddFinalizer(func() {
		if v, ok := f.states.LoadAndDelete(rc); ok {
			st := v.(*nativeFacadeState)
			st.provider, st.view, st.headers, st.claimHeaders, st.recheck = nil, nil, nil, nil, nil
		}
	})
	return nil
}

func (f *nativeCapFacade) Attach(rc *models.RequestContext, view models.NativeBody, binding models.NativeRouteBinding, provider providers.Provider, headers map[string]string) *models.APIError {
	v, ok := f.states.Load(rc)
	if !ok || !sameNativeProvider(provider, provider) {
		return nativeHookUnavailable()
	}
	st := v.(*nativeFacadeState)
	if st.view != nil || st.consumed {
		return nativeHookUnavailable()
	}
	if e := capbudget.AttachNative(rc, view, binding); e != nil {
		return e
	}
	st.provider, st.view, st.binding, st.headers = provider, view, binding, maps.Clone(headers)
	return nil
}

func (f *nativeCapFacade) BeforeDispatch(ctx context.Context, rc *models.RequestContext, view models.NativeBody, binding models.NativeRouteBinding, provider providers.Provider, headers map[string]string, recheck func() (models.NativeRouteBinding, *models.APIError)) (capbudget.NativeAttempt, *models.APIError) {
	v, ok := f.states.Load(rc)
	if !ok || f.handlers == nil || f.handlers.nativeCaps != f || recheck == nil {
		return nil, nativeHookUnavailable()
	}
	st := v.(*nativeFacadeState)
	if st.prepared || st.consumed || st.binding != binding || !sameNativeProvider(st.provider, provider) || !maps.Equal(st.headers, headers) {
		return nil, nativeHookUnavailable()
	}
	if e := capbudget.PrepareNativeDispatch(ctx, rc, view, binding); e != nil {
		return nil, e
	}
	st.prepared, st.recheck, st.claimHeaders = true, recheck, headers
	defer func() { st.recheck, st.claimHeaders = nil, nil }()
	// The routing-owned native branch of startAttempt calls claimPrepared.
	// At the S0-only cut the chat guard refuses; do not weaken it here.
	before := rc.AttemptCount()
	callCtx, finish, refusal := f.handlers.startAttempt(ctx, rc, models.AttemptPrimary, models.ReasonNone, binding.ProviderID, binding.SentModel, binding.CredentialType, provider)
	st.recheck = nil
	if refusal != "" || finish == nil {
		return nil, nativeHookUnavailable()
	}
	attempts := rc.AttemptsSnapshot()
	if !st.consumed || len(attempts) != before+1 || before != 0 {
		finish(nil, nil, nativeHookUnavailable())
		return nil, nativeHookUnavailable()
	}
	id := attempts[len(attempts)-1].FinancialID
	attempt, e := capbudget.BindNativeAttempt(callCtx, rc, id, func(usage *models.Usage, generation models.GenerationEvidence, failure *models.APIError) {
		// This response is only an existing attempt-evidence adapter. The native
		// request stays nil; no chat request/response is sent to the provider.
		response := &models.ChatCompletionResponse{ID: generation.ID, Model: generation.ServedModel, UpstreamProvider: generation.Provider, Usage: usage}
		var err error
		if failure != nil {
			err = failure
		}
		finish(response, usage, err)
		// Request-level post plugins see the final copied evidence too. The
		// attempt retains its independent copy through the existing finish.
		rc.Response = response
	})
	if e != nil {
		finish(nil, nil, e)
		return nil, e
	}
	return attempt, nil
}

func (f *nativeCapFacade) claimPrepared(rc *models.RequestContext, providerID, model string) *models.APIError {
	v, ok := f.states.Load(rc)
	if !ok {
		return nativeHookUnavailable()
	}
	st := v.(*nativeFacadeState)
	if !st.prepared || st.consumed || st.recheck == nil || providerID != st.binding.ProviderID || model != st.binding.SentModel {
		return nativeHookUnavailable()
	}
	// Fresh routing/capability/actual-provider recheck is INSIDE the claim,
	// immediately before all cap/credits/run barriers and the single CAS.
	recheck := st.recheck
	st.recheck = nil
	binding, e := recheck()
	if e != nil {
		return e
	}
	if binding != st.binding || !maps.Equal(st.headers, st.claimHeaders) {
		return nativeHookUnavailable()
	}
	if e := capbudget.ClaimPreparedNative(rc, providerID, model); e != nil {
		return e
	}
	st.consumed = true
	return nil
}

func (f *nativeCapFacade) Observe(a capbudget.NativeAttempt, event capbudget.NativeUsageEvent) *models.APIError {
	return capbudget.ObserveNative(a, event)
}
func (f *nativeCapFacade) End(a capbudget.NativeAttempt, reason capbudget.NativeEnd) *models.APIError {
	return capbudget.EndNative(a, reason)
}
func (f *nativeCapFacade) AfterBilling(ctx context.Context, rc *models.RequestContext) capbudget.NativeSettlement {
	if f.daily == nil || f.run == nil {
		return capbudget.NativeSettlement{State: capbudget.NativeSettlementUnavailable, Error: nativeHookUnavailable()}
	}
	daily := f.daily.FinishNativeCaps(ctx, rc)
	run := f.run.FinishNativeCaps(ctx, rc)
	return aggregateNativeSettlement(daily, run)
}

func aggregateNativeSettlement(a, b capbudget.NativeSettlement) capbudget.NativeSettlement {
	if a.State == capbudget.NativeSettled && b.State == capbudget.NativeSettled && a.Error == nil && b.Error == nil {
		return a
	}
	if a.State == capbudget.NativeSettlementUnavailable || b.State == capbudget.NativeSettlementUnavailable || a.State == 0 || b.State == 0 {
		return capbudget.NativeSettlement{State: capbudget.NativeSettlementUnavailable, Error: nativeHookUnavailable()}
	}
	return capbudget.NativeSettlement{State: capbudget.NativeFenced, Error: nativeHookUnavailable()}
}

func sameNativeProvider(a, b providers.Provider) bool {
	if a == nil || b == nil {
		return false
	}
	av, bv := reflect.ValueOf(a), reflect.ValueOf(b)
	return av.Kind() == reflect.Pointer && bv.Kind() == reflect.Pointer && !av.IsNil() && !bv.IsNil() && av.Type() == bv.Type() && av.Pointer() == bv.Pointer()
}
