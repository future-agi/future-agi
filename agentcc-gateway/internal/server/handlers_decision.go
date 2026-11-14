package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"math"
	"net/http"
	"slices"
	"sort"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
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
	rc.Metadata["client_ip"] = h.clientIP(r)
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

	// Decision bodies are forwarded byte for byte. Input guardrails get a
	// separate chat-shaped view, never the body that goes to the provider. It
	// is built only when an input guardrail may apply, and never for the
	// platform's own internal-key traffic (the Jev evaluator judging traces).
	var guardrailContent json.RawMessage
	if h.decisionInputGuardrailsApply(rc, orgCfg) {
		content, apiErr := decisionGuardrailContent(req.Body)
		if apiErr != nil {
			models.WriteError(w, apiErr)
			return
		}
		guardrailContent = content
		rc.GuardrailRequest = &models.ChatCompletionRequest{Messages: []models.Message{{
			Role: "user", Content: guardrailContent,
		}}}
	}

	target, err := h.resolveDecision(r.Context(), r.Header, rc, req, providerKey, hasProviderKey, orgID, orgCfg)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}
	rc.Provider = target.providerID
	req.SetUpstreamModel(target.upstreamModel)

	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		// A masking/redacting guardrail may mutate its chat-shaped view, but
		// cannot rewrite the decision body without changing its wire format.
		if guardrailContent != nil && decisionGuardrailRewritten(rc, guardrailContent) {
			rc.Flags.GuardrailTriggered = true
			rc.Metadata["guardrail_action"] = "blocked"
			return decisionRedactionError()
		}
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
		// A blocking redaction guardrail stops the pipeline before providerCall;
		// still explain why the decision body cannot be sent unchanged. Any
		// other error (a rate limit, budget, auth) is returned as it is.
		if guardrailContent != nil && isGuardrailBlock(err) && decisionGuardrailRewritten(rc, guardrailContent) {
			err = decisionRedactionError()
		}
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

func decisionGuardrailRewritten(rc *models.RequestContext, original json.RawMessage) bool {
	return rc.GuardrailRequest == nil || len(rc.GuardrailRequest.Messages) != 1 ||
		!bytes.Equal(rc.GuardrailRequest.Messages[0].Content, original)
}

func decisionRedactionError() *models.APIError {
	return models.ErrGuardrailBlocked("content_blocked", "redaction is not applied to decisions; request blocked")
}

// isGuardrailBlock reports whether err is a guardrail's content block.
func isGuardrailBlock(err error) bool {
	var apiErr *models.APIError
	return errors.As(err, &apiErr) && apiErr.Type == models.ErrTypeGuardrail && apiErr.Status == http.StatusForbidden
}

// decisionInputGuardrailsApply reports whether input guardrails may check
// this decision: a static (config.yaml) input rule, or an enabled org check
// that runs on input. Org checks count only when a guardrail plugin runs: it
// reports each check's stage, and without its callback any enabled check
// counts.
// Internal-key traffic is the platform's own (the Jev evaluator judges traces
// that may hold PII or injection text on purpose), so it is not checked; org
// keys, BYOK and managed, are.
func (h *Handlers) decisionInputGuardrailsApply(rc *models.RequestContext, orgCfg *tenant.OrgConfig) bool {
	if key := h.lookupRequestKey(rc); key != nil && key.KeyType == "internal" {
		return false
	}
	if h.guardrailEngine != nil && h.guardrailEngine.PreCount() > 0 {
		return true
	}
	if orgCfg == nil || orgCfg.Guardrails == nil {
		return false
	}
	if h.orgInputGuardrails != nil {
		// The guardrail plugin knows each check's stage.
		return h.orgInputGuardrails(rc.Metadata["org_id"])
	}
	// No guardrail plugin in the pipeline (guardrails.enabled is false): no
	// org check will run, so none applies.
	if h.engine == nil || h.engine.Plugin("guardrails") == nil {
		return false
	}
	for _, check := range orgCfg.Guardrails.Checks {
		if check != nil && check.Enabled {
			return true
		}
	}
	return false
}

// maxDecisionTextDepth bounds how deeply a decision field is read for the
// guardrail view. Real criteria nest a few levels; a body nested deeper is
// refused rather than sent on with text the guardrails never saw.
const maxDecisionTextDepth = 64

// decisionGuardrailContent is the text the input guardrails check: every
// top-level field but model and questions, and every question field but its
// type and name (the rule models.DecisionContentField shares with the vendor
// error filter), read in key order so the same body gives the same text.
// Strings are decoded (\uXXXX escapes included), object keys inside a value
// are text too ({"<criterion>": "<description>"}), and numbers are kept as
// written. The body is decoded once and walked once, with a depth cap. It is
// never used to build or rewrite the upstream body.
func decisionGuardrailContent(body []byte) (json.RawMessage, *models.APIError) {
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.UseNumber()
	var fields map[string]any
	if err := dec.Decode(&fields); err != nil {
		return nil, models.ErrBadRequest("invalid_json", "request body must be a single JSON object")
	}
	w := decisionTextWalk{}
	for _, key := range slices.Sorted(maps.Keys(fields)) {
		if key == "questions" {
			continue
		}
		if models.DecisionContentField(key, false) {
			w.walk(fields[key])
		}
	}
	appendQuestion := func(q any) {
		question, ok := q.(map[string]any)
		if !ok {
			w.walk(q)
			return
		}
		for _, key := range slices.Sorted(maps.Keys(question)) {
			if models.DecisionContentField(key, true) {
				w.walk(question[key])
			}
		}
	}
	switch questions := fields["questions"].(type) {
	case map[string]any:
		for _, id := range slices.Sorted(maps.Keys(questions)) {
			appendQuestion(questions[id])
		}
	case []any:
		for _, q := range questions {
			appendQuestion(q)
		}
	}
	if w.tooDeep {
		return nil, models.ErrBadRequest("decision_too_deep", fmt.Sprintf(
			"A decision field is nested more than %d levels deep, too deep for the gateway's guardrails to check. Flatten it.", maxDecisionTextDepth))
	}
	content, _ := json.Marshal(strings.Join(w.parts, "\n"))
	return content, nil
}

// decisionTextWalk collects the text of decoded JSON values, iteratively and
// depth-first in key order.
type decisionTextWalk struct {
	parts   []string
	tooDeep bool
}

func (w *decisionTextWalk) walk(root any) {
	type frame struct {
		value any
		depth int
	}
	stack := []frame{{root, 1}}
	for len(stack) > 0 && !w.tooDeep {
		f := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		switch v := f.value.(type) {
		case string:
			w.parts = append(w.parts, v)
		case json.Number:
			w.parts = append(w.parts, v.String())
		case []any, map[string]any:
			if f.depth >= maxDecisionTextDepth {
				w.tooDeep = true
				return
			}
			if list, ok := v.([]any); ok {
				for i := len(list) - 1; i >= 0; i-- {
					stack = append(stack, frame{list[i], f.depth + 1})
				}
				continue
			}
			obj := v.(map[string]any)
			keys := slices.Sorted(maps.Keys(obj))
			for i := len(keys) - 1; i >= 0; i-- {
				stack = append(stack, frame{obj[keys[i]], f.depth + 1}, frame{keys[i], f.depth})
			}
		}
	}
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
		key := h.lookupRequestKey(rc)
		if orgID, orgCfg := h.resolveOrgConfig(rc); orgID != "" && orgCfg != nil && key != nil && key.KeyType != "internal" {
			entries = h.orgDecisionModels(orgCfg)
			entries = slices.DeleteFunc(entries, func(entry decisionModel) bool {
				return !key.CanAccessProvider(entry.Provider) || !key.CanAccessModel(entry.ID)
			})
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
		baseURL := h.orgProviderCache.BaseURL(id, pcfg)
		format := apiFormatDecisionFormat(apiFormat, id, baseURL)
		openRouterChat := providers.IsOpenRouterChat(apiFormat, id, baseURL)
		for _, model := range pcfg.Models {
			if openRouterChat && !isOpenRouterDecisionModel(db, stripProviderPrefix(model, id)) {
				continue
			}
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
		_, format, _, ok := h.registryDecisionFormat(m.OwnedBy, stripProviderPrefix(m.ID, m.OwnedBy))
		if !ok {
			continue
		}
		if entry, ok := newDecisionModel(db, nil, m.OwnedBy, m.ID, format); ok {
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
	// A Models entry may carry the provider's own prefix
	// ("openrouter/typesafe/jev-1.13"); the call sends it upstream without it.
	bare := stripProviderPrefix(model, providerID)
	id := providerID + "/" + bare
	var known *modeldb.PricingInfo
	if db != nil {
		// The names a call of this entry goes by, in the cost plugin's order
		// (decisionModelNames: upstream, then provider-qualified), so the
		// listing shows the price it charges.
		known, _ = db.DecisionPricingAny(bare, id)
	}
	if format == models.DecisionFormatOpenAI && known == nil {
		// An OpenAI provider lists chat models too; only decision ones count.
		return decisionModel{}, false
	}
	entry := decisionModel{
		ID:       id,
		Object:   "model",
		Provider: providerID,
		Format:   format,
	}
	if orgCfg != nil && orgCfg.CostTracking != nil && orgCfg.CostTracking.Enabled {
		// The cost plugin's order (decisionOrgPriceNames) for a call of this
		// entry by its listed id: that id, then the name sent upstream. Only
		// a dated resolved name, which a listing cannot know, comes first.
		for _, name := range []string{entry.ID, bare} {
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
