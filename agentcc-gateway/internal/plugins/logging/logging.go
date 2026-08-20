package logging

import (
	"context"
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Plugin is a post-response pipeline plugin that asynchronously logs trace records.
type Plugin struct {
	emitter     *TraceEmitter
	flusher     *LogFlusher
	cfg         config.RequestLoggingConfig
	redactor    *privacy.Redactor
	tenantStore *tenant.Store
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
	orgRedactor := p.tenantStore.Redactor(rc.Metadata[tenant.MetadataKeyOrgID])

	// Apply redaction: org redactor (with org patterns) takes priority over global.
	// The flag goes on the record, not on rc: this runs inside the parallel
	// post-plugin window where rc is read-only, and the record was already
	// copied above — so marking rc could never have reached the emitted log.
	if orgRedactor != nil && orgRedactor.ShouldRedact() && record.RequestBody != nil {
		record = redactRecord(record, orgRedactor, mode)
		markRedacted(&record)
	} else if p.redactor != nil && p.redactor.ShouldRedact() && record.RequestBody != nil {
		record = redactRecord(record, p.redactor, mode)
		markRedacted(&record)
	}

	p.emitter.Emit(record)

	if p.flusher != nil {
		p.flusher.Enqueue(record)
	}

	return pipeline.ResultContinue()
}

// markRedacted records on the log record that its bodies were redacted.
// buildRecord leaves Metadata nil when the request carried none.
func markRedacted(record *TraceRecord) {
	if record.Metadata == nil {
		record.Metadata = make(map[string]string, 1)
	}
	record.Metadata["privacy_redacted"] = "true"
}

// Close drains buffered trace records and stops workers.
func (p *Plugin) Close() {
	if p.emitter != nil {
		p.emitter.Close()
	}
}
