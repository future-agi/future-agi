package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"slices"
	"sort"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// maxDecisionBodyBytes caps a decision body below the server-wide limit: a
// decision is a state and some questions, never megabytes of media.
const maxDecisionBodyBytes = 32 << 20

// decisionBodyLimit is the most a decision body may be.
func (h *Handlers) decisionBodyLimit() int64 {
	return min(h.maxBodySize, maxDecisionBodyBytes)
}

// SystemOne handles POST /v1/systemone: a System One body, forwarded byte for
// byte to a provider that speaks System One.
func (h *Handlers) SystemOne(w http.ResponseWriter, r *http.Request) {
	h.serveDecision(w, r, models.DecisionFormatSystemOne)
}

// Decisions handles POST /v1/decisions. The body picks the format: questions
// as an array is OpenAI Decisions, as an object System One, served exactly as
// /v1/systemone serves it.
func (h *Handlers) Decisions(w http.ResponseWriter, r *http.Request) {
	h.serveDecision(w, r, "")
}

// serveDecision runs one decision call. routeFormat is the only format the
// route takes, or "" for either.
func (h *Handlers) serveDecision(w http.ResponseWriter, r *http.Request, routeFormat string) {
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.EndpointType = "decision"

	// An internal caller's own provider key is for this one call. Take it off
	// the request before anything copies its headers for logs or spans.
	_, hasProviderKey := r.Header[http.CanonicalHeaderKey(models.ProviderKeyHeader)]
	providerKey := strings.TrimSpace(r.Header.Get(models.ProviderKeyHeader))
	r.Header.Del(models.ProviderKeyHeader)
	rc.RequestHeaders = cloneRequestHeaders(r)

	limit := h.decisionBodyLimit()
	body, err := io.ReadAll(io.LimitReader(r.Body, limit+1))
	if err != nil {
		models.WriteError(w, models.ErrBadRequest("read_error", "Failed to read request body"))
		return
	}
	if int64(len(body)) > limit {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusRequestEntityTooLarge,
			Type:    models.ErrTypeInvalidRequest,
			Code:    "request_too_large",
			Message: fmt.Sprintf("Request body exceeds maximum size of %d bytes", limit),
		})
		return
	}

	req, apiErr := models.ParseDecisionRequest(body)
	if apiErr != nil {
		models.WriteError(w, apiErr)
		return
	}
	if routeFormat != "" && req.Format != routeFormat {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusUnprocessableEntity,
			Type:    models.ErrTypeInvalidRequest,
			Code:    models.CodeFormatMismatch,
			Message: "questions is an array, which is the OpenAI Decisions format: send it to /v1/decisions. /v1/systemone takes questions as an object keyed by question id.",
		})
		return
	}

	rc.Model = req.Model
	rc.DecisionRequest = req
	rc.Metadata["client_ip"] = extractClientIP(r)
	setAuthMetadataFromRequest(rc, r)
	applyCallerMetadata(rc, r, nil)
	rc.Metadata["endpoint_type"] = "decision"
	rc.Metadata["decision_format"] = req.Format
	rc.Metadata["decision_question_count"] = strconv.Itoa(len(req.QuestionIDs))

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()
	rc.Metadata["timeout_ms"] = fmt.Sprintf("%d", timeout.Milliseconds())

	h.peekKeyOrgID(rc)
	orgID, orgCfg := h.resolveOrgConfig(rc)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}
	// Limits, budgets, cost tracking, IP rules and privacy apply as they do to
	// chat. Routing and model maps do not: resolveDecision is the only route.
	h.applyOrgRateLimitOverrides(orgCfg, rc)
	h.applyOrgBudgetOverrides(orgCfg, rc)
	h.applyOrgCostTrackingOverrides(orgCfg, rc)
	h.applyOrgIPACLOverrides(orgCfg, rc)
	h.applyOrgAlertingOverrides(orgCfg, rc)
	h.applyOrgPrivacyOverrides(orgCfg, rc)
	h.applyOrgAuditOverrides(orgCfg, rc)
	h.applyOrgModelDatabaseOverrides(orgCfg, rc)

	target, err := h.resolveDecision(r.Context(), r.Header, rc, req, providerKey, hasProviderKey, orgID, orgCfg)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}
	rc.Provider = target.providerID
	req.SetUpstreamModel(target.upstreamModel)

	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		// The auth plugin has run by now. Check again what it learned.
		if rc.Metadata["auth_type"] == "license" {
			return errBYOKRequired("Decision models run only on your own provider key; a managed service token cannot call them.")
		}
		if err := checkDecisionProviderACL(rc); err != nil {
			return err
		}
		resp, err := target.send(ctx, rc.DecisionRequest)
		if err != nil {
			return err
		}
		if resp == nil {
			return models.ErrInternal("no response from provider")
		}
		rc.DecisionResponse = resp
		rc.ResolvedModel = resp.Model
		if rc.ResolvedModel == "" {
			rc.ResolvedModel = rc.DecisionRequest.UpstreamModel
		}
		// Cost, budget, prometheus and logging read Response.Usage. The
		// decision itself stays off the chat-shaped response.
		rc.Response = &models.ChatCompletionResponse{
			Model: rc.ResolvedModel,
			Usage: &models.Usage{
				PromptTokens:     resp.Usage.InputTokens,
				CompletionTokens: resp.Usage.OutputTokens,
				TotalTokens:      resp.Usage.InputTokens + resp.Usage.OutputTokens,
			},
		}
		return nil
	}

	if err := h.engine.Process(ctx, rc, providerCall); err != nil {
		models.WriteErrorFromError(w, err)
		return
	}
	if rc.DecisionResponse == nil {
		models.WriteError(w, models.ErrInternal("no response from provider"))
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(rc.DecisionResponse.Body)
}

// checkDecisionProviderACL enforces a key's allowed providers. The auth plugin
// records them inside the pipeline, so the check runs in the provider call.
func checkDecisionProviderACL(rc *models.RequestContext) error {
	if rc.Metadata["key_type"] == "internal" {
		return nil
	}
	allowed, ok := rc.Metadata["auth_allowed_providers"]
	if !ok || slices.Contains(splitCSV(allowed), rc.Provider) {
		return nil
	}
	return models.ErrForbidden("API key does not have access to provider: " + rc.Provider)
}

// decisionModel is one entry of GET /v1/decisions/models.
type decisionModel struct {
	ID       string                `json:"id"`
	Object   string                `json:"object"`
	Provider string                `json:"provider"`
	Format   string                `json:"format"`
	Pricing  *decisionModelPricing `json:"pricing,omitempty"`
}

type decisionModelPricing struct {
	InputPerMTok  float64 `json:"input_per_mtok"`
	OutputPerMTok float64 `json:"output_per_mtok"`
}

// DecisionModels handles GET /v1/decisions/models: the decision models the
// caller can use, from its org's own provider credentials (config.yaml's
// providers when auth is off). An internal key has no org and lists none.
func (h *Handlers) DecisionModels(w http.ResponseWriter, r *http.Request) {
	if middleware.IsLicenseAuthorized(r.Context()) {
		models.WriteError(w, errBYOKRequired("Decision models run only on your own provider key; a managed service token cannot list them."))
		return
	}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	setAuthMetadataFromRequest(rc, r)

	entries := []decisionModel{}
	if h.keyStore == nil {
		entries = h.registryDecisionModels()
	} else {
		h.peekKeyOrgID(rc)
		if orgID, orgCfg := h.resolveOrgConfig(rc); orgID != "" && orgCfg != nil && rc.Metadata["key_type"] != "internal" {
			entries = h.orgDecisionModels(orgCfg)
		}
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].ID < entries[j].ID })

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]any{"object": "list", "data": entries})
}

// orgDecisionModels lists the decision models among an org's enabled,
// credentialed providers or keyless Generic endpoints. Every System One model
// qualifies; OpenAI models must be known decision models. The format comes from
// the provider's config: listing builds no provider, and leaves out one whose
// build its config alone shows would be refused.
func (h *Handlers) orgDecisionModels(orgCfg *tenant.OrgConfig) []decisionModel {
	entries := []decisionModel{}
	if h.orgProviderCache == nil {
		return entries
	}
	db := h.ModelDB()
	for id, pcfg := range orgCfg.Providers {
		if !h.orgDecisionProviderEnabled(id, pcfg) {
			continue
		}
		apiFormat, ok := h.orgProviderCache.APIFormat(id, pcfg)
		if !ok {
			continue
		}
		format := apiFormatDecisionFormat(apiFormat, id, pcfg.BaseURL)
		for _, model := range pcfg.Models {
			if entry, ok := newDecisionModel(db, orgCfg, id, model, format); ok {
				entries = append(entries, entry)
			}
		}
	}
	return entries
}

// registryDecisionModels lists config.yaml's decision models.
func (h *Handlers) registryDecisionModels() []decisionModel {
	entries := []decisionModel{}
	db := h.ModelDB()
	for _, m := range h.registry.ListAllModels() {
		p, ok := h.registry.GetProvider(m.OwnedBy)
		if !ok {
			continue
		}
		if entry, ok := newDecisionModel(db, nil, m.OwnedBy, m.ID, decisionFormatOf(p, m.OwnedBy, "")); ok {
			entries = append(entries, entry)
		}
	}
	return entries
}

// newDecisionModel builds a listing entry, priced from the org's custom
// pricing first and the bundled decision prices after.
func newDecisionModel(db *modeldb.ModelDB, orgCfg *tenant.OrgConfig, providerID, model, format string) (decisionModel, bool) {
	if format == "" {
		return decisionModel{}, false
	}
	var known *modeldb.PricingInfo
	if db != nil {
		known, _ = db.DecisionPricing(model)
	}
	if format == models.DecisionFormatOpenAI && known == nil {
		// An OpenAI provider lists chat models too; only decision ones count.
		return decisionModel{}, false
	}
	entry := decisionModel{
		ID:       providerID + "/" + stripProviderPrefix(model, providerID),
		Object:   "model",
		Provider: providerID,
		Format:   format,
	}
	if orgCfg != nil && orgCfg.CostTracking != nil && orgCfg.CostTracking.Enabled {
		for _, name := range []string{model, entry.ID} {
			if custom := orgCfg.CostTracking.CustomPricing[name]; custom != nil {
				entry.Pricing = &decisionModelPricing{InputPerMTok: custom.InputPerMTok, OutputPerMTok: custom.OutputPerMTok}
				return entry, true
			}
		}
	}
	if known != nil {
		// Rounded: a per-token price scaled back up picks up float noise.
		perMTok := func(v float64) float64 { return math.Round(v*1e9) / 1e9 }
		entry.Pricing = &decisionModelPricing{InputPerMTok: perMTok(known.InputPerMTok()), OutputPerMTok: perMTok(known.OutputPerMTok())}
	}
	return entry, true
}
