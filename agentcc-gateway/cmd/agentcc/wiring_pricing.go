package main

import (
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
)

// initPricing wires pricing: the cost plugin's landed-cost rates at init,
// and once the server runs, one pricer that reads the gateway's injected
// price lookup at call time. That pricer prices the cost plugin's attempts
// and is the gateway's injected `priced`, so no consumer imports the cost
// package. It adds no plugin: main builds the cost plugin.
func initPricing(d *wiringDeps) error {
	plugin := costPluginIn(d.plugins)
	if plugin != nil && d.cfg != nil {
		plugin.SetFees(costplugin.Fees{
			ProcessingFeeBps: d.cfg.Managed.ProcessingFeeBps,
			UpstreamFeeBps:   upstreamFeesBps(d),
		})
	}
	d.afterServer(func() error {
		h := d.handlers
		pricer := costplugin.NewPricer(h.ModelDB, func() models.PriceLookup { return h.Deps().Prices })
		if plugin != nil {
			plugin.SetPricer(pricer)
		}
		d.priced = pricer.Priced
		return nil
	})
	return nil
}

// costPluginIn returns the cost plugin among plugins, or nil.
func costPluginIn(plugins []pipeline.Plugin) *costplugin.Plugin {
	for _, p := range plugins {
		if cp, ok := p.(*costplugin.Plugin); ok {
			return cp
		}
	}
	return nil
}

// upstreamFeesBps is every configured provider's effective
// providers.<id>.upstream_fee_bps.
func upstreamFeesBps(d *wiringDeps) map[string]int {
	fees := make(map[string]int, len(d.cfg.Providers))
	for id, p := range d.cfg.Providers {
		pc := p
		fees[id] = pc.EffectiveUpstreamFeeBps()
	}
	return fees
}
