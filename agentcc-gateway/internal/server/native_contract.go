package server

import (
	"context"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// NativeCapHooks requires verified admission and package-issued attempt state.
// Attach records provisional state, not qualification. BeforeDispatch must
// synchronously recheck the selected route and actual provider before claiming.
// Nil hooks or recheck callbacks confer no authority. Implementations must not
// retain the HTTP request or pooled request context beyond its lifetime.
// The private method restricts the API; it does not prove handle provenance.
type NativeCapHooks interface {
	Bind(r *http.Request, rc *models.RequestContext, cfg *tenant.OrgConfig, protocol models.NativeProtocol) *models.APIError
	Attach(rc *models.RequestContext, view models.NativeBody, binding models.NativeRouteBinding, provider providers.Provider, headers map[string]string) *models.APIError
	BeforeDispatch(ctx context.Context, rc *models.RequestContext, view models.NativeBody, binding models.NativeRouteBinding, provider providers.Provider, headers map[string]string, recheck func() (models.NativeRouteBinding, *models.APIError)) (capbudget.NativeAttempt, *models.APIError)
	Observe(attempt capbudget.NativeAttempt, event capbudget.NativeUsageEvent) *models.APIError
	End(attempt capbudget.NativeAttempt, reason capbudget.NativeEnd) *models.APIError
	AfterBilling(ctx context.Context, rc *models.RequestContext) capbudget.NativeSettlement
	claimPrepared(rc *models.RequestContext, providerID, model string) *models.APIError
}
