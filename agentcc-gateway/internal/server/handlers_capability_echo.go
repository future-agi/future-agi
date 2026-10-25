package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/capability"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// echoRequest is the body of POST /v1/capabilities/echo.
type echoRequest struct {
	// Provider is "auto", one vendor or a list of vendors; it narrows the
	// org's order, never widens it.
	Provider json.RawMessage `json:"provider,omitempty"`
	// Input is sent to the vendor as it is, and echoed back.
	Input json.RawMessage `json:"input"`
	// Units is how many units the call is held and priced for (default 1):
	// a whole number, since echo's SKUs count requests.
	Units float64 `json:"units,omitempty"`
}

// CapabilityEcho handles POST /v1/capabilities/echo, the test capability:
// 404 unless the operator configured capabilities.echo with tools.enabled;
// otherwise capability.Do runs as the pipeline's provider call, so the call
// is key-authenticated, held, settled, logged and traced like every
// capability call.
func (h *Handlers) CapabilityEcho(w http.ResponseWriter, r *http.Request) {
	core := h.capabilityCore()
	if core == nil || !core.Serves(capability.EchoCapability) {
		models.WriteError(w, models.ErrNotFound("not_found", "Not found"))
		return
	}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.EndpointType = core.EndpointType(capability.EchoCapability)
	rc.RequestHeaders = cloneRequestHeaders(r)
	// rc.Model stays empty until capability.Do names it, after the pre
	// phase: a model name here would put the call under the auth plugin's
	// model allowlist and RBAC's models:<name> (handlers_search.go keeps
	// rc.Model empty for the same reason). A call the pre phase refuses is
	// therefore not logged yet.

	in, apiErr := h.readEchoRequest(r)
	if apiErr != nil {
		models.WriteError(w, apiErr)
		return
	}
	providers, apiErr := parseProviderField(in.Provider)
	if apiErr != nil {
		models.WriteError(w, apiErr)
		return
	}
	orgCfg, ok := h.capabilityPreamble(w, r, rc)
	if !ok {
		return
	}
	req := capability.Request{Org: orgCfg, Providers: providers, Units: in.Units, Body: in.Input}

	ctx, cancel := context.WithTimeout(r.Context(), core.Timeout(capability.EchoCapability))
	defer cancel()
	var res capability.Result
	if err := h.engine.Process(ctx, rc, capabilityCall(core, capability.EchoCapability, req, &res)); err != nil {
		writeCapabilityError(w, rc, err)
		return
	}
	writeCapabilityResult(w, rc, capability.EchoCapability, res)
}

// readEchoRequest reads and checks the body.
func (h *Handlers) readEchoRequest(r *http.Request) (echoRequest, *models.APIError) {
	var in echoRequest
	body, err := io.ReadAll(io.LimitReader(r.Body, h.maxBodySize+1))
	if err != nil {
		return in, models.ErrBadRequest("read_error", "Failed to read request body")
	}
	if int64(len(body)) > h.maxBodySize {
		return in, &models.APIError{Status: http.StatusRequestEntityTooLarge, Type: models.ErrTypeInvalidRequest,
			Code: "request_too_large", Message: fmt.Sprintf("Request body exceeds maximum size of %d bytes", h.maxBodySize)}
	}
	if err := json.Unmarshal(body, &in); err != nil {
		return in, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+err.Error())
	}
	if len(in.Input) == 0 {
		return in, models.ErrBadRequest("missing_input", "input is required")
	}
	if in.Units < 0 || in.Units != math.Trunc(in.Units) {
		return in, models.ErrBadRequest("invalid_units", "units must be a whole number, not negative")
	}
	return in, nil
}

// parseProviderField reads a capability request's provider field: absent or
// "auto" for the org's order, a vendor id, or a list of vendor ids.
func parseProviderField(raw json.RawMessage) ([]string, *models.APIError) {
	if len(raw) == 0 || string(raw) == "null" {
		return nil, nil
	}
	var one string
	if json.Unmarshal(raw, &one) == nil {
		return []string{one}, nil
	}
	var list []string
	if json.Unmarshal(raw, &list) == nil {
		return list, nil
	}
	return nil, models.ErrBadRequest("invalid_provider", `provider must be "auto", a vendor id or a list of vendor ids`)
}
