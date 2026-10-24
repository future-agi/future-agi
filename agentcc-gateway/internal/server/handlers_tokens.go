package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/modeldb/orgdb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/futureagi/agentcc-gateway/internal/tokenizer"
)

// CountTokens handles POST /v1/count_tokens.
// Counts tokens for a given model + messages without making an LLM call.
func (h *Handlers) CountTokens(w http.ResponseWriter, r *http.Request) {
	// Parse request body.
	body, err := io.ReadAll(io.LimitReader(r.Body, h.maxBodySize+1))
	if err != nil {
		models.WriteError(w, models.ErrBadRequest("read_error", "Failed to read request body"))
		return
	}
	if int64(len(body)) > h.maxBodySize {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusRequestEntityTooLarge,
			Type:    models.ErrTypeInvalidRequest,
			Code:    "request_too_large",
			Message: fmt.Sprintf("Request body exceeds maximum size of %d bytes", h.maxBodySize),
		})
		return
	}

	var req models.CountTokensRequest
	if err := json.Unmarshal(body, &req); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+err.Error()))
		return
	}

	if req.Model == "" {
		models.WriteError(w, models.ErrBadRequest("missing_model", "model is required"))
		return
	}
	if len(req.Messages) == 0 {
		models.WriteError(w, models.ErrBadRequest("missing_messages", "messages is required and must not be empty"))
		return
	}

	// Estimate token count.
	tokenCount := tokenizer.EstimateTokens(req.Messages)

	resp := models.CountTokensResponse{
		Model:      req.Model,
		TokenCount: tokenCount,
	}

	// Look up model context window from modelDB if available. The key's org's
	// Model Database max_input_tokens replaces it: that is the limit
	// the validation plugin holds the org's requests to.
	maxInput, known := 0, false
	if db := h.ModelDB(); db != nil {
		if info, ok := db.Get(req.Model); ok {
			maxInput, known = info.MaxInputTokens, true
		}
	}
	if limit := orgdb.MaxInputTokens(h.countTokensOverride(r, req.Model)); limit > 0 {
		maxInput, known = limit, true
	}
	if known {
		resp.MaxContextTokens = maxInput
		if maxInput > 0 {
			remaining := maxInput - tokenCount
			if remaining < 0 {
				remaining = 0
			}
			resp.RemainingTokens = remaining
		}
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(resp)
}

// countTokensOverride is the Model Database override the request's key's org
// has for model, or nil, matched as chat matches it (orgdb.Override) on the
// org's own provider that serves model, when one does.
func (h *Handlers) countTokensOverride(r *http.Request, model string) *tenant.ModelOverrideOrgConfig {
	if h.tenantStore == nil || h.keyStore == nil {
		return nil
	}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	setAuthMetadataFromRequest(rc, r)
	key := h.lookupRequestKey(rc)
	if key == nil || key.Metadata["org_id"] == "" {
		return nil
	}
	orgCfg := h.tenantStore.Get(key.Metadata["org_id"])
	if orgCfg == nil || orgCfg.ModelDatabase == nil || len(orgCfg.ModelDatabase.Overrides) == 0 {
		return nil
	}
	return orgdb.Override(orgCfg, orgProviderServing(orgCfg, model), model)
}

// orgProviderServing is the id of an org provider holding credentials that
// lists model (orgModelMatches, as chat resolves it), or "". With several,
// the first in id order.
func orgProviderServing(orgCfg *tenant.OrgConfig, model string) string {
	best := ""
	for id, pc := range orgCfg.Providers {
		if pc == nil || !pc.Enabled || !pc.HasCredentials() || (best != "" && id > best) {
			continue
		}
		for _, m := range pc.Models {
			if orgModelMatches(m, model, id) {
				best = id
				break
			}
		}
	}
	return best
}
