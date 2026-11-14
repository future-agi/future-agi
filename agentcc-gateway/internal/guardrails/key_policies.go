package guardrails

import (
	"context"
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
// or change any response, including when a check fails closed. Post-stage checks
// on streamed output can report a block but cannot retract already relayed text.
func (p *GuardrailPlugin) runKeyPolicies(ctx context.Context, rc *models.RequestContext, orgID string, cfg *tenant.GuardrailConfig, stage Stage) pipeline.PluginResult {
	keyID := rc.Metadata["auth_key_id"]
	if keyID == "" {
		return pipeline.ResultContinue()
	}
	policies := sortedKeyPolicies(cfg.KeyPoliciesFor(keyID))
	timeout := p.defaultTimeout
	if cfg.TimeoutMs > 0 {
		timeout = time.Duration(cfg.TimeoutMs) * time.Millisecond
	}
	for _, pol := range policies {
		names := sortedCheckNames(pol.Checks)
		checks := make([]orgCheck, 0, len(names))
		for _, name := range names {
			check := pol.Checks[name]
			if check == nil || !check.Enabled {
				continue
			}
			// Unlike org checks, these still run when a static rule uses the same name.
			g := p.getKeyPolicyGuardrail(orgID, name, keyPolicyCacheKey(name, pol.ID, check), check)
			if g != nil && g.Stage() == stage {
				checks = append(checks, orgCheck{name: name, check: check, g: g})
			}
		}
		if len(checks) == 0 {
			continue
		}
		input := checkInput(rc, stage == StagePost)
		monitor := strings.EqualFold(pol.Mode, "monitor")
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
	}
	return pipeline.ResultContinue()
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
func (p *GuardrailPlugin) getKeyPolicyGuardrail(orgID, name, cacheKey string, check *tenant.GuardrailCheck) Guardrail {
	if p.dynamicFactory != nil {
		if g := p.getCachedDynamicGuardrail(orgID, cacheKey); g != nil {
			return g
		}
		cfg := check.Config
		if cfg == nil {
			cfg = map[string]interface{}{}
		}
		if g := p.createDynamicGuardrailScoped(orgID, name, cacheKey, cfg); g != nil {
			return g
		}
	}
	return p.getGuardrailScoped(orgID, name, cacheKey, check)
}

func guardrailOutcome(rc *models.RequestContext, cr *CheckResult, action Action) (string, string) {
	message := cr.Message
	if action == ActionBlock {
		return message, "blocked"
	}
	if cr.InputRewritten {
		if rc.Request != nil {
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
		case mutatesInput(c.g) && cr.InputRewritten && rc.Request == nil:
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
		if outcome == "logged" && mutatesInput(c.g) && cr.InputRewritten && rc.Request == nil {
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
