package guardrails

import (
	"context"
	"fmt"
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
	defaultTimeout time.Duration

	// Cache for dynamically created guardrails (e.g., FI Eval with per-org API keys).
	dynamicMu    sync.RWMutex
	dynamicCache map[string]map[string]Guardrail // org_id -> check_name -> guardrail
}

// NewPlugin creates a new guardrail pipeline plugin.
func NewPlugin(engine *Engine, registry map[string]Guardrail, dynamicFactory DynamicFactory, policyStore *policy.Store, tenantStore *tenant.Store) *GuardrailPlugin {
	timeout := 30 * time.Second
	if engine != nil {
		timeout = engine.DefaultTimeout()
	}
	return &GuardrailPlugin{
		engine:         engine,
		registry:       registry,
		dynamicFactory: dynamicFactory,
		policyStore:    policyStore,
		tenantStore:    tenantStore,
		defaultTimeout: timeout,
		dynamicCache:   make(map[string]map[string]Guardrail),
	}
}

func (p *GuardrailPlugin) Name() string  { return "guardrails" }
func (p *GuardrailPlugin) Priority() int { return 50 } // After auth (20), RBAC (30), budget (40). Before cache (200).

// HasOrgInputChecks reports whether the org's config holds an enabled check
// that runs on input (a pre-stage guardrail the static rules do not already
// cover). A post-stage-only check, such as hallucination, does not count.
func (p *GuardrailPlugin) HasOrgInputChecks(orgID string) bool {
	if p == nil || p.tenantStore == nil || orgID == "" {
		return false
	}
	orgCfg := p.tenantStore.Get(orgID)
	if orgCfg == nil || orgCfg.Guardrails == nil || len(orgCfg.Guardrails.Checks) == 0 {
		return false
	}
	return len(p.collectOrgChecks(orgID, orgCfg.Guardrails, StagePre)) > 0
}

// ProcessRequest runs pre-stage guardrails.
func (p *GuardrailPlugin) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	// Speech has no chat body at admission. The relay binds this plugin after auth
	// and checks complete protocol text units before forwarding them instead.
	if rc.EndpointType == "speech_relay" || rc.EndpointType == "transcription_relay" {
		return pipeline.ResultContinue()
	}
	// 1. Run static engine rules (self-hosted / config.yaml rules).
	if p.engine != nil && p.engine.PreCount() > 0 {
		keyPolicy, reqPolicy, err := p.resolvePolicy(rc)
		if err != nil {
			return pipeline.ResultError(err)
		}

		input := checkInput(rc, false)

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
func (p *GuardrailPlugin) ProcessResponse(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if rc.EndpointType == "speech_relay" || rc.EndpointType == "transcription_relay" {
		return pipeline.ResultContinue()
	}
	// 1. Run static engine rules.
	if p.engine != nil && p.engine.PostCount() > 0 {
		keyPolicy, reqPolicy, err := p.resolvePolicy(rc)
		if err != nil {
			return pipeline.ResultError(err)
		}

		input := checkInput(rc, true)

		result := p.engine.RunPost(ctx, input, keyPolicy, reqPolicy)

		if result.Blocked {
			// The content goes; the usage stays. The upstream billed the
			// request, so cost, budgets and credits must still count it.
			if rc.Response != nil {
				rc.Response = &models.ChatCompletionResponse{Model: rc.Response.Model, Usage: rc.Response.Usage}
			}
			rc.Flags.GuardrailTriggered = true
			if len(result.Triggered) > 0 {
				tg := result.Triggered[0]
				rc.Metadata["guardrail_name"] = tg.Name
				rc.Metadata["guardrail_action"] = "blocked"
			}
			storeGuardrailResults(rc, result)
			if result.Unavailable {
				return pipeline.ResultError(models.ErrGuardrailUnavailable(p.buildBlockMessage(result)))
			}
			return pipeline.ResultError(models.ErrGuardrailBlocked(
				"content_blocked",
				p.buildBlockMessage(result),
			))
		}

		p.applyMetadata(rc, result)
	}

	// 2. Run dynamic guardrails from org config (managed mode).
	return p.runOrgGuardrails(ctx, rc, StagePost)
}

// orgCheck is one enabled org check resolved to its implementation.
type orgCheck struct {
	name  string
	check *tenant.GuardrailCheck
	g     Guardrail
}

// runOrgGuardrails runs guardrails defined in the org's tenant config.
// This enables managed mode where guardrail rules come from the control plane
// rather than from config.yaml.
//
// The org's pipeline settings are honoured:
//   - fail_open decides what happens when a check errors or times out
//     (only explicit tenant opt-in enables fail-open; omission is false).
//   - pipeline_mode "sequential" runs checks one after another and stops at
//     the first block; anything else (the dashboard default, "parallel")
//     runs them concurrently and stops waiting as soon as one blocks.
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
	gcfg := orgCfg.Guardrails

	checks := p.collectOrgChecks(orgID, gcfg, stage)
	if len(checks) == 0 {
		return pipeline.ResultContinue()
	}

	timeout := p.defaultTimeout
	if gcfg.TimeoutMs > 0 {
		timeout = time.Duration(gcfg.TimeoutMs) * time.Millisecond
	}
	failOpen := gcfg.FailOpen

	input := checkInput(rc, stage == StagePost)

	var outcomes []*CheckResult
	var blockIdx int
	if strings.EqualFold(strings.TrimSpace(gcfg.PipelineMode), "sequential") {
		outcomes, blockIdx = p.runOrgChecksSequential(ctx, checks, input, timeout, failOpen)
	} else {
		outcomes, blockIdx = p.runOrgChecksParallel(ctx, checks, input, timeout, failOpen)
	}

	result := &PipelineResult{}

	// The blocking check goes first so block metadata and the error message
	// name the check that actually blocked.
	if blockIdx >= 0 {
		p.recordOrgOutcome(result, orgID, checks[blockIdx], outcomes[blockIdx])
	}
	for i, c := range checks {
		if i == blockIdx {
			continue
		}
		p.recordOrgOutcome(result, orgID, c, outcomes[i])
	}

	if blockIdx >= 0 {
		blocker := checks[blockIdx].name
		result.Blocked = true
		storeGuardrailResults(rc, result)
		if len(result.Warnings) > 0 {
			rc.Metadata["guardrail_warnings"] = strings.Join(result.Warnings, "; ")
		}
		rc.Flags.GuardrailTriggered = true
		rc.Metadata["guardrail_name"] = blocker
		rc.Metadata["guardrail_action"] = "blocked"
		if stage == StagePost && rc.Response != nil {
			rc.Response = &models.ChatCompletionResponse{Model: rc.Response.Model, Usage: rc.Response.Usage}
		}
		if outcomes[blockIdx].Err != nil {
			return pipeline.ResultError(models.ErrGuardrailUnavailable(p.buildBlockMessage(result)))
		}
		return pipeline.ResultError(models.ErrGuardrailBlocked(
			"content_blocked",
			p.buildBlockMessage(result),
		))
	}

	// Apply metadata for non-blocking triggers.
	if len(result.Triggered) > 0 {
		p.applyMetadata(rc, result)
	}

	return pipeline.ResultContinue()
}

// collectOrgChecks returns the org's enabled checks for this stage, sorted by
// name so execution order (sequential) and result order are deterministic.
func (p *GuardrailPlugin) collectOrgChecks(orgID string, gcfg *tenant.GuardrailConfig, stage Stage) []orgCheck {
	names := make([]string, 0, len(gcfg.Checks))
	for name := range gcfg.Checks {
		names = append(names, name)
	}
	sort.Strings(names)

	checks := make([]orgCheck, 0, len(names))
	for _, name := range names {
		check := gcfg.Checks[name]
		if check == nil || !check.Enabled {
			continue
		}

		// Skip if already handled by static engine rules.
		if p.engine != nil && p.engine.HasRule(name) {
			continue
		}

		g := p.getGuardrail(orgID, name, check)
		if g == nil {
			slog.Debug("guardrail implementation not found",
				"name", name,
				"org_id", orgID,
			)
			continue
		}

		if g.Stage() != stage {
			continue
		}

		checks = append(checks, orgCheck{name: name, check: check, g: g})
	}
	return checks
}

// orgCheckBlocks reports whether a check outcome blocks the request.
func orgCheckBlocks(c orgCheck, cr *CheckResult) bool {
	return cr != nil && !cr.FailureAdmitted && shouldTrigger(cr, c.check.ConfidenceThreshold) && parseAction(c.check.Action) == ActionBlock
}

// runOrgChecksSequential runs checks one at a time, stopping at the first
// block. It returns per-check outcomes (nil = not run or passed) and the
// index of the blocking check, or -1.
func (p *GuardrailPlugin) runOrgChecksSequential(ctx context.Context, checks []orgCheck, input *CheckInput, timeout time.Duration, failOpen bool) ([]*CheckResult, int) {
	outcomes := make([]*CheckResult, len(checks))
	for i, c := range checks {
		outcomes[i] = p.runOrgCheck(ctx, c.g, input, timeout, failOpen)
		if orgCheckBlocks(c, outcomes[i]) {
			return outcomes, i
		}
	}
	return outcomes, -1
}

// runOrgChecksParallel runs remediators first in deterministic order, then
// read-only checks concurrently against independent snapshots of the remediated
// content. As soon as one blocks, pending checks are cancelled, but already
// collected outcomes are retained.
func (p *GuardrailPlugin) runOrgChecksParallel(ctx context.Context, checks []orgCheck, input *CheckInput, timeout time.Duration, failOpen bool) ([]*CheckResult, int) {
	if len(checks) == 1 {
		return p.runOrgChecksSequential(ctx, checks, input, timeout, failOpen)
	}

	runCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	type outcome struct {
		idx int
		cr  *CheckResult
	}
	// Buffered so goroutines never block after we stop reading.
	ch := make(chan outcome, len(checks))
	outcomes := make([]*CheckResult, len(checks))
	for i, c := range checks {
		if mutatesInput(c.g) {
			outcomes[i] = p.runOrgCheck(runCtx, c.g, input, timeout, failOpen)
			if orgCheckBlocks(c, outcomes[i]) {
				return outcomes, i
			}
		}
	}
	pending := 0
	for i, c := range checks {
		if mutatesInput(c.g) {
			continue
		}
		pending++
		owned, err := cloneCheckInput(input)
		if err != nil {
			ch <- outcome{idx: i, cr: &CheckResult{Err: err, FailureAdmitted: failOpen, Message: "guardrail_unavailable: could not snapshot guardrail input"}}
			continue
		}
		go func(i int, c orgCheck, owned *CheckInput) {
			ch <- outcome{idx: i, cr: p.runGuardrailSafe(runCtx, c.g, owned, timeout, failOpen)}
		}(i, c, owned)
	}

	for range pending {
		o := <-ch
		outcomes[o.idx] = o.cr
		if orgCheckBlocks(checks[o.idx], o.cr) {
			// Remaining checks are cancelled; anything they report now would
			// be an artefact of the cancellation, so drop it.
			cancel()
			return outcomes, o.idx
		}
	}
	return outcomes, -1
}

// recordOrgOutcome applies a check's threshold and action to the aggregate
// result. Block decisions are made by the caller.
func (p *GuardrailPlugin) recordOrgOutcome(result *PipelineResult, orgID string, c orgCheck, cr *CheckResult) {
	threshold := c.check.ConfidenceThreshold
	if !shouldTrigger(cr, threshold) {
		return
	}

	action := parseAction(c.check.Action)
	if cr.FailureAdmitted {
		action = ActionLog
	}
	result.Triggered = append(result.Triggered, TriggeredGuardrail{
		Name:      c.name,
		Score:     cr.Score,
		Threshold: threshold,
		Action:    action,
		Message:   cr.Message,
	})

	switch action {
	case ActionWarn:
		result.Warnings = append(result.Warnings, fmt.Sprintf("%s: %s", c.name, cr.Message))
	case ActionLog:
		slog.Info("guardrail triggered (log mode)",
			"guardrail", c.name,
			"org_id", orgID,
			"score", cr.Score,
			"threshold", threshold,
			"message", cr.Message,
		)
	}
}

// checkInput is what a guardrail checks. On a route that forwards the client's
// own body, Request is nil and Response holds usage only; the handler puts the
// text in GuardrailRequest and GuardrailResponse instead.
//
// A guardrail that rewrites content, as PII redaction does, rewrites Request,
// which is what the gateway sends upstream. On those routes it rewrites a
// copy, so it finds and reports what it would remove but cannot remove it.
func checkInput(rc *models.RequestContext, withResponse bool) *CheckInput {
	input := &CheckInput{Request: rc.Request, Metadata: rc.Metadata}
	if input.Request == nil {
		input.Request = rc.GuardrailRequest
	}
	if withResponse {
		input.Response = rc.Response
		if rc.GuardrailResponse != nil {
			input.Response = rc.GuardrailResponse
		}
	}
	return input
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
func (p *GuardrailPlugin) runGuardrailSafe(ctx context.Context, g Guardrail, input *CheckInput, timeout time.Duration, failOpen bool) *CheckResult {
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

	var res result
	select {
	case res = <-ch:
	case <-checkCtx.Done():
	}
	// A context-aware check can return a cancellation result at the same
	// instant the deadline fires. Apply the same org policy in either case.
	if checkCtx.Err() != nil {
		slog.Warn("dynamic guardrail timed out",
			"guardrail", g.Name(),
			"timeout", timeout,
			"fail_open", failOpen,
		)
		return &CheckResult{
			FailureAdmitted: failOpen,
			Pass:            false,
			Score:           1.0,
			Message:         fmt.Sprintf("guardrail_unavailable: %q", g.Name()),
			Err:             checkCtx.Err(),
		}
	}
	if res.err == nil {
		res.err = checkResultError(res.cr)
	}
	if res.err != nil {
		slog.Error("dynamic guardrail error",
			"guardrail", g.Name(),
			"error", res.err,
			"fail_open", failOpen,
		)
		return &CheckResult{
			FailureAdmitted: failOpen,
			Pass:            false,
			Score:           1.0,
			Message:         fmt.Sprintf("guardrail_unavailable: %q", g.Name()),
			Err:             res.err,
		}
	}
	return res.cr
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
		if result.Unavailable {
			return pipeline.ResultError(models.ErrGuardrailUnavailable(p.buildBlockMessage(result)))
		}
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
