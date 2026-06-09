package alerting

import (
	"context"
	"log/slog"
	"strconv"
	"sync"
	"time"

	alertpkg "github.com/futureagi/agentcc-gateway/internal/alerting"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Plugin is a pipeline plugin that records metrics and evaluates alert rules.
type Plugin struct {
	manager     *alertpkg.Manager
	enabled     bool
	tenantStore *tenant.Store
	orgManagers sync.Map // orgID -> *alertpkg.Manager (lazily built per-org managers)
}

// New creates a new alerting plugin.
func New(manager *alertpkg.Manager, enabled bool, tenantStore *tenant.Store) *Plugin {
	return &Plugin{
		manager:     manager,
		enabled:     enabled,
		tenantStore: tenantStore,
	}
}

func (p *Plugin) Name() string         { return "alerting" }
func (p *Plugin) Priority() int        { return 997 }
func (p *Plugin) IsPostParallel() bool { return true } // Metrics recording, safe to parallelize.

// ProcessRequest is a no-op.
func (p *Plugin) ProcessRequest(_ context.Context, _ *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// ProcessResponse records request metrics and evaluates alert rules (global + per-org).
func (p *Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	// Collect metrics once.
	metrics := p.collectMetrics(rc)

	// Global alerting.
	if p.enabled && p.manager != nil {
		for metric, value := range metrics {
			p.manager.Record(metric, value)
		}
		p.manager.Evaluate()
	}

	// Per-org alerting.
	if p.tenantStore != nil {
		orgID := rc.Metadata[tenant.MetadataKeyOrgID]
		if orgID != "" {
			orgCfg := p.tenantStore.Get(orgID)
			if orgCfg != nil && orgCfg.Alerting != nil && orgCfg.Alerting.Enabled {
				orgMgr := p.getOrgManager(orgID, orgCfg.Alerting)
				if orgMgr != nil {
					for metric, value := range metrics {
						orgMgr.Record(metric, value)
					}
					orgMgr.Evaluate()
				}
			}
		}
	}

	return pipeline.ResultContinue()
}

// InvalidateOrg removes the cached per-org manager so updated config is rebuilt.
func (p *Plugin) InvalidateOrg(orgID string) {
	if p == nil || orgID == "" {
		return
	}
	p.orgManagers.Delete(orgID)
}

// collectMetrics gathers metric values from the request context.
func (p *Plugin) collectMetrics(rc *models.RequestContext) map[string]float64 {
	metrics := make(map[string]float64, 5)

	metrics["request_count"] = 1

	if len(rc.Errors) > 0 {
		metrics["error_count"] = 1
	}

	if rc.Response != nil && rc.Response.Usage != nil {
		total := rc.Response.Usage.PromptTokens + rc.Response.Usage.CompletionTokens
		if total > 0 {
			metrics["tokens_total"] = float64(total)
		}
	}

	if costStr, ok := rc.Metadata["cost"]; ok {
		if cost, err := strconv.ParseFloat(costStr, 64); err == nil && cost > 0 {
			metrics["cost_total"] = cost
		}
	}

	metrics["latency_avg"] = float64(rc.Elapsed().Milliseconds())

	return metrics
}

// getOrgManager returns a lazily-built alerting manager for an org.
func (p *Plugin) getOrgManager(orgID string, alertCfg *tenant.AlertingConfig) *alertpkg.Manager {
	if v, ok := p.orgManagers.Load(orgID); ok {
		return v.(*alertpkg.Manager)
	}

	// Build channels from org config.
	channels := make(map[string]alertpkg.Channel, len(alertCfg.Channels))
	for _, ch := range alertCfg.Channels {
		if ch == nil {
			continue
		}
		switch ch.Type {
		case "webhook":
			channels[ch.Name] = alertpkg.NewWebhookChannel(ch.URL, ch.Headers)
		case "slack":
			channels[ch.Name] = alertpkg.NewSlackChannel(ch.URL)
		case "log":
			channels[ch.Name] = &alertpkg.LogChannel{}
		default:
			slog.Warn("unknown org alert channel type", "org_id", orgID, "name", ch.Name, "type", ch.Type)
		}
	}

	// Build rules from org config.
	rules := make([]*alertpkg.Rule, 0, len(alertCfg.Rules))
	for _, rc := range alertCfg.Rules {
		if rc == nil {
			continue
		}
		window := parseDuration(rc.Window, time.Minute)
		cooldown := parseDuration(rc.Cooldown, 0)
		rules = append(rules, alertpkg.NewRule(
			rc.Name, rc.Metric, rc.Condition, rc.Threshold,
			window, cooldown, rc.Channels,
		))
	}

	mgr := alertpkg.NewManagerWithChannels(rules, channels)
	actual, _ := p.orgManagers.LoadOrStore(orgID, mgr)
	return actual.(*alertpkg.Manager)
}

// parseDuration parses a duration string, returning fallback on error.
func parseDuration(s string, fallback time.Duration) time.Duration {
	if s == "" {
		return fallback
	}
	d, err := time.ParseDuration(s)
	if err != nil {
		return fallback
	}
	return d
}
