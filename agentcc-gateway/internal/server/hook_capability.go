package server

import (
	"context"
	"encoding/json"
	"net/http"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/capability"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// capabilityCores holds each server's capability core. Routes are
// registered when the server is built and the core only once the wiring
// runs (SetCapabilityCore), so handlers look it up per call. Keyed by the
// handlers, so servers built side by side (tests) never share one.
var capabilityCores sync.Map // *Handlers -> *capability.Core

// SetCapabilityCore installs the capability core the REST capability
// handlers call through.
func (s *Server) SetCapabilityCore(core *capability.Core) {
	if core == nil {
		capabilityCores.Delete(s.handlers)
		return
	}
	capabilityCores.Store(s.handlers, core)
}

func (h *Handlers) capabilityCore() *capability.Core {
	v, _ := capabilityCores.Load(h)
	core, _ := v.(*capability.Core)
	return core
}

// capabilityPreamble runs what a REST capability call shares with a chat
// call before its pipeline: the caller's metadata, the runtime identity, the
// agent context (a call that names no step is a tool step), the org and its
// config, and the content mode. It returns false once it has written a
// refusal.
func (h *Handlers) capabilityPreamble(w http.ResponseWriter, r *http.Request, rc *models.RequestContext) (*tenant.OrgConfig, bool) {
	setAuthMetadataFromRequest(rc, r)
	applyCallerMetadata(rc, r, nil)
	if apiErr := h.runtimeIdentity(rc); apiErr != nil {
		models.WriteError(w, apiErr)
		return nil, false
	}
	if apiErr := h.parseAgentContext(rc, r, nil); apiErr != nil {
		models.WriteError(w, apiErr)
		return nil, false
	}
	if a := &rc.Agent; a.IdentitySource != "" {
		if a.Step == "" {
			a.Step, a.StepSource = models.StepTool, stepSourceInferred
		}
		rc.Metadata[metaAgentTagged] = "true"
	}
	if rc.UserID == "" {
		rc.UserID = rc.Agent.UserID
	}
	h.peekKeyOrgID(rc)
	orgID, orgCfg := h.resolveOrgConfig(rc)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}
	h.resolveContentMode(rc, orgCfg)
	return orgCfg, true
}

// capabilityCall is a REST capability's provider call in the pipeline:
// capability.Do on core, so the budget, credits, logging and tracing plugins
// run around it. The result is kept in out.
func capabilityCall(core *capability.Core, name string, req capability.Request, out *capability.Result) pipeline.ProviderFunc {
	return func(ctx context.Context, rc *models.RequestContext) error {
		res, err := core.Do(ctx, rc, name, req)
		if err != nil {
			return err
		}
		*out = res
		return nil
	}
}

// capabilityResponse is a REST capability response: the vendor's result and
// the usage block.
type capabilityResponse struct {
	Object     string           `json:"object"`
	Capability string           `json:"capability"`
	Provider   string           `json:"provider"`
	Data       json.RawMessage  `json:"data"`
	Usage      capability.Usage `json:"usage"`
}

// writeCapabilityResult writes a served call with the capability headers.
// It carries no latency, health or cost header of the gateway's own, so a
// no_perf_disclosure vendor's figures never reach the client.
func writeCapabilityResult(w http.ResponseWriter, rc *models.RequestContext, name string, res capability.Result) {
	rc.ApplyResponseHeaders(w.Header())
	data := res.Body
	if len(data) == 0 {
		data = json.RawMessage("null")
	}
	writeJSON(w, http.StatusOK, capabilityResponse{Object: "capability.result", Capability: name,
		Provider: res.Provider, Data: data, Usage: res.Usage})
}

// writeCapabilityError writes a refused or failed call, with the capability
// headers of a call that got as far as a vendor.
func writeCapabilityError(w http.ResponseWriter, rc *models.RequestContext, err error) {
	rc.ApplyResponseHeaders(w.Header())
	models.WriteErrorFromError(w, err)
}
