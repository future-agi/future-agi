package server

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
)

// CreateEmbedding handles POST /v1/embeddings.
//
// An embedding request is served by the one target it resolves to, and
// never fails over to another model or host: vectors from a different
// model, or from a host whose weights are not shown to match, live in
// another space, so a failed target returns its error, with the request's
// attribution. It makes exactly one upstream attempt.
func (h *Handlers) CreateEmbedding(w http.ResponseWriter, r *http.Request) {
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.EndpointType = "embedding"
	rc.RequestHeaders = cloneRequestHeaders(r)
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

	var req models.EmbeddingRequest
	if err := json.Unmarshal(body, &req); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+err.Error()))
		return
	}
	if req.Model == "" {
		models.WriteError(w, models.ErrBadRequest("missing_model", "model is required"))
		return
	}
	if req.InputType != "" && !models.ValidEmbeddingInputType(req.InputType) {
		models.WriteError(w, inputTypeError(errInvalidInputType,
			fmt.Sprintf("input_type %q is not one of query, document, classification, clustering", req.InputType)))
		return
	}

	rc.Model = req.Model
	rc.EmbeddingRequest = &req
	// The checks on the request read its text (rc.PassThrough, in the chat
	// shape), and a check's rewrite of it goes into the input, which the provider
	// gets; X-Guardrail-Policy applies here as on chat.
	rc.PassThrough = requestview.Embedding(&req)

	setAuthMetadataFromRequest(rc, r)
	applyCallerMetadata(rc, r, nil)
	readGuardrailPolicyHeader(rc, r)

	// Resolve timeout and apply.
	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()
	rc.Metadata["timeout_ms"] = fmt.Sprintf("%d", timeout.Milliseconds())

	// Resolve per-org config.
	h.peekKeyOrgID(rc)
	orgID, orgCfg := h.resolveOrgConfig(rc)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}
	// Agent, run and step come only from the typed agent context, which
	// needs the org config for its framework aliases.
	if apiErr := h.parseAgentContext(rc, r, nil); apiErr != nil {
		models.WriteError(w, apiErr)
		return
	}
	h.applyOrgModelMapOverrides(orgCfg, rc)

	// Resolve provider.
	provider, upstreamModel, err := h.resolveProvider(ctx, rc, req.Model)
	if err != nil {
		if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
			if orgP, providerID, upstream := h.resolveOrgProvider(rc, orgID, orgCfg, req.Model); orgP != nil {
				provider = orgP
				rc.Provider = providerID
				rc.Metadata["org_provider"] = "true"
				rc.Metadata["billing_mode"] = billingBYOK // the org's own key: free to it, still metered
				req.Model = upstream
				err = nil
			}
		}
		if err != nil {
			models.WriteErrorFromError(w, err)
			return
		}
	} else {
		req.Model = upstreamModel
		if shouldApplyOrgProviderOverride(rc) {
			resolved := provider
			provider = h.applyOrgProviderOverride(orgID, orgCfg, rc.Provider, provider)
			if provider != resolved {
				rc.Metadata["billing_mode"] = billingBYOK
			}
		}
	}
	if apiErr := h.keyProviderACL(rc); apiErr != nil {
		models.WriteError(w, apiErr)
		return
	}

	// Type-assert to EmbeddingProvider.
	ep, ok := provider.(providers.EmbeddingProvider)
	if !ok {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_supported",
			Message: fmt.Sprintf("Provider %q does not support embeddings", rc.Provider),
		})
		return
	}
	if apiErr := inputTypeSupported(ep, rc.Provider, &req); apiErr != nil {
		models.WriteError(w, apiErr)
		return
	}

	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		return h.embedOnce(ctx, rc, ep)
	}

	if err := h.engine.Process(ctx, rc, providerCall); err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if rc.EmbeddingResponse == nil {
		models.WriteError(w, models.ErrInternal("no response from provider"))
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set(headerEmbeddingSpace, embeddingSpace(rc.Provider, rc.EmbeddingRequest, rc.EmbeddingResponse))
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(rc.EmbeddingResponse)
}

// embedOnce makes the request's one upstream attempt on the resolved target
// and records it. When the upstream reported no usage, a provider that can
// estimate it fills the response's usage with its estimate, marked
// usage_estimated, so the cost plugin never prices a paid call at $0; the
// caller then sees the estimate as the usage.
func (h *Handlers) embedOnce(ctx context.Context, rc *models.RequestContext, ep providers.EmbeddingProvider) error {
	req := rc.EmbeddingRequest
	attemptCtx, finish := h.beginAttempt(ctx, rc, models.AttemptPrimary, "", rc.Provider, req.Model, credentialType(rc))
	resp, err := ep.CreateEmbedding(attemptCtx, req)
	if err == nil && resp == nil {
		err = models.ErrInternal("no response from provider")
	}
	if err != nil {
		finish(nil, nil, err)
		return err
	}
	estimated := false
	if resp.Usage == nil {
		if est, ok := ep.(embeddingUsageEstimator); ok {
			resp.Usage = est.EstimateEmbeddingUsage(req)
			estimated = resp.Usage != nil
		}
	}
	var usage *models.Usage
	if resp.Usage != nil {
		usage = &models.Usage{PromptTokens: resp.Usage.PromptTokens, TotalTokens: resp.Usage.TotalTokens}
	}
	finish(resp, usage, nil)
	if estimated {
		// The cost plugin reads the request's flag; the attempt carries its
		// own, so the log marks the estimate even with cost tracking off.
		rc.Metadata["usage_estimated"] = "true"
		if attempts := rc.AttemptsSnapshot(); len(attempts) > 0 {
			rc.UpdateAttempt(attempts[len(attempts)-1].AttemptID, func(a *models.Attempt) { a.UsageEstimated = true })
		}
	}
	rc.EmbeddingResponse = resp
	return nil
}

// embeddingUsageEstimator is an embedding provider whose upstream may report
// no usage, and which can estimate it from the request (the Gemini adapter).
type embeddingUsageEstimator interface {
	EstimateEmbeddingUsage(req *models.EmbeddingRequest) *models.EmbeddingUsage
}

// embeddingInputTyper is an embedding provider that sends a request's
// input_type per request (Cohere's input_type, Gemini's taskType). Any other
// has no such parameter.
type embeddingInputTyper interface {
	AcceptsEmbeddingInputType() bool
}

// The embedding input_type refusals.
const (
	errInvalidInputType     = "invalid_input_type"
	errInputTypeUnsupported = "input_type_unsupported"
)

// inputTypeSupported refuses an input_type the provider has no parameter
// for (OpenAI's embeddings, for one), rather than dropping it silently: 400
// input_type_unsupported, before the upstream is called.
func inputTypeSupported(ep providers.EmbeddingProvider, providerID string, req *models.EmbeddingRequest) *models.APIError {
	if req.InputType == "" {
		return nil
	}
	if it, ok := ep.(embeddingInputTyper); ok && it.AcceptsEmbeddingInputType() {
		return nil
	}
	return inputTypeError(errInputTypeUnsupported,
		fmt.Sprintf("model %q on provider %q takes no input_type; send the request without it", req.Model, providerID))
}

func inputTypeError(code, message string) *models.APIError {
	param := "input_type"
	return &models.APIError{Status: http.StatusBadRequest, Type: models.ErrTypeInvalidRequest, Code: code, Message: message, Param: &param}
}

// headerEmbeddingSpace names the vector space an embeddings response's
// vectors live in, for callers to store beside their index.
const headerEmbeddingSpace = "x-agentcc-embedding-space"

// embeddingSpace is the x-agentcc-embedding-space value of resp:
// <model>@<dims>:<dtype>:<input_type>. The model is the one the request
// named, without the "<provider>/" routing prefix of the provider that
// served it; any other slash is part of the model id (an OpenAI-compatible
// upstream's "BAAI/bge-m3") and is kept, so two models never print the same
// space. dims is the first vector's length; dtype is float (the only one
// served); input_type is the request's, or "default" when it sent none and
// the provider's own default applied.
func embeddingSpace(providerID string, req *models.EmbeddingRequest, resp *models.EmbeddingResponse) string {
	inputType := req.InputType
	if inputType == "" {
		inputType = "default"
	}
	dims := 0
	if len(resp.Data) > 0 {
		dims = vectorLength(resp.Data[0].Embedding)
	}
	model := strings.TrimPrefix(req.Model, providerID+"/")
	return model + "@" + strconv.Itoa(dims) + ":float:" + inputType
}

// vectorLength is the number of values in one embedding: the elements of a
// JSON array of numbers, or the float32 values a base64 string encodes.
func vectorLength(raw json.RawMessage) int {
	raw = bytes.TrimSpace(raw)
	switch {
	case len(raw) >= 2 && raw[0] == '[':
		inner := bytes.TrimSpace(raw[1 : len(raw)-1])
		if len(inner) == 0 {
			return 0
		}
		return bytes.Count(inner, []byte(",")) + 1
	case len(raw) >= 2 && raw[0] == '"':
		var s string
		if json.Unmarshal(raw, &s) != nil {
			return 0
		}
		decoded, err := base64.StdEncoding.DecodeString(s)
		if err != nil {
			return 0
		}
		return len(decoded) / 4
	}
	return 0
}
