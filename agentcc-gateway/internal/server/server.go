package server

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/a2a"
	"github.com/futureagi/agentcc-gateway/internal/async"
	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/batch"
	"github.com/futureagi/agentcc-gateway/internal/cluster"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/edge"
	"github.com/futureagi/agentcc-gateway/internal/files"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/mcpsec"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/realtime"
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
	ready            atomic.Bool
}

// SetKeyRevocationPublisher attaches a pub/sub broadcaster for multi-replica
// key revocation. Call after New() and before Start().
func (s *Server) SetKeyRevocationPublisher(pub KeyRevocationPublisher) {
	if s.keyHandlers != nil {
		s.keyHandlers.SetRevocationPublisher(pub)
	}
}

// New creates a new gateway server.
func New(cfg *config.Config, configPath string, registry *providers.Registry, engine *pipeline.Engine, keyStore *auth.KeyStore, guardrailEngine *guardrails.Engine, policyStore *policy.Store, metricsRegistry *metrics.Registry, modelDBPtr *atomic.Pointer[modeldb.ModelDB], tenantStore *tenant.Store, onOrgConfigChange func(string)) *Server {
	if cfg != nil && cfg.Auth.Enabled && keyStore == nil {
		keyStore = auth.NewKeyStore(cfg.Auth)
	}

	orgProviderCache := providers.NewOrgProviderCache(cfg.Providers)

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
				flusher := routing.NewShadowFlusher(shadowStore, webhookURL, cfg.ControlPlane.WebhookSecret, flushInterval)
				go flusher.Run(context.Background())
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

	handlers := NewHandlers(registry, engine, cfg.Server.MaxRequestBodySize, cfg.Server.DefaultRequestTimeout, failover, modelFallbacks, condRouter, healthMonitor, cfg.Routing.ModelTimeouts, mirror, guardrailEngine, policyStore, cfg.Guardrails.Streaming, modelDBPtr, tenantStore, orgProviderCache, keyStore)
	s.handlers = handlers

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

	asyncProviderFn := func(ctx context.Context, req *models.ChatCompletionRequest, metadata map[string]string) (*models.ChatCompletionResponse, map[string]string, error) {
		rc := models.AcquireRequestContext()
		rc.Model = req.Model
		rc.Request = req
		rc.IsStream = false
		for k, v := range metadata {
			rc.Metadata[k] = v
		}

		provider, err := registry.Resolve(req.Model)
		if err != nil {
			rc.Release()
			return nil, nil, fmt.Errorf("no provider for model %q: %w", req.Model, err)
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

		err = engine.Process(ctx, rc, providerCall)
		meta := make(map[string]string, len(rc.Metadata))
		for k, v := range rc.Metadata {
			meta[k] = v
		}
		resp := rc.Response
		rc.Release()
		return resp, meta, err
	}

	asyncWorker := async.NewWorker(asyncStore, asyncProviderFn, async.DefaultMaxWorkers)
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
	router.Handle("GET", "/v1/models", handlers.ListModels)
	router.Handle("GET", "/v1/models/{model}", handlers.GetModel)

	// Assistants API.
	router.Handle("POST", "/v1/assistants", handlers.CreateAssistant)
	router.Handle("GET", "/v1/assistants", handlers.ListAssistants)
	router.Handle("GET", "/v1/assistants/{assistant_id}", handlers.GetAssistant)
	router.Handle("POST", "/v1/assistants/{assistant_id}", handlers.UpdateAssistant)
	router.Handle("DELETE", "/v1/assistants/{assistant_id}", handlers.DeleteAssistant)

	// Threads.
	router.Handle("POST", "/v1/threads", handlers.CreateThread)
	// NOTE: /v1/threads/runs must be before /v1/threads/{thread_id} to avoid "runs" matching as thread_id.
	router.Handle("POST", "/v1/threads/runs", handlers.CreateThreadAndRun)
	router.Handle("GET", "/v1/threads/{thread_id}", handlers.GetThread)
	router.Handle("POST", "/v1/threads/{thread_id}", handlers.UpdateThread)
	router.Handle("DELETE", "/v1/threads/{thread_id}", handlers.DeleteThread)

	// Messages.
	router.Handle("POST", "/v1/threads/{thread_id}/messages", handlers.CreateMessage)
	router.Handle("GET", "/v1/threads/{thread_id}/messages", handlers.ListMessages)
	router.Handle("GET", "/v1/threads/{thread_id}/messages/{message_id}", handlers.GetMessage)
	router.Handle("POST", "/v1/threads/{thread_id}/messages/{message_id}", handlers.UpdateMessage)
	router.Handle("DELETE", "/v1/threads/{thread_id}/messages/{message_id}", handlers.DeleteMessage)

	// Runs.
	router.Handle("POST", "/v1/threads/{thread_id}/runs", handlers.CreateRun)
	router.Handle("GET", "/v1/threads/{thread_id}/runs", handlers.ListRuns)
	// NOTE: cancel and submit_tool_outputs must be before /v1/threads/{thread_id}/runs/{run_id}.
	router.Handle("POST", "/v1/threads/{thread_id}/runs/{run_id}/cancel", handlers.CancelRun)
	router.Handle("POST", "/v1/threads/{thread_id}/runs/{run_id}/submit_tool_outputs", handlers.SubmitToolOutputs)
	router.Handle("GET", "/v1/threads/{thread_id}/runs/{run_id}", handlers.GetRun)
	router.Handle("POST", "/v1/threads/{thread_id}/runs/{run_id}", handlers.UpdateRun)

	// Run Steps.
	router.Handle("GET", "/v1/threads/{thread_id}/runs/{run_id}/steps", handlers.ListRunSteps)
	router.Handle("GET", "/v1/threads/{thread_id}/runs/{run_id}/steps/{step_id}", handlers.GetRunStep)

	// Vector Stores API.
	router.Handle("POST", "/v1/vector_stores", handlers.CreateVectorStore)
	router.Handle("GET", "/v1/vector_stores", handlers.ListVectorStores)
	// NOTE: Specific sub-resource routes before generic {vector_store_id} routes.
	router.Handle("POST", "/v1/vector_stores/{vector_store_id}/search", handlers.SearchVectorStore)
	router.Handle("POST", "/v1/vector_stores/{vector_store_id}/files", handlers.CreateVectorStoreFile)
	router.Handle("GET", "/v1/vector_stores/{vector_store_id}/files", handlers.ListVectorStoreFiles)
	router.Handle("DELETE", "/v1/vector_stores/{vector_store_id}/files/{file_id}", handlers.DeleteVectorStoreFile)
	router.Handle("POST", "/v1/vector_stores/{vector_store_id}/file_batches", handlers.CreateVectorStoreFileBatch)
	router.Handle("GET", "/v1/vector_stores/{vector_store_id}", handlers.GetVectorStore)
	router.Handle("POST", "/v1/vector_stores/{vector_store_id}", handlers.UpdateVectorStore)
	router.Handle("DELETE", "/v1/vector_stores/{vector_store_id}", handlers.DeleteVectorStore)

	// Native Google GenAI API.
	router.Handle("POST", "/v1beta/models/{model_action}", handlers.GenAIHandler)

	// Search API.
	router.Handle("POST", "/v1/search", handlers.Search)

	// OCR API.
	router.Handle("POST", "/v1/ocr", handlers.OCR)

	// Realtime WebSocket API.
	realtimeTracker := realtime.NewSessionTracker(5)
	realtimeHandler := NewRealtimeHandler(realtimeTracker, registry, keyStore, realtimeHandlerConfig{
		MaxSessionDuration: 3600 * time.Second,
		PingInterval:       30 * time.Second,
		PongTimeout:        10 * time.Second,
		ReadBufferSize:     32768,
		WriteBufferSize:    32768,
		MaxMessageSize:     16 * 1024 * 1024,
		AllowedOrigins:     cfg.CORS.AllowedOrigins,
	})
	router.Handle("GET", "/v1/realtime", realtimeHandler.HandleRealtime)

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

	// Prometheus metrics endpoint.
	if metricsRegistry != nil {
		router.Handle("GET", "/-/metrics", s.metricsHandler)
	}

	// Batch API endpoints.
	batchHandler := func(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
		rc := models.AcquireRequestContext()
		rc.Model = req.Model
		rc.Request = req
		rc.IsStream = false // batch requests are non-streaming

		provider, err := registry.Resolve(req.Model)
		if err != nil {
			rc.Release()
			return nil, nil, fmt.Errorf("no provider for model %q: %w", req.Model, err)
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

		err = engine.Process(ctx, rc, providerCall)
		meta := make(map[string]string, len(rc.Metadata))
		for k, v := range rc.Metadata {
			meta[k] = v
		}
		resp := rc.Response
		rc.Release()
		return resp, meta, err
	}
	s.batchProcessor = batch.NewProcessor(batchHandler)
	router.Handle("POST", "/-/batches", s.submitBatchHandler)
	router.Handle("GET", "/-/batches/{batch_id}", s.getBatchHandler)
	router.Handle("POST", "/-/batches/{batch_id}/cancel", s.cancelBatchHandler)

	// Warn at startup if admin token is not configured.
	if cfg.Admin.Token == "" {
		slog.Warn("AGENTCC_ADMIN_TOKEN is not set — all admin endpoints (/-/keys, /-/orgs, /-/config, /-/metrics, /-/batches) will return 401. Set admin.token in config.yaml or AGENTCC_ADMIN_TOKEN env var.")
	}

	// Admin key management endpoints.
	if keyStore != nil {
		s.keyHandlers = NewKeyHandlers(keyStore, cfg.Admin.Token)
		router.Handle("GET", "/-/keys", s.keyHandlers.ListKeys)
		router.Handle("POST", "/-/keys", s.keyHandlers.CreateKey)
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

	// Key rotation admin endpoints.
	{
		drainPeriod := 30 * time.Second                 // default
		rotMgr := rotation.NewManager(drainPeriod, nil) // onRotate callback not wired for now

		// Register each provider that has rotation enabled.
		for name, pCfg := range cfg.Providers {
			if pCfg.Rotation.Enabled {
				if d := pCfg.Rotation.DrainPeriod; d > 0 {
					drainPeriod = d
				}
				rotMgr.RegisterProvider(name, pCfg.APIKey)
			}
		}

		rotHandlers := NewRotationHandlers(rotMgr, cfg.Admin.Token)
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

	// MCP gateway.
	if cfg.MCP.Enabled {
		endpoint := cfg.MCP.Endpoint
		if endpoint == "" {
			endpoint = "/mcp"
		}
		mcpServer := mcp.NewServer(mcp.ServerConfig{
			MaxAgentDepth:   cfg.MCP.MaxAgentDepth,
			ToolCallTimeout: cfg.MCP.ToolCallTimeout,
			SessionTTL:      cfg.MCP.SessionTTL,
			Separator:       cfg.MCP.Separator,
		})

		// Attach per-key tool filtering if auth is configured.
		if keyStore != nil {
			mcpServer.SetKeyAuth(&mcpKeyAuth{keyStore: keyStore})
			slog.Info("mcp per-key tool filtering enabled")
		}

		// Attach MCP tool call guardrail if configured.
		if cfg.MCP.Guardrails.Enabled {
			guardCfg := map[string]interface{}{
				"blocked_tools":    cfg.MCP.Guardrails.BlockedTools,
				"allowed_servers":  cfg.MCP.Guardrails.AllowedServers,
				"validate_inputs":  cfg.MCP.Guardrails.ValidateInputs,
				"validate_outputs": cfg.MCP.Guardrails.ValidateOutputs,
				"custom_patterns":  cfg.MCP.Guardrails.CustomPatterns,
				"tool_rate_limits": cfg.MCP.Guardrails.ToolRateLimits,
			}
			mcpServer.SetGuard(mcpsec.NewToolGuard(guardCfg))
			slog.Info("mcp guardrails enabled",
				"blocked_tools", len(cfg.MCP.Guardrails.BlockedTools),
				"allowed_servers", len(cfg.MCP.Guardrails.AllowedServers),
				"custom_patterns", len(cfg.MCP.Guardrails.CustomPatterns),
				"tool_rate_limits", len(cfg.MCP.Guardrails.ToolRateLimits),
			)
		}

		// Set tool version/deprecation info from config.
		if len(cfg.MCP.ToolVersions) > 0 {
			versions := make(map[string]mcp.ToolVersionInfo, len(cfg.MCP.ToolVersions))
			for name, vc := range cfg.MCP.ToolVersions {
				versions[name] = mcp.ToolVersionInfo{
					Version:            vc.Version,
					Deprecated:         vc.Deprecated,
					DeprecationMessage: vc.DeprecationMessage,
					ReplacedBy:         vc.ReplacedBy,
				}
			}
			mcpServer.Registry().SetToolVersions(versions)
			slog.Info("mcp tool versions configured", "count", len(versions))
		}

		// Connect to upstream MCP servers.
		for id, serverCfg := range cfg.MCP.Servers {
			client := mcp.NewClient(mcp.ClientConfig{
				ServerID:      id,
				URL:           serverCfg.URL,
				Command:       serverCfg.Command,
				Args:          serverCfg.Args,
				TransportType: serverCfg.Transport,
				Auth: mcp.AuthConfig{
					Type:   serverCfg.Auth.Type,
					Token:  serverCfg.Auth.Token,
					Header: serverCfg.Auth.Header,
					Key:    serverCfg.Auth.Key,
				},
				ToolsCacheTTL: serverCfg.ToolsCacheTTL,
			})
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			if err := client.Connect(ctx); err != nil {
				slog.Error("failed to connect to MCP server", "server", id, "error", err)
				cancel()
				continue
			}
			cancel()
			mcpServer.RegisterClient(client)
		}

		router.Handle("POST", endpoint, mcpServer.HandlePost)
		router.Handle("GET", endpoint, mcpServer.HandleSSE)

		// Admin endpoints for MCP.
		router.Handle("GET", "/-/mcp/status", func(w http.ResponseWriter, r *http.Request) {
			if !s.checkAdminAuth(w, r) {
				return
			}
			status := map[string]interface{}{
				"enabled":   true,
				"sessions":  mcpServer.SessionCount(),
				"tools":     mcpServer.Registry().ToolCount(),
				"resources": mcpServer.Registry().ResourceCount(),
				"prompts":   mcpServer.Registry().PromptCount(),
				"servers":   mcpServer.ClientStatuses(),
			}
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(status)
		})
		router.Handle("GET", "/-/mcp/tools", func(w http.ResponseWriter, r *http.Request) {
			if !s.checkAdminAuth(w, r) {
				return
			}
			tools := mcpServer.Registry().ListToolsJSON()
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(tools)
		})

		// Tool execution playground endpoint.
		router.Handle("POST", "/-/mcp/test", func(w http.ResponseWriter, r *http.Request) {
			if !s.checkAdminAuth(w, r) {
				return
			}
			var req struct {
				Name      string                 `json:"name"`
				Arguments map[string]interface{} `json:"arguments"`
			}
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(map[string]string{"error": "invalid request body"})
				return
			}
			if req.Name == "" {
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(map[string]string{"error": "tool name is required"})
				return
			}
			result := mcpServer.TestTool(r.Context(), req.Name, req.Arguments)
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(result)
		})

		// Admin endpoints for MCP resources and prompts.
		router.Handle("GET", "/-/mcp/resources", func(w http.ResponseWriter, r *http.Request) {
			if !s.checkAdminAuth(w, r) {
				return
			}
			resources := mcpServer.Registry().ListResources()
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(resources)
		})
		router.Handle("GET", "/-/mcp/prompts", func(w http.ResponseWriter, r *http.Request) {
			if !s.checkAdminAuth(w, r) {
				return
			}
			prompts := mcpServer.Registry().ListPrompts()
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(prompts)
		})

		slog.Info("mcp gateway enabled",
			"endpoint", endpoint,
			"servers", len(cfg.MCP.Servers),
			"max_depth", cfg.MCP.MaxAgentDepth,
		)
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

			rec := httptest.NewRecorder()
			handlers.ChatCompletion(rec, httpReq)

			if rec.Code >= http.StatusBadRequest {
				var errResp models.ErrorResponse
				if err := json.Unmarshal(rec.Body.Bytes(), &errResp); err == nil && errResp.Error.Message != "" {
					if errResp.Error.Code != "" {
						return nil, fmt.Errorf("%s: %s", errResp.Error.Code, errResp.Error.Message)
					}
					return nil, fmt.Errorf(errResp.Error.Message)
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
		}, a2aRegistry, a2a.WithPipeline(engine, registry), a2a.WithChatCompletionExecutor(a2aExecutor))

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

	// Apply middleware: outermost wraps first.
	var handler http.Handler = router
	handler = middleware.Timeout(cfg.Server.DefaultRequestTimeout, "/v1/chat/completions")(handler)
	handler = middleware.KeyAuth(keyStore)(handler)
	handler = middleware.RequestID(handler)
	if cfg.CORS.Enabled {
		handler = middleware.CORS(cfg.CORS)(handler)
	}
	handler = middleware.Recovery(handler)

	s.httpServer = &http.Server{
		Addr:         cfg.Addr(),
		Handler:      handler,
		ReadTimeout:  cfg.Server.ReadTimeout,
		WriteTimeout: cfg.Server.WriteTimeout,
		IdleTimeout:  cfg.Server.IdleTimeout,
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

	if err := s.httpServer.Shutdown(ctx); err != nil {
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

func (s *Server) readyHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if s.ready.Load() {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"status":"ready"}`))
	} else {
		w.WriteHeader(http.StatusServiceUnavailable)
		w.Write([]byte(`{"status":"not_ready"}`))
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

func (s *Server) configHandler(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"server": map[string]interface{}{
			"port": s.cfg.Server.Port,
			"host": s.cfg.Server.Host,
		},
		"providers_count": s.registry.ProviderCount(),
		"models_count":    len(s.cfg.ModelMap),
		"plugins_count":   s.engine.PluginCount(),
	})
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

	b := s.batchProcessor.Submit(body.Requests, body.MaxConcurrency)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"batch_id":        b.ID,
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

	b, ok := s.batchProcessor.Get(batchID)
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

	b, ok := s.batchProcessor.Cancel(batchID)
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

// mcpKeyAuth adapts auth.KeyStore to the mcp.KeyAuthenticator interface.
type mcpKeyAuth struct {
	keyStore *auth.KeyStore
}

func (a *mcpKeyAuth) AuthenticateKey(rawKey string) (allowed []string, denied []string, valid bool) {
	key := a.keyStore.Authenticate(rawKey)
	if key == nil || !key.IsActive() {
		return nil, nil, false
	}
	return key.AllowedTools, key.DeniedTools, true
}
