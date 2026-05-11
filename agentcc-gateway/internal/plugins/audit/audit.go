package audit

import (
	"context"

	auditpkg "github.com/futureagi/agentcc-gateway/internal/audit"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// Plugin emits audit events as a pipeline plugin.
// Priority 900: runs last so all metadata from other plugins is available.
type Plugin struct {
	logger  *auditpkg.Logger
	enabled bool
}

// New creates a new audit plugin.
func New(logger *auditpkg.Logger, enabled bool) *Plugin {
	return &Plugin{
		logger:  logger,
		enabled: enabled,
	}
}

func (p *Plugin) Name() string     { return "audit" }
func (p *Plugin) Priority() int    { return 900 }
func (p *Plugin) IsPostParallel() bool { return true } // Async event emitter, safe to parallelize.

// ProcessRequest emits pre-request audit events.
func (p *Plugin) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.enabled || p.logger == nil {
		return pipeline.ResultContinue()
	}

	actor := buildActor(rc)
	resource := buildResource(rc)

	// Emit request_started event.
	p.logger.Emit(&auditpkg.Event{
		Category:  "request",
		Action:    "request_started",
		Severity:  "info",
		Actor:     actor,
		Resource:  resource,
		Outcome:   "started",
		RequestID: rc.RequestID,
		Metadata:  copyMetadata(rc, "rbac_role", "rbac_team", "budget_remaining"),
	})

	// Emit budget warning if present.
	if rc.Metadata["budget_warning"] == "true" {
		sev := "warn"
		outcome := "warning"
		if rc.Metadata["budget_blocked_by"] != "" {
			sev = "error"
			outcome = "denied"
		}
		p.logger.Emit(&auditpkg.Event{
			Category:  "budget",
			Action:    "budget_checked",
			Severity:  sev,
			Actor:     actor,
			Resource:  resource,
			Outcome:   outcome,
			Reason:    "budget_blocked_by:" + rc.Metadata["budget_blocked_by"],
			RequestID: rc.RequestID,
			Metadata:  copyMetadata(rc, "budget_remaining", "budget_blocked_by"),
		})
	}

	return pipeline.ResultContinue()
}

// ProcessResponse emits post-response audit events.
func (p *Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.enabled || p.logger == nil {
		return pipeline.ResultContinue()
	}

	actor := buildActor(rc)
	resource := buildResource(rc)

	// Determine outcome.
	outcome := "success"
	severity := "info"
	reason := ""

	if len(rc.Errors) > 0 {
		outcome = "error"
		severity = "error"
		reason = rc.Errors[0].Error()
	}

	if rc.Flags.GuardrailTriggered {
		p.logger.Emit(&auditpkg.Event{
			Category:  "guardrail",
			Action:    "guardrail_triggered",
			Severity:  "warn",
			Actor:     actor,
			Resource:  resource,
			Outcome:   "triggered",
			RequestID: rc.RequestID,
			Metadata:  copyMetadata(rc, "guardrail_triggered", "guardrail_action"),
		})
	}

	// Emit request_completed.
	meta := copyMetadata(rc, "cost", "budget_remaining", "budget_warning", "rbac_role")
	p.logger.Emit(&auditpkg.Event{
		Category:  "request",
		Action:    "request_completed",
		Severity:  severity,
		Actor:     actor,
		Resource:  resource,
		Outcome:   outcome,
		Reason:    reason,
		RequestID: rc.RequestID,
		Metadata:  meta,
	})

	return pipeline.ResultContinue()
}

// Close drains the audit logger.
func (p *Plugin) Close() {
	if p.logger != nil {
		p.logger.Close()
	}
}

func buildActor(rc *models.RequestContext) auditpkg.Actor {
	return auditpkg.Actor{
		Type:  "api_key",
		ID:    rc.Metadata["auth_key_id"],
		Name:  rc.Metadata["auth_key_name"],
		Owner: rc.Metadata["auth_key_owner"],
		Team:  rc.Metadata["key_team"],
		Role:  rc.Metadata["rbac_role"],
		IP:    rc.Metadata["client_ip"],
	}
}

func buildResource(rc *models.RequestContext) *auditpkg.Resource {
	if rc.Model == "" && rc.Provider == "" {
		return nil
	}
	return &auditpkg.Resource{
		Type:     "model",
		ID:       rc.Model,
		Provider: rc.Provider,
	}
}

func copyMetadata(rc *models.RequestContext, keys ...string) map[string]string {
	m := make(map[string]string)
	for _, k := range keys {
		if v := rc.Metadata[k]; v != "" {
			m[k] = v
		}
	}
	if len(m) == 0 {
		return nil
	}
	return m
}
