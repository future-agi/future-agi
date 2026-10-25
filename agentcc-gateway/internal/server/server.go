package server

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"time"

	catalogdata "github.com/futureagi/agentcc-gateway/catalog"
	"github.com/futureagi/agentcc-gateway/internal/a2a"
	"github.com/futureagi/agentcc-gateway/internal/async"
	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/batch"
	"github.com/futureagi/agentcc-gateway/internal/cluster"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/edge"
	"github.com/futureagi/agentcc-gateway/internal/files"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/speech"
	"github.com/futureagi/agentcc-gateway/internal/realtime"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/responses"
	"github.com/futureagi/agentcc-gateway/internal/rotation"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/scheduled"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/futureagi/agentcc-gateway/internal/video"
)

// Server is the Agentcc gateway HTTP server.
type Server struct {
	cfg              *config.Config
	configPath       string
	httpServer       *http.Server
	router           *Router
	registry         *providers.Registry
	engine           *pipeline.Engine
	handlers         *Handlers
	keyStore         *auth.KeyStore
	keyHandlers      *KeyHandlers
	metricsRegistry  *metrics.Registry
	batchProcessor   *batch.Processor
	clusterMgr       *cluster.Manager
	TenantStore      *tenant.Store
	OrgProviderCache *providers.OrgProviderCache
	asyncWorker      *async.Worker
	shadowFlusher    *routing.ShadowFlusher
	ready            atomic.Bool
	// waitForKeyList holds /readyz at 503 until the key store has loaded a
	// key list from the control plane, when one syncs keys (readyHandler).
	waitForKeyList bool
	managed        ManagedSettings
	mcp            *mcp.Server // nil when MCP is disabled (routes_mcp.go)

	// orgSectionsEnforced is GET /-/config's org_sections_enforced, set by
	// SetOrgSectionsEnforced; nil leaves the field out.
	orgSectionsEnforced []string
	// orgSectionsNotEnforced is GET /-/config's org_sections_not_enforced:
	// section -> why this node does not enforce it (SetOrgSectionsNotEnforced).
	orgSectionsNotEnforced map[string]string
	// rotation serves provider key rotation (admin_rotation.go).
	rotation *rotation.Manager
}

// routeStub is one routes_<concern>.go file's registration function.
type routeStub struct {
	name     string
	register func(s *Server, router *Router)
}

// routeStubs are the per-concern route files, registered by New in this
// order. Each concern has its own routes_<concern>.go file.
var routeStubs = []routeStub{
	{"catalog_admin", (*Server).registerCatalogAdminRoutes},
	{"credits_admin", (*Server).registerCreditsAdminRoutes},
	{"capabilities", (*Server).registerCapabilitiesRoutes},
	{"models", (*Server).registerModelsRoutes},
	{"generation", (*Server).registerGenerationRoutes},
	{"account", (*Server).registerAccountRoutes},
	{"key", (*Server).registerKeyRoutes},
	{"feedback", (*Server).registerFeedbackRoutes},
	{"catalog_access", (*Server).registerCatalogAccessRoutes},
	{"replay", (*Server).registerReplayRoutes},
	{"spendguard", (*Server).registerSpendGuardRoutes},
	{"runtime", (*Server).registerRuntimeRoutes},
	{"mcp", (*Server).registerMCPRoutes},
	{"capability_echo", (*Server).registerCapabilityEchoRoutes},
}

// Handlers returns the server's HTTP handlers, for the wiring that installs
// dependencies on them after New.
func (s *Server) Handlers() *Handlers { return s.handlers }

// SetKeyRevocationPublisher attaches a pub/sub broadcaster for multi-replica
// key revocation. Call after New() and before Start().
func (s *Server) SetKeyRevocationPublisher(pub KeyRevocationPublisher) {
	if s.keyHandlers != nil {
		s.keyHandlers.SetRevocationPublisher(pub)
	}
}

// New creates a new gateway server.
func New(cfg *config.Config, configPath string, registry *providers.Registry, engine *pipeline.Engine, keyStore *auth.KeyStore, guardrailEngine *guardrails.Engine, policyStore *policy.Store, metricsRegistry *metrics.Registry, modelDBPtr *atomic.Pointer[modeldb.ModelDB], tenantStore *tenant.Store, onOrgConfigChange func(string), redisClients ...*redisstate.Client) *Server {
	var redisClient *redisstate.Client
	if len(redisClients) > 0 {
		redisClient = redisClients[0]
	}
	authEnabled := false
	if cfg != nil {
		authEnabled = cfg.Auth.Enabled
	}
	if cfg != nil && keyStore == nil {
		keyStore = auth.NewKeyStore(cfg.Auth)
	}
	// Load settles this, but a Config built in code need not have been through
	// it, and a server without a ceiling is one no request is bounded by.
	if cfg != nil && cfg.Server.MaxRequestTimeout <= 0 {
		cfg.Server.MaxRequestTimeout = cfg.Server.WriteTimeout
	}
	authKeyStore := keyStore
	if !authEnabled {
		authKeyStore = nil
	}

	orgProviderCache := providers.NewOrgProviderCache(cfg.Providers, cfg.OrgProviders.AllowPrivateURLs)
	if cfg.OrgProviders.AllowPrivateURLs {
		slog.Warn("org provider base URLs may point at private/LAN addresses; do not enable this on a gateway shared by untrusted orgs",
			"env", config.EnvAllowPrivateProviderURLs)
	}

	s := &Server{
		cfg:              cfg,
		configPath:       configPath,
		registry:         registry,
		engine:           engine,
		keyStore:         keyStore,
		metricsRegistry:  metricsRegistry,
		TenantStore:      tenantStore,
		OrgProviderCache: orgProviderCache,
	}
	// The startup config decides: cmd/agentcc starts the control plane sync
	// once, from it, and a reload does not restart it.
	s.waitForKeyList = cfg.ControlPlane.SyncsKeys()

	// Create retry and failover handlers if routing is configured.
	var retryer *routing.Retryer
	if cfg.Routing.Retry.Enabled {
		retryer = routing.NewRetryer(cfg.Routing.Retry)
		slog.Info("retry enabled",
			"max_retries", cfg.Routing.Retry.MaxRetries,
		)
	}

	// Create circuit breaker registry.
	var cbReg *routing.CircuitBreakerRegistry
	if cfg.Routing.CircuitBreaker.Enabled && registry.Router() != nil {
		cbReg = routing.NewCircuitBreakerRegistry(cfg.Routing.CircuitBreaker, registry.Router().SetHealthy)
		slog.Info("circuit breaker enabled",
			"failure_threshold", cfg.Routing.CircuitBreaker.FailureThreshold,
			"cooldown", cfg.Routing.CircuitBreaker.Cooldown,
		)
	}

	var failover *routing.Failover
	if registry.Router() != nil && cfg.Routing.Failover.Enabled {
		failover = routing.NewFailover(cfg.Routing.Failover, registry.Router(), retryer, cbReg)
		slog.Info("failover enabled",
			"max_attempts", cfg.Routing.Failover.MaxAttempts,
		)
	}

	// Create model fallback chains.
	var modelFallbacks *routing.ModelFallbacks
	if len(cfg.Routing.ModelFallbacks) > 0 {
		modelFallbacks = routing.NewModelFallbacks(cfg.Routing.ModelFallbacks)
		slog.Info("model fallbacks configured",
			"models", len(cfg.Routing.ModelFallbacks),
		)
	}

	// Create conditional router.
	var condRouter *routing.ConditionalRouter
	if len(cfg.Routing.ConditionalRoutes) > 0 {
		var err error
		condRouter, err = routing.NewConditionalRouter(cfg.Routing.ConditionalRoutes)
		if err != nil {
			slog.Error("failed to create conditional router", "error", err)
		} else if condRouter != nil {
			slog.Info("conditional routing enabled",
				"routes", condRouter.RouteCount(),
			)
		}
	}

	// Create health monitor.
	var healthMonitor *routing.HealthMonitor
	var latencyTracker *routing.LatencyTracker
	if registry.Router() != nil {
		latencyTracker = registry.Router().GetLatencyTracker()
	}
	healthMonitor = routing.NewHealthMonitor(latencyTracker, cbReg)

	// Create traffic mirror.
	var mirror *routing.Mirror
	if cfg.Routing.Mirror.Enabled && len(cfg.Routing.Mirror.Rules) > 0 {
		mirrorLookup := func(providerID string) (routing.MirrorProvider, bool) {
			return registry.GetProvider(providerID)
		}
		mirror = routing.NewMirror(cfg.Routing.Mirror, mirrorLookup)
		slog.Info("traffic mirroring enabled",
			"rules", len(cfg.Routing.Mirror.Rules),
		)

		// Shadow result capture: create store + flusher when capture is enabled.
		if cfg.Routing.Mirror.CaptureResults {
			maxStored := cfg.Routing.Mirror.MaxStored
			if maxStored <= 0 {
				maxStored = 10000
			}
			shadowStore := routing.NewShadowStore(maxStored)
			mirror.Store = shadowStore

			// Start flusher if control plane URL is configured.
			if cfg.ControlPlane.URL != "" {
				flushInterval := time.Duration(cfg.Routing.Mirror.FlushIntervalSec) * time.Second
				if flushInterval <= 0 {
					flushInterval = 60 * time.Second
				}
				webhookURL := routing.FormatWebhookURL(cfg.ControlPlane.URL)
				s.shadowFlusher = routing.NewShadowFlusher(shadowStore, webhookURL, cfg.ControlPlane.WebhookSecret, flushInterval)
				go s.shadowFlusher.Run(context.Background())
				slog.Info("shadow result capture enabled",
					"max_stored", maxStored,
					"flush_interval", flushInterval.String(),
					"webhook_url", webhookURL,
				)
			} else {
				slog.Info("shadow result capture enabled (no flusher — control_plane.url not set)",
					"max_stored", maxStored,
				)
			}
		}
	}

	handlers := NewHandlers(registry, engine, cfg.Server.MaxRequestBodySize, cfg.Server.DefaultRequestTimeout, cfg.Server.MaxRequestTimeout, failover, modelFallbacks, condRouter, healthMonitor, cfg.Routing.ModelTimeouts, mirror, guardrailEngine, policyStore, cfg.Guardrails.Streaming, modelDBPtr, tenantStore, orgProviderCache, authKeyStore)
	// A streamed completion exists nowhere else — it has to be assembled while
	// the chunks go past, and only if something is going to record it.
	handlers.SetCaptureStreamContent(cfg.Logging.RequestLogging.IncludeBodies || (cfg.OTel.Enabled && cfg.OTel.IncludeBodies))
	handlers.reserveDefaultOutputTokens = cfg.Managed.ReserveDefaultOutputTokens
	s.handlers = handlers

	// The model shutdown calendar and the provider hosts it is looked up by.
	if cal, err := catalogdata.Deprecations(); err != nil {
		slog.Error("model shutdown calendar not loaded; no model is refused as shut down", "error", err)
	} else {
		handlers.deprecations = cal
	}
	handlers.providerHosts = providerHostsFor(cfg, registry)
	// catalog.thought_signature_models as the operator set it: nil
	// keeps the eligibility engine's own rule.
	handlers.thoughtSignatureModels = cfg.Catalog.ThoughtSignatureModels

	// Set up Files API store.
	fileStore := files.NewStore()
	handlers.fileStore = fileStore

	// Set up Responses API store.
	responsesStore := responses.NewStore(responses.DefaultResponseTTL)
	handlers.responsesStore = responsesStore

	// Set up Video Generation store.
	handlers.videoStore = video.NewMemoryStore()

	// Set up async inference support.
	asyncStore := async.NewStore()
	handlers.asyncStore = asyncStore

	// Each job carries its own executor, built by the handler that admitted
	// it (AsyncMiddleware), so it runs on that request's routing. The worker
	// has no provider of its own; asyncWorkerConfig bounds how much of it one
	// org may hold.
	asyncWorker := async.NewWorker(asyncStore, asyncWorkerConfig(cfg))
	handlers.asyncWorker = asyncWorker
	s.asyncWorker = asyncWorker
	asyncWorker.Start()

	// --- Phase 12A: Advanced routing initialization ---

	// Complexity-based routing.
	if cfg.Routing.Complexity.Enabled {
		handlers.complexityAnalyzer = routing.NewComplexityAnalyzer(cfg.Routing.Complexity)
		if handlers.complexityAnalyzer != nil {
			slog.Info("complexity routing enabled",
				"tiers", len(cfg.Routing.Complexity.Tiers),
			)
		}
	}

	// Fastest-response (race) mode.
	if cfg.Routing.DefaultStrategy == "fastest" || cfg.Routing.Fastest.MaxConcurrent > 0 {
		handlers.raceExecutor = routing.NewRaceExecutor(
			cfg.Routing.Fastest.MaxConcurrent,
			cfg.Routing.Fastest.CancelDelay,
			cfg.Routing.Fastest.ExcludedProviders,
		)
		slog.Info("fastest-response (race) mode enabled",
			"max_concurrent", cfg.Routing.Fastest.MaxConcurrent,
		)
	}

	// Provider locking.
	handlers.providerLockResolver = routing.NewProviderLockResolver(cfg.Routing.ProviderLock)
	handlers.routingStrategy = cfg.Routing.DefaultStrategy

	// Model access groups.
	if len(cfg.Routing.AccessGroups) > 0 {
		handlers.accessGroupChecker = routing.NewAccessGroupChecker(cfg.Routing.AccessGroups)
		slog.Info("model access groups configured",
			"groups", len(cfg.Routing.AccessGroups),
		)
	}

	// Scheduled completions.
	if cfg.Routing.Scheduled.Enabled {
		scheduledStore := scheduled.NewMemoryStore(cfg.Routing.Scheduled.MaxPendingJobs)
		handlers.scheduledStore = scheduledStore
		handlers.scheduledMaxAhead = cfg.Routing.Scheduled.MaxScheduleAhead
		retryAttempts := cfg.Routing.Scheduled.RetryAttempts
		if retryAttempts <= 0 {
			retryAttempts = 3
		}
		handlers.scheduledRetryAttempts = retryAttempts

		// The execute function calls back into the gateway pipeline.
		schedExecuteFn := func(requestJSON json.RawMessage) (json.RawMessage, error) {
			var req models.ChatCompletionRequest
			if err := json.Unmarshal(requestJSON, &req); err != nil {
				return nil, fmt.Errorf("invalid scheduled request: %w", err)
			}

			rc := models.AcquireRequestContext()
			// Each run of a job (a retry is another run) is a run of its own,
			// which no HTTP request carries in: a new request id and trace
			// id from the source the RequestID middleware uses.
			rc.RequestID = middleware.NewID()
			rc.TraceID = middleware.NewID()
			rc.Model = req.Model
			rc.Request = &req
			rc.IsStream = false

			provider, err := registry.Resolve(req.Model)
			if err != nil {
				rc.Release()
				return nil, fmt.Errorf("no provider for model %q: %w", req.Model, err)
			}
			rc.Provider = provider.ID()

			providerCall := func(callCtx context.Context, callRC *models.RequestContext) error {
				resp, err := provider.ChatCompletion(callCtx, callRC.Request)
				if err != nil {
					return err
				}
				callRC.Response = resp
				callRC.ResolvedModel = resp.Model
				return nil
			}

			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
			defer cancel()
			if err := engine.Process(ctx, rc, providerCall); err != nil {
				rc.Release()
				return nil, err
			}
			resp := rc.Response
			rc.Release()
			if resp == nil {
				return nil, fmt.Errorf("no response from provider")
			}
			return json.Marshal(resp)
		}

		scheduler := scheduled.NewScheduler(
			scheduledStore, schedExecuteFn,
			cfg.Routing.Scheduled.ResultTTL,
			cfg.Routing.Scheduled.RetryBackoff,
			cfg.Routing.Scheduled.WorkerCount,
		)
		_ = scheduler // scheduler runs in background goroutines
		slog.Info("scheduled completions enabled",
			"workers", cfg.Routing.Scheduled.WorkerCount,
		)
	}

	// Build route table.
	router := NewRouter()
	s.router = router

	// Proxy endpoints.
	router.Handle("POST", "/v1/chat/completions", handlers.ChatCompletion)
	router.Handle("POST", "/v1/completions", handlers.TextCompletion)
	router.Handle("POST", "/v1/embeddings", handlers.CreateEmbedding)
	router.Handle("POST", "/v1/images/generations", handlers.CreateImage)
	router.Handle("POST", "/v1/audio/speech", handlers.CreateSpeech)
	router.Handle("POST", "/v1/audio/transcriptions", handlers.CreateTranscription)
	router.Handle("POST", "/v1/audio/speech/stream", handlers.StreamSpeech)
	router.Handle("POST", "/v1/audio/translations", handlers.CreateTranslation)
	router.Handle("POST", "/v1/rerank", handlers.Rerank)
	router.Handle("POST", "/v1/responses", handlers.CreateResponse)
	router.Handle("GET", "/v1/responses/{id}", handlers.GetResponse)
	router.Handle("DELETE", "/v1/responses/{id}", handlers.DeleteResponse)
	// Files API.
	router.Handle("POST", "/v1/files", handlers.UploadFile)
	router.Handle("GET", "/v1/files", handlers.ListFiles)
	router.Handle("GET", "/v1/files/{file_id}", handlers.GetFile)
	router.Handle("DELETE", "/v1/files/{file_id}", handlers.DeleteFile)
	router.Handle("GET", "/v1/files/{file_id}/content", handlers.GetFileContent)

	// Native Anthropic Messages API.
	router.Handle("POST", "/v1/messages/count_tokens", handlers.AnthropicCountTokens) // count_tokens before /v1/messages
	router.Handle("POST", "/v1/messages", handlers.AnthropicMessages)

	router.Handle("POST", "/v1/count_tokens", handlers.CountTokens)

	// Assistants API. With assistants.enabled false (default true) every
	// assistants, threads and vector stores route answers 404 naming the
	// switch, and nothing is proxied.
	assistantsRoute := assistantsSwitch(cfg.Assistants.Enabled)
	router.Handle("POST", "/v1/assistants", assistantsRoute(handlers.CreateAssistant))
	router.Handle("GET", "/v1/assistants", assistantsRoute(handlers.ListAssistants))
	router.Handle("GET", "/v1/assistants/{assistant_id}", assistantsRoute(handlers.GetAssistant))
	router.Handle("POST", "/v1/assistants/{assistant_id}", assistantsRoute(handlers.UpdateAssistant))
	router.Handle("DELETE", "/v1/assistants/{assistant_id}", assistantsRoute(handlers.DeleteAssistant))

	// Threads.
	router.Handle("POST", "/v1/threads", assistantsRoute(handlers.CreateThread))
	// NOTE: /v1/threads/runs must be before /v1/threads/{thread_id} to avoid "runs" matching as thread_id.
	router.Handle("POST", "/v1/threads/runs", assistantsRoute(handlers.CreateThreadAndRun))
	router.Handle("GET", "/v1/threads/{thread_id}", assistantsRoute(handlers.GetThread))
	router.Handle("POST", "/v1/threads/{thread_id}", assistantsRoute(handlers.UpdateThread))
	router.Handle("DELETE", "/v1/threads/{thread_id}", assistantsRoute(handlers.DeleteThread))

	// Messages.
	router.Handle("POST", "/v1/threads/{thread_id}/messages", assistantsRoute(handlers.CreateMessage))
	router.Handle("GET", "/v1/threads/{thread_id}/messages", assistantsRoute(handlers.ListMessages))
	router.Handle("GET", "/v1/threads/{thread_id}/messages/{message_id}", assistantsRoute(handlers.GetMessage))
	router.Handle("POST", "/v1/threads/{thread_id}/messages/{message_id}", assistantsRoute(handlers.UpdateMessage))
	router.Handle("DELETE", "/v1/threads/{thread_id}/messages/{message_id}", assistantsRoute(handlers.DeleteMessage))

	// Runs.
	router.Handle("POST", "/v1/threads/{thread_id}/runs", assistantsRoute(handlers.CreateRun))
	router.Handle("GET", "/v1/threads/{thread_id}/runs", assistantsRoute(handlers.ListRuns))
	// NOTE: cancel and submit_tool_outputs must be before /v1/threads/{thread_id}/runs/{run_id}.
	router.Handle("POST", "/v1/threads/{thread_id}/runs/{run_id}/cancel", assistantsRoute(handlers.CancelRun))
	router.Handle("POST", "/v1/threads/{thread_id}/runs/{run_id}/submit_tool_outputs", assistantsRoute(handlers.SubmitToolOutputs))
	router.Handle("GET", "/v1/threads/{thread_id}/runs/{run_id}", assistantsRoute(handlers.GetRun))
	router.Handle("POST", "/v1/threads/{thread_id}/runs/{run_id}", assistantsRoute(handlers.UpdateRun))

	// Run Steps.
	router.Handle("GET", "/v1/threads/{thread_id}/runs/{run_id}/steps", assistantsRoute(handlers.ListRunSteps))
	router.Handle("GET", "/v1/threads/{thread_id}/runs/{run_id}/steps/{step_id}", assistantsRoute(handlers.GetRunStep))

	// Vector Stores API.
	router.Handle("POST", "/v1/vector_stores", assistantsRoute(handlers.CreateVectorStore))
	router.Handle("GET", "/v1/vector_stores", assistantsRoute(handlers.ListVectorStores))
	// NOTE: Specific sub-resource routes before generic {vector_store_id} routes.
	router.Handle("POST", "/v1/vector_stores/{vector_store_id}/search", assistantsRoute(handlers.SearchVectorStore))
	router.Handle("POST", "/v1/vector_stores/{vector_store_id}/files", assistantsRoute(handlers.CreateVectorStoreFile))
	router.Handle("GET", "/v1/vector_stores/{vector_store_id}/files", assistantsRoute(handlers.ListVectorStoreFiles))
	router.Handle("DELETE", "/v1/vector_stores/{vector_store_id}/files/{file_id}", assistantsRoute(handlers.DeleteVectorStoreFile))
	router.Handle("POST", "/v1/vector_stores/{vector_store_id}/file_batches", assistantsRoute(handlers.CreateVectorStoreFileBatch))
	router.Handle("GET", "/v1/vector_stores/{vector_store_id}", assistantsRoute(handlers.GetVectorStore))
	router.Handle("POST", "/v1/vector_stores/{vector_store_id}", assistantsRoute(handlers.UpdateVectorStore))
	router.Handle("DELETE", "/v1/vector_stores/{vector_store_id}", assistantsRoute(handlers.DeleteVectorStore))

	// Native Google GenAI API.
	router.Handle("POST", "/v1beta/models/{model_action}", handlers.GenAIHandler)
	router.Handle("POST", "/v1beta/models/{provider}/{model_action}", handlers.GenAIHandler)

	// Search API.
	router.Handle("POST", "/v1/search", handlers.Search)

	// OCR API.
	router.Handle("POST", "/v1/ocr", handlers.OCR)

	// Realtime WebSocket API.
	realtimeTracker := realtime.NewSessionTracker(5)
	realtimeHandler := NewRealtimeHandler(realtimeTracker, registry, authKeyStore, realtimeHandlerConfig{
		MaxSessionDuration: 3600 * time.Second,
		PingInterval:       30 * time.Second,
		PongTimeout:        10 * time.Second,
		ReadBufferSize:     32768,
		WriteBufferSize:    32768,
		MaxMessageSize:     16 * 1024 * 1024,
		AllowedOrigins:     cfg.CORS.AllowedOrigins,
	})
	router.Handle("GET", "/v1/realtime", realtimeHandler.HandleRealtime)

	// Speech vendors' own streaming APIs (Deepgram, Cartesia), each under the vendor's name.
	for _, ep := range speech.Endpoints() {
		router.Handle("GET", "/"+ep.Vendor+ep.Path, handlers.SpeechRelay(ep.Vendor, ep.Path))
	}

	// Video Generation API.
	router.Handle("POST", "/v1/videos", handlers.SubmitVideo)
	router.Handle("GET", "/v1/videos", handlers.ListVideos)
	// NOTE: specific sub-routes before generic {video_id}.
	router.Handle("GET", "/v1/videos/{video_id}", handlers.GetVideoStatus)
	router.Handle("DELETE", "/v1/videos/{video_id}", handlers.DeleteVideo)

	// Scheduled completions API.
	router.Handle("POST", "/v1/scheduled", handlers.SubmitScheduled)
	router.Handle("GET", "/v1/scheduled", handlers.ListScheduledJobs)
	router.Handle("GET", "/v1/scheduled/{job_id}", handlers.GetScheduledJob)
	router.Handle("DELETE", "/v1/scheduled/{job_id}", handlers.CancelScheduledJob)

	// Async inference endpoints.
	router.Handle("GET", "/v1/async/{job_id}", handlers.GetAsyncJob)
	router.Handle("DELETE", "/v1/async/{job_id}", handlers.DeleteAsyncJob)

	// Health endpoints.
	router.Handle("GET", "/healthz", s.healthHandler)
	router.Handle("GET", "/readyz", s.readyHandler)
	router.Handle("GET", "/livez", s.healthHandler)

	// Admin endpoints (core).
	router.Handle("POST", "/-/reload", s.reloadHandler)
	router.Handle("GET", "/-/config", s.configHandler)
	router.Handle("GET", "/-/health/providers", s.providerHealthHandler)
	router.Handle("GET", "/-/health/providers/{org_id}", s.orgProviderHealthHandler)

	// Prometheus metrics endpoint, at prometheus.path (default /-/metrics).
	if metricsRegistry != nil {
		router.Handle("GET", metricsPath(cfg), s.metricsHandler)
	}

	// Batch API endpoints. Each item runs as a POST /v1/chat/completions
	// request through this server's own handler chain (built below), as a
	// request authenticated by the batch key the submit names: its
	// own request id, the batch request's trace id, and the key's org config,
	// providers, guardrails, budgets, credits and request log. With auth on,
	// a submit must name that key.
	s.batchProcessor = batch.NewProcessor(
		batch.RunAsChatRequest(func() http.Handler { return s.httpServer.Handler }),
		batch.WithKeyStore(keyStore, authEnabled),
	)
	router.Handle("POST", "/-/batches", s.submitBatchHandler)
	router.Handle("GET", "/-/batches/{batch_id}", s.getBatchHandler)
	router.Handle("POST", "/-/batches/{batch_id}/cancel", s.cancelBatchHandler)

	// Warn at startup if admin token is not configured.
	if cfg.Admin.Token == "" {
		slog.Warn("AGENTCC_ADMIN_TOKEN is not set — all admin endpoints (/-/keys, /-/orgs, /-/config, " + metricsPath(cfg) + ", /-/batches) will return 401. Set admin.token in config.yaml or AGENTCC_ADMIN_TOKEN env var.")
	}

	// Admin key management endpoints.
	if keyStore != nil {
		s.keyHandlers = NewKeyHandlers(keyStore, cfg.Admin.Token)
		router.Handle("GET", "/-/keys", s.keyHandlers.ListKeys)
		router.Handle("POST", "/-/keys", s.keyHandlers.CreateKey)
		router.Handle("POST", "/-/keys/sync", s.keyHandlers.ImportKeys)
		router.Handle("GET", "/-/keys/{key_id}", s.keyHandlers.GetKey)
		router.Handle("DELETE", "/-/keys/{key_id}", s.keyHandlers.RevokeKey)
		router.Handle("PUT", "/-/keys/{key_id}", s.keyHandlers.UpdateKey)
		router.Handle("POST", "/-/keys/{key_id}/credits", s.keyHandlers.AddCredits)
	}

	// Per-org config admin endpoints (multi-tenant).
	{
		orgHandlers := NewOrgConfigHandlers(tenantStore, cfg.Admin.Token, orgProviderCache)
		if onOrgConfigChange != nil {
			orgHandlers.SetOnConfigChange(onOrgConfigChange)
		}
		router.Handle("PUT", "/-/orgs/{org_id}/config", orgHandlers.SetOrgConfig)
		router.Handle("GET", "/-/orgs/{org_id}/config", orgHandlers.GetOrgConfig)
		router.Handle("DELETE", "/-/orgs/{org_id}/config", orgHandlers.DeleteOrgConfig)
		router.Handle("GET", "/-/orgs/configs", orgHandlers.ListOrgConfigs)
		router.Handle("POST", "/-/orgs/configs/bulk", orgHandlers.BulkLoadOrgConfigs)
	}

	// Per-concern routes, each in its own routes_<concern>.go file.
	for _, rs := range routeStubs {
		rs.register(s, router)
	}

	// Key rotation admin endpoints: each config.yaml provider with
	// rotation.enabled, with its own drain_period; a promote validates the
	// new key upstream, then swaps the provider's instance (admin_rotation.go).
	{
		s.rotation = newRotationManager(cfg, registry)
		rotHandlers := NewRotationHandlers(s.rotation, cfg.Admin.Token)
		router.Handle("POST", "/-/admin/providers/{id}/rotate", rotHandlers.StartRotation)
		router.Handle("GET", "/-/admin/providers/{id}/rotation", rotHandlers.GetRotationStatus)
		router.Handle("POST", "/-/admin/providers/{id}/rotate/promote", rotHandlers.PromoteRotation)
		router.Handle("POST", "/-/admin/providers/{id}/rotate/rollback", rotHandlers.RollbackRotation)
	}

	// Cluster admin endpoint.
	if cfg.Cluster.Enabled {
		clusterMgr := cluster.NewManager(cfg.Cluster, cfg.Addr(), "0.1.0")
		s.clusterMgr = clusterMgr
		clusterHandlers := NewClusterHandlers(clusterMgr, cfg.Admin.Token)
		router.Handle("GET", "/-/cluster/nodes", clusterHandlers.ListNodes)
	}

	// Edge config endpoint — generates CF Worker script.
	if cfg.Edge.Enabled {
		edgeCfg := cfg.Edge
		router.Handle("GET", "/-/admin/edge/config", func(w http.ResponseWriter, r *http.Request) {
			if !s.checkAdminAuth(w, r) {
				return
			}
			script := edge.Generate(edgeCfg)
			w.Header().Set("Content-Type", "application/javascript")
			w.WriteHeader(http.StatusOK)
			w.Write([]byte(script))
		})
	}

	// A2A protocol.
	if cfg.A2A.Enabled {
		// Build agent configs for the registry.
		agentConfigs := make(map[string]a2a.AgentConfig, len(cfg.A2A.Agents))
		for name, agentCfg := range cfg.A2A.Agents {
			skills := make([]a2a.Skill, 0, len(agentCfg.Skills))
			for _, s := range agentCfg.Skills {
				skills = append(skills, a2a.Skill{
					ID: s.ID, Name: s.Name, Description: s.Description,
				})
			}
			agentConfigs[name] = a2a.AgentConfig{
				URL: agentCfg.URL,
				Auth: a2a.A2AAuth{
					Type:   agentCfg.Auth.Type,
					Token:  agentCfg.Auth.Token,
					Header: agentCfg.Auth.Header,
					Key:    agentCfg.Auth.Key,
				},
				Description: agentCfg.Description,
				Skills:      skills,
			}
		}

		a2aRegistry := a2a.NewRegistry(agentConfigs)
		// A task's chat request passes the RequestID middleware, as every
		// request the HTTP server receives does: its run gets a request id of
		// its own, and carries the trace id of the A2A request that started
		// the task (x-agentcc-trace-id below), else one of its own.
		a2aChat := middleware.RequestID(http.HandlerFunc(handlers.ChatCompletion))
		a2aExecutor := func(ctx context.Context, authHeader string, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
			body, err := json.Marshal(req)
			if err != nil {
				return nil, err
			}

			httpReq := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewReader(body)).WithContext(ctx)
			httpReq.Header.Set("Content-Type", "application/json")
			if authHeader != "" {
				httpReq.Header.Set("Authorization", authHeader)
			}
			if traceID := models.GetTraceID(ctx); traceID != "" {
				httpReq.Header.Set("x-agentcc-trace-id", traceID)
			}

			rec := httptest.NewRecorder()
			a2aChat.ServeHTTP(rec, httpReq)

			if rec.Code >= http.StatusBadRequest {
				var errResp models.ErrorResponse
				if err := json.Unmarshal(rec.Body.Bytes(), &errResp); err == nil && errResp.Error.Message != "" {
					if errResp.Error.Code != "" {
						return nil, fmt.Errorf("%s: %s", errResp.Error.Code, errResp.Error.Message)
					}
					return nil, errors.New(errResp.Error.Message)
				}
				return nil, fmt.Errorf("chat completion failed with status %d", rec.Code)
			}

			var resp models.ChatCompletionResponse
			if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
				return nil, err
			}
			return &resp, nil
		}

		a2aServer := a2a.NewServer(a2a.CardConfig{
			Name:        cfg.A2A.Card.Name,
			Description: cfg.A2A.Card.Description,
			Version:     cfg.A2A.Card.Version,
		}, a2aRegistry, a2a.WithPipeline(engine, registry), a2a.WithChatCompletionExecutor(a2aExecutor),
			a2a.WithCaller(handlers.a2aCaller))

		// Fetch agent cards in background.
		go a2aRegistry.FetchCards(context.Background())

		// Register A2A agents as a provider so "a2a/<agent-name>" models resolve.
		if a2aRegistry.Count() > 0 {
			a2aProvider := a2a.NewProvider(a2aRegistry)
			agentModels := make([]string, 0, a2aRegistry.Count())
			for _, name := range a2aRegistry.Names() {
				agentModels = append(agentModels, "a2a/"+name)
			}
			registry.RegisterProvider("a2a", a2aProvider, agentModels)
		}

		router.Handle("GET", "/.well-known/agent.json", a2aServer.HandleAgentCard)
		router.Handle("GET", "/v1/.well-known/agent.json", a2aServer.HandleAgentCard)
		router.Handle("POST", "/a2a", a2aServer.HandleMessage)
		router.Handle("POST", "/v1/a2a", a2aServer.HandleMessage)
		router.Handle("GET", "/v1/agents", a2aServer.ListAgents)

		slog.Info("a2a protocol enabled", "agents", len(cfg.A2A.Agents))
	}

	// Shadow testing admin endpoints.
	router.Handle("GET", "/-/shadow/stats", func(w http.ResponseWriter, r *http.Request) {
		if !s.checkAdminAuth(w, r) {
			return
		}
		m := s.handlers.mirror.Load()
		if m == nil || m.Store == nil {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]string{"status": "disabled"})
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(m.Store.Stats())
	})

	// API surfaces outside /v1. KeyAuth gates each of these roots, and every
	// path and method under it, exactly as it gates /v1. The native Google
	// API and the speech vendors' relayed APIs are always served (their
	// routes above), so they are always gated.
	keyGatedRoots := append([]string{middleware.GenAIRoot}, middleware.SpeechRelayRoots()...)
	if cfg.MCP.Enabled {
		keyGatedRoots = append(keyGatedRoots, mcpEndpoint(cfg))
	}
	if cfg.A2A.Enabled {
		keyGatedRoots = append(keyGatedRoots, "/a2a")
	}

	// Apply middleware: outermost wraps first.
	var handler http.Handler = router
	// One ceiling, server.max_request_timeout, on every route, chat
	// completions included. The MCP endpoint's paths (mcpTimeoutPrefixes) are the
	// exception: the GET stream sets its own write deadline on every write, and
	// each tool call has its own timeout (mcp.tool_call_timeout), so a
	// request deadline would only cut the stream.
	handler = middleware.TimeoutSkipping(cfg.Server.MaxRequestTimeout, nil, mcpTimeoutPrefixes(cfg))(handler)
	// KeyAuth applies the pipeline's IP rules on every gated path, with its
	// own ip_acl instance (keyGateIPACL).
	handler = middleware.KeyAuth(authKeyStore, authEnabled, keyGateIPACL(cfg, tenantStore), keyGatedRoots...)(handler)
	// Every IP rule (KeyAuth's and the pipeline's, through ClientIP) reads the
	// client IP this resolves: the TCP peer, or with server.trusted_proxies
	// the X-Forwarded-For entry its trusted proxies vouch for. Load's Validate
	// refused a config it cannot parse; a config built without Load and
	// refused here trusts no proxy.
	clientIPs, err := middleware.NewClientIPResolver(cfg.Server)
	if err != nil {
		slog.Error("client IP: trusting no proxy", "error", err)
		clientIPs = nil
	}
	middleware.LogTrustedProxyWarnings(cfg)
	handler = middleware.TrustedClientIP(clientIPs)(handler)
	handler = middleware.LicenseAuth(cfg.LicenseAuth, redisstate.NewLicenseStore(redisClient))(handler)
	handler = s.runtimeTokenMiddleware(handler) // runs before license auth
	handler = middleware.RequestID(handler)
	if cfg.CORS.Enabled {
		handler = middleware.CORS(cfg.CORS)(handler)
	}
	handler = middleware.Recovery(handler)

	s.httpServer = &http.Server{
		Addr:              cfg.Addr(),
		Handler:           handler,
		ReadHeaderTimeout: cfg.Server.ReadHeaderTimeout,
		ReadTimeout:       cfg.Server.ReadTimeout,
		WriteTimeout:      cfg.Server.WriteTimeout,
		IdleTimeout:       cfg.Server.IdleTimeout,
	}

	return s
}

// Start begins listening for HTTP requests. Blocks until the server stops.
func (s *Server) Start() error {
	s.ready.Store(true)

	// Start cluster heartbeat if enabled.
	if s.clusterMgr != nil {
		s.clusterMgr.Start()
	}

	slog.Info("agentcc gateway starting",
		"addr", s.httpServer.Addr,
		"providers", s.registry.ProviderCount(),
		"plugins", s.engine.PluginCount(),
	)

	if err := s.httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		return fmt.Errorf("server error: %w", err)
	}
	return nil
}

// Shutdown gracefully stops the server.
func (s *Server) Shutdown(ctx context.Context) error {
	slog.Info("shutting down gateway...")
	s.ready.Store(false)

	// Drain and deregister cluster node.
	if s.clusterMgr != nil {
		s.clusterMgr.Drain()
		s.clusterMgr.Stop()
	}

	// Stop async workers.
	if s.asyncWorker != nil {
		s.asyncWorker.Stop()
	}

	err := s.httpServer.Shutdown(ctx)
	// Close the MCP server's upstream clients, so each stdio child runs its
	// shutdown ladder instead of being orphaned in its own process group.
	// Clients close in parallel within ctx.
	if s.mcp != nil {
		if mcpErr := s.mcp.Shutdown(ctx); mcpErr != nil {
			slog.Warn("mcp upstreams not all closed before the shutdown deadline", "error", mcpErr)
		}
	}
	// Send the shadow results captured so far with what is left of ctx; if
	// the requests used it all, this logs how many were not sent.
	if s.shadowFlusher != nil {
		s.shadowFlusher.Close(ctx)
	}
	if err != nil {
		return fmt.Errorf("shutdown error: %w", err)
	}

	if err := s.registry.Close(); err != nil {
		slog.Warn("error closing registry", "error", err)
	}

	slog.Info("gateway shut down cleanly")
	return nil
}

// Health/ready handlers.

func (s *Server) healthHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write([]byte(`{"status":"ok"}`))
}

// readyHandler answers /readyz, which the readiness probe reads: a replica
// that answers 200 is sent traffic. One whose control plane syncs keys is not
// ready until its first key sync has loaded them: before that it knows only
// its config.yaml keys, and would answer every key made in the app 401.
// Until a list loads, the control plane's answer that its database has no key
// table yet (AppHasNoKeyTable: no key made in the app) counts as one, so a
// new install whose migrations run after the gateway is ready (the Helm
// chart's post-install bootstrap job under `helm install --wait`) can finish.
// Liveness (/healthz, /livez) does not wait.
func (s *Server) readyHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	switch {
	case !s.ready.Load():
		w.WriteHeader(http.StatusServiceUnavailable)
		w.Write([]byte(`{"status":"not_ready"}`))
	case s.waitForKeyList && !s.keyStore.KeyListLoaded() && !s.keyStore.AppHasNoKeyTable():
		w.WriteHeader(http.StatusServiceUnavailable)
		w.Write([]byte(`{"status":"not_ready","reason":"waiting for the first key sync from the control plane"}`))
	default:
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"status":"ready"}`))
	}
}

func (s *Server) reloadHandler(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}

	w.Header().Set("Content-Type", "application/json")

	if s.configPath == "" {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status": "error",
			"error":  "no config file path configured; reload requires a config file",
		})
		return
	}

	// Re-read config from disk.
	newCfg, err := config.Load(s.configPath)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status": "validation_failed",
			"errors": []string{err.Error()},
		})
		return
	}

	// Reload routing components.
	if err := s.handlers.ReloadRouting(newCfg, s.registry); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status": "reload_failed",
			"errors": []string{err.Error()},
		})
		return
	}

	// Update stored config.
	s.cfg = newCfg

	slog.Info("config reloaded successfully", "config_path", s.configPath)

	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status": "reloaded",
		"changes": map[string]interface{}{
			"routing_strategy":   newCfg.Routing.DefaultStrategy,
			"conditional_routes": len(newCfg.Routing.ConditionalRoutes),
			"model_fallbacks":    len(newCfg.Routing.ModelFallbacks),
			"targets":            len(newCfg.Routing.Targets),
		},
	})
}

// configHandler answers GET /-/config: this node's settings the console
// shows, never a secret, and the org config sections it enforces.
func (s *Server) configHandler(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}

	contentMode := s.cfg.Privacy.DefaultContentMode
	if contentMode == "" {
		contentMode = "full"
	}
	body := map[string]interface{}{
		"server": map[string]interface{}{
			"port":                   s.cfg.Server.Port,
			"host":                   s.cfg.Server.Host,
			"read_timeout_ms":        s.cfg.Server.ReadTimeout.Milliseconds(),
			"write_timeout_ms":       s.cfg.Server.WriteTimeout.Milliseconds(),
			"idle_timeout_ms":        s.cfg.Server.IdleTimeout.Milliseconds(),
			"max_request_timeout_ms": s.cfg.Server.MaxRequestTimeout.Milliseconds(),
		},
		"logging": map[string]interface{}{
			"level":           s.cfg.Logging.Level,
			"request_logging": s.cfg.Logging.RequestLogging.Enabled,
			"include_bodies":  s.cfg.Logging.RequestLogging.IncludeBodies,
			"content_mode":    contentMode,
		},
		// Batches live in this node's memory. A submission's own
		// max_concurrency wins; 10 is what an unset one gets
		// (batch.Processor), and 10 MiB is the submit body cap
		// (submitBatchHandler).
		"batch": map[string]interface{}{
			"storage":                 "memory",
			"default_max_concurrency": 10,
			"max_body_bytes":          10485760,
		},
		// How this node runs an organization's guardrail checks: without
		// guardrails.enabled no org check runs; an org check's error or
		// timeout follows the node's guardrails.fail_open; org checks run in
		// turn and the first block stops the rest ("sequential"). An org's
		// own fail_open and pipeline_mode are stored, not read; the console
		// shows these values read-only in their place.
		"guardrail_pipeline": map[string]interface{}{
			"enabled":   s.cfg.Guardrails.Enabled,
			"fail_open": s.cfg.Guardrails.FailOpen,
			"mode":      "sequential",
		},
		"providers_count": s.registry.ProviderCount(),
		"models_count":    len(s.cfg.ModelMap),
		"plugins_count":   s.engine.PluginCount(),
	}
	if s.orgSectionsEnforced != nil {
		body["org_sections_enforced"] = s.orgSectionsEnforced
	}
	if len(s.orgSectionsNotEnforced) > 0 {
		body["org_sections_not_enforced"] = s.orgSectionsNotEnforced
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(body)
}

func (s *Server) metricsHandler(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}
	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	w.Write([]byte(s.metricsRegistry.Render()))
}

// Batch API handlers.

func (s *Server) submitBatchHandler(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}
	// Who the items run as: the batch key (batch.KeyHeader), checked before
	// anything is read or started.
	caller, apiErr := s.batchProcessor.CallerFor(r)
	if apiErr != nil {
		models.WriteError(w, apiErr)
		return
	}
	// Limit batch request body to 10MB to prevent OOM.
	const maxBatchBodySize = 10 << 20
	limited := io.LimitReader(r.Body, maxBatchBodySize+1)

	var body struct {
		Requests       []*models.ChatCompletionRequest `json:"requests"`
		MaxConcurrency int                             `json:"max_concurrency"`
	}
	if err := json.NewDecoder(limited).Decode(&body); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error": "invalid JSON: " + err.Error(),
		})
		return
	}

	if len(body.Requests) == 0 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error": "requests array is required and must not be empty",
		})
		return
	}

	// Each item's run carries this request's trace id: the caller's
	// x-agentcc-trace-id, or the one the RequestID middleware made for it.
	b := s.batchProcessor.SubmitAs(body.Requests, body.MaxConcurrency, w.Header().Get("x-agentcc-trace-id"), caller)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"batch_id":        b.ID,
		"org_id":          b.OrgID,
		"key_id":          b.KeyID,
		"status":          b.GetStatus(),
		"total":           b.Total,
		"max_concurrency": b.MaxConc,
		"created_at":      b.CreatedAt,
	})
}

func (s *Server) getBatchHandler(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}
	batchID := r.URL.Query().Get("batch_id")
	if batchID == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error": "batch_id is required",
		})
		return
	}

	// Only the batch's own org (org_id; none for a batch without a key)
	// finds it: another org's batch is not found.
	b, ok := s.batchProcessor.GetForOrg(batchID, r.URL.Query().Get("org_id"))
	if !ok {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error": "batch not found",
		})
		return
	}

	resp := map[string]interface{}{
		"batch_id":        b.ID,
		"org_id":          b.OrgID,
		"key_id":          b.KeyID,
		"status":          b.GetStatus(),
		"total":           b.Total,
		"max_concurrency": b.MaxConc,
		"created_at":      b.CreatedAt,
	}
	if ct := b.GetCompletedAt(); ct != nil {
		resp["completed_at"] = ct
	}

	status := b.GetStatus()
	if status == "completed" || status == "cancelled" {
		resp["results"] = b.GetResults()
		summary := b.Summary()
		resp["summary"] = map[string]interface{}{
			"total_cost":          summary.TotalCost,
			"total_input_tokens":  summary.TotalInputTokens,
			"total_output_tokens": summary.TotalOutputTokens,
			"completed":           summary.Completed,
			"failed":              summary.Failed,
			"cancelled":           summary.Cancelled,
		}
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(resp)
}

func (s *Server) cancelBatchHandler(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}
	batchID := r.URL.Query().Get("batch_id")
	if batchID == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error": "batch_id is required",
		})
		return
	}

	b, ok := s.batchProcessor.CancelForOrg(batchID, r.URL.Query().Get("org_id"))
	if !ok {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error": "batch not found",
		})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"batch_id": b.ID,
		"org_id":   b.OrgID,
		"status":   b.GetStatus(),
	})
}

func (s *Server) providerHealthHandler(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)

	health := s.handlers.healthMonitor.GetAllHealth()
	json.NewEncoder(w).Encode(map[string]interface{}{
		"providers": health,
	})
}

func (s *Server) orgProviderHealthHandler(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}

	orgID := r.URL.Query().Get("org_id")
	if orgID == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "org_id is required"})
		return
	}

	if s.TenantStore == nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]string{"error": "tenant store not configured"})
		return
	}

	orgCfg := s.TenantStore.Get(orgID)
	if orgCfg == nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]string{"error": "org config not found"})
		return
	}

	orgProviderIDs := make([]string, 0, len(orgCfg.Providers))
	for id := range orgCfg.Providers {
		orgProviderIDs = append(orgProviderIDs, id)
	}

	allHealth := s.handlers.healthMonitor.GetAllHealthWithProviders(orgProviderIDs)

	orgSet := make(map[string]bool, len(orgProviderIDs))
	for _, id := range orgProviderIDs {
		orgSet[id] = true
	}

	filtered := make([]interface{}, 0, len(orgProviderIDs))
	for _, ph := range allHealth {
		if orgSet[ph.ProviderID] {
			filtered = append(filtered, ph)
		}
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"org_id":    orgID,
		"providers": filtered,
	})
}

// ConvertModelOverrides converts config overrides to modeldb overrides.
func ConvertModelOverrides(cfgOverrides map[string]config.ModelOverrideConfig) map[string]modeldb.ModelOverride {
	if len(cfgOverrides) == 0 {
		return nil
	}
	out := make(map[string]modeldb.ModelOverride, len(cfgOverrides))
	for id, co := range cfgOverrides {
		mo := modeldb.ModelOverride{
			Provider:        co.Provider,
			Mode:            co.Mode,
			MaxInputTokens:  co.MaxInputTokens,
			MaxOutputTokens: co.MaxOutputTokens,
			DeprecationDate: co.DeprecationDate,
			Regions:         co.Regions,
		}
		if co.Pricing != nil {
			mo.Pricing = &modeldb.PricingOverride{
				InputPerToken:       co.Pricing.InputPerToken,
				OutputPerToken:      co.Pricing.OutputPerToken,
				CachedInputPerToken: co.Pricing.CachedInputPerToken,
				BatchInputPerToken:  co.Pricing.BatchInputPerToken,
				BatchOutputPerToken: co.Pricing.BatchOutputPerToken,
				InputPerSecond:      co.Pricing.InputPerSecond,
				InputPerCharacter:   co.Pricing.InputPerCharacter,
			}
		}
		if co.Capabilities != nil {
			mo.Capabilities = &modeldb.CapOverride{
				FunctionCalling:   co.Capabilities.FunctionCalling,
				ParallelToolCalls: co.Capabilities.ParallelToolCalls,
				Vision:            co.Capabilities.Vision,
				AudioInput:        co.Capabilities.AudioInput,
				AudioOutput:       co.Capabilities.AudioOutput,
				PDFInput:          co.Capabilities.PDFInput,
				Streaming:         co.Capabilities.Streaming,
				ResponseSchema:    co.Capabilities.ResponseSchema,
				SystemMessages:    co.Capabilities.SystemMessages,
				PromptCaching:     co.Capabilities.PromptCaching,
				Reasoning:         co.Capabilities.Reasoning,
			}
		}
		out[id] = mo
	}
	return out
}

// checkAdminAuth validates the admin token using constant-time comparison.
// Returns true if authorized, false if rejected (and writes 401 response).
func (s *Server) checkAdminAuth(w http.ResponseWriter, r *http.Request) bool {
	if s.cfg.Admin.Token == "" {
		// No token configured — deny all admin requests for safety.
		slog.Warn("admin request denied: AGENTCC_ADMIN_TOKEN not configured")
		w.WriteHeader(http.StatusUnauthorized)
		w.Write([]byte(`{"error":"admin token not configured"}`))
		return false
	}
	token := r.Header.Get("Authorization")
	expected := "Bearer " + s.cfg.Admin.Token
	if subtle.ConstantTimeCompare([]byte(token), []byte(expected)) != 1 {
		w.WriteHeader(http.StatusUnauthorized)
		w.Write([]byte(`{"error":"unauthorized"}`))
		return false
	}
	return true
}

// a2aCaller is the A2A server's CallerFunc: the A2A caller is the key KeyAuth
// authenticated, so an A2A task belongs to the same org as that key's jobs,
// files and stored responses.
func (h *Handlers) a2aCaller(r *http.Request) (a2a.Caller, bool) {
	id, ok := h.callerIdentity(r)
	return a2a.Caller{KeyID: id.KeyID, OrgID: id.OrgID}, ok
}

// mcpKeyAuth adapts auth.KeyStore to the mcp.KeyAuthenticator interface.
type mcpKeyAuth struct {
	keyStore *auth.KeyStore
}

// AuthenticateKey returns the key's identity, which owns the MCP sessions it
// opens, and its tool lists.
func (a *mcpKeyAuth) AuthenticateKey(rawKey string) (mcp.KeyGrant, bool) {
	key := a.keyStore.Authenticate(rawKey)
	if key == nil || !key.IsActive() {
		return mcp.KeyGrant{}, false
	}
	id := a.keyStore.Identity(key)
	return mcp.KeyGrant{
		KeyID:        id.KeyID,
		OrgID:        id.OrgID,
		AllowedTools: key.AllowedTools,
		DeniedTools:  key.DeniedTools,
	}, true
}

// metricsPath is where the Prometheus metrics endpoint is served:
// prometheus.path, with a leading slash, or /-/metrics when unset. It needs
// the admin token wherever it is; a path under /v1 would also need an API key
// (KeyAuth gates /v1), so pick one outside it.
func metricsPath(cfg *config.Config) string {
	p := cfg.Prometheus.Path
	if p == "" {
		return "/-/metrics"
	}
	if p[0] != '/' {
		p = "/" + p
	}
	return p
}

// assistantsSwitch returns the wrapper for the assistants, threads and vector
// stores routes: the handler itself with assistants.enabled on, else one that
// answers 404 naming the switch.
func assistantsSwitch(enabled bool) func(http.HandlerFunc) http.HandlerFunc {
	if enabled {
		return func(h http.HandlerFunc) http.HandlerFunc { return h }
	}
	return func(http.HandlerFunc) http.HandlerFunc {
		return func(w http.ResponseWriter, r *http.Request) {
			models.WriteError(w, models.ErrNotFound("assistants_disabled",
				"The Assistants API (assistants, threads, vector stores) is turned off on this gateway: assistants.enabled is false in its config."))
		}
	}
}
