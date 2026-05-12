package logging

import (
	"context"
	"log/slog"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Plugin is a post-response pipeline plugin that asynchronously logs trace records.
type Plugin struct {
	emitter      *TraceEmitter
	flusher      *LogFlusher
	cfg          config.RequestLoggingConfig
	redactor     *privacy.Redactor
	tenantStore  *tenant.Store
	orgRedactors sync.Map
}

// New creates a new logging plugin.
func New(cfg config.RequestLoggingConfig, tenantStore *tenant.Store) *Plugin {
	p := &Plugin{
		cfg:         cfg,
		tenantStore: tenantStore,
	}
	if cfg.Enabled {
		p.emitter = NewTraceEmitter(cfg)
		if cfg.IncludeBodies {
			slog.Warn("request body logging is enabled — request and response content will appear in logs")
		}
	}
	return p
}

func (p *Plugin) SetFlusher(f *LogFlusher) {
	p.flusher = f
}

func (p *Plugin) SetRedactor(r *privacy.Redactor) {
	p.redactor = r
}

func (p *Plugin) Name() string         { return "logging" }
func (p *Plugin) Priority() int        { return 900 }
func (p *Plugin) IsPostParallel() bool { return true } // Read-only observer, safe to parallelize.

// ProcessRequest is a no-op for the logging plugin.
func (p *Plugin) ProcessRequest(_ context.Context, _ *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// ProcessResponse builds a trace record from the request context and emits it asynchronously.
func (p *Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.cfg.Enabled || p.emitter == nil {
		return pipeline.ResultContinue()
	}

	// Skip logging for requests without a model (e.g., health checks that somehow enter the pipeline).
	if rc.Model == "" && rc.Request == nil {
		return pipeline.ResultContinue()
	}

	// Skip logging/webhook for internal service keys — billing is handled
	// at the Django activity level, not via gateway webhook.
	if rc.Metadata["key_type"] == "internal" {
		return pipeline.ResultContinue()
	}

	record := buildRecord(rc, p.cfg)

	// Determine effective privacy mode: per-org > per-key > global.
	// Fix key mismatch: handler sets "org_privacy_mode", read both keys.
	mode := rc.Metadata["privacy_mode"]
	if orgMode := rc.Metadata["org_privacy_mode"]; orgMode != "" {
		mode = orgMode
	}

	// Check per-org privacy config from tenant store for richer redaction (custom patterns).
	orgRedactor := p.getOrgRedactor(rc)

	// Apply redaction: org redactor (with org patterns) takes priority over global.
	if orgRedactor != nil && orgRedactor.ShouldRedact() && record.RequestBody != nil {
		record = redactRecord(record, orgRedactor, mode)
		rc.SetMetadata("privacy_redacted", "true")
	} else if p.redactor != nil && p.redactor.ShouldRedact() && record.RequestBody != nil {
		record = redactRecord(record, p.redactor, mode)
		rc.SetMetadata("privacy_redacted", "true")
	}

	p.emitter.Emit(record)

	if p.flusher != nil {
		p.flusher.Enqueue(record)
	}

	return pipeline.ResultContinue()
}

// getOrgRedactor returns a per-org privacy redactor if the org has privacy config.
func (p *Plugin) getOrgRedactor(rc *models.RequestContext) *privacy.Redactor {
	if p.tenantStore == nil {
		return nil
	}
	orgID := rc.Metadata[tenant.MetadataKeyOrgID]
	if orgID == "" {
		return nil
	}
	orgCfg := p.tenantStore.Get(orgID)
	if orgCfg == nil || orgCfg.Privacy == nil || !orgCfg.Privacy.Enabled {
		return nil
	}

	// Check cache first.
	if v, ok := p.orgRedactors.Load(orgID); ok {
		return v.(*privacy.Redactor)
	}

	// Build redactor from org patterns.
	patterns := make([]privacy.PatternConfig, 0, len(orgCfg.Privacy.Patterns))
	for _, pat := range orgCfg.Privacy.Patterns {
		if pat != nil && pat.Pattern != "" {
			patterns = append(patterns, privacy.PatternConfig{
				Name:    pat.Name,
				Pattern: pat.Pattern,
			})
		}
	}

	mode := orgCfg.Privacy.Mode
	if mode == "" {
		mode = privacy.ModePatterns
	}
	redactor := privacy.New(mode, patterns)
	p.orgRedactors.Store(orgID, redactor)
	return redactor
}

// InvalidateOrg removes the cached per-org redactor so updated privacy config is rebuilt.
func (p *Plugin) InvalidateOrg(orgID string) {
	if p == nil || orgID == "" {
		return
	}
	p.orgRedactors.Delete(orgID)
}

// Close drains buffered trace records and stops workers.
func (p *Plugin) Close() {
	if p.emitter != nil {
		p.emitter.Close()
	}
}
