package guardrails

import (
	"context"
	"fmt"
	"github.com/futureagi/agentcc-gateway/internal/reflexion"
	"log/slog"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// DynamicFactory creates a Guardrail from a name and config map.
// Used for creating dynamic guardrails (FI Eval, webhooks, etc.) from org config
// when they are not in the static registry.
type DynamicFactory func(name string, cfg map[string]interface{}) Guardrail

// GuardrailPlugin wraps the guardrail engine as a pipeline plugin.
type GuardrailPlugin struct {
	engine         *Engine
	registry       map[string]Guardrail // all registered guardrail implementations
	dynamicFactory DynamicFactory       // creates guardrails not in registry
	policyStore    *policy.Store
	tenantStore    *tenant.Store // nil-safe; per-org guardrail overrides
	failOpen       bool
	defaultTimeout time.Duration

	// Cache for dynamically created guardrails (e.g., FI Eval with per-org API keys).
	dynamicMu    sync.RWMutex
	dynamicCache map[string]map[string]Guardrail // org_id -> check_name -> guardrail
}

// NewPlugin creates a new guardrail pipeline plugin.
func NewPlugin(engine *Engine, registry map[string]Guardrail, dynamicFactory DynamicFactory, policyStore *policy.Store, tenantStore *tenant.Store) *GuardrailPlugin {
	timeout := 30 * time.Second
	failOpen := false
	if engine != nil {
		timeout = engine.DefaultTimeout()
		failOpen = engine.FailOpen()
	}
	return &GuardrailPlugin{
		engine:         engine,
		registry:       registry,
		dynamicFactory: dynamicFactory,
		policyStore:    policyStore,
		tenantStore:    tenantStore,
		failOpen:       failOpen,
		defaultTimeout: timeout,
		dynamicCache:   make(map[string]map[string]Guardrail),
	}
}

func (p *GuardrailPlugin) Name() string  { return "guardrails" }
func (p *GuardrailPlugin) Priority() int { return 50 } // After auth (20), RBAC (30), budget (40). Before cache (200).

// ProcessRequest runs pre-stage guardrails.
func (p *GuardrailPlugin) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if err := p.validateReflectionConfig(rc); err != nil {
		return pipeline.ResultError(err)
	}
	keyPolicy, reqPolicy, err := p.resolvePolicy(rc)
	if err != nil {
		return pipeline.ResultError(err)
	}
	// 1. Run static engine rules (self-hosted / config.yaml rules).
	if p.engine != nil && p.engine.PreCount() > 0 {

		input := &CheckInput{
			Request:  rc.Request,
			Metadata: rc.Metadata,
		}

		result := p.engine.RunPre(ctx, input, keyPolicy, reqPolicy)
		pluginResult := p.processResult(rc, result)
		if pluginResult.Action != pipeline.Continue {
			return pluginResult
		}
	}

	// 2. Run dynamic guardrails from org config (managed mode).
	return p.runOrgGuardrails(ctx, rc, StagePre)
}

// ProcessResponse runs post-stage guardrails.
func (p *GuardrailPlugin) ProcessResponse(ctx context.Context, rc *models.RequestContext) (result pipeline.PluginResult) {
	warnings, hadWarnings := rc.Metadata["guardrail_warnings"]
	preResults := len(rc.GuardrailResults)
	defer func() {
		if result.Error != nil {
			rc.Response = nil
			rc.Metadata["guardrail_action"] = "blocked"
			if hadWarnings {
				rc.Metadata["guardrail_warnings"] = warnings
			} else {
				delete(rc.Metadata, "guardrail_warnings")
			}
			for i := preResults; i < len(rc.GuardrailResults); i++ {
				rc.GuardrailResults[i].Message = "response check failed"
			}
		}
	}()
	if rc.Response == nil {
		return pipeline.ResultContinue()
	}
	result = pipeline.ResultContinue()
	if p.engine != nil && p.engine.PostCount() > 0 {
		keyPolicy, reqPolicy, err := p.resolvePolicy(rc)
		if err != nil {
			return pipeline.ResultError(err)
		}
		input := &CheckInput{Request: rc.Request, Response: rc.Response, Metadata: rc.Metadata, DeferAsync: true}
		checks := p.engine.RunPost(ctx, input, keyPolicy, reqPolicy)
		if checks.Blocked {
			result = p.postBlock(rc, checks)
		} else {
			p.applyMetadata(rc, checks)
		}
	}
	// Do not return early: any reject-only rule vetoes repair of this candidate.
	dynamic := p.runOrgGuardrails(ctx, rc, StagePost)
	if dynamic.Error == nil {
		return result
	}
	if result.Error == nil {
		return dynamic
	}
	if dynamic.Reflection == nil {
		result.Reflection = nil
	} else if result.Reflection != nil {
		result.Reflection.MaxAttempts = min(result.Reflection.MaxAttempts, dynamic.Reflection.MaxAttempts)
		result.Reflection.Reason += "\n" + dynamic.Reflection.Reason
		result.Reflection.Debug = result.Reflection.Debug || dynamic.Reflection.Debug
	}
	return result
}

// runOrgGuardrails runs guardrails defined in the org's tenant config.
// This enables managed mode where guardrail rules come from the control plane
// rather than from config.yaml.
func (p *GuardrailPlugin) runOrgGuardrails(ctx context.Context, rc *models.RequestContext, stage Stage) pipeline.PluginResult {
	if p.tenantStore == nil {
		return pipeline.ResultContinue()
	}

	orgID := rc.Metadata["org_id"]
	if orgID == "" {
		return pipeline.ResultContinue()
	}

	orgCfg := p.tenantStore.Get(orgID)
	if orgCfg == nil || orgCfg.Guardrails == nil || len(orgCfg.Guardrails.Checks) == 0 {
		return pipeline.ResultContinue()
	}

	keyPolicy, reqPolicy, err := p.resolvePolicy(rc)
	if err != nil {
		return pipeline.ResultError(err)
	}
	result := &PipelineResult{}

	names := make([]string, 0, len(orgCfg.Guardrails.Checks))
	for name := range orgCfg.Guardrails.Checks {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		check := orgCfg.Guardrails.Checks[name]
		if check == nil || !check.Enabled {
			continue
		}

		// Skip if already handled by static engine rules.
		if p.engine != nil && p.engine.HasRule(name) {
			continue
		}

		// Get or create the guardrail implementation.
		g := p.getGuardrail(orgID, name, check)
		if g == nil {
			slog.Debug("guardrail implementation not found",
				"name", name,
				"org_id", orgID,
			)
			continue
		}

		// Check stage matches.
		if configuredStage(check.Config, g.Stage()) != stage {
			continue
		}

		action, threshold, enabled := effectiveCheckPolicy(name, parseAction(check.Action), check.ConfidenceThreshold, keyPolicy, reqPolicy)
		if !enabled {
			continue
		}

		// Determine timeout.
		timeout := p.defaultTimeout
		if orgCfg.Guardrails.TimeoutMs > 0 {
			timeout = time.Duration(orgCfg.Guardrails.TimeoutMs) * time.Millisecond
		}

		// Build input.
		input := &CheckInput{
			Request:  rc.Request,
			Metadata: rc.Metadata,
		}
		if stage == StagePost {
			input.Response = rc.Response
			input = responseOnlyInput(g, input)
		}

		// Run with timeout and panic recovery.
		cr := p.runGuardrailSafe(ctx, g, input, timeout)
		if cr == nil {
			continue
		}

		// Apply threshold.
		triggered := shouldTrigger(cr, threshold)
		if !triggered {
			continue
		}

		tg := TriggeredGuardrail{
			Name:      name,
			Score:     cr.Score,
			Threshold: threshold,
			Action:    action,
			Message:   cr.Message,
		}
		if stage == StagePost && action == ActionBlock && !cr.OperationalError {
			tg.Reflection, _ = reflexion.Parse(check.Config)
		}
		result.Triggered = append(result.Triggered, tg)

		switch action {
		case ActionBlock:
			result.Blocked = true
			if stage == StagePre {
				return p.processResult(rc, result)
			}
		case ActionWarn:
			result.Warnings = append(result.Warnings, fmt.Sprintf("%s: %s", name, cr.Message))
		case ActionLog:
			message := cr.Message
			if stage == StagePost {
				message = "response check triggered"
			}
			slog.Info("guardrail triggered (log mode)",
				"guardrail", name,
				"org_id", orgID,
				"score", cr.Score,
				"threshold", threshold,
				"message", message,
			)
		}
	}

	if result.Blocked {
		return p.postBlock(rc, result)
	}

	// Apply metadata for non-blocking triggers.
	if len(result.Triggered) > 0 {
		p.applyMetadata(rc, result)
	}

	return pipeline.ResultContinue()
}

// getGuardrail resolves a guardrail implementation by name.
// Checks the static registry first, then the dynamic cache, and finally
// tries to create a new dynamic guardrail from the check config.
func (p *GuardrailPlugin) getGuardrail(orgID, name string, check *tenant.GuardrailCheck) Guardrail {
	preferDynamic := check != nil && len(check.Config) > 0 && p.dynamicFactory != nil

	if preferDynamic {
		if g := p.getCachedDynamicGuardrail(orgID, name); g != nil {
			return g
		}

		if g := p.createDynamicGuardrail(orgID, name, check.Config); g != nil {
			return g
		}
	}

	// 1. Try static registry (built-in guardrails like pii-detection, prompt-injection, etc.).
	if p.registry != nil {
		if g, ok := p.registry[name]; ok {
			return g
		}
	}

	// 2. Try dynamic cache (previously created external guardrails).
	if g := p.getCachedDynamicGuardrail(orgID, name); g != nil {
		return g
	}

	// 3. Create dynamic guardrail from check config (FI Eval, webhooks, etc.).
	if check.Config == nil || p.dynamicFactory == nil {
		return nil
	}

	g := p.createDynamicGuardrail(orgID, name, check.Config)
	if g == nil {
		return nil
	}

	return g
}

func (p *GuardrailPlugin) getCachedDynamicGuardrail(orgID, name string) Guardrail {
	p.dynamicMu.RLock()
	defer p.dynamicMu.RUnlock()

	if orgCache, ok := p.dynamicCache[orgID]; ok {
		if g, ok := orgCache[name]; ok {
			return g
		}
	}

	return nil
}

func (p *GuardrailPlugin) createDynamicGuardrail(orgID, name string, cfg map[string]interface{}) Guardrail {
	if cfg == nil || p.dynamicFactory == nil {
		return nil
	}

	g := p.dynamicFactory(name, cfg)
	if g == nil {
		return nil
	}

	// Cache it for future requests.
	p.dynamicMu.Lock()
	if _, ok := p.dynamicCache[orgID]; !ok {
		p.dynamicCache[orgID] = make(map[string]Guardrail)
	}
	p.dynamicCache[orgID][name] = g
	p.dynamicMu.Unlock()

	slog.Info("created dynamic guardrail from org config",
		"name", name,
		"org_id", orgID,
	)

	return g
}

// runGuardrailSafe runs a guardrail check with timeout and panic recovery.
func (p *GuardrailPlugin) runGuardrailSafe(ctx context.Context, g Guardrail, input *CheckInput, timeout time.Duration) *CheckResult {
	original := input
	owned, err := snapshotInput(input)
	if err != nil {
		return &CheckResult{Pass: false, Score: 1, Message: "invalid guardrail input", OperationalError: true}
	}
	input = owned
	checkCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	type result struct {
		cr  *CheckResult
		err error
	}
	ch := make(chan result, 1)

	go func() {
		defer func() {
			if r := recover(); r != nil {
				slog.Error("dynamic guardrail panicked",
					"guardrail", g.Name(),
					"panic", r,
				)
				ch <- result{err: fmt.Errorf("guardrail panicked: %v", r)}
			}
		}()
		cr := g.Check(checkCtx, input)
		ch <- result{cr: cr}
	}()

	select {
	case res := <-ch:
		if checkCtx.Err() != nil {
			res.err = checkCtx.Err()
		}
		if res.err != nil {
			slog.Error("dynamic guardrail error",
				"guardrail", g.Name(),
				"error", res.err,
				"fail_open", p.failOpen,
			)
			if p.failOpen {
				return nil
			}
			return &CheckResult{
				Pass:             false,
				Score:            1.0,
				Message:          fmt.Sprintf("guardrail %q error: %v", g.Name(), res.err),
				OperationalError: true,
			}
		}
		applyCheckMutations(original, owned)
		return res.cr
	case <-checkCtx.Done():
		slog.Warn("dynamic guardrail timed out",
			"guardrail", g.Name(),
			"timeout", timeout,
			"fail_open", p.failOpen,
		)
		if p.failOpen {
			return nil
		}
		return &CheckResult{
			Pass:             false,
			Score:            1.0,
			Message:          fmt.Sprintf("guardrail %q timed out", g.Name()),
			OperationalError: true,
		}
	}
}

// resolvePolicy looks up the per-org, per-key, and per-request policy overrides.
// Priority order (highest wins): per-request > per-key > per-org > global default.
func (p *GuardrailPlugin) resolvePolicy(rc *models.RequestContext) (*policy.Policy, policy.RequestPolicy, *models.APIError) {
	// 1. Look up per-org guardrail policy from tenant store.
	var orgPolicy *policy.Policy
	if orgID := rc.Metadata["org_id"]; orgID != "" && p.tenantStore != nil {
		if orgCfg := p.tenantStore.Get(orgID); orgCfg != nil {
			orgPolicy = orgCfg.GuardrailPolicy()
		}
	}

	// 2. Look up per-key policy.
	var keyPolicy *policy.Policy
	if keyID := rc.Metadata["auth_key_id"]; keyID != "" && p.policyStore != nil {
		keyPolicy = p.policyStore.Get(keyID)
	}

	// 3. Merge: org is base, key overrides take precedence.
	merged := mergePolicy(orgPolicy, keyPolicy)

	// 4. Check per-request override header.
	reqPolicyStr := rc.Metadata["x-guardrail-policy"]
	if reqPolicyStr == "" {
		return merged, policy.RequestPolicyNone, nil
	}

	// Validate that the key allows per-request overrides.
	if rc.Metadata["key_allow_policy_override"] != "true" {
		return nil, policy.RequestPolicyNone, models.ErrForbidden(
			"API key does not allow guardrail policy overrides. Set metadata.allow_policy_override: true",
		)
	}

	rp, valid := policy.ParseRequestPolicy(reqPolicyStr)
	if !valid {
		return nil, policy.RequestPolicyNone, models.ErrBadRequest(
			"invalid_policy",
			fmt.Sprintf("invalid X-Guardrail-Policy value: %q (valid: log-only, disabled, strict)", reqPolicyStr),
		)
	}

	return merged, rp, nil
}

// mergePolicy merges org-level and key-level policies.
// Key-level overrides take precedence over org-level.
// Returns nil if both are nil.
func mergePolicy(org, key *policy.Policy) *policy.Policy {
	if org == nil {
		return key
	}
	if key == nil {
		return org
	}

	// Both exist — merge with key taking precedence.
	merged := &policy.Policy{
		Overrides: make(map[string]policy.Override, len(org.Overrides)+len(key.Overrides)),
	}

	// Start with org overrides.
	for name, ov := range org.Overrides {
		merged.Overrides[name] = ov
	}

	// Key overrides replace org overrides.
	for name, ov := range key.Overrides {
		merged.Overrides[name] = ov
	}

	return merged
}

func (p *GuardrailPlugin) processResult(rc *models.RequestContext, result *PipelineResult) pipeline.PluginResult {
	if result.Blocked {
		rc.Flags.GuardrailTriggered = true
		if len(result.Triggered) > 0 {
			tg := result.Triggered[0]
			rc.Metadata["guardrail_name"] = tg.Name
			rc.Metadata["guardrail_action"] = "blocked"
		}
		// Store structured results even for blocks, so logs capture which guardrails fired.
		storeGuardrailResults(rc, result)
		return pipeline.ResultError(models.ErrGuardrailBlocked(
			"content_blocked",
			p.buildBlockMessage(result),
		))
	}

	p.applyMetadata(rc, result)
	return pipeline.ResultContinue()
}

func (p *GuardrailPlugin) applyMetadata(rc *models.RequestContext, result *PipelineResult) {
	if len(result.Triggered) > 0 {
		rc.Flags.GuardrailTriggered = true
		names := make([]string, len(result.Triggered))
		for i, tg := range result.Triggered {
			names[i] = tg.Name
		}
		rc.Metadata["guardrail_name"] = strings.Join(names, ",")

		if len(result.Warnings) > 0 {
			rc.Metadata["guardrail_action"] = "warned"
			rc.Metadata["guardrail_warnings"] = strings.Join(result.Warnings, "; ")
		} else {
			rc.Metadata["guardrail_action"] = "logged"
		}

		// Store structured guardrail results for downstream logging.
		storeGuardrailResults(rc, result)
	}
}

// storeGuardrailResults copies triggered guardrail details to the RequestContext
// so the logging plugin can include them in trace records.
func storeGuardrailResults(rc *models.RequestContext, result *PipelineResult) {
	for _, tg := range result.Triggered {
		rc.GuardrailResults = append(rc.GuardrailResults, models.GuardrailResult{
			Name:      tg.Name,
			Score:     tg.Score,
			Threshold: tg.Threshold,
			Action:    actionString(tg.Action),
			Message:   tg.Message,
		})
	}
}

func actionString(a Action) string {
	switch a {
	case ActionBlock:
		return "block"
	case ActionWarn:
		return "warn"
	case ActionLog:
		return "log"
	default:
		return "unknown"
	}
}

func (p *GuardrailPlugin) buildBlockMessage(result *PipelineResult) string {
	if len(result.Triggered) > 0 {
		tg := result.Triggered[0]
		return fmt.Sprintf("Request blocked by guardrail: %s — %s", tg.Name, tg.Message)
	}
	return "Request blocked by guardrail"
}

// InvalidateDynamicCache removes cached dynamic guardrails for an org.
// Call this when an org's guardrail config changes.
func (p *GuardrailPlugin) InvalidateDynamicCache(orgID string) {
	p.dynamicMu.Lock()
	delete(p.dynamicCache, orgID)
	p.dynamicMu.Unlock()
}
