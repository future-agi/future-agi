package main

import (
	"encoding/json"
	"fmt"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/capabilities"
	"github.com/futureagi/agentcc-gateway/internal/capability"
	"github.com/futureagi/agentcc-gateway/internal/toolcatalog"
)

// initCapability builds the capability core when tools.enabled is set: the
// adapters the operator config registers, the tool price cards, the
// operator's managed vendor credentials and the orgs' BYOK ones, the
// managed-credit store and the tool markup. It installs the core for
// capability.Do, the publisher plugin that writes each call's attempts and
// billing after the cost plugin, the core's cells on the capability matrix,
// and the core on the server's REST capability handlers. With tools off,
// nothing is installed and every capability route answers 404.
//
// The tool catalog is loaded once and is the core's CardSource. With tools
// enabled, a dangling mcp.managed_servers price_card, a managed server that
// names a byok_only card, or a managed operator provider for a byok_only
// vendor fails startup. With tools off, the managed servers' price cards are
// still resolved, but operator providers are not judged as tool credentials:
// an LLM provider that shares a tool vendor's id (replicate) stays a model
// provider until a provider kind tells the two apart.
func initCapability(d *wiringDeps) error {
	if !d.cfg.Tools.Enabled {
		if len(d.cfg.MCP.ManagedServers) == 0 {
			return nil
		}
		servers := *d.cfg
		servers.Providers = nil
		_, _, err := toolcatalog.LoadForConfig(&servers)
		return err
	}
	cat, _, err := toolcatalog.LoadForConfig(d.cfg)
	if err != nil {
		return err
	}
	reg := capability.NewRegistry()
	if err := capability.RegisterConfigured(reg, d.cfg); err != nil {
		return err
	}
	cards, err := cardSourceOf(cat)
	if err != nil {
		return err
	}

	var ledger billing.Ledger
	if d.orgLedger != nil {
		ledger = d.orgLedger.Ledger()
	}
	m := d.cfg.Managed
	core := capability.NewCore(capability.Options{
		Registry:                 reg,
		Cards:                    cards,
		Credentials:              capability.ConfigCredentials{Operator: d.cfg.Providers},
		Ledger:                   ledger,
		MarkupBps:                m.ToolMarkupBps(),
		ProcessingFeeBps:         m.ProcessingFeeBps,
		HoldTTL:                  m.HoldTTL,
		AccountMissingRetryAfter: m.AccountMissingRetryAfter,
		ReserveDefaultUSD:        d.cfg.Tools.ReserveDefaultUSD,
		CacheTTL:                 time.Duration(d.cfg.Tools.CacheTTLSeconds) * time.Second,
		Timeouts:                 d.cfg.Capabilities.Timeout,
	})
	capability.Install(core)
	capabilities.SetToolSource(core.ToolCells)
	d.plugins = append(d.plugins, capability.NewPlugin())
	d.afterServer(func() error {
		d.server.SetCapabilityCore(core)
		return nil
	})
	return nil
}

// cardSourceOf is the capability core's view of the loaded tool catalog.
// Both packages decode the same tool-price-card-v1 shape, so each
// card goes through its JSON form once, at startup, and the core never
// imports the catalog.
func cardSourceOf(cat *toolcatalog.Catalog) (*capability.StaticCards, error) {
	all := cat.Cards()
	cards := make([]*capability.Card, 0, len(all))
	for _, c := range all {
		raw, err := json.Marshal(c)
		if err != nil {
			return nil, fmt.Errorf("tool catalog: card %s/%s: %w", c.Capability, c.Vendor, err)
		}
		parsed, err := capability.CardsFromJSON(raw)
		if err != nil {
			return nil, fmt.Errorf("tool catalog: card %s/%s: %w", c.Capability, c.Vendor, err)
		}
		cards = append(cards, parsed...)
	}
	return capability.NewStaticCards(cards...), nil
}
