package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"sort"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	alertpkg "github.com/futureagi/agentcc-gateway/internal/alerting"
	"github.com/futureagi/agentcc-gateway/internal/audit"
	"github.com/futureagi/agentcc-gateway/internal/auth"
	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/contentmod"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/expression"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/external"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/futureagi"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/hallucination"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/injection"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/language"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/leakage"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/mcpsec"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/orgfactory"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/secrets"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/sysprompt"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/toolperm"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/topic"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/webhook"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	alertplugin "github.com/futureagi/agentcc-gateway/internal/plugins/alerting"
	auditplugin "github.com/futureagi/agentcc-gateway/internal/plugins/audit"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	budgetplugin "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	ipaclplugin "github.com/futureagi/agentcc-gateway/internal/plugins/ipacl"
	loggingplugin "github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	otelplugin "github.com/futureagi/agentcc-gateway/internal/plugins/otel"
	promplugin "github.com/futureagi/agentcc-gateway/internal/plugins/prometheus"
	quotaplugin "github.com/futureagi/agentcc-gateway/internal/plugins/quota"
	ratelimitplugin "github.com/futureagi/agentcc-gateway/internal/plugins/ratelimit"
	rbacplugin "github.com/futureagi/agentcc-gateway/internal/plugins/rbac"
	toolpolicyplugin "github.com/futureagi/agentcc-gateway/internal/plugins/toolpolicy"
	validationplugin "github.com/futureagi/agentcc-gateway/internal/plugins/validation"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/rbac"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/rotation"
	secretspkg "github.com/futureagi/agentcc-gateway/internal/secrets"
	"github.com/futureagi/agentcc-gateway/internal/server"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func main() {
	configPath := flag.String("config", "", "Path to config file (YAML/JSON)")
	flag.Parse()

	// Also check env var for config path.
	if *configPath == "" {
		*configPath = os.Getenv("AGENTCC_CONFIG_FILE")
	}

	// Load configuration.
	cfg, err := config.Load(*configPath)
	if err != nil {
		slog.Error("failed to load config", "error", err)
		os.Exit(1)
	}

	// Resolve provider API keys from secret managers (if any use secret URIs).
	if err := secretspkg.ResolveProviderSecrets(cfg); err != nil {
		slog.Error("failed to resolve secrets", "error", err)
		os.Exit(1)
	}

	// Initialize logger.
	initLogger(cfg.Logging.Level)
	warnUnbuiltOptions(cfg)

	slog.Info("agentcc gateway initializing",
		"version", "0.1.0",
		"config_file", *configPath,
	)

	// Create provider registry.
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		slog.Error("failed to create provider registry", "error", err)
		os.Exit(1)
	}

	// Managed providers' model catalogs, seeded from the bundled snapshot so
	// managed routing and pricing work before the first live fetch.
	catalogStore := catalog.NewStore()
	catalogSources := server.ManagedCatalogSources(registry)
	catalog.Seed(catalogStore, catalogSources)

	// Create tenant store (per-org config isolation).
	tenantStore := tenant.NewStore()

	// Initialize shared Redis client for multi-replica state (rate limits,
	// budgets, credits, cluster). Non-fatal: if Redis is unavailable, the
	// gateway operates in single-instance mode with in-memory state.
	var redisClient *redisstate.Client
	var redisBudgetStore *redisstate.BudgetStore
	var redisCreditStore *redisstate.CreditStore
	redisPrefix := redisKeyPrefix(cfg)
	if cfg.Redis.Enabled {
		rc, err := redisstate.NewClient(redisstate.Config{
			Address:  cfg.Redis.Address,
			Password: cfg.Redis.Password,
			DB:       cfg.Redis.DB,
			PoolSize: cfg.Redis.PoolSize,
			Timeout:  cfg.Redis.Timeout,
		})
		if err != nil {
			slog.Warn("redis state unavailable, running in single-instance mode", "error", err)
		} else {
			redisClient = rc
			redisBudgetStore = redisstate.NewBudgetStore(redisClient, redisPrefix+"budget:")
			redisCreditStore = redisstate.NewCreditStore(redisClient, redisPrefix+"credits:")
			slog.Info("redis state enabled", "address", cfg.Redis.Address, "prefix", redisPrefix)
		}
	}

	// Create the keystore even when request auth is disabled. Admin key
	// lifecycle routes still need a store, while /v1 auth remains controlled
	// by cfg.Auth.Enabled.
	keyStore := auth.NewKeyStore(cfg.Auth)
	keyStore.SetSyncGrace(cfg.ControlPlane.EffectiveKeySyncGrace())
	var plugins []pipeline.Plugin

	// Every IP rule the pipeline applies (ip_acl and a key's allowed_ips)
	// reads the client IP off rc; this puts it there on the routes whose
	// handlers do not (priority 5: before ip_acl and auth).
	plugins = append(plugins, clientIPPlugin{})

	// Create IP ACL plugin (priority 10: runs before auth). The node's rules
	// need ip_acl.enabled; an org's ip_acl applies with ip_acl.enabled, or when
	// org configs can reach this node and it resolves client IPs
	// (buildIPACLPlugin).
	ipaclPlugin := buildIPACLPlugin(cfg, tenantStore)
	if ipaclPlugin != nil {
		plugins = append(plugins, ipaclPlugin)
	}

	if cfg.Auth.Enabled {
		plugins = append(plugins, authplugin.New(keyStore, true))
		slog.Info("auth enabled", "config_keys", keyStore.Count())
	}

	// Control plane sync runs in the background (started below, next to the
	// periodic sync) and never blocks startup. The gateway serves immediately
	// with config.yaml keys, then picks up Django-managed keys and org configs
	// as soon as the backend is reachable.
	if cfg.ControlPlane.URL != "" {
		slog.Info("control plane sync enabled",
			"interval", cfg.ControlPlane.SyncInterval.String(),
			"startup_sync", cfg.ControlPlane.SyncOnStartup,
			"key_sync_grace", cfg.ControlPlane.EffectiveKeySyncGrace().String(),
		)
	}

	// Create RBAC plugin.
	if cfg.RBAC.Enabled {
		rbacStore := rbac.NewStore(cfg.RBAC)
		plugins = append(plugins, rbacplugin.New(rbacStore, true))
		slog.Info("rbac enabled",
			"roles", len(cfg.RBAC.Roles),
			"teams", len(cfg.RBAC.Teams),
			"default_role", cfg.RBAC.DefaultRole,
		)
	}

	// Create budget plugin. The node's budgets need budgets.enabled; an org's
	// budgets apply whenever org configs can reach this node.
	budgetPlugin, budgetTracker := buildBudgetPlugin(cfg, tenantStore, redisBudgetStore)
	if budgetPlugin != nil {
		plugins = append(plugins, budgetPlugin)
		// Seed budget counters from historical spend in Postgres (with retry).
		if cfg.ControlPlane.URL != "" {
			go seedBudgetSpend(cfg, tenantStore, budgetPlugin, budgetTracker, redisBudgetStore)
		}
	}

	// Create quota enforcement plugin (free tier gateway request limits).
	if redisClient != nil {
		plugins = append(plugins, quotaplugin.New(redisClient.Redis(), true))
		slog.Info("quota enforcement enabled")
	}

	// Create guardrail plugin.
	var grEngine *guardrails.Engine
	var policyStore *policy.Store
	var guardrailPlugin *guardrails.GuardrailPlugin
	if cfg.Guardrails.Enabled {
		guardrailRegistry := make(map[string]guardrails.Guardrail)

		// Register built-in guardrails.
		guardrailRegistry["pii-detection"] = pii.New(findRuleConfig(cfg.Guardrails.Rules, "pii-detection"))
		guardrailRegistry["content-moderation"] = contentmod.New(findRuleConfig(cfg.Guardrails.Rules, "content-moderation"))
		guardrailRegistry["keyword-blocklist"] = blocklist.New(findRuleConfig(cfg.Guardrails.Rules, "keyword-blocklist"))
		guardrailRegistry["input-validation"] = validation.New(findRuleConfig(cfg.Guardrails.Rules, "input-validation"))
		guardrailRegistry["prompt-injection"] = injection.New(findRuleConfig(cfg.Guardrails.Rules, "prompt-injection"))
		guardrailRegistry["secret-detection"] = secrets.New(findRuleConfig(cfg.Guardrails.Rules, "secret-detection"))
		guardrailRegistry["topic-restriction"] = topic.New(findRuleConfig(cfg.Guardrails.Rules, "topic-restriction"))
		guardrailRegistry["language-detection"] = language.New(findRuleConfig(cfg.Guardrails.Rules, "language-detection"))
		guardrailRegistry["system-prompt-protection"] = sysprompt.New(findRuleConfig(cfg.Guardrails.Rules, "system-prompt-protection"))
		guardrailRegistry["hallucination-detection"] = hallucination.New(findRuleConfig(cfg.Guardrails.Rules, "hallucination-detection"))
		guardrailRegistry["data-leakage-prevention"] = leakage.New(findRuleConfig(cfg.Guardrails.Rules, "data-leakage-prevention"))

		// Register dynamic guardrails: webhooks and expressions.
		for _, rule := range cfg.Guardrails.Rules {
			if _, exists := guardrailRegistry[rule.Name]; exists {
				continue
			}
			if webhook.IsWebhookConfig(rule.Config) {
				guardrailRegistry[rule.Name] = webhook.NewConfigFileRule(rule.Name, rule.Config)
				slog.Info("registered webhook guardrail", "name", rule.Name)
			} else if expression.IsExpressionConfig(rule.Config) {
				guardrailRegistry[rule.Name] = expression.New(rule.Name, rule.Config)
				slog.Info("registered expression guardrail", "name", rule.Name)
			} else if futureagi.IsFutureAGIConfig(rule.Config) {
				// A config-file rule is operator-trusted: the one place a
				// guardrail expands ${VAR} from the environment.
				guardrailRegistry[rule.Name] = futureagi.NewConfigFileRule(rule.Name, rule.Config)
				slog.Info("registered futureagi guardrail", "name", rule.Name)
			} else if toolperm.IsToolPermConfig(rule.Config) {
				guardrailRegistry[rule.Name] = toolperm.New(rule.Name, rule.Config)
				slog.Info("registered tool permission guardrail", "name", rule.Name)
			} else if mcpsec.IsMCPSecConfig(rule.Config) {
				guardrailRegistry[rule.Name] = mcpsec.New(rule.Name, rule.Config)
				slog.Info("registered MCP security guardrail", "name", rule.Name)
			} else if external.IsExternalProviderConfig(rule.Config) {
				guardrailRegistry[rule.Name] = external.NewConfigFileRule(rule.Name, rule.Config)
				slog.Info("registered external guardrail", "name", rule.Name, "provider", rule.Config["provider"])
			}
		}

		grEngine = guardrails.NewEngine(cfg.Guardrails, guardrailRegistry)

		// Build per-key guardrail policy store.
		policyStore = policy.NewStore()
		if keyStore != nil {
			for _, k := range keyStore.List() {
				// Find the key config to get guardrail policy.
				for _, keyCfg := range cfg.Auth.Keys {
					if keyCfg.Name == k.Name && keyCfg.Guardrails != nil {
						policyStore.Register(k.ID, keyCfg.Guardrails)
					}
				}
			}
		}

		// The guarded egress dialer for the callbacks an org config names (a
		// webhook or external guardrail endpoint, a FutureAGI eval base_url):
		// built from the operator list mcp.egress.private_allow, so a
		// self-hosted LAN service is reached only when the operator listed it,
		// and a loopback, private or cloud-metadata URL is refused. The
		// config.yaml rules above are operator-trusted and keep a plain client,
		// as does a FutureAGI eval call to the operator's own FI_BASE_URL.
		grEgress, err := egress.New(egress.Options{PrivateAllow: cfg.MCP.Egress.PrivateAllow})
		if err != nil {
			slog.Error("guardrail egress dialer", "error", err)
			os.Exit(1)
		}

		// Dynamic factory creates guardrails from org config for managed mode.
		// orgfactory.New(grEgress) is the one org-config construction path: it
		// treats an org config as caller-controlled, reads no gateway
		// environment variable (unlike the config.yaml registry loop above),
		// and dials every callback an org config names through grEgress.
		guardrailPlugin = guardrails.NewPlugin(grEngine, guardrailRegistry, orgfactory.New(grEgress), policyStore, tenantStore)
		plugins = append(plugins, guardrailPlugin)
		slog.Info("guardrails enabled",
			"pre_guardrails", grEngine.PreCount(),
			"post_guardrails", grEngine.PostCount(),
			"registry_size", len(guardrailRegistry),
		)
	}

	// Create tool policy plugin (priority 60: after guardrails, before
	// validation). The node's policy needs tool_policy.enabled; an org's
	// tool_policy applies whenever org configs can reach this node.
	if toolPolicyPlugin := buildToolPolicyPlugin(cfg, tenantStore); toolPolicyPlugin != nil {
		plugins = append(plugins, toolPolicyPlugin)
	}

	// Create cache plugin with pluggable backend.
	if cfg.Cache.Enabled {
		cacheBackend, err := cache.NewBackend(cfg.Cache)
		if err != nil {
			slog.Error("failed to create cache backend", "error", err)
			os.Exit(1)
		}
		cachePlugin := cacheplugin.New(cacheBackend, cfg.Cache.DefaultTTL)

		if cfg.Cache.Semantic.Enabled {
			semBackend, err := cache.NewSemanticBackend(cfg.Cache.Semantic)
			if err != nil {
				slog.Error("failed to create semantic cache backend", "error", err)
				os.Exit(1)
			}
			cachePlugin.SetSemanticStore(semBackend)
			slog.Info("semantic cache enabled",
				"backend", cfg.Cache.Semantic.Backend,
				"threshold", cfg.Cache.Semantic.Threshold,
				"dims", cfg.Cache.Semantic.Dimensions,
			)
		}

		// Edge caching support.
		if cfg.Cache.Edge.Enabled {
			edgeHandler := cacheplugin.NewEdgeCacheHandler(cfg.Cache.Edge)
			cachePlugin.SetEdgeHandler(edgeHandler)
			slog.Info("edge cache enabled",
				"default_ttl", cfg.Cache.Edge.DefaultTTL,
				"cacheable_models", len(cfg.Cache.Edge.CacheableModels),
			)
		}

		plugins = append(plugins, cachePlugin)
		slog.Info("cache enabled", "backend", cfg.Cache.Backend, "ttl", cfg.Cache.DefaultTTL)
	}

	// Create rate limit plugin with optional Redis backend.
	var rateLimiter ratelimitplugin.Limiter
	localLimiter := ratelimitplugin.NewRateLimiter()
	if redisClient != nil {
		rateLimiter = redisstate.NewRateLimiter(redisClient, localLimiter, redisPrefix+"rl:")
	} else {
		rateLimiter = localLimiter
	}
	plugins = append(plugins, ratelimitplugin.New(cfg.RateLimiting, keyStore, rateLimiter))

	// Create model metadata database (shared between plugins and server).
	mdb := server.BuildModelDB(server.ConvertModelOverrides(cfg.ModelDatabase.Overrides), catalogStore)
	slog.Info("model database initialized", "models", mdb.Count())
	var sharedModelDB atomic.Pointer[modeldb.ModelDB]
	sharedModelDB.Store(mdb)
	modelDBGetter := func() *modeldb.ModelDB { return sharedModelDB.Load() }

	// Create model validation plugin (priority 150: after auth, before cache).
	if cfg.ModelDatabase.IsValidationEnabled() {
		// The tenant store gives each org's Model Database overlay (limits and
		// capability switches) to its own requests.
		vp := validationplugin.New(true, modelDBGetter)
		vp.SetTenantStore(tenantStore)
		plugins = append(plugins, vp)
		slog.Info("model validation enabled")
	}

	// Create cost tracking plugin.
	costPlugin := costplugin.New(cfg.CostTracking.Enabled, modelDBGetter, tenantStore, cfg.CostTracking.AliasCostFactors)
	costPlugin.SetManagedMarkup(cfg.Managed.MarkupPercent)
	plugins = append(plugins, costPlugin)

	// Per-concern wiring (wiring_<concern>.go), in wiringOrder. Each init may
	// add the pipeline plugins it owns and register work for once the server
	// is built.
	wiring := &wiringDeps{
		cfg:            cfg,
		registry:       registry,
		redis:          redisClient,
		keyStore:       keyStore,
		catalogStore:   catalogStore,
		catalogSources: catalogSources,
		creditStore:    redisCreditStore,
		plugins:        plugins,
	}
	wiring.seedInjected(modelDBGetter)
	if err := runWiring(wiring, wiringOrder); err != nil {
		slog.Error("failed to wire the gateway", "error", err)
		os.Exit(1)
	}
	plugins = wiring.plugins

	// Create logging plugin.
	loggingPlugin := loggingplugin.New(cfg.Logging.RequestLogging, tenantStore)

	// Attach privacy redactor if enabled. Kept in scope because every sink
	// that exports request or response content redacts through it.
	var globalRedactor *privacy.Redactor
	if cfg.Privacy.Enabled {
		patterns := make([]privacy.PatternConfig, len(cfg.Privacy.Patterns))
		for i, p := range cfg.Privacy.Patterns {
			patterns[i] = privacy.PatternConfig{Name: p.Name, Pattern: p.Pattern}
		}
		globalRedactor = privacy.New(cfg.Privacy.Mode, patterns)
		loggingPlugin.SetRedactor(globalRedactor)
		slog.Info("privacy mode enabled", "mode", cfg.Privacy.Mode, "patterns", globalRedactor.PatternCount())
	}

	plugins = append(plugins, loggingPlugin)

	// Start log flusher if control plane URL is configured.
	if cfg.ControlPlane.URL != "" && cfg.Logging.RequestLogging.Enabled {
		flushInterval := 5 * time.Second
		maxBuffered := 5000
		webhookURL := loggingplugin.FormatLogsWebhookURL(cfg.ControlPlane.URL)
		logFlusher := loggingplugin.NewLogFlusher(webhookURL, cfg.ControlPlane.WebhookSecret, flushInterval, maxBuffered)
		loggingPlugin.SetFlusher(logFlusher)
		wiring.events = logFlusher.EnqueueEvent
		go logFlusher.Run(context.Background())
		slog.Info("log flusher enabled",
			"flush_interval", flushInterval.String(),
			"max_buffered", maxBuffered,
			"webhook_url", webhookURL,
			"webhook_secret_set", cfg.ControlPlane.WebhookSecret != "",
		)
	}

	// Create audit plugin.
	var auditPlugin *auditplugin.Plugin
	if cfg.Audit.Enabled {
		auditLogger := audit.NewLogger(cfg.Audit)
		auditPlugin = auditplugin.New(auditLogger, true)
		plugins = append(plugins, auditPlugin)
		slog.Info("audit logging enabled", "sinks", len(cfg.Audit.Sinks))
	}

	// Create alerting plugin. The node's rules need alerting.enabled; an
	// org's rules run whenever org configs can reach this node, and its
	// channels dial only through the guarded egress dialer.
	alertingPlugin, err := buildAlertingPlugin(cfg, tenantStore)
	if err != nil {
		slog.Error("alerting egress dialer", "error", err)
		os.Exit(1)
	}
	if alertingPlugin != nil {
		plugins = append(plugins, alertingPlugin)
	}

	// Called when an org config changes: each plugin drops what it cached for
	// that org. The logging plugin is deliberately absent here: its redactor
	// cache lives on tenantStore, which invalidates itself on every config
	// change.
	var orgInvalidators []func(string)
	if guardrailPlugin != nil {
		orgInvalidators = append(orgInvalidators, guardrailPlugin.InvalidateDynamicCache)
	}
	if budgetPlugin != nil {
		orgInvalidators = append(orgInvalidators, budgetPlugin.InvalidateOrg)
	}
	if ipaclPlugin != nil {
		orgInvalidators = append(orgInvalidators, ipaclPlugin.InvalidateOrg)
	}
	if alertingPlugin != nil {
		orgInvalidators = append(orgInvalidators, alertingPlugin.InvalidateOrg)
	}
	onOrgConfigChange := chainOrgInvalidators(orgInvalidators)

	// Create Prometheus metrics registry and plugin.
	var metricsRegistry *metrics.Registry
	if cfg.Prometheus.Enabled {
		metricsRegistry = metrics.NewRegistry()
		plugins = append(plugins, promplugin.New(metricsRegistry, true))
		slog.Info("prometheus metrics enabled")
	}

	// Create OTel plugin.
	var otelPlugin *otelplugin.Plugin
	if cfg.OTel.Enabled {
		otelPlugin = otelplugin.New(cfg.OTel)
		otelPlugin.SetTenantStore(tenantStore)
		otelPlugin.SetRedactor(globalRedactor)
		plugins = append(plugins, otelPlugin)
		slog.Info("otel enabled", "exporter", cfg.OTel.Exporter, "sample_rate", cfg.OTel.SampleRate)
	}

	// Create pipeline engine.
	engine := pipeline.NewEngine(plugins...)

	// Create and start server (shares the same ModelDB pointer for hot-reload).
	srv := server.New(cfg, *configPath, registry, engine, keyStore, grEngine, policyStore, metricsRegistry, &sharedModelDB, tenantStore, onOrgConfigChange, redisClient)
	// GET /-/config reports the org config sections this node enforces,
	// read off the plugins the engine runs.
	srv.SetOrgSectionsEnforced(orgSectionsEnforced(plugins, cfg))
	srv.SetOrgSectionsNotEnforced(orgSectionsNotEnforced(cfg))

	// Register onChange callback on tenantStore so that periodic sync (MergeBulk)
	// evicts the OrgProviderCache for any org whose config changed. Without this,
	// credential rotations recovered via periodic sync use stale cached providers.
	tenantStore.SetOnChange(func(orgID string) {
		srv.OrgProviderCache.Evict(orgID)
		if onOrgConfigChange != nil {
			onOrgConfigChange(orgID)
		}
	})

	// Background context for all sync + pub/sub goroutines. Cancelled on shutdown.
	syncCtxBg, syncCancelBg := context.WithCancel(context.Background())

	// The wiring that needs the running server, in wiringOrder.
	wiring.engine, wiring.server, wiring.handlers, wiring.ctx = engine, srv, srv.Handlers(), syncCtxBg
	if err := wiring.startServerWiring(); err != nil {
		slog.Error("failed to wire the gateway", "error", err)
		os.Exit(1)
	}
	// Every afterServer callback has run: hand the injected dependencies
	// over, once, so consumers see what the wiring set, never a default.
	srv.Handlers().SetInjectedDeps(wiring.injected())
	logUpstreamFees(cfg)

	// Set up Redis pub/sub for multi-replica key revocation.
	// When one replica revokes a key, all others revoke it immediately
	// instead of waiting for the next periodic sync (up to 15s).
	if redisClient != nil && keyStore != nil {
		broadcaster := redisstate.NewKeyRevocationBroadcaster(redisClient)
		srv.SetKeyRevocationPublisher(broadcaster)

		// Start subscriber goroutine — listens for revocations from other replicas.
		go broadcaster.Subscribe(syncCtxBg, func(keyID string) {
			keyStore.Revoke(keyID)
		})
		slog.Info("key revocation pub/sub enabled")
	}

	// Provider key rotation reaches every replica: a promote is broadcast
	// over Redis pub/sub (transient, the key sealed under the admin token)
	// and applied by the others.
	if redisClient != nil && cfg.Admin.Token != "" && rotationConfigured(cfg) {
		rotationBroadcaster, err := rotation.NewBroadcaster(redisClient.Redis(), cfg.Admin.Token, redisPrefix)
		if err != nil {
			slog.Error("provider key rotation broadcast", "error", err)
			os.Exit(1)
		}
		srv.SetRotationBroadcaster(rotationBroadcaster)
		go rotationBroadcaster.Subscribe(syncCtxBg, srv.ApplyRemoteRotation)
		slog.Info("provider key rotation pub/sub enabled", "channel", rotationBroadcaster.Channel())
	}

	// Control plane sync: the startup sync retries until the backend answers
	// (it can take minutes on a first boot), next to the periodic re-sync if
	// an interval is set. Do NOT add another ticker.
	if cfg.ControlPlane.URL != "" {
		go tenant.RunControlPlaneSync(syncCtxBg, cfg.ControlPlane.SyncOnStartup, cfg.ControlPlane.SyncInterval,
			cfg.ControlPlane.URL, cfg.ControlPlane.AdminToken, tenantStore, keyStore)
	}

	// Handle shutdown signals.
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)

	shutdownDone := make(chan struct{})
	go func() {
		defer close(shutdownDone)
		sig := <-sigCh
		signal.Stop(sigCh) // a second signal stops the process at once
		slog.Info("received signal", "signal", sig)

		// Stop control plane sync.
		syncCancelBg()

		shutdownCtx, cancel := context.WithTimeout(context.Background(), cfg.Server.ShutdownTimeout)
		defer cancel()

		// When requests outlast shutdown_timeout, still deliver what is
		// buffered below, then exit 1.
		shutdownErr := srv.Shutdown(shutdownCtx)
		if shutdownErr != nil {
			slog.Error("shutdown error", "error", shutdownErr)
		}

		// Drain buffered trace records and deliver the buffered request logs.
		loggingPlugin.Close()
		if auditPlugin != nil {
			auditPlugin.Close()
		}
		if otelPlugin != nil {
			otelPlugin.Close()
		}
		if redisClient != nil {
			redisClient.Close()
		}
		if shutdownErr != nil {
			os.Exit(1)
		}
	}()

	if err := srv.Start(); err != nil {
		slog.Error("server error", "error", err)
		os.Exit(1)
	}
	// Start returns as soon as the shutdown begins. Wait for the rest of it
	// (in-flight requests, the last request-log flush) before exiting.
	<-shutdownDone
}

// findRuleConfig extracts the Config map for a named guardrail rule.
func findRuleConfig(rules []config.GuardrailRuleConfig, name string) map[string]interface{} {
	for _, r := range rules {
		if r.Name == name {
			return r.Config
		}
	}
	return nil
}

func initLogger(level string) {
	var logLevel slog.Level
	switch strings.ToLower(level) {
	case "debug":
		logLevel = slog.LevelDebug
	case "warn":
		logLevel = slog.LevelWarn
	case "error":
		logLevel = slog.LevelError
	default:
		logLevel = slog.LevelInfo
	}

	handler := slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level: logLevel,
	})
	slog.SetDefault(slog.New(handler))
}

// logUpstreamFees logs the effective upstream fee of every managed or
// catalog-sourced provider at startup, so a fee left to its default is
// visible, and whether its resale terms are recorded.
func logUpstreamFees(cfg *config.Config) {
	names := make([]string, 0, len(cfg.Providers))
	for name, p := range cfg.Providers {
		if p.Managed || p.CatalogSource != "" {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	for _, name := range names {
		p := cfg.Providers[name]
		slog.Info("provider upstream fee",
			"provider", name,
			"upstream_fee_bps", p.EffectiveUpstreamFeeBps(),
			"fee_configured", p.UpstreamFeeBps != nil,
			"catalog_source", p.CatalogSource,
			"resale_terms_cleared", p.ResaleTermsRef != "",
		)
	}
}

// rotationConfigured reports whether any provider has key rotation on.
func rotationConfigured(cfg *config.Config) bool {
	for _, p := range cfg.Providers {
		if p.Rotation.Enabled {
			return true
		}
	}
	return false
}

// orgConfigsArrive reports whether org configs can reach this node: from the
// control plane's sync (control_plane.url) or through the admin API's
// PUT /-/orgs/{org_id}/config (admin.token). When they can, the plugins that
// read an org's budgets, tool_policy and alerting sections are built whatever
// the node's own switches say, so an org's saved section takes effect; the
// org-only ip_acl plugin also needs the node to resolve client IPs
// (buildIPACLPlugin). A node that no org config can reach builds them only
// for its own switches.
func orgConfigsArrive(cfg *config.Config) bool {
	return cfg.OrgConfigsArrive()
}

// buildIPACLPlugin builds the pipeline's IP ACL plugin: with the node's rules
// when ip_acl.enabled, and with none of them (orgs' ip_acl only) when org
// configs can arrive and the node resolves client IPs (server.trusted_proxies
// or server.trusted_hops: middleware.OrgIPACLWithoutNodeSwitch, the rule the
// server's KeyAuth gate builds its ip_acl on too). nil otherwise: without
// a trusted proxy every request behind a load balancer seems to come from the
// balancer, so an org's ip_acl is not enforced there (orgSectionsNotEnforced
// says why, and a start-up warning names the fix). An org that sets no ip_acl
// is unaffected: the plugin reads the org's config and finds none.
func buildIPACLPlugin(cfg *config.Config, tenantStore *tenant.Store) *ipaclplugin.Plugin {
	switch {
	case cfg.IPACL.Enabled:
		p := ipaclplugin.New(cfg.IPACL, tenantStore)
		slog.Info("ip acl enabled", "scope", "node+org", "allow_rules", p.AllowCount(), "deny_rules", p.DenyCount())
		return p
	case middleware.OrgIPACLWithoutNodeSwitch(cfg):
		slog.Info("ip acl enabled", "scope", "org", "node_rules", false)
		return ipaclplugin.New(config.IPACLConfig{}, tenantStore)
	}
	return nil
}

// orgSectionsNotEnforced names, with the reason, each org config section this
// node does not enforce for a reason the console should state (GET /-/config
// org_sections_not_enforced): ip_acl when org configs can arrive, the node's
// ip_acl switch is off and no trusted proxy is set. nil when there is none.
func orgSectionsNotEnforced(cfg *config.Config) map[string]string {
	if !cfg.IPACL.Enabled && orgConfigsArrive(cfg) && !middleware.ResolvesClientIP(cfg.Server) {
		return map[string]string{"ip_acl": middleware.OrgIPACLClientIPUnknown}
	}
	return nil
}

// clientIPPlugin puts the client IP the HTTP layer resolved for the request
// (middleware.ClientIP: the TCP peer, or with server.trusted_proxies the
// address they vouch for) on rc.Metadata["client_ip"] when the route's handler
// did not, so every IP rule the pipeline applies reads it on every route that
// runs the pipeline: ip_acl, the node's and each org's, and a key's
// allowed_ips.
//
// It never replaces a value a handler set. Those handlers hand the pipeline
// their request's context (with a timeout), which carries the resolved
// address; a context without one (work that outlives its request) leaves the
// key unset, and the IP rules refuse the call.
type clientIPPlugin struct{}

func (clientIPPlugin) Name() string  { return "client_ip" }
func (clientIPPlugin) Priority() int { return 5 } // before ipacl (10) and auth (20)

func (clientIPPlugin) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if rc.Metadata["client_ip"] == "" {
		// middleware.ClientIP reads the address TrustedClientIP recorded on
		// the request's context; it is given that context alone, so with no
		// address recorded it falls back to an empty peer and returns "".
		if ip := middleware.ClientIP((&http.Request{}).WithContext(ctx)); ip != "" {
			rc.Metadata["client_ip"] = ip
		}
	}
	return pipeline.ResultContinue()
}

func (clientIPPlugin) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// buildBudgetPlugin builds the budget plugin and, with budgets.enabled, the
// node's tracker: with the node's budgets when budgets.enabled, and with orgs'
// budgets only (no tracker) when org configs can arrive. nil, nil when
// neither applies. The plugin counts every org's org-level spend, locally
// and written through to Redis when Redis is configured, budgets set or not.
func buildBudgetPlugin(cfg *config.Config, tenantStore *tenant.Store, redisBudgetStore *redisstate.BudgetStore) (*budgetplugin.Plugin, *budgetpkg.Tracker) {
	if !cfg.Budgets.Enabled && !orgConfigsArrive(cfg) {
		return nil, nil
	}
	var tracker *budgetpkg.Tracker
	if cfg.Budgets.Enabled {
		tracker = budgetpkg.NewTracker(cfg.Budgets)
	}
	pricing := budgetplugin.NewPricingLookup(cfg.CostTracking.CustomPricing)
	p := budgetplugin.New(tracker, pricing, cfg.Budgets.Enabled, tenantStore)
	if redisBudgetStore != nil {
		p.SetRedisBudget(redisBudgetStore)
	}
	if cfg.Budgets.Enabled {
		slog.Info("budgets enabled", "scope", "node+org", "default_period", cfg.Budgets.DefaultPeriod, "redis", redisBudgetStore != nil)
	} else {
		slog.Info("budgets enabled", "scope", "org", "node_rules", false, "redis", redisBudgetStore != nil)
	}
	return p, tracker
}

// seedBudgetSpend seeds the budget counters from the spend the control plane
// recorded (with retry), so a restart does not forget what an org has spent.
// With the node's budgets off it seeds only the orgs whose config enables
// budgets; the plugin counts every org's spend from its own calls.
func seedBudgetSpend(cfg *config.Config, tenantStore *tenant.Store, budgetPlugin *budgetplugin.Plugin, tracker *budgetpkg.Tracker, redisBudgetStore *redisstate.BudgetStore) {
	period := cfg.Budgets.DefaultPeriod
	if period == "" {
		period = "monthly"
	}

	var summary *tenant.SpendSummary
	backoff := 2 * time.Second
	maxAttempts := 5
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		seedCtx, seedCancel := context.WithTimeout(context.Background(), 30*time.Second)
		var err error
		summary, err = tenant.SyncSpendFromControlPlane(seedCtx, cfg.ControlPlane.URL, cfg.ControlPlane.AdminToken, period)
		seedCancel()
		if err == nil {
			break
		}
		slog.Warn("spend seed failed, retrying",
			"attempt", attempt, "max", maxAttempts, "backoff", backoff, "error", err)
		if attempt == maxAttempts {
			slog.Error("spend seed exhausted retries (counters start at zero)")
			return
		}
		time.Sleep(backoff)
		backoff = time.Duration(float64(backoff) * 1.5)
	}

	if summary == nil {
		return
	}
	if tracker == nil {
		// Only orgs whose config enables budgets are seeded, so wait (bounded)
		// for the org configs: the spend summary can arrive before the
		// control plane's first org config sync has loaded them.
		waitForOrgConfigs(tenantStore, 2*time.Minute)
	}
	for orgID, orgSpend := range summary.Orgs {
		if tracker == nil && !orgBudgetsEnabled(tenantStore, orgID) {
			continue
		}
		budgetPlugin.SeedOrgSpend(orgID, budgetplugin.OrgSpendSummary{
			TotalSpend: orgSpend.TotalSpend,
			PerKey:     orgSpend.PerKey,
			PerUser:    orgSpend.PerUser,
			PerModel:   orgSpend.PerModel,
		})
		// Seed global tracker org level.
		if tracker != nil {
			tracker.SeedSpend("org", orgID, orgSpend.TotalSpend, orgSpend.PerModel)
		}
		// Also seed Redis if available.
		if redisBudgetStore != nil {
			redisBudgetStore.SeedSpend(orgID, "org", "", period, orgSpend.TotalSpend, orgSpend.PerModel)
			for keyName, spend := range orgSpend.PerKey {
				redisBudgetStore.SeedSpend(orgID, "key", keyName, period, spend, nil)
			}
			for userName, spend := range orgSpend.PerUser {
				redisBudgetStore.SeedSpend(orgID, "user", userName, period, spend, nil)
			}
		}
	}
}

// waitForOrgConfigs returns once the tenant store holds an org config, or
// after max.
func waitForOrgConfigs(tenantStore *tenant.Store, max time.Duration) {
	for deadline := time.Now().Add(max); tenantStore.Count() == 0 && time.Now().Before(deadline); {
		time.Sleep(time.Second)
	}
}

// orgBudgetsEnabled reports whether orgID's config, as this node holds it,
// enables budgets.
func orgBudgetsEnabled(tenantStore *tenant.Store, orgID string) bool {
	orgCfg := tenantStore.Get(orgID)
	return orgCfg != nil && orgCfg.Budgets != nil && orgCfg.Budgets.Enabled
}

// buildToolPolicyPlugin builds the tool policy plugin: with the node's policy
// when tool_policy.enabled, and with none of it (orgs' tool_policy only) when
// org configs can arrive. nil when neither applies.
func buildToolPolicyPlugin(cfg *config.Config, tenantStore *tenant.Store) *toolpolicyplugin.Plugin {
	switch {
	case cfg.ToolPolicy.Enabled:
		slog.Info("tool policy enabled",
			"scope", "node+org",
			"allow", len(cfg.ToolPolicy.Allow),
			"deny", len(cfg.ToolPolicy.Deny),
			"max_tools", cfg.ToolPolicy.MaxToolsPerRequest,
		)
		return toolpolicyplugin.New(cfg.ToolPolicy, tenantStore)
	case orgConfigsArrive(cfg):
		slog.Info("tool policy enabled", "scope", "org", "node_rules", false)
		return toolpolicyplugin.New(config.ToolPolicyConfig{}, tenantStore)
	}
	return nil
}

// buildAlertingPlugin builds the alerting plugin: with the node's rules and
// channels when alerting.enabled, and with orgs' rules only when org configs
// can arrive. nil when neither applies. An organization's own channels dial
// through the guarded egress dialer built from mcp.egress.private_allow, so
// an org-set URL on a loopback, private or metadata address is refused
// unless the operator listed it; the operator's own channels are built from
// config.yaml and keep a plain client.
func buildAlertingPlugin(cfg *config.Config, tenantStore *tenant.Store) (*alertplugin.Plugin, error) {
	if !cfg.Alerting.Enabled && !orgConfigsArrive(cfg) {
		return nil, nil
	}
	alertEgress, err := egress.New(egress.Options{PrivateAllow: cfg.MCP.Egress.PrivateAllow})
	if err != nil {
		return nil, err
	}
	var alertManager *alertpkg.Manager
	if cfg.Alerting.Enabled {
		alertManager = alertpkg.NewManager(cfg.Alerting)
		slog.Info("alerting enabled", "scope", "node+org", "rules", alertManager.RuleCount())
	} else {
		slog.Info("alerting enabled", "scope", "org", "node_rules", false)
	}
	return alertplugin.New(alertManager, cfg.Alerting.Enabled, tenantStore, alertEgress), nil
}

// chainOrgInvalidators returns one org-config-change callback that runs each
// of fns in order, or nil when there are none.
func chainOrgInvalidators(fns []func(string)) func(string) {
	if len(fns) == 0 {
		return nil
	}
	return func(orgID string) {
		for _, fn := range fns {
			fn(orgID)
		}
	}
}

// orgSectionPlugins maps a pipeline plugin's name to the org config section it
// enforces.
var orgSectionPlugins = map[string]string{
	"alerting":   "alerting",
	"budget":     "budgets",
	"cache":      "cache",
	"ipacl":      "ip_acl",
	"ratelimit":  "rate_limiting",
	"toolpolicy": "tool_policy",
}

// orgSectionsEnforced names, sorted, the org config sections this node
// enforces, read off the plugins the engine runs: each is listed
// when the plugin that reads it is installed. cache is listed only on a node
// with a cache (the cache plugin exists only then), and rate_limiting only
// with the node's rate_limiting switch on, since the rate-limit plugin is
// always installed but applies nothing with the switch off.
func orgSectionsEnforced(plugins []pipeline.Plugin, cfg *config.Config) []string {
	seen := make(map[string]bool, len(orgSectionPlugins))
	for _, p := range plugins {
		section, ok := orgSectionPlugins[p.Name()]
		if !ok || (section == "rate_limiting" && !cfg.RateLimiting.Enabled) {
			continue
		}
		seen[section] = true
	}
	out := make([]string, 0, len(seen))
	for section := range seen {
		out = append(out, section)
	}
	sort.Strings(out)
	return out
}

// warnUnbuiltOptions logs one startup warning naming each configured option
// this build does not act on: an audit sink of type file or webhook (only the
// stdout sink is written) and cluster.redis_url (cluster membership is per
// node and the URL is never read). Those configs still start; the warning
// names the option, never its value (a sink URL or a Redis URL can carry a
// credential).
func warnUnbuiltOptions(cfg *config.Config) {
	var unused []string
	for i, sink := range cfg.Audit.Sinks {
		if sink.Type == "file" || sink.Type == "webhook" {
			unused = append(unused, fmt.Sprintf("audit.sinks[%d] (type %s: only the stdout sink is written)", i, sink.Type))
		}
	}
	if cfg.Cluster.RedisURL != "" {
		unused = append(unused, "cluster.redis_url (cluster membership is per node; the URL is not read)")
	}
	if len(unused) > 0 {
		slog.Warn("configured options not used by this build", "options", unused)
	}
}
