package alerting

import (
	"context"
	"log/slog"
	"strconv"
	"sync"
	"time"

	alertpkg "github.com/futureagi/agentcc-gateway/internal/alerting"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Plugin is a pipeline plugin that records metrics and evaluates alert rules.
type Plugin struct {
	manager     *alertpkg.Manager
	enabled     bool
	tenantStore *tenant.Store
	// dialer guards the egress of an organization's own alert channels: an
	// org-set webhook or slack URL is dialled through it, so a loopback,
	// private or metadata address is refused unless the operator listed it
	// (mcp.egress.private_allow). nil is the guard with no
	// exceptions.
	dialer      *egress.Dialer
	orgManagers sync.Map // orgID -> *alertpkg.Manager (lazily built per-org managers)
	// orgCooldowns keeps each org rule's last firing across manager
	// rebuilds: every org config push (whichever section changed) drops the
	// org's manager, and a rule rebuilt with a fresh state would fire again
	// inside its cooldown.
	orgCooldowns sync.Map // orgID -> *orgCooldowns
}

// orgCooldowns is one org's rule cooldown states, keyed by rule name (and
// its occurrence, for two rules of one name).
type orgCooldowns struct {
	mu     sync.Mutex
	byRule map[string]*alertpkg.CooldownState
}

// New creates a new alerting plugin. dialer guards an organization's own
// alert channels; the operator's global channels (manager) are built from
// config.yaml and keep a plain client.
func New(manager *alertpkg.Manager, enabled bool, tenantStore *tenant.Store, dialer *egress.Dialer) *Plugin {
	return &Plugin{
		manager:     manager,
		enabled:     enabled,
		tenantStore: tenantStore,
		dialer:      dialer,
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

	// Build channels from org config. An org's webhook and slack channels
	// send through the guarded egress client, so an org-set URL reaches a
	// loopback or private address only when mcp.egress.private_allow lists
	// it, and never a metadata address.
	client := p.dialer.CallbackClient(alertpkg.ChannelTimeout)
	channels := make(map[string]alertpkg.Channel, len(alertCfg.Channels))
	for _, ch := range alertCfg.Channels {
		if ch == nil {
			continue
		}
		switch ch.Type {
		case "webhook":
			channels[ch.Name] = alertpkg.NewWebhookChannelWithClient(client, ch.URL, ch.Headers)
		case "slack":
			channels[ch.Name] = alertpkg.NewSlackChannelWithClient(client, ch.URL)
		case "log":
			channels[ch.Name] = &alertpkg.LogChannel{}
		default:
			slog.Warn("unknown org alert channel type", "org_id", orgID, "name", ch.Name, "type", ch.Type)
		}
	}

	// Build rules from org config. Each rule carries its severity
	// (default warning) and its cooldown (default 15m, tenant.
	// DefaultAlertCooldown), and shares its cooldown state with the rule of
	// the same name in the org's previous manager.
	rules := make([]*alertpkg.Rule, 0, len(alertCfg.Rules))
	keys := make([]string, 0, len(alertCfg.Rules))
	for _, rc := range alertCfg.Rules {
		if rc == nil {
			continue
		}
		keys = append(keys, rc.Name)
	}
	states := p.cooldownStates(orgID, keys)
	i := 0
	for _, rc := range alertCfg.Rules {
		if rc == nil {
			continue
		}
		window := parseDuration(rc.Window, time.Minute)
		severity, known := rc.AlertSeverity()
		if !known {
			slog.Warn("org alert rule severity not recognised; sent as the default",
				"org_id", orgID, "rule", rc.Name, "severity", rc.Severity, "default", tenant.DefaultAlertSeverity)
		}
		if d, err := time.ParseDuration(rc.Cooldown); rc.Cooldown != "" && (err != nil || d < 0) {
			slog.Warn("org alert rule cooldown is not a duration; using the default",
				"org_id", orgID, "rule", rc.Name, "cooldown", rc.Cooldown, "default", tenant.DefaultAlertCooldown.String())
		}
		cooldown := rc.AlertCooldown()
		rules = append(rules, alertpkg.NewRule(
			rc.Name, rc.Metric, rc.Condition, rc.Threshold,
			window, cooldown, rc.Channels,
		).WithSeverity(severity).WithCooldownState(states[i]))
		i++
	}

	mgr := alertpkg.NewManagerWithChannels(rules, channels)
	actual, _ := p.orgManagers.LoadOrStore(orgID, mgr)
	return actual.(*alertpkg.Manager)
}

// cooldownStates returns one cooldown state per rule key, in order: the
// state the org's previous manager used for the same key (rule name and its
// occurrence among rules of that name), else a new one. States of rules no
// longer configured are dropped.
func (p *Plugin) cooldownStates(orgID string, names []string) []*alertpkg.CooldownState {
	v, _ := p.orgCooldowns.LoadOrStore(orgID, &orgCooldowns{byRule: map[string]*alertpkg.CooldownState{}})
	oc := v.(*orgCooldowns)
	oc.mu.Lock()
	defer oc.mu.Unlock()
	seen := make(map[string]int, len(names))
	next := make(map[string]*alertpkg.CooldownState, len(names))
	out := make([]*alertpkg.CooldownState, len(names))
	for i, name := range names {
		key := name + "\x00" + strconv.Itoa(seen[name])
		seen[name]++
		st := oc.byRule[key]
		if st == nil {
			st = alertpkg.NewCooldownState()
		}
		next[key] = st
		out[i] = st
	}
	oc.byRule = next
	return out
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
