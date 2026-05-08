package main

import (
	"context"
	"flag"
	"log/slog"
	"os"
	"os/signal"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	alertpkg "github.com/futureagi/agentcc-gateway/internal/alerting"
	"github.com/futureagi/agentcc-gateway/internal/audit"
	"github.com/futureagi/agentcc-gateway/internal/auth"
	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/cache"
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
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/secrets"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/sysprompt"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/toolperm"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/topic"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/webhook"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	alertplugin "github.com/futureagi/agentcc-gateway/internal/plugins/alerting"
	auditplugin "github.com/futureagi/agentcc-gateway/internal/plugins/audit"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	budgetplugin "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
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
	secretspkg "github.com/futureagi/agentcc-gateway/internal/secrets"
	"github.com/futureagi/agentcc-gateway/internal/server"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// syncWithRetry retries control plane sync (org configs + API keys) with
// fixed 2s interval until both succeed. Called as a background goroutine
// so the gateway serves immediately while sync completes.
func syncWithRetry(cfg *config.Config, tenantStore *tenant.Store, keyStore *auth.KeyStore) {
	const retryInterval = 2 * time.Second
	const maxAttempts = 60 // 2 minutes max

	tenantSynced := false
	keySynced := keyStore == nil

	for attempt := 1; attempt <= maxAttempts; attempt++ {
		slog.Info("retrying control plane sync",
			"attempt", attempt, "need_tenants", !tenantSynced, "need_keys", !keySynced)

		if !tenantSynced {
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			if err := tenant.SyncFromControlPlane(ctx, cfg.ControlPlane.URL, cfg.ControlPlane.AdminToken, tenantStore); err == nil {
				tenantSynced = true
			}
			cancel()
		}
		if !keySynced {
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			if err := auth.SyncKeysFromControlPlane(ctx, cfg.ControlPlane.URL, cfg.ControlPlane.AdminToken, keyStore); err == nil {
				keySynced = true
			}
			cancel()
		}

		if tenantSynced && keySynced {
			keyCount := 0
			if keyStore != nil {
				keyCount = keyStore.Count()
			}
			slog.Info("background sync succeeded",
				"attempt", attempt, "orgs", tenantStore.Count(), "keys", keyCount)
			return
		}

		time.Sleep(retryInterval)
	}

	slog.Error("control plane sync failed after all retries — gateway running with empty config",
		"max_attempts", maxAttempts)
}

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

	// Create tenant store (per-org config isolation).
	tenantStore := tenant.NewStore()

	// Initialize shared Redis client for multi-replica state (rate limits,
	// budgets, credits, cluster). Non-fatal: if Redis is unavailable, the
	// gateway operates in single-instance mode with in-memory state.
	var redisClient *redisstate.Client
	var redisBudgetStore *redisstate.BudgetStore
	var redisCreditStore *redisstate.CreditStore
	redisPrefix := cfg.Redis.Prefix
	if redisPrefix == "" {
		redisPrefix = "agentcc:"
	}
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

	// Create keystore and auth plugin.
	var keyStore *auth.KeyStore
	var plugins []pipeline.Plugin

	// Create IP ACL plugin (priority 50: runs before auth).
	var ipaclPlugin *ipaclplugin.Plugin
	if cfg.IPACL.Enabled {
		ipaclPlugin = ipaclplugin.New(cfg.IPACL, tenantStore)
		plugins = append(plugins, ipaclPlugin)
		slog.Info("ip acl enabled", "allow_rules", ipaclPlugin.AllowCount(), "deny_rules", ipaclPlugin.DenyCount())
	}

	if cfg.Auth.Enabled {
		keyStore = auth.NewKeyStore(cfg.Auth)
		plugins = append(plugins, authplugin.New(keyStore, true))
		slog.Info("auth enabled", "config_keys", keyStore.Count())
	}

	// Start control plane sync in background — never blocks startup.
	// The gateway serves immediately with config.yaml keys, then picks up
	// Django-managed keys and org configs as soon as the backend is reachable.
	if cfg.ControlPlane.URL != "" {
		// Initial sync: retry every 2s until both succeed (non-blocking).
		if cfg.ControlPlane.SyncOnStartup {
			go syncWithRetry(cfg, tenantStore, keyStore)
		}
		// Periodic sync is handled by StartPeriodicSync below (line ~583).
		// Do NOT add a second periodic ticker here — it doubles the load.
		slog.Info("control plane sync enabled",
			"interval", cfg.ControlPlane.SyncInterval.String(),
			"startup_sync", cfg.ControlPlane.SyncOnStartup,
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

	// Create budget plugin.
	var budgetPlugin *budgetplugin.Plugin
	if cfg.Budgets.Enabled {
		tracker := budgetpkg.NewTracker(cfg.Budgets)
		pricing := budgetplugin.NewPricingLookup(cfg.CostTracking.CustomPricing)
		budgetPlugin = budgetplugin.New(tracker, pricing, true, tenantStore)
		if redisBudgetStore != nil {
			budgetPlugin.SetRedisBudget(redisBudgetStore)
		}
		plugins = append(plugins, budgetPlugin)
		slog.Info("budgets enabled", "default_period", cfg.Budgets.DefaultPeriod, "redis", redisBudgetStore != nil)

		// Seed budget counters from historical spend in Postgres (with retry).
		if cfg.ControlPlane.URL != "" {
			go func() {
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
				for orgID, orgSpend := range summary.Orgs {
					budgetPlugin.SeedOrgSpend(orgID, budgetplugin.OrgSpendSummary{
						TotalSpend: orgSpend.TotalSpend,
						PerKey:     orgSpend.PerKey,
						PerUser:    orgSpend.PerUser,
						PerModel:   orgSpend.PerModel,
					})
					// Seed global tracker org level.
					tracker.SeedSpend("org", orgID, orgSpend.TotalSpend, orgSpend.PerModel)
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
			}()
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
	var onOrgConfigChange func(string) // called when an org config changes (e.g. invalidate guardrail cache)
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
				guardrailRegistry[rule.Name] = webhook.New(rule.Name, rule.Config)
				slog.Info("registered webhook guardrail", "name", rule.Name)
			} else if expression.IsExpressionConfig(rule.Config) {
				guardrailRegistry[rule.Name] = expression.New(rule.Name, rule.Config)
				slog.Info("registered expression guardrail", "name", rule.Name)
			} else if futureagi.IsFutureAGIConfig(rule.Config) {
				guardrailRegistry[rule.Name] = futureagi.New(rule.Name, rule.Config)
				slog.Info("registered futureagi guardrail", "name", rule.Name)
			} else if toolperm.IsToolPermConfig(rule.Config) {
				guardrailRegistry[rule.Name] = toolperm.New(rule.Name, rule.Config)
				slog.Info("registered tool permission guardrail", "name", rule.Name)
			} else if mcpsec.IsMCPSecConfig(rule.Config) {
				guardrailRegistry[rule.Name] = mcpsec.New(rule.Name, rule.Config)
				slog.Info("registered MCP security guardrail", "name", rule.Name)
			} else if external.IsExternalProviderConfig(rule.Config) {
				guardrailRegistry[rule.Name] = external.New(rule.Name, rule.Config)
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

		// Dynamic factory creates guardrails from org config for managed mode.
		dynamicFactory := func(name string, cfg map[string]interface{}) guardrails.Guardrail {
			switch name {
			case "pii-detection":
				return pii.New(cfg)
			case "content-moderation":
				return contentmod.New(cfg)
			case "keyword-blocklist":
				return blocklist.New(cfg)
			case "input-validation":
				return validation.New(cfg)
			case "prompt-injection":
				return injection.New(cfg)
			case "secret-detection":
				return secrets.New(cfg)
			case "topic-restriction":
				return topic.New(cfg)
			case "language-detection":
				return language.New(cfg)
			case "system-prompt-protection":
				return sysprompt.New(cfg)
			case "hallucination-detection":
				return hallucination.New(cfg)
			case "data-leakage-prevention":
				return leakage.New(cfg)
			}
			if futureagi.IsFutureAGIConfig(cfg) {
				return futureagi.New(name, cfg)
			}
			if webhook.IsWebhookConfig(cfg) {
				return webhook.New(name, cfg)
			}
			if expression.IsExpressionConfig(cfg) {
				return expression.New(name, cfg)
			}
			if toolperm.IsToolPermConfig(cfg) {
				return toolperm.New(name, cfg)
			}
			if mcpsec.IsMCPSecConfig(cfg) {
				return mcpsec.New(name, cfg)
			}
			if external.IsExternalProviderConfig(cfg) {
				return external.New(name, cfg)
			}
			return nil
		}

		guardrailPlugin := guardrails.NewPlugin(grEngine, guardrailRegistry, dynamicFactory, policyStore, tenantStore)
		plugins = append(plugins, guardrailPlugin)
		onOrgConfigChange = func(orgID string) {
			guardrailPlugin.InvalidateDynamicCache(orgID)
			if budgetPlugin != nil {
				budgetPlugin.InvalidateOrg(orgID)
			}
			if ipaclPlugin != nil {
				ipaclPlugin.InvalidateOrg(orgID)
			}
		}
		slog.Info("guardrails enabled",
			"pre_guardrails", grEngine.PreCount(),
			"post_guardrails", grEngine.PostCount(),
			"registry_size", len(guardrailRegistry),
		)
	}

	// Ensure budget invalidation is wired even when guardrails are disabled.
	if onOrgConfigChange == nil && budgetPlugin != nil {
		onOrgConfigChange = func(orgID string) {
			budgetPlugin.InvalidateOrg(orgID)
		}
	}

	// Create tool policy plugin (priority 140: after auth, before validation).
	if cfg.ToolPolicy.Enabled {
		plugins = append(plugins, toolpolicyplugin.New(cfg.ToolPolicy, tenantStore))
		slog.Info("tool policy enabled",
			"allow", len(cfg.ToolPolicy.Allow),
			"deny", len(cfg.ToolPolicy.Deny),
			"max_tools", cfg.ToolPolicy.MaxToolsPerRequest,
		)
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
	mdb := modeldb.New(modeldb.BundledModels, server.ConvertModelOverrides(cfg.ModelDatabase.Overrides))
	slog.Info("model database initialized", "models", mdb.Count())
	var sharedModelDB atomic.Pointer[modeldb.ModelDB]
	sharedModelDB.Store(mdb)
	modelDBGetter := func() *modeldb.ModelDB { return sharedModelDB.Load() }

	// Create model validation plugin (priority 150: after auth, before cache).
	if cfg.ModelDatabase.IsValidationEnabled() {
		plugins = append(plugins, validationplugin.New(true, modelDBGetter))
		slog.Info("model validation enabled")
	}

	// Create cost tracking plugin.
	plugins = append(plugins, costplugin.New(cfg.CostTracking.Enabled, modelDBGetter, tenantStore, cfg.CostTracking.AliasCostFactors))

	// Create credits plugin for managed keys with optional Redis backend.
	if keyStore != nil {
		creditsPlugin := creditsplugin.New(true, keyStore)
		if redisCreditStore != nil {
			creditsPlugin.SetRedisCredit(redisCreditStore)

			// Seed Redis credit balances from local keystore (Postgres-sourced).
			// Uses SETNX so it won't overwrite if another replica already seeded.
			seeded := 0
			for _, k := range keyStore.List() {
				if !k.IsManaged() {
					continue
				}
				micros := k.BalanceMicros()
				if micros <= 0 {
					continue
				}
				if redisCreditStore.SeedBalance(k.ID, micros) {
					seeded++
				}
			}
			if seeded > 0 {
				slog.Info("seeded credit balances into redis", "count", seeded)
			}
		}
		plugins = append(plugins, creditsPlugin)
	}

	// Create logging plugin.
	loggingPlugin := loggingplugin.New(cfg.Logging.RequestLogging, tenantStore)

	// Attach privacy redactor if enabled.
	if cfg.Privacy.Enabled {
		patterns := make([]privacy.PatternConfig, len(cfg.Privacy.Patterns))
		for i, p := range cfg.Privacy.Patterns {
			patterns[i] = privacy.PatternConfig{Name: p.Name, Pattern: p.Pattern}
		}
		redactor := privacy.New(cfg.Privacy.Mode, patterns)
		loggingPlugin.SetRedactor(redactor)
		slog.Info("privacy mode enabled", "mode", cfg.Privacy.Mode, "patterns", redactor.PatternCount())
	}

	plugins = append(plugins, loggingPlugin)

	// Start log flusher if control plane URL is configured.
	if cfg.ControlPlane.URL != "" && cfg.Logging.RequestLogging.Enabled {
		flushInterval := 5 * time.Second
		maxBuffered := 5000
		webhookURL := loggingplugin.FormatLogsWebhookURL(cfg.ControlPlane.URL)
		logFlusher := loggingplugin.NewLogFlusher(webhookURL, cfg.ControlPlane.WebhookSecret, flushInterval, maxBuffered)
		loggingPlugin.SetFlusher(logFlusher)
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

	// Create alerting plugin.
	var alertingPlugin *alertplugin.Plugin
	if cfg.Alerting.Enabled {
		alertManager := alertpkg.NewManager(cfg.Alerting)
		alertingPlugin = alertplugin.New(alertManager, true, tenantStore)
		plugins = append(plugins, alertingPlugin)
		slog.Info("alerting enabled", "rules", alertManager.RuleCount())
	}

	if onOrgConfigChange != nil {
		prev := onOrgConfigChange
		onOrgConfigChange = func(orgID string) {
			prev(orgID)
			if alertingPlugin != nil {
				alertingPlugin.InvalidateOrg(orgID)
			}
			if loggingPlugin != nil {
				loggingPlugin.InvalidateOrg(orgID)
			}
		}
	} else if alertingPlugin != nil || loggingPlugin != nil || ipaclPlugin != nil {
		onOrgConfigChange = func(orgID string) {
			if alertingPlugin != nil {
				alertingPlugin.InvalidateOrg(orgID)
			}
			if loggingPlugin != nil {
				loggingPlugin.InvalidateOrg(orgID)
			}
			if ipaclPlugin != nil {
				ipaclPlugin.InvalidateOrg(orgID)
			}
		}
	}

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
		plugins = append(plugins, otelPlugin)
		slog.Info("otel enabled", "exporter", cfg.OTel.Exporter, "sample_rate", cfg.OTel.SampleRate)
	}

	// Create pipeline engine.
	engine := pipeline.NewEngine(plugins...)

	// Create and start server (shares the same ModelDB pointer for hot-reload).
	srv := server.New(cfg, *configPath, registry, engine, keyStore, grEngine, policyStore, metricsRegistry, &sharedModelDB, tenantStore, onOrgConfigChange)

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

	// Start periodic config sync if configured.
	if cfg.ControlPlane.SyncInterval > 0 && cfg.ControlPlane.URL != "" {
		go tenant.StartPeriodicSync(syncCtxBg, cfg.ControlPlane.SyncInterval, cfg.ControlPlane.URL, cfg.ControlPlane.AdminToken, tenantStore, keyStore)
	}

	// Handle shutdown signals.
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)

	go func() {
		sig := <-sigCh
		slog.Info("received signal", "signal", sig)

		// Stop periodic sync.
		syncCancelBg()

		shutdownCtx, cancel := context.WithTimeout(context.Background(), cfg.Server.ShutdownTimeout)
		defer cancel()

		if err := srv.Shutdown(shutdownCtx); err != nil {
			slog.Error("shutdown error", "error", err)
			os.Exit(1)
		}

		// Drain buffered trace records.
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
	}()

	if err := srv.Start(); err != nil {
		slog.Error("server error", "error", err)
		os.Exit(1)
	}
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
