package server

import (
	"net/http"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// headerModelDeprecated tells the caller the model it was served expires on
// the date it carries (eligibility rule 4).
const headerModelDeprecated = "x-agentcc-model-deprecated"

// codeModelNotPermitted is the error code of an explicit model eligibility
// refuses, with the rule's code in metadata.reason.
const codeModelNotPermitted = "model_not_permitted"

// Verdict is why a model on a provider is not eligible for a request. A nil
// *Verdict means eligible.
type Verdict struct {
	// Code is the rejection code: an auto rejection's reason, and
	// metadata.reason of a refused explicit route.
	Code    string
	Message string
	// Status is the HTTP status of a refused explicit route; 0 means 403.
	Status int
}

// apiError is the response to an explicit route the verdict refuses: 403
// model_not_permitted with the rule's code in metadata.reason, except an
// expired model (410 model_expired) and a provider filter nothing matches
// (404 no_provider_matches), which keep their own codes.
func (v *Verdict) apiError() *models.APIError {
	status := v.Status
	if status == 0 {
		status = http.StatusForbidden
	}
	errType, code := models.ErrTypePermission, codeModelNotPermitted
	if status == http.StatusNotFound || status == http.StatusGone {
		errType, code = models.ErrTypeNotFound, v.Code
	}
	return &models.APIError{Status: status, Type: errType, Code: code, Message: v.Message,
		Metadata: map[string]string{"reason": v.Code}}
}

// checkEligibility is part of call-order step 11: whether model on
// providerID may serve the request, by the eligibility engine (the key ACL,
// the org's provider and managed allow and deny lists, model state, data
// policy, request constraints). resolveRoute checks the route with it,
// explicit or auto-chosen, and buildAutoPool every auto candidate
// (checkAutoCandidate). A route it allows takes the verdict's endpoint set,
// effective data policy and deprecation header. A request still naming the
// literal auto here (a provider lock skips auto routing) is judged as an
// auto candidate and takes nothing. Keep observeHookAt as its first call.
func (h *Handlers) checkEligibility(rc *models.RequestContext, orgCfg *tenant.OrgConfig, providerID, model string) *Verdict {
	h.observeHookAt(hookCheckEligibility, rc, providerID, model)
	auto := routing.IsAutoModel(rc.Model)
	aclModel := model
	if !auto {
		aclModel = rc.Model // the explicit route: the model the caller asked for
	}
	v := h.judge(rc, orgCfg, providerID, model, aclModel, auto)
	if !v.OK {
		return refusal(&v)
	}
	if !auto {
		h.applyVerdict(rc, &v)
	}
	return nil
}

// checkAutoCandidate is checkEligibility for one auto candidate, which takes
// nothing from the verdict, since it may not be chosen. model is the model
// the candidate is sent upstream as, on which every rule but the key's model
// ACL is judged; aclModel is the candidate's own id, which auto answers with
// and rc.Model becomes, on which the key's model ACL is judged, as on an
// explicit route. They differ for a listed provider-prefixed managed model
// ("openrouter/x-ai/grok-5", sent as "x-ai/grok-5"). Keep observeHookAt as
// its first call.
func (h *Handlers) checkAutoCandidate(rc *models.RequestContext, orgCfg *tenant.OrgConfig, providerID, model, aclModel string) *Verdict {
	h.observeHookAt(hookCheckEligibility, rc, providerID, model)
	if v := h.judge(rc, orgCfg, providerID, model, aclModel, true); !v.OK {
		return refusal(&v)
	}
	return nil
}

// refusal is the Verdict of an engine verdict that refuses the candidate.
func refusal(v *eligibility.Verdict) *Verdict {
	return &Verdict{Code: v.Code, Message: v.Reason, Status: eligibility.StatusFor(v.Code)}
}

// fallbackHopAllowed is part of call-order step 14: whether the request may
// move on to model on providerID after an attempt failed. The hop is judged
// by the same engine as the route it falls back from, and an allowed hop
// takes its own endpoint set and data policy. A refused hop is never
// dispatched: failoverHops.next records it as a skipped attempt (reason
// policy). Keep observeHookAt as its first call.
func (h *Handlers) fallbackHopAllowed(rc *models.RequestContext, orgCfg *tenant.OrgConfig, providerID, model string) bool {
	h.observeHookAt(hookFallbackHopAllowed, rc, providerID, model)
	v := h.judge(rc, orgCfg, providerID, model, model, false)
	if !v.OK {
		return false
	}
	h.applyVerdict(rc, &v)
	return true
}

// judge runs the eligibility engine for model on providerID. The key's
// model ACL is judged on aclModel: on an explicit route the model the caller
// asked for (rc.Model after alias resolution, as the auth plugin judges it),
// else the candidate's or the hop's model. Everything else is judged on the
// model sent upstream.
func (h *Handlers) judge(rc *models.RequestContext, orgCfg *tenant.OrgConfig, providerID, model, aclModel string, auto bool) eligibility.Verdict {
	managed := h.managedCandidate(rc, orgCfg, providerID, auto)
	in := eligibility.Input{
		Model:      model,
		ACLModel:   aclModel,
		ProviderID: providerID,
		Auto:       auto,
		Key: eligibility.KeyACL{
			AllowedModels:    rc.Metadata[metaKeyAllowedModels],
			AllowedProviders: rc.Metadata[metaKeyAllowedProviders],
			Internal:         rc.Metadata["key_type"] == "internal",
		},
		Org:             orgCfg,
		Managed:         managed,
		Aggregator:      h.aggregatorProvider(orgCfg, providerID),
		Entry:           h.catalogEntry(providerID, model),
		Request:         rc.Request,
		Prefs:           rc.Routing.Provider,
		Env:             rc.Agent.Env,
		ModeRestriction: rc.Billing.ModeRestriction,
		// As the operator set it; nil keeps the engine's rule.
		ThoughtSignatureModels: h.thoughtSignatureModels,
		// The calendar, looked up by the serving provider's type.
		Shutdowns: h.deprecations,
		Host:      h.deprecationHost(orgCfg, providerID),
	}
	if groups := rc.Metadata["key_access_groups"]; groups != "" && h.accessGroupChecker.IsEnabled() {
		in.Key.Groups, in.Key.GroupCheck = splitCSV(groups), h.accessGroupChecker
	}
	if pc := orgProviderConfig(orgCfg, providerID); pc != nil && !managed {
		in.ZDRAttested = pc.ZDRAttested
		in.DataRegion = pc.DataRegion
		if in.DataRegion == "" {
			in.DataRegion = pc.AWSRegion
		}
	}
	return eligibility.Check(&in)
}

// deprecationHost is providerID's host in the model shutdown calendar
// (the provider's type, never its gateway ID). An
// org's own provider is judged by its own config when it names a base URL or
// a service account, or no configured provider has its ID (as
// OrgProviderCache builds it); otherwise the configured provider's host,
// set at wiring. "" when unknown, so Lookup falls back to the model prefix.
func (h *Handlers) deprecationHost(orgCfg *tenant.OrgConfig, providerID string) string {
	host, configured := h.providerHosts[providerID]
	if pc := orgProviderConfig(orgCfg, providerID); pc != nil && (!configured || pc.BaseURL != "" || pc.ServiceAccountJSON != "") {
		format := pc.APIFormat
		if format == "" {
			format = inferOrgAPIFormat(providerID)
		}
		return modeldb.DeprecationHost(format, pc.BaseURL)
	}
	return host
}

// providerHostsFor is each configured provider's calendar host: its
// api_format and base URL for a config provider, its type for a managed one.
func providerHostsFor(cfg *config.Config, registry *providers.Registry) map[string]string {
	hosts := make(map[string]string, len(cfg.Providers))
	for id, pc := range cfg.Providers {
		hosts[id] = modeldb.DeprecationHost(pc.APIFormat, pc.BaseURL)
	}
	if registry != nil {
		for _, mp := range registry.ManagedProviders() {
			if _, ok := hosts[mp.ID]; !ok {
				hosts[mp.ID] = modeldb.DeprecationHost(mp.Type, mp.CatalogURL)
			}
		}
	}
	return hosts
}

// shutDownModel is the 410 model_expired answer for model on providerID when
// the calendar says the serving host shut it down, nil otherwise. It is for a
// handler that runs no eligibility (images); routed chat requests get the same
// refusal from eligibility rule 4.
func (h *Handlers) shutDownModel(orgCfg *tenant.OrgConfig, providerID, model string) *models.APIError {
	model = strings.TrimPrefix(model, providerID+"/")
	d, gone := h.deprecations.ShutDown(h.deprecationHost(orgCfg, providerID), model, time.Now())
	if !gone {
		return nil
	}
	return (&Verdict{Code: eligibility.CodeModelExpired, Message: d.Reason(), Status: http.StatusGone}).apiError()
}

// catalogEntry is providerID's catalog entry for model, or nil.
func (h *Handlers) catalogEntry(providerID, model string) *catalog.Model {
	m, _ := h.catalog.Load().Get(providerID, model)
	return m
}

// managedCandidate reports whether an attempt on providerID would be billed
// to the org as managed. A routed request knows (resolution set its billing
// mode); an auto candidate is judged as resolution would judge it: a
// customer key on a managed provider the org holds no credentials for.
func (h *Handlers) managedCandidate(rc *models.RequestContext, orgCfg *tenant.OrgConfig, providerID string, auto bool) bool {
	if !auto {
		return rc.Metadata["billing_mode"] == billingManaged
	}
	return h.keyStore != nil && rc.Metadata["key_type"] != "internal" &&
		h.registry.IsManaged(providerID) && !orgHasCredentials(orgCfg, providerID)
}

// aggregatorProvider reports whether providerID is an OpenRouter-type
// upstream: a managed provider of type openrouter (the org's own key may be
// on it), or an org provider whose base URL is OpenRouter's.
func (h *Handlers) aggregatorProvider(orgCfg *tenant.OrgConfig, providerID string) bool {
	if h.registry.IsManaged(providerID) {
		for _, mp := range h.registry.ManagedProviders() {
			if mp.ID == providerID {
				return mp.Type == openRouterType
			}
		}
	}
	pc := orgProviderConfig(orgCfg, providerID)
	return pc != nil && strings.Contains(strings.ToLower(pc.BaseURL), "openrouter.ai")
}

// orgProviderConfig is the org's own enabled, credentialed config for
// providerID, or nil.
func orgProviderConfig(orgCfg *tenant.OrgConfig, providerID string) *tenant.ProviderConfig {
	if !orgHasCredentials(orgCfg, providerID) {
		return nil
	}
	return orgCfg.Providers[providerID]
}

// applyVerdict gives an allowed route the verdict's data policy, its
// endpoint set (a managed candidate's ranked eligible endpoints, which the
// aggregator is pinned to and the credit hold is reserved at; none
// otherwise) and, for a deprecated model, the header naming its expiration
// date.
func (h *Handlers) applyVerdict(rc *models.RequestContext, v *eligibility.Verdict) {
	rc.Routing.Effective = v.Effective
	if v.Deprecated != "" {
		rc.SetResponseHeader(headerModelDeprecated, v.Deprecated)
	}
	set := v.EndpointSet(eligibility.MaxEndpointSet)
	if len(set) == 0 {
		rc.Billing.EndpointSet, rc.Billing.EndpointID, rc.Billing.UpstreamProvider = nil, "", ""
		return
	}
	snapshotID := h.catalog.Load().ID()
	refs := make([]models.EndpointRef, len(set))
	for i, e := range set {
		refs[i] = models.EndpointRef{ID: e.ID, UpstreamProvider: e.UpstreamProvider, Price: eligibility.PriceCard(e, snapshotID)}
	}
	rc.Billing.EndpointSet = refs
	rc.Billing.EndpointID, rc.Billing.UpstreamProvider = refs[0].ID, refs[0].UpstreamProvider
}
