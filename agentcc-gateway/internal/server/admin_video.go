package server

import (
	"encoding/json"
	"io"
	"net/http"
	"sort"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/audit"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
)

func (s *Server) adminVideoCapabilities(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}
	caps, _ := capability.NewRegistry().Service("byteplus")
	modelsOut := make([]map[string]any, 0, len(caps.Models))
	for _, m := range caps.Models {
		ops := map[string]any{}
		for name, spec := range m.Operations {
			ops[string(name)] = map[string]any{"roles": spec.Roles, "required_roles": spec.RequiredRoles, "aspect_ratios": spec.AspectRatios}
		}
		inputs := map[string]any{}
		for role, spec := range m.Inputs {
			inputs[string(role)] = map[string]any{"min_count": spec.MinCount, "max_count": spec.MaxCount, "max_bytes": spec.MaxBytes, "max_pixels": spec.MaxPixels, "min_dimension": spec.MinDimension, "max_dimension": spec.MaxDimension, "min_ratio": spec.MinRatio, "max_ratio": spec.MaxRatio, "formats": spec.Formats}
		}
		options := map[string]any{}
		for name, spec := range m.Options {
			options[name] = map[string]any{"type": spec.Type, "values": spec.Values, "min": spec.Min, "max": spec.Max, "step": spec.Step, "offset": spec.Offset, "default": spec.Default}
		}
		var auto any
		if m.AutoDuration != nil {
			auto = map[string]any{"value": m.AutoDuration.Value, "bounded": m.AutoDuration.Bounded}
		}
		var tariff any
		if m.Tariff != nil {
			tariff = map[string]any{"unit": m.Tariff.Unit, "revision": m.Tariff.Revision}
		}
		modelsOut = append(modelsOut, map[string]any{"model_id": m.ModelID, "operations": ops, "durations": map[string]any{"min": m.Durations.Min, "max": m.Durations.Max, "enum": m.Durations.Enum}, "auto_duration": auto, "resolutions": m.Resolutions, "aspect_ratios": m.AspectRatios, "fps": m.FPS, "max_outputs": m.MaxOutputs, "native_multi_output": m.NativeMultiOutput, "audio": map[string]any{"generated": m.Audio.Generated, "input_refs": m.Audio.InputRefs, "audio_only": m.Audio.AudioOnly}, "inputs": inputs, "prompt_limit": map[string]any{"unit": m.PromptLimit.Unit, "max": m.PromptLimit.Max}, "provider_options": options, "tariff": tariff, "access": m.Access})
	}
	sort.Slice(modelsOut, func(i, j int) bool { return modelsOut[i]["model_id"].(string) < modelsOut[j]["model_id"].(string) })
	record := map[string]any{"service": caps.Service, "revision": caps.Revision, "regions": caps.Regions, "models": modelsOut, "configured": s.cfg.Video.Providers["byteplus"].Enabled, "submit_idempotency": caps.SubmitIdempotency, "correlation_lookup": caps.CorrelationLookup, "cancel_support": caps.CancelSupport, "output_acl": caps.OutputACL, "output_url_expiry_seconds": caps.OutputURLExpiry.Seconds(), "output_url_refresh": caps.OutputURLRefresh, "bills_on_failure": caps.BillsOnFailure, "webhook": caps.Webhook, "poll_min_interval_seconds": caps.PollMinInterval.Seconds()}
	videoJSON(w, 200, map[string]any{"object": "list", "data": []any{record}})
}
func adminVideoBody(w http.ResponseWriter, r *http.Request, out any) bool {
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096))
	d.DisallowUnknownFields()
	if e := d.Decode(out); e != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_request", "Invalid admin video request"))
		return false
	}
	if e := d.Decode(new(any)); e != io.EOF {
		models.WriteError(w, models.ErrBadRequest("invalid_request", "One JSON object is required"))
		return false
	}
	return true
}
func (s *Server) adminVideoKillSwitch(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) || !s.handlers.videoReady(w) {
		return
	}
	var body struct {
		Enabled *bool `json:"enabled"`
	}
	if !adminVideoBody(w, r, &body) {
		return
	}
	if body.Enabled == nil {
		models.WriteError(w, models.ErrBadRequest("invalid_request", "enabled is required"))
		return
	}
	if e := s.handlers.videoService.SetKillSwitch(r.Context(), *body.Enabled); e != nil {
		videoError(w, e)
		return
	}
	s.emitVideoAudit(r, "video.killswitch", "video:v1:killswitch", map[string]string{"enabled": strconv.FormatBool(*body.Enabled)})
	videoJSON(w, 200, map[string]any{"enabled": *body.Enabled})
}
func (s *Server) adminVideoAttach(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) || !s.handlers.videoReady(w) {
		return
	}
	var body struct {
		ProviderJobID string `json:"provider_job_id"`
	}
	if !adminVideoBody(w, r, &body) {
		return
	}
	id := r.URL.Query().Get("id")
	j, e := s.handlers.videoService.Attach(r.Context(), id, body.ProviderJobID)
	if e != nil {
		videoError(w, e)
		return
	}
	s.emitVideoAudit(r, "video.attach", id, map[string]string{"org_id": j.OrgID, "provider_job_id": j.ProviderJobID, "status": j.Status, "settlement_only": strconv.FormatBool(j.IsTerminal())})
	videoJSON(w, 200, j.ToStatusResponse())
}
func (s *Server) adminVideoUnsettled(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) || !s.handlers.videoReady(w) {
		return
	}
	jobs, e := s.handlers.videoService.Unsettled(r.Context())
	if e != nil {
		videoError(w, e)
		return
	}
	sort.Slice(jobs, func(i, j int) bool { return jobs[i].ID < jobs[j].ID })
	rows := make([]map[string]any, 0, len(jobs))
	for _, j := range jobs {
		row := j.ToStatusResponse()
		row["org_id"] = j.OrgID
		row["provider_job_id"] = j.ProviderJobID
		row["reserved_micros"] = j.ReservedMicros
		row["settled_micros"] = j.SettledMicros
		row["settlement_state"] = j.SettlementState
		row["deleted"] = j.DeletedAt != nil
		delete(row, "metadata")
		rows = append(rows, row)
	}
	videoJSON(w, 200, map[string]any{"object": "list", "data": rows, "total": len(rows)})
}
func (s *Server) emitVideoAudit(r *http.Request, action, id string, metadata map[string]string) {
	s.videoAuditOnce.Do(func() {
		if s.videoAudit == nil {
			cfg := s.cfg.Audit
			// Financial operator actions are always audited, including on gateways
			// where optional request auditing or category filters are disabled.
			cfg.Categories = nil
			cfg.MinSeverity = "info"
			s.videoAudit = audit.NewLogger(cfg)
		}
	})
	s.videoAudit.Emit(&audit.Event{Category: "video", Action: action, Severity: "info", Actor: audit.Actor{Type: "admin", ID: "admin-token"}, Resource: &audit.Resource{Type: "video", ID: id}, Outcome: "success", RequestID: models.GetRequestID(r.Context()), Metadata: metadata})
}
