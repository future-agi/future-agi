package main

import (
	"context"
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/server"
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

	// Injected dependencies: seedInjected sets their safe defaults before the
	// inits run; the wiring step that provides one overwrites its field in its
	// init or an afterServer callback; main hands them to the handlers once
	// every afterServer callback has run. Consumers never import the provider.
	prices      models.PriceLookup                // the model database, then the catalog
	priced      func(endpoint, model string) bool // whether an endpoint prices a model
	modelHealth routing.ModelHealthReader         // model health
	events      models.EventSink                  // the log flusher

	// Set before the afterServer callbacks run.
	engine   *pipeline.Engine
	server   *server.Server
	handlers *server.Handlers
	ctx      context.Context // cancelled when shutdown begins

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
// uses it as is.
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

// wiringOrder is the order main runs the wiring inits in. Each concern has
// its own wiring_<concern>.go file; a new concern is added here.
var wiringOrder = []wiringStep{
	{"catalog", initCatalog},
	{"modelhealth", initModelHealth},
	{"pricing", initPricing},
	{"credits", initCredits},
	{"outcomes", initOutcomes},
	{"agentbudget", initAgentBudget},
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
