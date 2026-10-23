package logging

import (
	"context"
	"log/slog"
	"sync"
	"time"

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

// guardrailWarningsMetadata is the gateway-owned metadata key the guardrail
// plugin joins its warn-action messages into ("<name>: <message>; ...",
// guardrails/plugin.go applyMetadata). The messages can name tools, quote
// matched content or carry a judge's reading of the prompt, so content mode
// none drops the key with the results' messages.
const guardrailWarningsMetadata = "guardrail_warnings"

// ProcessResponse builds a trace record from the request context and emits it asynchronously.
func (p *Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.cfg.Enabled || p.emitter == nil {
		return pipeline.ResultContinue()
	}

	// A request without a model is logged only on an API whose requests may
	// name none (loggedWithoutModel); any other (a health check that somehow
	// enters the pipeline) is not.
	if rc.Model == "" && rc.Request == nil && !loggedWithoutModel(rc.EndpointType) {
		return pipeline.ResultContinue()
	}

	// Internal service keys stay out of the request log: Django bills their
	// callers at the activity level, and ingesting them would also emit
	// gateway usage events for the same calls.
	if rc.Metadata["key_type"] == "internal" {
		return pipeline.ResultContinue()
	}

	record := buildRecord(rc, p.cfg)
	content := p.contentMode(rc)

	// An empty key_log_content only says the key sets no content mode. The
	// early key peek copies it on every endpoint and only the chat step 5
	// removes it, so it is dropped from the record's own metadata copy.
	if v, ok := record.Metadata[privacy.KeyLogContentRequestMetadata]; ok && v == "" {
		delete(record.Metadata, privacy.KeyLogContentRequestMetadata)
	}

	// Only full keeps bodies, so only full has anything to redact.
	if content == models.ContentModeFull {
		p.redact(&record, rc)
	}
	// none keeps no error text, in the trace line either (the webhook entry
	// drops it in buildLogEntry), and no guardrail messages, which can name
	// tools or quote matched content: neither in the results nor in the
	// warnings joined into metadata. The record owns its results' copy and
	// its metadata map, which nothing else reads until Emit below.
	if content == models.ContentModeNone {
		record.ErrorMessage = ""
		for i := range record.GuardrailResults {
			record.GuardrailResults[i].Message = ""
		}
		delete(record.Metadata, guardrailWarningsMetadata)
	}

	p.emitter.Emit(record)

	if p.flusher != nil {
		p.flusher.EnqueueRequest(record, rc, LogEntryOptions{IncludeBodies: content == models.ContentModeFull, ContentMode: content})
	}

	return pipeline.ResultContinue()
}

// loggedWithoutModel reports whether endpoint is an API whose requests are
// logged though they may name no model: the Assistants, Vector Stores and
// Files APIs, which the gateway passes through (a list, a retrieval, a
// vector store or a file names none). Each request on them has a request
// log entry, as on the model endpoints.
func loggedWithoutModel(endpoint string) bool {
	switch endpoint {
	case "assistants", "vector_stores", "files":
		return true
	}
	return false
}

// contentMode is the request's content mode: the one the
// chat pipeline resolved at step 5, else, for an endpoint that has no step 5,
// the one the same sources give now, with the key's log_content as the auth
// plugin copied it. The fallback reads the org's stored config, not a rollout
// candidate (step 4 does not run either); a candidate shares stable's privacy
// (tenant.buildCandidate replaces only routing and managed_models), so the
// mode is the same, and TestRolloutCandidateSharesContentMode pins it.
func (p *Plugin) contentMode(rc *models.RequestContext) models.ContentMode {
	if rc.Privacy.ContentMode != "" {
		return rc.Privacy.ContentMode
	}
	src := privacy.ContentModeSources{
		Key:     rc.Metadata[privacy.KeyLogContentRequestMetadata],
		Request: privacy.RequestContentMode(rc.RequestHeaders),
		Default: privacy.DefaultContentMode(),
	}
	if p.tenantStore != nil {
		orgID := rc.Metadata["org_id"]
		if orgID == "" {
			orgID = rc.Metadata[tenant.MetadataKeyOrgID]
		}
		if orgID != "" {
			src.Org = p.tenantStore.Get(orgID).ContentMode()
		}
	}
	return privacy.ResolveContentMode(src).ContentMode
}

// redact applies privacy redaction to the record's bodies: the org's redactor
// (with its own patterns) takes priority over the global one. The flag goes
// on the record, not on rc: this runs inside the parallel post-plugin window
// where rc is read-only, and the record is already a copy.
func (p *Plugin) redact(record *TraceRecord, rc *models.RequestContext) {
	if record.RequestBody == nil {
		return
	}
	// Determine effective privacy mode: per-org > per-key > global.
	// The handler sets "org_privacy_mode"; both keys are read.
	mode := rc.Metadata["privacy_mode"]
	if orgMode := rc.Metadata["org_privacy_mode"]; orgMode != "" {
		mode = orgMode
	}
	r := p.tenantStore.Redactor(rc.Metadata[tenant.MetadataKeyOrgID])
	if r == nil || !r.ShouldRedact() {
		r = p.redactor
	}
	if r != nil && r.ShouldRedact() {
		*record = redactRecord(*record, r, mode)
		markRedacted(record)
	}
}

// markRedacted records on the log record that its bodies were redacted.
// buildRecord leaves Metadata nil when the request carried none.
func markRedacted(record *TraceRecord) {
	if record.Metadata == nil {
		record.Metadata = make(map[string]string, 1)
	}
	record.Metadata["privacy_redacted"] = "true"
}

// shutdownFlushTimeout bounds Close's last attempt to deliver request logs,
// which runs alongside the emitter's drain. For the request logs to be sent
// before SIGKILL, a stopping gateway needs
//
//	grace ≥ preStop + shutdown_timeout + max(shutdownFlushTimeout, emitterDrainTimeout)
//
// where grace and preStop are the Helm chart's
// agentccGateway.terminationGracePeriodSeconds and preStopSleepSeconds, and
// shutdown_timeout is the gateway's server.shutdown_timeout. The closers that
// run after Plugin.Close (audit, OTel) are not covered.
const shutdownFlushTimeout = 4 * time.Second

// Close drains buffered trace records and stops workers while it makes a
// last, bounded attempt to deliver the request logs still buffered for the
// webhook.
func (p *Plugin) Close() {
	var wg sync.WaitGroup
	if p.flusher != nil {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ctx, cancel := context.WithTimeout(context.Background(), shutdownFlushTimeout)
			defer cancel()
			p.flusher.Close(ctx)
		}()
	}
	if p.emitter != nil {
		p.emitter.Close()
	}
	wg.Wait()
}
