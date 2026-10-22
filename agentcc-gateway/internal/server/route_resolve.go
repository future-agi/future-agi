package server

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// RouteResult is how resolveRoute reached its provider.
type RouteResult struct {
	// AutoDecision is set when the request asked for model "auto".
	AutoDecision *routing.AutoDecision
	// ProviderLocked is set when a provider-lock header chose the provider.
	ProviderLocked bool
	// KeyBound is set when the provider is already the instance the key may
	// call (providerForKey), so the org provider override was skipped.
	KeyBound bool
}

// resolveRoute picks the provider for a chat request: model access groups,
// provider lock, auto routing, complexity routing, provider resolution, the
// org provider override, the key's provider ACL and checkEligibility on the
// route reached, explicit or auto-chosen, in that order (call-order step
// 11). It writes
// the routing metadata on rc and may rewrite rc.Model and rc.Request.Model.
// An error is the response to send.
//
// The org comes from rc.Metadata["org_id"], which the caller must set first:
// ChatCompletion does, from the request's key. A caller that has not set it
// gets org-less routing (no org-first resolution, no org override) and no
// error. endpoint names the API surface, in rc.EndpointType's vocabulary
// ("chat" for ChatCompletion); it is reserved for per-surface eligibility and
// not read here. The result is returned by value so the chat hot path
// allocates nothing for it.
//
// The provider-ACL exit below reads auth_allowed_providers, which
// peekKeyOrgID records from the request's key before routing.
func (h *Handlers) resolveRoute(ctx context.Context, r *http.Request, rc *models.RequestContext, orgCfg *tenant.OrgConfig, endpoint string) (providers.Provider, RouteResult, error) {
	orgID := rc.Metadata["org_id"]
	var route RouteResult

	if middleware.IsLicenseAuthorized(r.Context()) && rc.Metadata["key_access_groups"] == "" {
		rc.Metadata["key_access_groups"] = "internal"
		rc.Metadata["key_type"] = "internal"
	}

	// 1. Model access group alias resolution and access check. The literal
	// auto is not checked: eligibility judges the key's groups on every auto
	// candidate.
	keyGroups := splitCSV(rc.Metadata["key_access_groups"])
	if h.accessGroupChecker.IsEnabled() && len(keyGroups) > 0 {
		resolved := h.accessGroupChecker.ResolveAlias(rc.Model, keyGroups)
		if resolved != rc.Model {
			rc.SetMetadata("model_alias", rc.Model)
			rc.Model = resolved
			rc.Request.Model = resolved
		}
		if !routing.IsAutoModel(rc.Model) {
			matchedGroup, allowed := h.accessGroupChecker.Check(rc.Model, keyGroups)
			if !allowed {
				desc := h.accessGroupChecker.DescribeAllowed(keyGroups)
				return nil, RouteResult{}, models.ErrForbidden(
					fmt.Sprintf("Model %q is not available for this API key. %s", rc.Model, desc))
			}
			if matchedGroup != "" {
				rc.SetMetadata("access_group_matched", matchedGroup)
			}
		}
	}

	// 2. Provider locking — bypasses all routing if set.
	var lockedProvider providers.Provider
	if h.providerLockResolver.IsEnabled() {
		lockTarget := h.providerLockResolver.ExtractLock(rc, r.Header)
		if lockTarget != "" {
			if err := h.providerLockResolver.Validate(lockTarget); err != nil {
				return nil, RouteResult{}, models.ErrForbidden(err.Error())
			}
			p, ok := h.registry.GetProvider(lockTarget)
			if !ok {
				return nil, RouteResult{}, models.ErrNotFound("provider_not_found",
					fmt.Sprintf("Locked provider %q not found", lockTarget))
			}
			p, err := h.providerForKey(ctx, rc, orgID, orgCfg, lockTarget, p, rc.Model)
			if err != nil {
				if errors.Is(err, errProviderNotPermitted) {
					err = models.ErrForbidden(fmt.Sprintf("provider %q is not available for this API key", lockTarget))
				}
				return nil, RouteResult{}, err
			}
			route.KeyBound = h.orgProvidersOnly(ctx, rc)
			route.ProviderLocked = true
			lockedProvider = p
			rc.Provider = lockTarget
			rc.SetMetadata("provider_locked", "true")
			rc.SetMetadata("provider_lock_target", lockTarget)
		}
	}

	// 3. Auto routing: `model: "auto"` asks the gateway to choose.
	if !route.ProviderLocked && routing.IsAutoModel(rc.Model) {
		d, err := h.routeAuto(ctx, r, rc, orgCfg)
		if err != nil {
			return nil, RouteResult{}, autoErrorResponse(err)
		}
		route.AutoDecision = d
	}

	// 4. Complexity-based routing (only if not provider-locked or auto-routed).
	if !route.ProviderLocked && route.AutoDecision == nil && h.complexityAnalyzer.IsEnabled() && !strings.Contains(rc.Model, "/") {
		if override := r.Header.Get("x-agentcc-complexity-override"); override != "" {
			rc.SetMetadata("complexity_override", override)
		}
		result := h.complexityAnalyzer.Analyze(rc.Request)
		originalModel := rc.Model
		rc.Model = result.Model
		rc.Request.Model = result.Model
		routing.SetComplexityMetadata(rc, result, originalModel)
		if result.Provider != "" {
			rc.SetMetadata("complexity_provider", result.Provider)
		}
	}

	// Resolve provider (with load balancing and failover if configured).
	var provider providers.Provider
	if route.ProviderLocked {
		provider = lockedProvider
	} else if p, ok := h.autoManagedProvider(ctx, rc, orgCfg, route.AutoDecision); ok {
		provider = p
		route.KeyBound = true
	} else {
		var err error
		// Chat's routed model is already on rc.Request.
		provider, _, err = h.resolveProviderOrgFirst(ctx, rc, orgID, orgCfg, rc.Request.Model)
		if err != nil {
			return nil, RouteResult{}, err
		}
	}

	// Internal service keys skip the org provider override and the provider ACL
	// check below.
	if shouldApplyOrgProviderOverride(rc) && !route.KeyBound {
		resolved := provider
		provider = h.applyOrgProviderOverride(orgID, orgCfg, rc.Provider, provider)
		if provider != resolved {
			rc.Metadata["billing_mode"] = billingBYOK
		}
	}
	if rc.Metadata["key_type"] != "internal" {
		// Enforce provider ACL after routing (auth plugin stores allowed providers).
		if allowedCSV, ok := rc.Metadata["auth_allowed_providers"]; ok && rc.Provider != "" {
			allowed := false
			for _, p := range splitCSV(allowedCSV) {
				if p == rc.Provider {
					allowed = true
					break
				}
			}
			if !allowed {
				return nil, RouteResult{}, models.ErrForbidden("API key does not have access to provider: " + rc.Provider)
			}
		}
	}
	// An auto-chosen route passed as a candidate; it is judged again as the
	// route, so it takes its verdict's endpoint set and data policy.
	if v := h.checkEligibility(rc, orgCfg, rc.Provider, rc.Request.Model); v != nil {
		return nil, RouteResult{}, v.apiError()
	}
	return provider, route, nil
}
