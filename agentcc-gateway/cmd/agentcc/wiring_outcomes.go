package main

import (
	"github.com/futureagi/agentcc-gateway/internal/models"
	outcomesplugin "github.com/futureagi/agentcc-gateway/internal/plugins/outcomes"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/routing/affinity"
	"github.com/futureagi/agentcc-gateway/internal/routing/outcomes"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// initOutcomes wires the agent signal stores: session/model affinity pins
// and outcome counters in Redis under the gateway's key prefix (an in-memory
// fallback without it), the
// outcomes post-plugin that fills them and marks unhealthy managed models
// degraded in the catalog, and POST /v1/feedback. The plugin reads model
// health, the event sink and org configs per request through the handlers,
// because main installs them only after the server wiring has run.
func initOutcomes(d *wiringDeps) error {
	affinityStore, outcomeStore := newSignalStores(d.redis, redisKeyPrefix(d.cfg))
	plugin := outcomesplugin.New(outcomesplugin.Deps{
		Affinity: affinityStore,
		Outcomes: outcomeStore,
		Catalog:  d.catalogStore,
		Health: func() routing.ModelHealthReader {
			if d.handlers == nil {
				return nil
			}
			return d.handlers.Deps().ModelHealth
		},
		Events: func() models.EventSink {
			if d.handlers == nil {
				return nil
			}
			return d.handlers.Deps().Events
		},
		OrgConfig: func(orgID string) *tenant.OrgConfig {
			if d.server == nil || d.server.TenantStore == nil {
				return nil
			}
			return d.server.TenantStore.Get(orgID)
		},
	})
	d.plugins = append(d.plugins, plugin)
	d.afterServer(func() error {
		d.server.SetOutcomeStore(outcomeStore)
		go plugin.RunRecovery(d.ctx)
		return nil
	})
	return nil
}

// newSignalStores returns the affinity and outcome stores, on Redis when the
// gateway has it, every key there under prefix.
func newSignalStores(redis *redisstate.Client, prefix string) (*affinity.Store, *outcomes.Store) {
	if redis == nil {
		return affinity.NewStore(nil, affinity.DefaultMemoryEntries, nil),
			outcomes.NewStore(nil, affinity.DefaultMemoryEntries, nil)
	}
	backend := redisstate.NewAgentSignalStore(redis, prefix)
	return affinity.NewStore(backend, affinity.DefaultMemoryEntries, nil),
		outcomes.NewStore(backend, affinity.DefaultMemoryEntries, nil)
}
