// Package tenant provides per-organization gateway configuration.
// Each org can have its own provider API keys, guardrail pipeline,
// routing strategy, cache settings, and rate limits. The gateway is
// shared infrastructure, but per-request behavior is determined by
// the org that owns the API key.
package tenant

// OrgConfig holds the merged configuration for a single organization.
type OrgConfig struct {
	Providers    map[string]*ProviderConfig `json:"providers,omitempty"`
	Guardrails   *GuardrailConfig           `json:"guardrails,omitempty"`
	Routing      *RoutingConfig             `json:"routing,omitempty"`
	Cache        *CacheConfig               `json:"cache,omitempty"`
	RateLimiting *RateLimitConfig           `json:"rate_limiting,omitempty"`
	Budgets      *BudgetsConfig             `json:"budgets,omitempty"`
	CostTracking *CostTrackingConfig        `json:"cost_tracking,omitempty"`
	IPACL        *IPACLConfig               `json:"ip_acl,omitempty"`
	Alerting     *AlertingConfig            `json:"alerting,omitempty"`
	Privacy      *PrivacyConfig             `json:"privacy,omitempty"`
	ToolPolicy   *ToolPolicyConfig          `json:"tool_policy,omitempty"`
	MCP           *MCPOrgConfig              `json:"mcp,omitempty"`
	Audit         *AuditOrgConfig            `json:"audit,omitempty"`
	A2A           *A2AOrgConfig              `json:"a2a,omitempty"`
	ModelDatabase *ModelDatabaseOrgConfig    `json:"model_database,omitempty"`
	ModelMap       map[string]string          `json:"model_map,omitempty"`
	ManagedModels  *ManagedModelsConfig       `json:"managed_models,omitempty"`

	// Tools is the org's capability configuration; read it
	// through ToolsCapability and ToolsPolicy.
	Tools *ToolsConfig `json:"tools,omitempty"`
	// Identity is delegated identity; read it through
	// IdentityPolicy. Its client secret and grants exist only when pushed.
	Identity *IdentityConfig `json:"identity,omitempty"`

	// Meta is push-time metadata from the control plane (never stored there).
	Meta *ConfigMeta `json:"meta,omitempty"`
}

// ManagedModelsConfig opts an org in to the gateway's managed providers: the
// ones FutureAGI holds the credentials for (config.yaml providers marked
// managed). Requests they serve are billed to the org with the managed markup.
// Fields whose default is not the zero value are pointers; read them through
// ManagedPolicy.
type ManagedModelsConfig struct {
	Enabled bool `json:"enabled"`

	// Model and vendor globs. Deny wins over allow.
	AllowModels  []string `json:"allow_models,omitempty"`
	DenyModels   []string `json:"deny_models,omitempty"`
	AllowVendors []string `json:"allow_vendors,omitempty"`
	DenyVendors  []string `json:"deny_vendors,omitempty"`

	FallbackToManaged   bool  `json:"fallback_to_managed,omitempty"`
	AllowFree           *bool `json:"allow_free,omitempty"`
	Hedging             bool  `json:"hedging,omitempty"`
	AllowDataCollection bool  `json:"allow_data_collection,omitempty"`

	// UpstreamKeys exists only in a pushed payload: per-org upstream keys by
	// managed provider id, assembled by the control plane at push time.
	UpstreamKeys map[string]*ManagedUpstreamKey `json:"upstream_keys,omitempty"`

	MaxCallCostUSD           *float64                 `json:"max_call_cost_usd,omitempty"`
	DefaultMaxTokensEstimate *int                     `json:"default_max_tokens_estimate,omitempty"`
	SpendGuard               *ManagedSpendGuardConfig `json:"spend_guard,omitempty"`
	Topup                    *ManagedTopupConfig      `json:"topup,omitempty"`
}

// ManagedUpstreamKey is one per-org managed upstream key.
type ManagedUpstreamKey struct {
	APIKey  string `json:"api_key"`
	KeyHash string `json:"key_hash"`
	Label   string `json:"label,omitempty"`
}

// ManagedSpendGuardConfig caps an org's managed spend velocity. A nil cap is unset.
type ManagedSpendGuardConfig struct {
	MaxUSDPerMinute   *float64 `json:"max_usd_per_minute,omitempty"`
	MaxUSDPerHour     *float64 `json:"max_usd_per_hour,omitempty"`
	AlertUSDPerHour   *float64 `json:"alert_usd_per_hour,omitempty"`
	NewOrgDailyCapUSD *float64 `json:"new_org_daily_cap_usd,omitempty"`
	AutoPause         bool     `json:"auto_pause,omitempty"`
}

// ManagedTopupConfig bounds managed-credit top-ups.
type ManagedTopupConfig struct {
	MinUSD *float64 `json:"min_usd,omitempty"`
}

// ManagedModelsEnabled reports whether the org may use managed providers.
func (c *OrgConfig) ManagedModelsEnabled() bool {
	return c != nil && c.ManagedModels != nil && c.ManagedModels.Enabled
}

// ProviderConfig holds per-org provider settings.
type ProviderConfig struct {
	APIKey         string   `json:"api_key"`
	BaseURL        string   `json:"base_url,omitempty"`
	APIFormat      string   `json:"api_format,omitempty"`
	APIPathPrefix  *string  `json:"api_path_prefix,omitempty"`
	Models         []string `json:"models,omitempty"`
	Timeout        int      `json:"timeout,omitempty"` // seconds
	Weight         float64  `json:"weight,omitempty"`
	Enabled        bool     `json:"enabled"`
	MaxConcurrent  int      `json:"max_concurrent,omitempty"`
	ConnPoolSize   int      `json:"conn_pool_size,omitempty"`

	// AWS Bedrock credentials (per-org, pushed from Django).
	AWSAccessKeyID     string `json:"aws_access_key_id,omitempty"`
	AWSSecretAccessKey string `json:"aws_secret_access_key,omitempty"`
	AWSRegion          string `json:"aws_region,omitempty"`
	AWSSessionToken    string `json:"aws_session_token,omitempty"`
	ServiceAccountJSON string `json:"service_account_json,omitempty"`

	ZDRAttested bool   `json:"zdr_attested,omitempty"`
	DataRegion  string `json:"data_region,omitempty"`
}

// HasCredentials returns true if the provider has any form of authentication configured.
func (p *ProviderConfig) HasCredentials() bool {
	return p.APIKey != "" || p.ServiceAccountJSON != "" || (p.AWSAccessKeyID != "" && p.AWSSecretAccessKey != "")
}

// GuardrailConfig holds per-org guardrail pipeline settings.
type GuardrailConfig struct {
	PipelineMode string                     `json:"pipeline_mode,omitempty"` // "parallel" or "sequential"
	FailOpen     bool                       `json:"fail_open,omitempty"`
	TimeoutMs    int                        `json:"timeout_ms,omitempty"`
	Checks       map[string]*GuardrailCheck `json:"checks,omitempty"`

	// ToolCalls runs guardrail checks on agent tool calls. Read through ToolCallGuard.
	ToolCalls *ToolCallGuardrailConfig `json:"tool_calls,omitempty"`
}

// ToolCallGuardrailConfig names existing guardrail checks to run on tool calls.
type ToolCallGuardrailConfig struct {
	Enabled        bool     `json:"enabled,omitempty"`
	Checks         []string `json:"checks,omitempty"`
	OnBlock        string   `json:"on_block,omitempty"` // "error" (default) or "strip"
	ApplyTo        string   `json:"apply_to,omitempty"` // "all" (default), "managed" or "byok"
	MaxArgsBytes   *int     `json:"max_args_bytes,omitempty"`
	StopRunOnBlock bool     `json:"stop_run_on_block,omitempty"`
}

// GuardrailCheck configures a single guardrail check for an org.
type GuardrailCheck struct {
	Enabled             bool                   `json:"enabled"`
	Action              string                 `json:"action,omitempty"` // "block", "warn", "mask", "log"
	ConfidenceThreshold float64                `json:"confidence_threshold,omitempty"`
	Config              map[string]interface{} `json:"config,omitempty"` // check-specific settings
}

// RoutingConfig holds per-org routing strategy settings. Its JSON form is
// read and written by routing_accessors.go, which keeps an explicit
// fallback_enabled false apart from an absent one.
type RoutingConfig struct {
	Strategy string `json:"strategy,omitempty"` // "round_robin", "weighted", "least_latency", "cost_optimized", "fastest"
	// FallbackEnabled is the org's model fallback switch as set; read the
	// effective value through FallbackOn (unset with chains is on).
	FallbackEnabled bool `json:"fallback_enabled,omitempty"`
	// fallbackOff records fallback_enabled set explicitly to false (the
	// JSON methods read and write it as fallback_enabled).
	fallbackOff         bool
	FallbackStatusCodes []int               `json:"fallback_on_status_codes,omitempty"`
	ModelFallbacks      map[string][]string  `json:"model_fallbacks,omitempty"`
	ConditionalRoutes   []ConditionalRoute   `json:"conditional_routes,omitempty"`
	DefaultModel        string              `json:"default_model,omitempty"`

	// Advanced routing features (Phase 12A)
	Complexity   *ComplexityRoutingConfig `json:"complexity,omitempty"`
	Fastest      *FastestResponseConfig   `json:"fastest,omitempty"`
	Scheduled    *ScheduledConfig         `json:"scheduled,omitempty"`
	Adaptive     *AdaptiveRoutingConfig   `json:"adaptive,omitempty"`
	ProviderLock *ProviderLockConfig      `json:"provider_lock,omitempty"`
	AccessGroups map[string]*AccessGroup  `json:"access_groups,omitempty"`

	// Reliability features
	Failover       *FailoverConfig          `json:"failover,omitempty"`
	CircuitBreaker *CircuitBreakerConfig    `json:"circuit_breaker,omitempty"`
	Retry          *RetryConfig             `json:"retry,omitempty"`
	Mirror         *MirrorConfig            `json:"mirror,omitempty"`
	ModelTimeouts  map[string]string        `json:"model_timeouts,omitempty"` // model -> duration string e.g. "30s"

	// Auto tunes how `model: "auto"` picks a model.
	Auto *AutoRoutingConfig `json:"auto,omitempty"`

	// Agent-era routing policy. Pointer and zero-valued fields take their
	// defaults through the OrgConfig accessors.
	DataPolicy       *DataPolicyConfig         `json:"data_policy,omitempty"`
	Agents           *AgentsConfig             `json:"agents,omitempty"`
	Presets          map[string]*RoutingPreset `json:"presets,omitempty"`
	Idempotency      *IdempotencyConfig        `json:"idempotency,omitempty"`
	PromptCaching    *PromptCachingConfig      `json:"prompt_caching,omitempty"`
	MaxTotalAttempts *int                      `json:"max_total_attempts,omitempty"`
	RetryBudget      *RetryBudgetConfig        `json:"retry_budget,omitempty"`

	// Rollout stages a candidate policy. Its Routing never carries a Rollout.
	Rollout *RolloutConfig `json:"rollout,omitempty"`
}

// RolloutConfig stages a candidate routing policy on a share of traffic.
type RolloutConfig struct {
	Version       string               `json:"version"`
	Percent       int                  `json:"percent"`   // 0-100
	StickyBy      string               `json:"sticky_by"` // "session", "key" or "request"
	StartedAt     string               `json:"started_at,omitempty"`
	Routing       *RoutingConfig       `json:"routing"`
	ManagedModels *ManagedModelsConfig `json:"managed_models,omitempty"`
}

// DataPolicyConfig restricts where request data may go.
type DataPolicyConfig struct {
	ZDR                bool     `json:"zdr,omitempty"`
	DataCollection     string   `json:"data_collection,omitempty"` // "allow" (default) or "deny"
	AllowedRegions     []string `json:"allowed_regions,omitempty"`
	BlockPreviewInEnvs []string `json:"block_preview_in_envs,omitempty"`
}

// AgentsConfig is the org policy for agent traffic.
type AgentsConfig struct {
	DefaultModel          string              `json:"default_model,omitempty"`
	BudgetScopeTTLSeconds *int                `json:"budget_scope_ttl_seconds,omitempty"`
	StepPresets           map[string]string   `json:"step_presets,omitempty"` // step -> preset name
	AlwaysEmitMetadata    *bool               `json:"always_emit_metadata,omitempty"`
	UnpricedUnderCap      string              `json:"unpriced_under_cap,omitempty"` // "reject" (default) or "allow"
	FrameworkAliases      *bool               `json:"framework_aliases,omitempty"`
	PriceCeiling          *AgentsPriceCeiling `json:"price_ceiling,omitempty"`

	// Envelopes are run-tree limits; read through AgentEnvelopes.
	Envelopes *AgentsEnvelopesConfig `json:"envelopes,omitempty"`
}

// AgentsPriceCeiling bounds what auto and omega-default may select, in billed
// prices with markup included. A nil field is unbounded.
type AgentsPriceCeiling struct {
	PromptUSDPerMTok     *float64 `json:"prompt_usd_per_mtok,omitempty"`
	CompletionUSDPerMTok *float64 `json:"completion_usd_per_mtok,omitempty"`
	RequestUSD           *float64 `json:"request_usd,omitempty"`
}

// RoutingPreset is a named bundle of model choice, provider preferences and params.
type RoutingPreset struct {
	Model    string               `json:"model,omitempty"`
	Models   []string             `json:"models,omitempty"` // at most 5
	Provider *ProviderPrefs       `json:"provider,omitempty"`
	Auto     *RoutingPresetAuto   `json:"auto,omitempty"`
	Params   *RoutingPresetParams `json:"params,omitempty"`
}

// RoutingPresetAuto tunes auto routing inside a preset.
type RoutingPresetAuto struct {
	Preference string `json:"preference,omitempty"`
}

// RoutingPresetParams are request parameters a preset sets.
type RoutingPresetParams struct {
	Temperature     *float64 `json:"temperature,omitempty"`
	TopP            *float64 `json:"top_p,omitempty"`
	MaxTokens       *int     `json:"max_tokens,omitempty"`
	ReasoningEffort string   `json:"reasoning_effort,omitempty"`
}

// ProviderPrefs is the OpenRouter-shaped provider preference object.
type ProviderPrefs struct {
	Order                  []string          `json:"order,omitempty"`
	Only                   []string          `json:"only,omitempty"`
	Ignore                 []string          `json:"ignore,omitempty"`
	AllowFallbacks         *bool             `json:"allow_fallbacks,omitempty"`
	RequireParameters      *bool             `json:"require_parameters,omitempty"`
	DataCollection         string            `json:"data_collection,omitempty"`
	ZDR                    *bool             `json:"zdr,omitempty"`
	Quantizations          []string          `json:"quantizations,omitempty"`
	Sort                   *ProviderSort     `json:"sort,omitempty"`
	MaxPrice               *ProviderMaxPrice `json:"max_price,omitempty"`
	PreferredMaxLatency    *PercentileBound  `json:"preferred_max_latency,omitempty"`
	PreferredMinThroughput *PercentileBound  `json:"preferred_min_throughput,omitempty"`
}

// ProviderMaxPrice caps provider prices: prompt and completion in USD per
// million tokens, request and image in USD per unit.
type ProviderMaxPrice struct {
	Prompt     *float64 `json:"prompt,omitempty"`
	Completion *float64 `json:"completion,omitempty"`
	Request    *float64 `json:"request,omitempty"`
	Image      *float64 `json:"image,omitempty"`
}

// IdempotencyConfig controls Idempotency-Key handling. Read through Idempotency.
type IdempotencyConfig struct {
	Enabled         *bool `json:"enabled,omitempty"`
	TTLSeconds      *int  `json:"ttl_seconds,omitempty"`
	AcceptAttemptID bool  `json:"accept_attempt_id,omitempty"`
}

// PromptCachingConfig turns on agent-loop prompt caching ("off" or "auto").
type PromptCachingConfig struct {
	Mode string `json:"mode,omitempty"`
}

// RetryBudgetConfig bounds retries and hedges per org. Read through RetryBudget.
type RetryBudgetConfig struct {
	MaxRetryRatio      *float64 `json:"max_retry_ratio,omitempty"`
	MaxHedgesPerMinute *int     `json:"max_hedges_per_minute,omitempty"`
}

// AutoRoutingConfig tunes `model: "auto"` for an org. Every field is optional:
// an org with no config still gets auto routing over the models it can reach.
type AutoRoutingConfig struct {
	Preference       string   `json:"preference,omitempty"` // "cost", "balanced" (default), "quality" or "latency"
	Candidates       []string `json:"candidates,omitempty"` // restricts the pool to these models
	MaxInputPerMTok  float64  `json:"max_input_per_mtok,omitempty"`
	MaxOutputPerMTok float64  `json:"max_output_per_mtok,omitempty"`

	StepProfiles            map[string]*AutoStepProfile `json:"step_profiles,omitempty"` // by step
	Affinity                *AutoAffinityConfig         `json:"affinity,omitempty"`
	OutcomeFeedback         *AutoOutcomeFeedbackConfig  `json:"outcome_feedback,omitempty"`
	DeadlineLatencyFraction *float64                    `json:"deadline_latency_fraction,omitempty"`
	LatencyTargetMs         int                         `json:"latency_target_ms,omitempty"`
	QualityPriors           map[string]float64          `json:"quality_priors,omitempty"` // model -> 0..1
	ExcludeStates           []string                    `json:"exclude_states,omitempty"`
	Selector                *AutoSelectorConfig         `json:"selector,omitempty"`
}

// AutoStepProfile bounds the model tier auto may pick for one step.
type AutoStepProfile struct {
	MinTier string   `json:"min_tier,omitempty"`
	MaxTier string   `json:"max_tier,omitempty"`
	Require []string `json:"require,omitempty"`
	Prefer  string   `json:"prefer,omitempty"`
}

// AutoAffinityConfig keeps a session on the model it last used. Read through AutoAffinity.
type AutoAffinityConfig struct {
	Enabled    *bool `json:"enabled,omitempty"`
	TTLSeconds *int  `json:"ttl_seconds,omitempty"`
}

// AutoOutcomeFeedbackConfig penalises models with bad reported outcomes. Read through AutoOutcomes.
type AutoOutcomeFeedbackConfig struct {
	Enabled       *bool    `json:"enabled,omitempty"`
	WindowSeconds *int     `json:"window_seconds,omitempty"`
	MinSamples    *int     `json:"min_samples,omitempty"`
	PenaltyWeight *float64 `json:"penalty_weight,omitempty"`
}

// AutoSelectorConfig is the optional LLM selector. Read through AutoSelector.
type AutoSelectorConfig struct {
	Enabled            bool     `json:"enabled,omitempty"`
	Mode               string   `json:"mode,omitempty"` // "observe" (default) or "enforce"
	Model              string   `json:"model,omitempty"`
	TimeoutMs          *int     `json:"timeout_ms,omitempty"`
	MaxCostUSD         *float64 `json:"max_cost_usd,omitempty"`
	DailyBudgetUSD     *float64 `json:"daily_budget_usd,omitempty"`
	AmbiguityThreshold *float64 `json:"ambiguity_threshold,omitempty"`
	MaxCandidates      *int     `json:"max_candidates,omitempty"`
}

// ConditionalRoute defines a metadata-based routing rule.
type ConditionalRoute struct {
	Field    string      `json:"field"`
	Operator string      `json:"operator"` // "$eq", "$ne", "$in", "$regex", etc.
	Value    interface{} `json:"value"`
	Target   string      `json:"target"`
}

// ComplexityRoutingConfig routes requests to different models based on prompt complexity.
type ComplexityRoutingConfig struct {
	Enabled     bool                           `json:"enabled"`
	DefaultTier string                         `json:"default_tier,omitempty"`
	Tiers       map[string]*ComplexityTier     `json:"tiers,omitempty"`
	Weights     map[string]float64             `json:"weights,omitempty"` // signal weights for scoring
}

// ComplexityTier maps a complexity tier to a model/provider.
type ComplexityTier struct {
	MaxScore int    `json:"max_score"`
	Model    string `json:"model"`
	Provider string `json:"provider,omitempty"`
}

// FastestResponseConfig sends requests to multiple providers and returns the first response.
type FastestResponseConfig struct {
	Enabled           bool     `json:"enabled"`
	MaxConcurrent     int      `json:"max_concurrent,omitempty"`
	CancelDelay       string   `json:"cancel_delay,omitempty"` // duration string e.g. "100ms"
	ExcludedProviders []string `json:"excluded_providers,omitempty"`

	HedgeAfterMs int  `json:"hedge_after_ms,omitempty"`
	MaxHedges    int  `json:"max_hedges,omitempty"` // at most 2
	AllowManaged bool `json:"allow_managed,omitempty"`
}

// ScheduledConfig allows scheduling completion requests for future execution.
type ScheduledConfig struct {
	Enabled          bool   `json:"enabled"`
	MaxPendingJobs   int    `json:"max_pending_jobs,omitempty"`
	ResultTTL        string `json:"result_ttl,omitempty"`         // duration string
	MaxScheduleAhead string `json:"max_schedule_ahead,omitempty"` // duration string
	RetryAttempts    int    `json:"retry_attempts,omitempty"`
	WorkerCount      int    `json:"worker_count,omitempty"`
}

// AdaptiveRoutingConfig dynamically adjusts provider weights based on real-time performance.
type AdaptiveRoutingConfig struct {
	Enabled          bool                `json:"enabled"`
	LearningRequests int                 `json:"learning_requests,omitempty"`
	UpdateInterval   string              `json:"update_interval,omitempty"` // duration string
	SmoothingFactor  float64             `json:"smoothing_factor,omitempty"`
	MinWeight        float64             `json:"min_weight,omitempty"`
	SignalWeights    *SignalWeights      `json:"signal_weights,omitempty"`
}

// SignalWeights configures how different performance signals affect adaptive routing weights.
type SignalWeights struct {
	Latency   float64 `json:"latency"`
	ErrorRate float64 `json:"error_rate"`
	Cost      float64 `json:"cost"`
}

// ProviderLockConfig restricts which providers can be used for an org.
type ProviderLockConfig struct {
	Enabled          bool     `json:"enabled"`
	AllowedProviders []string `json:"allowed_providers,omitempty"`
	DenyProviders    []string `json:"deny_providers,omitempty"`
}

// AccessGroup defines a named group of models that can be assigned to API keys.
type AccessGroup struct {
	Models      []string          `json:"models,omitempty"`
	Description string            `json:"description,omitempty"`
	Aliases     map[string]string `json:"aliases,omitempty"`
}

// FailoverConfig controls provider failover behavior per org.
type FailoverConfig struct {
	Enabled           bool   `json:"enabled"`
	MaxAttempts       int    `json:"max_attempts,omitempty"`
	OnStatusCodes     []int  `json:"on_status_codes,omitempty"`
	OnTimeout         bool   `json:"on_timeout,omitempty"`
	PerAttemptTimeout string `json:"per_attempt_timeout,omitempty"` // duration string e.g. "30s"
}

// CircuitBreakerConfig controls circuit breaker behavior per org.
type CircuitBreakerConfig struct {
	Enabled          bool   `json:"enabled"`
	FailureThreshold int    `json:"failure_threshold,omitempty"`
	SuccessThreshold int    `json:"success_threshold,omitempty"`
	Cooldown         string `json:"cooldown,omitempty"` // duration string e.g. "30s"
	OnStatusCodes    []int  `json:"on_status_codes,omitempty"`
}

// RetryConfig controls retry behavior per org.
type RetryConfig struct {
	Enabled       bool    `json:"enabled"`
	MaxRetries    int     `json:"max_retries,omitempty"`
	InitialDelay  string  `json:"initial_delay,omitempty"` // duration string
	MaxDelay      string  `json:"max_delay,omitempty"`     // duration string
	Multiplier    float64 `json:"multiplier,omitempty"`
	OnStatusCodes []int   `json:"on_status_codes,omitempty"`
	OnTimeout     bool    `json:"on_timeout,omitempty"`
}

// MirrorConfig controls traffic mirroring per org.
type MirrorConfig struct {
	Enabled bool          `json:"enabled"`
	Rules   []MirrorRule  `json:"rules,omitempty"`
}

// MirrorRule defines a single traffic mirroring rule.
type MirrorRule struct {
	SourceModel    string  `json:"source_model"`
	TargetProvider string  `json:"target_provider"`
	TargetModel    string  `json:"target_model"`
	SampleRate     float64 `json:"sample_rate"`
}

// CacheConfig holds per-org cache settings.
type CacheConfig struct {
	Enabled    bool              `json:"enabled"`
	Backend    string            `json:"backend,omitempty"`    // "memory", "disk", "redis", "s3", "azure-blob", "gcs"
	DefaultTTL int               `json:"default_ttl,omitempty"` // seconds
	MaxEntries int               `json:"max_entries,omitempty"`

	// L1 backend-specific configs
	Disk      *DiskCacheConfig      `json:"disk,omitempty"`
	Redis     *RedisCacheConfig     `json:"redis,omitempty"`
	S3        *S3CacheConfig        `json:"s3,omitempty"`
	AzureBlob *AzureBlobCacheConfig `json:"azure_blob,omitempty"`
	GCS       *GCSCacheConfig       `json:"gcs,omitempty"`

	// L2 semantic cache
	Semantic *SemanticCacheConfig `json:"semantic,omitempty"`

	// Edge caching
	Edge *EdgeCacheConfig `json:"edge,omitempty"`
}

// DiskCacheConfig holds disk-based cache settings.
type DiskCacheConfig struct {
	Directory       string `json:"directory,omitempty"`
	MaxSizeBytes    int64  `json:"max_size_bytes,omitempty"`
	Compress        bool   `json:"compress,omitempty"`
	CleanupInterval string `json:"cleanup_interval,omitempty"` // duration string
}

// RedisCacheConfig holds Redis cache settings.
type RedisCacheConfig struct {
	Address   string   `json:"address,omitempty"`
	Addresses []string `json:"addresses,omitempty"` // cluster/sentinel
	Password  string   `json:"password,omitempty"`
	DB        int      `json:"db,omitempty"`
	Mode      string   `json:"mode,omitempty"` // "single", "sentinel", "cluster"
	PoolSize  int      `json:"pool_size,omitempty"`
	TLS       bool     `json:"tls,omitempty"`
	KeyPrefix string   `json:"key_prefix,omitempty"`
	Compress  bool     `json:"compress,omitempty"`
}

// S3CacheConfig holds S3 cache settings.
type S3CacheConfig struct {
	Bucket         string `json:"bucket,omitempty"`
	Prefix         string `json:"prefix,omitempty"`
	Region         string `json:"region,omitempty"`
	AccessKeyID    string `json:"access_key_id,omitempty"`
	SecretAccessKey string `json:"secret_access_key,omitempty"`
	Compress       bool   `json:"compress,omitempty"`
}

// AzureBlobCacheConfig holds Azure Blob Storage cache settings.
type AzureBlobCacheConfig struct {
	Container        string `json:"container,omitempty"`
	Prefix           string `json:"prefix,omitempty"`
	ConnectionString string `json:"connection_string,omitempty"`
	SASToken         string `json:"sas_token,omitempty"`
	Compress         bool   `json:"compress,omitempty"`
}

// GCSCacheConfig holds Google Cloud Storage cache settings.
type GCSCacheConfig struct {
	Bucket          string `json:"bucket,omitempty"`
	Prefix          string `json:"prefix,omitempty"`
	Project         string `json:"project,omitempty"`
	CredentialsFile string `json:"credentials_file,omitempty"`
	Compress        bool   `json:"compress,omitempty"`
}

// SemanticCacheConfig holds L2 semantic/vector cache settings.
type SemanticCacheConfig struct {
	Enabled    bool              `json:"enabled"`
	Backend    string            `json:"backend,omitempty"` // "qdrant", "weaviate", "pinecone"
	Threshold  float64           `json:"threshold,omitempty"`
	Dimensions int               `json:"dimensions,omitempty"`
	MaxEntries int               `json:"max_entries,omitempty"`
	Qdrant     *QdrantConfig     `json:"qdrant,omitempty"`
	Weaviate   *WeaviateConfig   `json:"weaviate,omitempty"`
	Pinecone   *PineconeConfig   `json:"pinecone,omitempty"`
}

// QdrantConfig holds Qdrant vector DB settings.
type QdrantConfig struct {
	URL        string `json:"url,omitempty"`
	Collection string `json:"collection,omitempty"`
	APIKey     string `json:"api_key,omitempty"`
}

// WeaviateConfig holds Weaviate vector DB settings.
type WeaviateConfig struct {
	URL    string `json:"url,omitempty"`
	Class  string `json:"class,omitempty"`
	APIKey string `json:"api_key,omitempty"`
}

// PineconeConfig holds Pinecone vector DB settings.
type PineconeConfig struct {
	URL    string `json:"url,omitempty"`
	APIKey string `json:"api_key,omitempty"`
}

// EdgeCacheConfig holds CDN edge caching settings.
type EdgeCacheConfig struct {
	Enabled         bool     `json:"enabled"`
	DefaultTTL      int      `json:"default_ttl,omitempty"` // seconds
	MaxSize         int      `json:"max_size,omitempty"`    // bytes
	CacheableModels []string `json:"cacheable_models,omitempty"`
	RequireOptIn    bool     `json:"require_opt_in,omitempty"`
}

// RateLimitConfig holds per-org rate limiting settings.
type RateLimitConfig struct {
	Enabled     bool `json:"enabled"`
	GlobalRPM   int  `json:"global_rpm,omitempty"`
	GlobalTPM   int  `json:"global_tpm,omitempty"`
	PerKeyRPM   int  `json:"per_key_rpm,omitempty"`
	PerKeyTPM   int  `json:"per_key_tpm,omitempty"`
	PerModelRPM int  `json:"per_model_rpm,omitempty"`
	PerUserRPM  int  `json:"per_user_rpm,omitempty"`

	PerKeyMaxParallel int `json:"per_key_max_parallel,omitempty"`
}

// BudgetsConfig holds per-org spend budget settings with hierarchical levels.
type BudgetsConfig struct {
	Enabled       bool    `json:"enabled"`
	DefaultPeriod string  `json:"default_period,omitempty"` // "daily", "weekly", "monthly", "total"
	WarnThreshold float64 `json:"warn_threshold,omitempty"` // 0-1, e.g. 0.8 = 80%
	OrgLimit      float64 `json:"org_limit,omitempty"`      // org-wide spend limit in USD (flat, backward compat)
	OrgPeriod     string  `json:"org_period,omitempty"`     // overrides default_period for org
	HardLimit     bool    `json:"hard_limit,omitempty"`     // block when exceeded vs warn
	// Action "throttle" holds the org budget past its limit to ThrottleRPM
	// requests a minute (read through OrgBudgetAction, OrgThrottleRPM).
	Action      string `json:"action,omitempty"`
	ThrottleRPM int    `json:"throttle_rpm,omitempty"`

	// Hierarchical budget levels.
	Teams map[string]*BudgetLevelConfig `json:"teams,omitempty"`
	Users map[string]*BudgetLevelConfig `json:"users,omitempty"`
	Keys  map[string]*BudgetLevelConfig `json:"keys,omitempty"`
	Tags  map[string]*BudgetLevelConfig `json:"tags,omitempty"`

	// Budget-hold behaviour; read through BudgetsFailMode and
	// BudgetsNoMaxTokens.
	FailMode    string `json:"fail_mode,omitempty"`     // "closed" (default) or "open"
	NoMaxTokens string `json:"no_max_tokens,omitempty"` // "reserve_default" (default) or "clamp"
}

// BudgetLevelConfig defines a spend budget at any hierarchy level.
type BudgetLevelConfig struct {
	Limit    float64            `json:"limit"`
	Period   string             `json:"period,omitempty"`     // overrides default_period
	Hard     *bool              `json:"hard,omitempty"`       // nil = default true
	PerModel map[string]float64 `json:"per_model,omitempty"`

	// IncludeBYOK nil means true: the budget counts BYOK spend too.
	IncludeBYOK     *bool     `json:"include_byok,omitempty"`
	AlertThresholds []float64 `json:"alert_thresholds,omitempty"`

	// Action "throttle" holds the level past its limit to ThrottleRPM
	// requests a minute (read through LevelAction, LevelThrottleRPM).
	Action      string `json:"action,omitempty"`
	ThrottleRPM int    `json:"throttle_rpm,omitempty"`
}

// CostTrackingConfig holds per-org cost tracking settings.
type CostTrackingConfig struct {
	Enabled       bool                      `json:"enabled"`
	CustomPricing map[string]*CustomPricing `json:"custom_pricing,omitempty"` // model -> pricing override
}

// CustomPricing holds custom per-model pricing for an org.
type CustomPricing struct {
	InputPerMTok  float64 `json:"input_per_mtok"`
	OutputPerMTok float64 `json:"output_per_mtok"`
}

// IPACLConfig holds per-org IP access control settings.
type IPACLConfig struct {
	Enabled bool     `json:"enabled"`
	Allow   []string `json:"allow,omitempty"` // allowed IPs/CIDRs
	Deny    []string `json:"deny,omitempty"`  // denied IPs/CIDRs
}

// AlertingConfig holds per-org alerting settings.
type AlertingConfig struct {
	Enabled  bool                `json:"enabled"`
	Rules    []*AlertRuleConfig  `json:"rules,omitempty"`
	Channels []*AlertChannelConfig `json:"channels,omitempty"`
}

// AlertRuleConfig configures a single alert rule for an org.
type AlertRuleConfig struct {
	Name      string   `json:"name"`
	Metric    string   `json:"metric"`    // error_count, request_count, cost_total, latency_avg, tokens_total
	Condition string   `json:"condition"` // ">=", ">", "<=", "<", "=="
	Threshold float64  `json:"threshold"`
	Window    string   `json:"window,omitempty"`   // duration string e.g. "5m"
	Cooldown  string   `json:"cooldown,omitempty"` // duration string e.g. "15m"; read through AlertCooldown (default 15m)
	Channels  []string `json:"channels,omitempty"` // channel names to notify
	Severity  string   `json:"severity,omitempty"` // info, warning or critical; read through AlertSeverity (default warning)
}

// AlertChannelConfig configures an alert notification channel.
type AlertChannelConfig struct {
	Name    string            `json:"name"`
	Type    string            `json:"type"`              // "webhook", "slack", "log"
	URL     string            `json:"url,omitempty"`
	Headers map[string]string `json:"headers,omitempty"`
}

// PrivacyConfig holds per-org PII redaction settings.
type PrivacyConfig struct {
	Enabled  bool                    `json:"enabled"`
	Mode     string                  `json:"mode,omitempty"` // "full", "patterns", "none"
	Patterns []*RedactPatternConfig  `json:"redact_patterns,omitempty"`

	// Retention and ContentMode are read through Retention and ContentMode.
	Retention   *PrivacyRetentionConfig `json:"retention,omitempty"`
	ContentMode string                  `json:"content_mode,omitempty"` // "full" (default), "metadata" or "none"
}

// PrivacyRetentionConfig sets retention TTLs. A nil TTL keeps rows indefinitely.
type PrivacyRetentionConfig struct {
	RequestLogDays *int  `json:"request_log_days,omitempty"`
	AttemptDays    *int  `json:"attempt_days,omitempty"` // nil = RequestLogDays
	StoreBodies    *bool `json:"store_bodies,omitempty"` // nil = true
}

// RedactPatternConfig defines a named regex pattern for PII redaction.
type RedactPatternConfig struct {
	Name    string `json:"name"`
	Pattern string `json:"pattern"`
}

// ToolPolicyConfig holds per-org tool/function calling policy.
type ToolPolicyConfig struct {
	Enabled            bool     `json:"enabled"`
	MaxToolsPerRequest int      `json:"max_tools_per_request,omitempty"`
	DefaultAction      string   `json:"default_action,omitempty"` // "strip" or "reject"
	Allow              []string `json:"allow,omitempty"`
	Deny               []string `json:"deny,omitempty"`
}

// MCPOrgConfig holds per-org MCP (Model Context Protocol) settings: org
// config mcp v2. Every v2 key is optional, and an unset key takes its
// documented default; read v2 settings through the accessors in
// config_mcp.go, never through the raw pointers.
type MCPOrgConfig struct {
	Enabled        bool                               `json:"enabled"`
	Access         *MCPAccessConfig                   `json:"access,omitempty"`
	Servers        map[string]*MCPServerOrgConfig     `json:"servers,omitempty"`
	VirtualServers map[string]*MCPVirtualServerConfig `json:"virtual_servers,omitempty"`
	ManagedTools   *MCPManagedToolsConfig             `json:"managed_tools,omitempty"`
	Pinning        *MCPPinningConfig                  `json:"pinning,omitempty"`
	Scanning       *MCPScanningConfig                 `json:"scanning,omitempty"`
	Guardrails     *MCPGuardrailOrgConfig             `json:"guardrails,omitempty"`
	FlowControl    *MCPFlowControlConfig              `json:"flow_control,omitempty"`
	Policy         *MCPPolicyConfig                   `json:"policy,omitempty"`
	Shaping        *MCPShapingConfig                  `json:"shaping,omitempty"`
}

// MCPServerOrgConfig configures a single upstream MCP server for an org.
// Read it with defaults applied through OrgConfig.MCPServer.
type MCPServerOrgConfig struct {
	URL       string `json:"url,omitempty"`
	Transport string `json:"transport,omitempty"` // "http", or "sse" for a 2024-11-05 upstream
	Era       string `json:"era,omitempty"`       // "auto" (default) or a protocol revision

	Credential *MCPServerCredential `json:"credential,omitempty"`
	// Auth is the inline credential form (bearer or api_key); Credential
	// is the preferred form.
	Auth *MCPAuthOrgConfig `json:"auth,omitempty"`

	TrustedAnnotations   *bool    `json:"trusted_annotations,omitempty"`
	TimeoutMs            *int     `json:"timeout_ms,omitempty"`
	ToolsCacheTTL        string   `json:"tools_cache_ttl,omitempty"` // legacy duration string e.g. "5m"
	ToolsCacheTTLSeconds *int     `json:"tools_cache_ttl_seconds,omitempty"`
	ForwardHeaders       []string `json:"forward_headers,omitempty"`
	PropagateDepth       *bool    `json:"propagate_depth,omitempty"`
	Enabled              *bool    `json:"enabled,omitempty"`

	OAuth  *MCPServerOAuthConfig `json:"oauth,omitempty"`
	Labels []string              `json:"labels,omitempty"` // private_data, untrusted_input, egress
}

// MCPAuthOrgConfig holds auth settings for an upstream MCP server.
type MCPAuthOrgConfig struct {
	Type   string `json:"type,omitempty"`   // "bearer", "api_key", "none"
	Token  string `json:"token,omitempty"`  // for bearer
	Header string `json:"header,omitempty"` // for api_key
	Key    string `json:"key,omitempty"`    // for api_key
}

// MCPGuardrailOrgConfig controls per-org MCP tool call guardrails.
type MCPGuardrailOrgConfig struct {
	Enabled         bool           `json:"enabled"`
	BlockedTools    []string       `json:"blocked_tools,omitempty"`
	AllowedServers  []string       `json:"allowed_servers,omitempty"`
	ValidateInputs  bool           `json:"validate_inputs"`
	ValidateOutputs bool           `json:"validate_outputs"`
	ToolRateLimits  map[string]int `json:"tool_rate_limits,omitempty"`
}

// AuditOrgConfig holds per-org audit logging settings.
type AuditOrgConfig struct {
	Enabled     bool               `json:"enabled"`
	MinSeverity string             `json:"min_severity,omitempty"` // info, warn, error, critical
	Categories  []string           `json:"categories,omitempty"`
	Sinks       []*AuditSinkConfig `json:"sinks,omitempty"`
}

// AuditSinkConfig defines an audit log sink for an org.
type AuditSinkConfig struct {
	Type    string            `json:"type"`              // stdout, file, webhook
	Path    string            `json:"path,omitempty"`    // for file sink
	URL     string            `json:"url,omitempty"`     // for webhook sink
	Headers map[string]string `json:"headers,omitempty"` // for webhook sink
}

// A2AOrgConfig holds per-org A2A (Agent-to-Agent) protocol settings.
type A2AOrgConfig struct {
	Enabled bool                        `json:"enabled"`
	Card    *A2ACardConfig              `json:"card,omitempty"`
	Agents  map[string]*A2AAgentConfig  `json:"agents,omitempty"`
}

// A2ACardConfig holds per-org agent card customization.
type A2ACardConfig struct {
	Name        string `json:"name,omitempty"`
	Description string `json:"description,omitempty"`
	Version     string `json:"version,omitempty"`
}

// A2AAgentConfig configures a single external A2A agent per-org.
type A2AAgentConfig struct {
	URL         string            `json:"url"`
	Auth        *A2AAuthConfig    `json:"auth,omitempty"`
	Description string            `json:"description,omitempty"`
	Skills      []*A2ASkillConfig `json:"skills,omitempty"`
}

// A2AAuthConfig holds auth settings for an external A2A agent.
type A2AAuthConfig struct {
	Type   string `json:"type,omitempty"`   // "bearer", "api_key", "none"
	Token  string `json:"token,omitempty"`
	Header string `json:"header,omitempty"`
}

// A2ASkillConfig defines a skill of an external A2A agent.
type A2ASkillConfig struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
}

// ModelDatabaseOrgConfig holds per-org model database overrides.
type ModelDatabaseOrgConfig struct {
	Overrides map[string]*ModelOverrideOrgConfig `json:"overrides,omitempty"`
}

// ModelOverrideOrgConfig allows overriding model metadata per-org.
// Pointer fields are optional — nil means "don't override".
type ModelOverrideOrgConfig struct {
	MaxInputTokens  *int                   `json:"max_input_tokens,omitempty"`
	MaxOutputTokens *int                   `json:"max_output_tokens,omitempty"`
	Pricing         *PricingOverrideOrg    `json:"pricing,omitempty"`
	Capabilities    *CapabilityOverrideOrg `json:"capabilities,omitempty"`
}

// PricingOverrideOrg allows overriding per-token pricing per-org.
type PricingOverrideOrg struct {
	InputPerToken       *float64 `json:"input_per_token,omitempty"`
	OutputPerToken      *float64 `json:"output_per_token,omitempty"`
	CachedInputPerToken *float64 `json:"cached_input_per_token,omitempty"`
	BatchInputPerToken  *float64 `json:"batch_input_per_token,omitempty"`
	BatchOutputPerToken *float64 `json:"batch_output_per_token,omitempty"`
}

// CapabilityOverrideOrg allows overriding capability flags per-org.
type CapabilityOverrideOrg struct {
	FunctionCalling   *bool `json:"function_calling,omitempty"`
	ParallelToolCalls *bool `json:"parallel_tool_calls,omitempty"`
	Vision            *bool `json:"vision,omitempty"`
	AudioInput        *bool `json:"audio_input,omitempty"`
	AudioOutput       *bool `json:"audio_output,omitempty"`
	PDFInput          *bool `json:"pdf_input,omitempty"`
	Streaming         *bool `json:"streaming,omitempty"`
	ResponseSchema    *bool `json:"response_schema,omitempty"`
	SystemMessages    *bool `json:"system_messages,omitempty"`
	PromptCaching     *bool `json:"prompt_caching,omitempty"`
	Reasoning         *bool `json:"reasoning,omitempty"`
}

// ToolsConfig is org config tools v1. It has no managed flag:
// the managed opt-in is mcp.managed_tools. Read it through ToolsCapability
// and ToolsPolicy.
type ToolsConfig struct {
	Capabilities map[string]*ToolsCapabilityConfig `json:"capabilities,omitempty"`
	Policy       *ToolsPolicyConfig                `json:"policy,omitempty"`
}

// ToolsCapabilityConfig is one capability's org settings. Pinned options are
// developer-pinned: tool arguments cannot override them.
type ToolsCapabilityConfig struct {
	Enabled           *bool                  `json:"enabled,omitempty"`
	Providers         []string               `json:"providers,omitempty"` // vendor order
	MaxCostUSDPerCall *float64               `json:"max_cost_usd_per_call,omitempty"`
	Defaults          map[string]interface{} `json:"defaults,omitempty"`
	Pinned            map[string]interface{} `json:"pinned,omitempty"`
}

// ToolsPolicyConfig is the org's capability policy.
type ToolsPolicyConfig struct {
	Domains               *ToolsDomainsPolicy `json:"domains,omitempty"`
	NoTrainingVendorsOnly *bool               `json:"no_training_vendors_only,omitempty"`
	ZDRRequired           *bool               `json:"zdr_required,omitempty"`
	Regions               []string            `json:"regions,omitempty"`
	RetentionOverride     string              `json:"retention_override,omitempty"` // none, metadata or full; tightens only
}

// ToolsDomainsPolicy allows and denies capability target domains.
type ToolsDomainsPolicy struct {
	Allow []string `json:"allow,omitempty"`
	Deny  []string `json:"deny,omitempty"`
}

// ToolsCapabilitySettings is one capability's settings with defaults applied.
type ToolsCapabilitySettings struct {
	Enabled           bool
	Providers         []string // empty: the price cards' default order
	MaxCostUSDPerCall *float64 // nil: no per-call cap
	Defaults          map[string]interface{}
	Pinned            map[string]interface{}
}

// ToolsCapability returns the org's settings for one capability; an
// unconfigured capability is disabled.
func (c *OrgConfig) ToolsCapability(capability string) ToolsCapabilitySettings {
	var t ToolsCapabilityConfig
	if c != nil && c.Tools != nil && c.Tools.Capabilities[capability] != nil {
		t = *c.Tools.Capabilities[capability]
	}
	return ToolsCapabilitySettings{
		Enabled:           boolOr(t.Enabled, false),
		Providers:         t.Providers,
		MaxCostUSDPerCall: t.MaxCostUSDPerCall,
		Defaults:          t.Defaults,
		Pinned:            t.Pinned,
	}
}

// ToolsPolicySettings is tools.policy with defaults applied.
type ToolsPolicySettings struct {
	DomainsAllow          []string
	DomainsDeny           []string
	NoTrainingVendorsOnly bool
	ZDRRequired           bool
	Regions               []string // empty: no region pin
	RetentionOverride     string   // "": no override
}

// ToolsPolicy returns the org's capability policy.
func (c *OrgConfig) ToolsPolicy() ToolsPolicySettings {
	var p ToolsPolicyConfig
	if c != nil && c.Tools != nil && c.Tools.Policy != nil {
		p = *c.Tools.Policy
	}
	out := ToolsPolicySettings{
		NoTrainingVendorsOnly: boolOr(p.NoTrainingVendorsOnly, false),
		ZDRRequired:           boolOr(p.ZDRRequired, false),
		Regions:               p.Regions,
		RetentionOverride:     p.RetentionOverride,
	}
	if p.Domains != nil {
		out.DomainsAllow, out.DomainsDeny = p.Domains.Allow, p.Domains.Deny
	}
	return out
}

// IdentityConfig is delegated identity. IdP.ClientSecret and
// DelegatedGrants exist only in a pushed payload, assembled at push time.
type IdentityConfig struct {
	RequireHumanSponsor *bool                       `json:"require_human_sponsor,omitempty"`
	Grants              *IdentityGrantsConfig      `json:"grants,omitempty"`
	IdP                 *IdentityIdPConfig         `json:"idp,omitempty"`
	DelegatedGrants     map[string]*DelegatedGrant `json:"delegated_grants,omitempty"`
}

// IdentityGrantsConfig bounds standing grants.
type IdentityGrantsConfig struct {
	MaxTTLDays *int `json:"max_ttl_days,omitempty"`
}

// IdentityIdPConfig is the org's identity provider for ID-JAG.
type IdentityIdPConfig struct {
	Issuer        string `json:"issuer,omitempty"`
	TokenEndpoint string `json:"token_endpoint,omitempty"`
	ClientID      string `json:"client_id,omitempty"`
	ClientSecret  string `json:"client_secret,omitempty"` // push-only
}

// DelegatedGrant is a standing grant that lets an agent act for its grantor.
type DelegatedGrant struct {
	GrantID          string                `json:"grant_id"`
	AgentID          string                `json:"agent_id"`
	GrantorUserID    string                `json:"grantor_user_id"`
	Scopes           *DelegatedGrantScopes `json:"scopes"`
	MaxUSD           *float64              `json:"max_usd,omitempty"`
	PreapprovedRules []string              `json:"preapproved_rules,omitempty"`
	ExpiresAt        string                `json:"expires_at"`
}

// DelegatedGrantScopes is what a grant lets its agent use.
type DelegatedGrantScopes struct {
	Servers []string `json:"servers,omitempty"`
	Tools   []string `json:"tools,omitempty"`
	Models  []string `json:"models,omitempty"`
}

// IdentitySettings is identity with its defaults applied.
type IdentitySettings struct {
	RequireHumanSponsor bool
	GrantsMaxTTLDays    int
	IdP                 IdentityIdPConfig
	DelegatedGrants     map[string]*DelegatedGrant
}

// IdentityPolicy returns the org's delegated-identity settings.
func (c *OrgConfig) IdentityPolicy() IdentitySettings {
	var i IdentityConfig
	if c != nil && c.Identity != nil {
		i = *c.Identity
	}
	out := IdentitySettings{
		RequireHumanSponsor: boolOr(i.RequireHumanSponsor, false),
		GrantsMaxTTLDays:    30,
		DelegatedGrants:     i.DelegatedGrants,
	}
	if i.Grants != nil {
		out.GrantsMaxTTLDays = intOr(i.Grants.MaxTTLDays, 30)
	}
	if i.IdP != nil {
		out.IdP = *i.IdP
	}
	return out
}

// BudgetsFailMode is budgets.fail_mode: "closed" (default) or "open".
func (c *OrgConfig) BudgetsFailMode() string {
	if c == nil || c.Budgets == nil {
		return "closed"
	}
	return stringOr(c.Budgets.FailMode, "closed")
}

// BudgetsNoMaxTokens is budgets.no_max_tokens: "reserve_default" (default)
// or "clamp".
func (c *OrgConfig) BudgetsNoMaxTokens() string {
	if c == nil || c.Budgets == nil {
		return "reserve_default"
	}
	return stringOr(c.Budgets.NoMaxTokens, "reserve_default")
}

// AgentsEnvelopesConfig is run-tree envelopes.
type AgentsEnvelopesConfig struct {
	Defaults       *AgentsEnvelopeDefaults `json:"defaults,omitempty"`
	OnBreach       string                  `json:"on_breach,omitempty"` // "stop" (default), "pause", "downgrade" or "warn"
	SoftThreshold  *float64                `json:"soft_threshold,omitempty"`
	AllowDowngrade *bool                   `json:"allow_downgrade,omitempty"`
	DowngradeModel string                  `json:"downgrade_model,omitempty"`
	Loop           *AgentsEnvelopeLoop     `json:"loop,omitempty"`
}

// AgentsEnvelopeDefaults are the default envelope limits; nil is unbounded.
type AgentsEnvelopeDefaults struct {
	MaxUSD                        *float64 `json:"max_usd,omitempty"`
	MaxUncachedInputTokensPerHour *int     `json:"max_uncached_input_tokens_per_hour,omitempty"`
	MaxCalls                      *int     `json:"max_calls,omitempty"`
	MaxWallMs                     *int     `json:"max_wall_ms,omitempty"`
	MaxConcurrentChildren         *int     `json:"max_concurrent_children,omitempty"`
}

// AgentsEnvelopeLoop is the loop guard.
type AgentsEnvelopeLoop struct {
	Action string `json:"action,omitempty"` // "stop" (default) or "warn"
}

// EnvelopeSettings is routing.agents.envelopes with defaults applied.
type EnvelopeSettings struct {
	Defaults       AgentsEnvelopeDefaults
	OnBreach       string
	SoftThreshold  float64
	AllowDowngrade bool
	DowngradeModel string
	LoopAction     string
}

// AgentEnvelopes returns the org's run-tree envelope policy.
func (c *OrgConfig) AgentEnvelopes() EnvelopeSettings {
	var e AgentsEnvelopesConfig
	if r := c.routing(); r.Agents != nil && r.Agents.Envelopes != nil {
		e = *r.Agents.Envelopes
	}
	out := EnvelopeSettings{
		OnBreach:       stringOr(e.OnBreach, "stop"),
		SoftThreshold:  floatOr(e.SoftThreshold, 0.8),
		AllowDowngrade: boolOr(e.AllowDowngrade, false),
		DowngradeModel: e.DowngradeModel,
		LoopAction:     "stop",
	}
	if e.Defaults != nil {
		out.Defaults = *e.Defaults
	}
	if e.Loop != nil {
		out.LoopAction = stringOr(e.Loop.Action, "stop")
	}
	return out
}
