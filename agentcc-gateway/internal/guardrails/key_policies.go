package guardrails

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"hash/fnv"
	"slices"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Precedence: org checks run first, followed by key policies in priority/name/ID
// order. Block > redact > warn > log; key policies cannot disable or weaken an
// org check. Monitor policies inspect snapshots only: they never block, redact,
// or change any response, including when a check fails closed. Streaming requests
// with selected-key Enforce Block post checks are refused during admission.
func (p *GuardrailPlugin) runKeyPolicies(ctx context.Context, rc *models.RequestContext, orgID string, cfg *tenant.GuardrailConfig, stage Stage) pipeline.PluginResult {
	_, keyID := selectedPrincipal(rc)
	if keyID == "" {
		return pipeline.ResultContinue()
	}
	resolved := p.keyPoliciesForInvocation(ctx, orgID, keyID, cfg)
	cfg = resolved.config
	rc.Inspection.SetPolicyRevision(inspectionStage(stage), selectedTextPolicyRevision(resolved, rc, stage))
	if rc.Inspection.IsDecided(inspectionStage(stage), inspectionPayload(rc, stage)) {
		return pipeline.ResultContinue()
	}
	wasComplete := rc.Inspection.IsComplete(inspectionStage(stage), inspectionPayload(rc, stage))
	ranChecks := false
	timeout := p.defaultTimeout
	if cfg.TimeoutMs > 0 {
		timeout = time.Duration(cfg.TimeoutMs) * time.Millisecond
	}
	for _, entry := range resolved.policies {
		pol := entry.policy
		monitor := strings.EqualFold(pol.Mode, "monitor")
		checks := make([]orgCheck, 0, len(entry.checks))
		for _, c := range entry.checks {
			if selectedCheckStage(rc, c.g) != stage {
				continue
			}
			if !monitor && parseAction(c.check.Action) == ActionBlock {
				rc.Inspection.Require(inspectionStage(stage))
			}
			if notApplicable, _, _ := inspectionCapability(rc, stage, true); notApplicable {
				continue
			}
			c = keyPolicyRouteCheck(rc, c)
			if unsupported, ok := c.g.(unsupportedRouteCheck); ok && !monitor && parseAction(c.check.Action) == ActionBlock {
				return p.refuseKeyPolicyRoute(rc, pol, c, unsupported)
			}
			if usesSelectedTextView(c.g) {
				c.g = selectedTextCheck{Guardrail: c.g}
			}
			checks = append(checks, c)
		}
		if len(checks) == 0 {
			continue
		}
		ranChecks = true
		input := selectedCheckInput(rc, stage == StagePost)
		var outcomes []*CheckResult
		blockIdx := -1
		sequential := strings.EqualFold(strings.TrimSpace(cfg.PipelineMode), "sequential")
		if monitor {
			outcomes = p.simulateKeyPolicy(ctx, checks, input, timeout, sequential)
		} else if sequential {
			outcomes, blockIdx = p.runOrgChecksSequential(ctx, checks, input, timeout, cfg.FailOpen)
		} else {
			outcomes, blockIdx = p.runOrgChecksParallel(ctx, checks, input, timeout, cfg.FailOpen)
		}
		result := &PipelineResult{}
		if blockIdx >= 0 {
			p.recordKeyOutcome(result, rc, pol, checks[blockIdx], outcomes[blockIdx], monitor)
		}
		for i, c := range checks {
			if i != blockIdx {
				p.recordKeyOutcome(result, rc, pol, c, outcomes[i], monitor)
			}
		}
		if monitor {
			if len(result.Triggered) > 0 {
				rc.Flags.GuardrailMonitored = true
				storeGuardrailResults(rc, result)
			}
			continue
		}
		if blockIdx >= 0 {
			result.Blocked = true
			storeGuardrailResults(rc, result)
			if len(result.Warnings) > 0 {
				rc.Metadata["guardrail_warnings"] = joinNonEmpty("; ", rc.Metadata["guardrail_warnings"], strings.Join(result.Warnings, "; "))
			}
			rc.Flags.GuardrailTriggered = true
			rc.Metadata["guardrail_name"] = checks[blockIdx].name
			rc.Metadata["guardrail_action"] = "blocked"
			if stage == StagePost && rc.Response != nil {
				rc.Response = &models.ChatCompletionResponse{Model: rc.Response.Model, Usage: rc.Response.Usage}
			}
			if outcomes[blockIdx].Err != nil {
				return pipeline.ResultError(models.ErrGuardrailUnavailable(p.buildBlockMessage(result)))
			}
			return pipeline.ResultError(models.ErrGuardrailBlocked("content_blocked", p.buildBlockMessage(result)))
		}
		if len(result.Triggered) > 0 {
			p.addKeyPolicyMetadata(rc, result)
		}
		refreshInspection(rc, stage, wasComplete)
	}
	if ranChecks && wasComplete {
		rc.Inspection.SetPolicyRevision(inspectionStage(stage), selectedTextPolicyRevision(resolved, rc, stage))
		rc.Inspection.Decide(inspectionStage(stage), inspectionPayload(rc, stage))
	}
	return pipeline.ResultContinue()
}

// selectedPolicyRevision keeps repeated-seam decisions tied to the resolved
// current policy, including Monitor/Enforce and fail-open transitions. It stores
// only a digest, never private checker config, in the request-local receipt.
func selectedPolicyRevision(resolved *resolvedKeyPolicies) string {
	policies := make([]*tenant.KeyGuardrailPolicy, 0, len(resolved.policies))
	for _, entry := range resolved.policies {
		policies = append(policies, entry.policy)
	}
	body, err := json.Marshal(struct {
		Policies     []*tenant.KeyGuardrailPolicy
		FailOpen     bool
		PipelineMode string
		TimeoutMs    int
	}{policies, resolved.config.FailOpen, resolved.config.PipelineMode, resolved.config.TimeoutMs})
	if err != nil {
		return "" // unencodable policy cannot reuse a previous decision
	}
	digest := sha256.Sum256(body)
	return string(digest[:])
}

// addKeyPolicyMetadata records an enforce policy's non-blocking triggers on
// top of what the org checks and earlier policies recorded. applyMetadata
// alone replaces their check names, action and warnings, and the audit record
// copies guardrail_action, so a policy that only logged would hide an org
// check that warned.
func (p *GuardrailPlugin) addKeyPolicyMetadata(rc *models.RequestContext, result *PipelineResult) {
	prevNames := rc.Metadata["guardrail_name"]
	prevAction := rc.Metadata["guardrail_action"]
	prevWarnings := rc.Metadata["guardrail_warnings"]
	p.applyMetadata(rc, result)
	if prevNames != "" {
		names := strings.Split(prevNames, ",")
		for _, name := range strings.Split(rc.Metadata["guardrail_name"], ",") {
			if !slices.Contains(names, name) {
				names = append(names, name)
			}
		}
		rc.Metadata["guardrail_name"] = strings.Join(names, ",")
	}
	if metadataActionRank(prevAction) > metadataActionRank(rc.Metadata["guardrail_action"]) {
		rc.Metadata["guardrail_action"] = prevAction
	}
	if w := joinNonEmpty("; ", prevWarnings, strings.Join(result.Warnings, "; ")); w != "" {
		rc.Metadata["guardrail_warnings"] = w
	}
}

func metadataActionRank(action string) int {
	switch action {
	case "blocked":
		return 3
	case "warned":
		return 2
	case "logged":
		return 1
	}
	return 0
}

func joinNonEmpty(sep string, parts ...string) string {
	kept := parts[:0:0]
	for _, part := range parts {
		if part != "" {
			kept = append(kept, part)
		}
	}
	return strings.Join(kept, sep)
}

// sortedKeyPolicies orders policies as they run: priority, then name, then ID.
func sortedKeyPolicies(policies []*tenant.KeyGuardrailPolicy) []*tenant.KeyGuardrailPolicy {
	sort.Slice(policies, func(i, j int) bool {
		a, b := policies[i], policies[j]
		if a.Priority != b.Priority {
			return a.Priority < b.Priority
		}
		if a.Name != b.Name {
			return a.Name < b.Name
		}
		return a.ID < b.ID
	})
	return policies
}

func sortedCheckNames(checks map[string]*tenant.GuardrailCheck) []string {
	names := make([]string, 0, len(checks))
	for name := range checks {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// keyPolicyCacheKey names a key-policy check's instance in the dynamic cache.
// It includes a hash of the check's config, so an instance built from a config
// a request read just before a change can never be served for the new config:
// the cache is cleared on a config change, but a request that started earlier
// can store its instance again right after.
func keyPolicyCacheKey(name, policyID string, check *tenant.GuardrailCheck) string {
	b, err := json.Marshal(check.Config)
	if err != nil {
		// Unencodable config: never cache under a shared name.
		return fmt.Sprintf("%s@%s#%p", name, policyID, check)
	}
	h := fnv.New64a()
	h.Write(b)
	return fmt.Sprintf("%s@%s#%x", name, policyID, h.Sum64())
}

// simulateKeyPolicy runs a monitor policy's checks in the order enforce would
// (remediators first in parallel mode, name order in sequential mode) against
// one private snapshot, so a remediation is visible to the checks after it,
// exactly as when enforced. Nothing is written back to the request, nothing
// stops early, and a failed check is admitted (recorded, never a 503).
func (p *GuardrailPlugin) simulateKeyPolicy(ctx context.Context, checks []orgCheck, input *CheckInput, timeout time.Duration, sequential bool) []*CheckResult {
	outcomes := make([]*CheckResult, len(checks))
	owned, err := cloneCheckInput(input)
	if err != nil {
		for i := range checks {
			outcomes[i] = &CheckResult{Err: err, FailureAdmitted: true, Message: "guardrail_unavailable: could not snapshot guardrail input"}
		}
		return outcomes
	}
	if sequential {
		for i, c := range checks {
			outcomes[i] = p.runOrgCheck(ctx, c.g, owned, timeout, true)
		}
		return outcomes
	}
	for i, c := range checks {
		if mutatesInput(c.g) {
			outcomes[i] = p.runOrgCheck(ctx, c.g, owned, timeout, true)
		}
	}
	// Read-only checks run concurrently, each on its own copy of the
	// remediated snapshot (runOrgCheck clones before running).
	var wg sync.WaitGroup
	for i, c := range checks {
		if mutatesInput(c.g) {
			continue
		}
		wg.Add(1)
		go func(i int, c orgCheck) {
			defer wg.Done()
			outcomes[i] = p.runOrgCheck(ctx, c.g, owned, timeout, true)
		}(i, c)
	}
	wg.Wait()
	return outcomes
}

const passThroughNote = " (client's body was forwarded unchanged on this pass-through route)"

// getKeyPolicyGuardrail resolves a key-policy check from that policy's own
// config, even an empty one, so it never runs the gateway's static instance,
// which config.yaml may have configured for every request. It falls back to
// the static registry only for checks the factory can't build.
func (p *GuardrailPlugin) getKeyPolicyGuardrail(orgID, name, cacheKey string, check *tenant.GuardrailCheck) (resolved Guardrail) {
	defer func() { resolved = selectedThresholdReady(resolved, check) }()
	// Realtime adapters also call this resolver directly, without the HTTP
	// resolveKeyPolicies envelope. Invalid transport must not become defaults.
	if check != nil && check.ConfigurationError != "" {
		return unavailableKeyPolicyCheck{name: name}
	}
	if p.dynamicFactory != nil {
		if g := p.getCachedDynamicGuardrail(orgID, cacheKey); g != nil {
			return validateSelectedKeyPolicyCheck(name, g, check)
		}
		cfg := check.Config
		if cfg == nil {
			cfg = map[string]interface{}{}
		}
		if g := p.createDynamicGuardrailScoped(orgID, name, cacheKey, cfg); g != nil {
			return validateSelectedKeyPolicyCheck(name, g, check)
		}
	}
	// The factory was already attempted with the policy's own config. Do not
	// retry it through getGuardrailScoped: a failed resolution is one unavailable
	// check, while a legitimately configured static/custom implementation remains
	// a valid fallback.
	return validateSelectedKeyPolicyCheck(name, p.registry[name], check)
}

func guardrailOutcome(rc *models.RequestContext, cr *CheckResult, action Action) (string, string) {
	message := cr.Message
	if action == ActionBlock {
		return message, "blocked"
	}
	if cr.InputRewritten {
		if rc.Request != nil && !rc.GuardrailInspectionOnly {
			return message, "redacted"
		}
		return message + passThroughNote, "logged"
	}
	if action == ActionWarn {
		return message, "warned"
	}
	return message, "logged"
}

func (p *GuardrailPlugin) recordKeyOutcome(result *PipelineResult, rc *models.RequestContext, pol *tenant.KeyGuardrailPolicy, c orgCheck, cr *CheckResult, monitor bool) {
	threshold := c.check.ConfidenceThreshold
	if !shouldTrigger(cr, threshold) {
		return
	}
	action := parseAction(c.check.Action)
	if cr.FailureAdmitted {
		action = ActionLog
	}
	message, outcome := guardrailOutcome(rc, cr, action)
	wouldAction := ""
	if monitor {
		wouldAction = actionString(parseAction(c.check.Action))
		action = ActionLog
		switch {
		case cr.Err != nil:
			outcome = "logged"
		case wouldAction == "block":
			outcome = "would_block"
		case mutatesInput(c.g) && cr.InputRewritten && (rc.Request == nil || rc.GuardrailInspectionOnly):
			// Even enforced, the gateway forwards the client's own body here.
			outcome = "logged"
		case mutatesInput(c.g) && cr.InputRewritten:
			outcome = "would_redact"
		case wouldAction == "warn":
			outcome = "would_warn"
		default:
			outcome = "logged"
		}
		// A monitor check runs on a snapshot, so no client body was changed.
		message = cr.Message
		if outcome == "logged" && mutatesInput(c.g) && cr.InputRewritten && (rc.Request == nil || rc.GuardrailInspectionOnly) {
			message += passThroughNote
		}
	}
	result.Triggered = append(result.Triggered, TriggeredGuardrail{
		Name: c.name, Score: cr.Score, Threshold: threshold, Action: action, Message: message,
		PolicyID: pol.ID, PolicyName: pol.Name, PolicyScope: "key", Mode: map[bool]string{true: "monitor", false: "enforce"}[monitor],
		Outcome: outcome, WouldAction: wouldAction,
	})
	if !monitor && action == ActionWarn {
		result.Warnings = append(result.Warnings, fmt.Sprintf("%s: %s", c.name, cr.Message))
	}
}
