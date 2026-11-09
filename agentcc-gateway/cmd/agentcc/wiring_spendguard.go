package main

import (
	"fmt"
	"log/slog"
	"time"

	alertpkg "github.com/futureagi/agentcc-gateway/internal/alerting"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
	"github.com/futureagi/agentcc-gateway/internal/models"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/plugins/spendguard"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// initSpendGuard wires the managed spend guard (plan G17): the pre-plugin
// that admits each managed request against the velocity windows, the new-org
// cap and the pauses before the credits reserve, its anomaly alerts, and the
// admin route that clears an auto-pause. The windows are shared by every
// replica, so it needs Redis; without it the credits ledger is memory-only
// (tests) or fails closed, and the guard is off.
//
// An organization's own alert channels receive its anomaly alerts through the
// guarded egress dialer built from mcp.egress.private_allow, as its other
// alerts do (buildAlertingPlugin, SSRF-F3): an org-set URL on a loopback,
// private or metadata address is refused unless the operator listed it. The
// operator's own channels are built from config.yaml and keep a plain client.
func initSpendGuard(d *wiringDeps) error {
	if d.redis == nil {
		if len(d.catalogSources) > 0 {
			slog.Warn("managed spend guard is off: it needs Redis")
		}
		return nil
	}
	orgEgress, err := egress.New(egress.Options{PrivateAllow: d.cfg.MCP.Egress.PrivateAllow})
	if err != nil {
		return fmt.Errorf("spend guard alert egress dialer: %w", err)
	}
	m := d.cfg.Managed
	guard := spendguard.NewGuard(redisstate.NewSpendGuardStore(d.redis, ""), m.SpendGuard)
	// The same managed.* values the credits plugin holds with (initCredits).
	est := &spendguard.Estimator{Settings: creditsplugin.Settings{
		MarkupPercent:              m.MarkupPercent,
		HoldTTL:                    m.HoldTTL,
		ReserveDefaultOutputTokens: m.ReserveDefaultOutputTokens,
		UnpricedReserveUSD:         m.UnpricedReserveUSD,
		AccountMissingRetryAfter:   m.AccountMissingRetryAfter,
	}}
	d.plugins = append(d.plugins, spendguard.New(guard, est.Estimate))

	d.afterServer(func() error {
		h, srv := d.handlers, d.server
		// Read per call: main installs the injected prices only after every
		// afterServer callback has run.
		est.Prices = func(providerID, model, endpointID string) (models.PriceCard, bool) {
			return h.Deps().Prices(providerID, model, endpointID)
		}
		est.MaxOutput = func(providerID, model string) int {
			db := h.ModelDB()
			if db == nil {
				return 0
			}
			if info, ok := db.Get(providerID + "/" + model); ok {
				return info.MaxOutputTokens
			}
			if info, ok := db.Get(model); ok {
				return info.MaxOutputTokens
			}
			return 0
		}
		est.OrgMaxTokens = h.OrgDefaultMaxTokensEstimate
		guard.SetOrgConfig(func(orgID string) *tenant.OrgConfig {
			if srv.TenantStore == nil {
				return nil
			}
			return srv.TenantStore.Get(orgID)
		})
		guard.SetAlerter(func(ev spendguard.AnomalyEvent) {
			h.Deps().Events(spendguard.EventKindAnomaly, ev)
			if channels, names := anomalyChannels(d.cfg.Alerting, srv.TenantStore, orgEgress, ev.OrgID); len(names) > 0 {
				go sendAnomalyAlert(channels, names, ev)
			}
		})
		srv.SetSpendGuard(guard)
		return nil
	})
	return nil
}

// anomalyChannels are the alert channels an org's spend anomaly goes to, by
// name, and their names in order: the operator's (alerting.channels) when
// alerting is on, with a plain client, and the org's own when its alerting is
// on, named "org:<name>" so the two never collide. The org's webhook and
// slack channels send through orgEgress's callback client, so an org-set URL
// cannot reach the operator's internal network (SSRF-F3); a nil orgEgress is
// the guard with no operator exceptions.
func anomalyChannels(global config.AlertingConfig, tenants *tenant.Store, orgEgress *egress.Dialer, orgID string) (map[string]alertpkg.Channel, []string) {
	channels := map[string]alertpkg.Channel{}
	var names []string
	add := func(name string, ch alertpkg.Channel) {
		if _, dup := channels[name]; !dup {
			names = append(names, name)
		}
		channels[name] = ch
	}
	if global.Enabled {
		for _, ch := range global.Channels {
			switch ch.Type {
			case "webhook":
				add(ch.Name, alertpkg.NewWebhookChannel(ch.URL, ch.Headers))
			case "slack":
				add(ch.Name, alertpkg.NewSlackChannel(ch.URL))
			case "log":
				add(ch.Name, &alertpkg.LogChannel{})
			default:
				slog.Warn("unknown alert channel type", "name", ch.Name, "type", ch.Type)
			}
		}
	}
	if tenants == nil {
		return channels, names
	}
	org := tenants.Get(orgID)
	if org == nil || org.Alerting == nil || !org.Alerting.Enabled {
		return channels, names
	}
	client := orgEgress.CallbackClient(alertpkg.ChannelTimeout)
	for _, ch := range org.Alerting.Channels {
		if ch == nil {
			continue
		}
		name := "org:" + ch.Name
		switch ch.Type {
		case "webhook":
			add(name, alertpkg.NewWebhookChannelWithClient(client, ch.URL, ch.Headers))
		case "slack":
			add(name, alertpkg.NewSlackChannelWithClient(client, ch.URL))
		case "log":
			add(name, &alertpkg.LogChannel{})
		default:
			slog.Warn("unknown org alert channel type", "org_id", orgID, "name", ch.Name, "type", ch.Type)
		}
	}
	return channels, names
}

// sendAnomalyAlert delivers ev to the named channels through the alerting
// package, as a rule named managed_spend.anomaly over the metric that tripped
// (labelled with the org, so the operator's channels can tell orgs apart), at
// the threshold it crossed.
func sendAnomalyAlert(channels map[string]alertpkg.Channel, names []string, ev spendguard.AnomalyEvent) {
	metric, value, threshold := "managed_spend_usd_5m", ev.Spend5mUSD, max(1, 10*ev.TrailingAvg5mUSD)
	if ev.Reason == spendguard.AnomalyHourlyThreshold {
		metric, value, threshold = "managed_spend_usd_1h", ev.Spend1hUSD, ev.AlertUSDPerHour
	}
	metric = fmt.Sprintf("%s{org_id=%q,auto_paused=\"%t\"}", metric, ev.OrgID, ev.AutoPaused)
	rule := alertpkg.NewRule(spendguard.EventKindAnomaly, metric, ">", threshold, 5*time.Minute, 0, names)
	mgr := alertpkg.NewManagerWithChannels([]*alertpkg.Rule{rule}, channels)
	mgr.Record(metric, value)
	mgr.Evaluate()
}
