package server

import (
	"context"
	"encoding/json"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/futureagi/agentcc-gateway/internal/toolview"
	"net/http"
)

// ComputerUseSource publishes coherent immutable revisions. Implementations
// must bind actual provider/credential instances privately, not infer revisions
// from names or credentials. Installation is startup-only, before serving.
type ComputerUseSource interface{ Snapshot() ComputerUseSnapshot }
type ComputerUseSnapshot interface {
	Capabilities() *modeldb.ComputerUseCapabilities
	CandidateBindings(orgID, providerID, sentModel, endpointType string, mode models.CredentialType, required models.ComputerUseRequirement) []models.NativeRouteBinding
	ProviderBinding(orgID, providerID, sentModel, endpointType, endpointID string, mode models.CredentialType, required models.ComputerUseRequirement, provider providers.Provider) (models.NativeRouteBinding, bool)
}

func (h *Handlers) SetComputerUseSource(source ComputerUseSource) { h.computerUseSource = source }
func (h *Handlers) computerUseSnapshot(rc *models.RequestContext) ComputerUseSnapshot {
	if !rc.Routing.ComputerUse.Required || h.computerUseSource == nil {
		return nil
	}
	return h.computerUseSource.Snapshot()
}
func computerUseCapabilities(s ComputerUseSnapshot) *modeldb.ComputerUseCapabilities {
	if s == nil {
		return nil
	}
	return s.Capabilities()
}
func (h *Handlers) computerUseCandidates(s ComputerUseSnapshot, rc *models.RequestContext, cfg *tenant.OrgConfig, providerID, model string, auto bool) []models.NativeRouteBinding {
	if s == nil {
		return nil
	}
	mode := models.CredentialBYOK
	if h.managedCandidate(rc, cfg, providerID, auto) {
		mode = models.CredentialManaged
	}
	bindings := append([]models.NativeRouteBinding(nil), s.CandidateBindings(rc.Metadata[tenant.MetadataKeyOrgID], providerID, model, rc.EndpointType, mode, rc.Routing.ComputerUse)...)
	kept := bindings[:0]
	for _, b := range bindings {
		if b.EndpointType == rc.EndpointType && b.ProviderID == providerID && b.SentModel == model && b.CredentialType == mode && modeldb.MatchesComputerUse(b, rc.Routing.ComputerUse) {
			kept = append(kept, b)
		}
	}
	return kept
}
func nativeGenerateEndpoint(endpoint string) bool {
	switch endpoint {
	case "anthropic_messages", "responses", "genai_generate", "genai_stream":
		return true
	}
	return false
}
func nativeControlRequired(rc *models.RequestContext, cfg *tenant.OrgConfig) bool {
	return rc.Routing.ComputerUse.Required || capbudget.RequestAdmission(rc) != nil || capbudget.CapsEnabled(cfg) || rc.Agent.MaxCostUSD != nil || rc.Agent.BudgetUSD != nil || rc.Agent.BudgetRemainingUSD != nil
}
func nativeHooksUnavailable() *models.APIError {
	return &models.APIError{Status: http.StatusServiceUnavailable, Type: models.ErrTypeServer, Code: "budget_state_unavailable", Message: "native admission is unavailable"}
}

// Bind consumes only the cap owner's verified authority. These scalar hints
// may require a control, but can never manufacture one or relax its checks.
func (h *Handlers) bindNativeEntry(r *http.Request, rc *models.RequestContext, cfg *tenant.OrgConfig, protocol models.NativeProtocol) *models.APIError {
	if h.nativeCaps == nil {
		if nativeControlRequired(rc, cfg) {
			return nativeHooksUnavailable()
		}
		return nil
	}
	return h.nativeCaps.Bind(r, rc, cfg, protocol)
}
func (h *Handlers) attachNativeEntry(ctx context.Context, rc *models.RequestContext, cfg *tenant.OrgConfig, view *toolview.NativeRequest, provider providers.Provider, headers map[string]string, sentModel string) *models.APIError {
	if rc.Request != nil || view == nil {
		return nativeHooksUnavailable()
	}
	required, err := view.ComputerUseRequirement(ctx)
	if err != nil {
		return computerUseUnavailable()
	}
	// An already accepted restriction cannot be overwritten by an empty view.
	if rc.Routing.ComputerUse.Required {
		required = rc.Routing.ComputerUse
	}
	if view.ValidateComputerUse(ctx, required) != nil || (required.Required && required.Stream != rc.IsStream) {
		return computerUseUnavailable()
	}
	rc.Routing.ComputerUse = required
	if required.Required {
		// The route is chosen: fix the identity the request's fallback plan is
		// judged for, just before admission judges it (fallback_plan.go).
		fixFallbackRoute(rc)
		if v := h.checkEligibility(rc, cfg, rc.Provider, sentModel); v != nil {
			err := v.apiError()
			err.Message = "native route is not eligible"
			return err
		}
		binding, refusal := h.checkComputerUseBeforeDispatch(ctx, rc, cfg, provider, sentModel)
		if refusal != nil {
			return refusal
		}
		rc.Routing.ComputerUseBinding = binding
	}
	if h.nativeCaps == nil {
		if nativeControlRequired(rc, cfg) {
			return nativeHooksUnavailable()
		}
		return nil
	}
	return h.nativeCaps.Attach(rc, view, rc.Routing.ComputerUseBinding, provider, headers)
}

// beforeNativeDispatch returns only the sealed attempt bytes/context for a
// controlled call. Uncontrolled native compatibility keeps its existing path.
func (h *Handlers) beforeNativeDispatch(ctx context.Context, rc *models.RequestContext, view models.NativeBody, actual any, headers map[string]string, sentModel string) (context.Context, []byte, capbudget.NativeAttempt, *models.APIError) {
	_, cfg := h.resolveOrgConfig(rc)
	if h.nativeCaps == nil {
		if nativeControlRequired(rc, cfg) {
			return nil, nil, nil, nativeHooksUnavailable()
		}
		return ctx, view.Body(), nil, nil
	}
	provider, ok := actual.(providers.Provider)
	if !ok {
		return nil, nil, nil, nativeHooksUnavailable()
	}
	if sentModel == "" {
		var sent struct {
			Model string `json:"model"`
		}
		if json.Unmarshal(view.Body(), &sent) != nil || sent.Model == "" {
			return nil, nil, nil, nativeHooksUnavailable()
		}
		sentModel = sent.Model
	}
	recheck := func() (models.NativeRouteBinding, *models.APIError) {
		return h.checkComputerUseBeforeDispatch(ctx, rc, cfg, provider, sentModel)
	}
	attempt, err := h.nativeCaps.BeforeDispatch(ctx, rc, view, rc.Routing.ComputerUseBinding, provider, headers, recheck)
	if err != nil {
		return nil, nil, nil, err
	}
	if attempt == nil || attempt.Context() == nil || attempt.FinancialID() == "" || len(attempt.Body()) == 0 {
		return nil, nil, nil, nativeHooksUnavailable()
	}
	rc.AddFinalizer(func() { h.nativeCaps.End(attempt, capbudget.NativeAborted) })
	return attempt.Context(), attempt.Body(), attempt, nil
}
func (h *Handlers) endNativeAttempt(attempt capbudget.NativeAttempt, reason capbudget.NativeEnd) *models.APIError {
	if attempt == nil {
		return nil
	}
	if h.nativeCaps == nil {
		return nativeHooksUnavailable()
	}
	return h.nativeCaps.End(attempt, reason)
}
func (h *Handlers) afterNativeBilling(ctx context.Context, rc *models.RequestContext) {
	if h.nativeCaps != nil {
		h.nativeCaps.AfterBilling(ctx, rc)
	}
}
func computerUseUnavailable() *models.APIError {
	return &models.APIError{Status: http.StatusServiceUnavailable, Type: models.ErrTypeServer, Code: eligibility.CodeComputerUseUnavailable, Message: "computer use is unavailable for this route"}
}

// checkComputerUseBeforeDispatch re-reads the trusted source using the actual
// selected provider. It does not mutate the selected binding: the cap facade
// compares this value to its sealed quote at the synchronous claim boundary.
func (h *Handlers) checkComputerUseBeforeDispatch(ctx context.Context, rc *models.RequestContext, cfg *tenant.OrgConfig, provider providers.Provider, sentModel string) (models.NativeRouteBinding, *models.APIError) {
	if !rc.Routing.ComputerUse.Required {
		return models.NativeRouteBinding{}, nil
	}
	s := h.computerUseSnapshot(rc)
	if ctx.Err() != nil || s == nil || provider == nil || provider.ID() != rc.Provider {
		return models.NativeRouteBinding{}, computerUseUnavailable()
	}
	mode := h.attemptCredential(orgCredentials(rc), rc.Provider, credentialType(rc))
	b, ok := s.ProviderBinding(rc.Metadata[tenant.MetadataKeyOrgID], rc.Provider, sentModel, rc.EndpointType, rc.Billing.EndpointID, mode, rc.Routing.ComputerUse, provider)
	if !ok || b.ProviderID != rc.Provider || b.SentModel != sentModel || b.EndpointType != rc.EndpointType || b.CredentialType != mode || !modeldb.MatchesComputerUse(b, rc.Routing.ComputerUse) || s.Capabilities().Lookup(b).Support() != modeldb.ComputerUseSupported {
		return models.NativeRouteBinding{}, computerUseUnavailable()
	}
	if rc.Billing.EndpointID != "" && (b.EndpointID != rc.Billing.EndpointID || b.UpstreamProvider != rc.Billing.UpstreamProvider) {
		return models.NativeRouteBinding{}, computerUseUnavailable()
	}
	v := h.judgeForComputerUseBindings(rc, cfg, rc.Provider, sentModel, rc.Model, false, []models.NativeRouteBinding{b}, s.Capabilities())
	if !v.OK {
		err := refusal(&v).apiError()
		err.Message = "native route is not eligible"
		return models.NativeRouteBinding{}, err
	}
	return b, nil
}
