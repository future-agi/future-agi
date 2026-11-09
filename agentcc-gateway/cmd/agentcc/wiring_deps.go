package main

import (
	"context"
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	otelplugin "github.com/futureagi/agentcc-gateway/internal/plugins/otel"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/server"
	"github.com/futureagi/agentcc-gateway/internal/toolcatalog"
)

// wiringDeps is what the per-concern wiring_<concern>.go files share. Each
// file's init runs once, in wiringOrder, while main is still assembling the
// pipeline: it may append the plugins it owns to plugins, and register work
// that needs the running server with afterServer. engine, server, handlers
// and ctx are set only once those callbacks run.
type wiringDeps struct {
	cfg            *config.Config
	registry       *providers.Registry
	redis          *redisstate.Client // nil without Redis
	keyStore       *auth.KeyStore
	catalogStore   *catalog.Store
	catalogSources []catalog.Source
	creditStore    *redisstate.CreditStore // nil without Redis

	// plugins is the pipeline being assembled; inits append to it.
	plugins []pipeline.Plugin

	// orgLedger holds the org balances managed requests draw on (credits).
	orgLedger *creditsplugin.OrgLedger

	// toolCatalog is SR7's tool catalog when tools.enabled is set, nil
	// otherwise: the capability step sets it, and later steps read it (IC-10
	// hands it to the org config handlers; M18's billing step reuses it).
	toolCatalog *toolcatalog.Catalog

	// connectorCatalog is the connector catalog (the embedded bundle, or
	// connectors.catalog_dir): the connectors step sets it, the MCP pool and
	// the org config handlers place a catalog_ref server's credential by it.
	connectorCatalog credtemplate.Catalog

	// Injected dependencies (plan G0b contract g): seedInjected sets their
	// safe defaults before the inits run; the providing lane overwrites its
	// field in its init or an afterServer callback; main hands them to the
	// handlers once every afterServer callback has run. Consumers never
	// import the provider.
	prices      models.PriceLookup                // G4, then G22
	priced      func(endpoint, model string) bool // G7
	modelHealth routing.ModelHealthReader         // G1
	events      models.EventSink                  // the log flusher

	// Set before the afterServer callbacks run. main remains the OTel shutdown owner.
	otelPlugin *otelplugin.Plugin
	engine     *pipeline.Engine
	server     *server.Server
	handlers   *server.Handlers
	ctx        context.Context // cancelled when shutdown begins

	step    string // the init running now, for error messages
	pending []pendingWiring
}

type pendingWiring struct {
	step string
	fn   func() error
}

// seedInjected sets every injected dependency to its safe default: prices
// from the model database, no model health, and events dropped until the log
// flusher takes them.
func (d *wiringDeps) seedInjected(modelDB func() *modeldb.ModelDB) {
	d.prices = server.ModelDBPriceLookup(modelDB)
	d.priced = server.ModelDBPriced(modelDB)
	d.modelHealth = routing.NoModelHealth
	d.events = models.DiscardEvents
}

// defaultRedisKeyPrefix is the Redis key prefix when redis.prefix is unset.
const defaultRedisKeyPrefix = "agentcc:"

// redisKeyPrefix is the prefix the gateway's Redis state keys carry:
// redis.prefix, else defaultRedisKeyPrefix. The budget, credit and
// rate-limit stores add their own segment after it; the agent-signal store
// uses it as is (W2 ruling 61(d)).
func redisKeyPrefix(cfg *config.Config) string {
	if cfg == nil || cfg.Redis.Prefix == "" {
		return defaultRedisKeyPrefix
	}
	return cfg.Redis.Prefix
}

// injected is what the handlers read the injected dependencies from.
func (d *wiringDeps) injected() server.InjectedDeps {
	return server.InjectedDeps{Prices: d.prices, Priced: d.priced, ModelHealth: d.modelHealth, Events: d.events}
}

// afterServer registers fn to run once the server is built.
func (d *wiringDeps) afterServer(fn func() error) {
	d.pending = append(d.pending, pendingWiring{d.step, fn})
}

// wiringStep is one wiring_<concern>.go file's init.
type wiringStep struct {
	name string
	init func(d *wiringDeps) error
}

// wiringOrder is the order main runs the wiring inits in. Each concern's lane
// owns its wiring_<concern>.go file; adding a concern here is an integrator
// change.
var wiringOrder = []wiringStep{
	{"catalog", initCatalog},
	{"modelhealth", initModelHealth},
	{"pricing", initPricing},
	{"credits", initCredits},
	{"outcomes", initOutcomes},
	{"agentbudget", initAgentBudget},
	{"keyscope", initKeyScope},
	{"spendguard", initSpendGuard},
	{"idempotency", initIdempotency},
	{"generation", initGeneration},
	{"telemetry", initTelemetry},
	{"promptcache", initPromptCache},
	{"selector", initSelector},
	{"runtimeauth", initRuntimeAuth},
	{"toolcallguard", initToolCallGuard},
	{"routing_options", initRoutingOptions},
	{"privacy", initPrivacy},
	{"capability", initCapability},
	{"connectors", initConnectors},
	{"mcp", initMCP},
}

// runWiring runs each step's init once, in order, and stops at the first
// error.
func runWiring(d *wiringDeps, steps []wiringStep) error {
	for _, s := range steps {
		d.step = s.name
		if err := s.init(d); err != nil {
			return fmt.Errorf("wiring %s: %w", s.name, err)
		}
	}
	return nil
}

// startServerWiring runs the afterServer callbacks, in registration order,
// once engine, server, handlers and ctx are set. It stops at the first error.
func (d *wiringDeps) startServerWiring() error {
	for _, p := range d.pending {
		if err := p.fn(); err != nil {
			return fmt.Errorf("wiring %s: %w", p.step, err)
		}
	}
	d.pending = nil
	return nil
}
