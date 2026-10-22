package guardrails

import (
	"context"
	"fmt"
	"log/slog"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/credentials"
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

func (p *GuardrailPlugin) Name() string { return "guardrails" }

// Priority 34: after auth (20) and RBAC (30), whose key identity and
// permissions the checks read, and the request quota (33), which refuses a
// paused or over-limit org without reading its requests; before the
// response cache (35), so every request is checked by the policy that
// applies to it before any answer the cache holds is served to it. The
// checks run before the budget (40) too, which runs after the cache so that
// a hit is served whatever the budget; a rewrite (pii-detection's mask) is
// in the request the cache keys its entry by.
func (p *GuardrailPlugin) Priority() int { return 34 }

// ProcessRequest runs pre-stage guardrails.
func (p *GuardrailPlugin) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	var keyPolicy *policy.Policy
	reqPolicy := policy.RequestPolicyNone

	if p.engine != nil && (p.engine.PreCount() > 0 || p.engine.PostCount() > 0) {
		// X-Guardrail-Policy is checked here, before the provider call, even
		// when every static rule is on the output, so a value the key may not
		// set, or one that is not a policy, is refused before the provider is called.
		kp, rp, err := p.resolvePolicy(rc)
		if err != nil {
			return pipeline.ResultError(err)
		}
		keyPolicy, reqPolicy = kp, rp
	}

	// 0. Fail closed: a request the gateway cannot read, all or part of it
	// (a body passed through to the provider's own API that does not have
	// its API's shape, or that holds a value nested deeper than
	// requestview.MaxDepth, embeddings given as token ids, a transcription's
	// audio, an OCR document, a tool call's arguments nested deeper than
	// MaxArgumentsDepth), is refused when a check on the request would
	// block it, since that check could never read it, or would rewrite it
	// (a remediation, in any mode), since its rewrite could never be
	// applied.
	// The checks share what was read in the request's tool calls'
	// arguments (ctx's argumentsCache): each is read once.
	ctx, reason := p.unreadableRequest(ctx, rc)
	if reason != "" {
		if blocking := p.blockingRequestChecks(rc, keyPolicy, reqPolicy); len(blocking) > 0 {
			return pipeline.ResultError(p.refuseUnreadableRequest(rc, blocking[0], "checks the request", reason))
		}
		if rewriting := p.rewritingRequestChecks(rc, keyPolicy, reqPolicy); len(rewriting) > 0 {
			return pipeline.ResultError(p.refuseUnreadableRequest(rc, rewriting[0], "rewrites the request", reason))
		}
	}

	// 1. Run static engine rules (self-hosted / config.yaml rules).
	if p.engine.PreCount() > 0 {
		input := &CheckInput{
			Request:  requestForChecks(ctx, rc),
			Metadata: checkMetadata(rc),
		}

		result := p.engine.RunPre(ctx, input, keyPolicy, reqPolicy)
		pluginResult := p.processResult(rc, result)
		if pluginResult.Action != pipeline.Continue {
			return pluginResult
		}
	}

	// 2. Run dynamic guardrails from org config (managed mode).
	if result := p.runOrgGuardrails(ctx, rc, StagePre); result.Action != pipeline.Continue {
		return result
	}

	// 2b. A check that rewrote the request (a remediation) rewrote the chat
	// shape of a body passed through to the provider's own API (or, on
	// native /v1/completions, the chat request built from the prompt the
	// provider gets): the rewrite goes into what the handler sends, as it
	// reaches the provider on /v1/chat/completions, or the request is
	// refused when it cannot.
	if rc.PassThrough != nil {
		if err := rc.PassThrough.ApplyChecks(); err != nil {
			return pipeline.ResultError(p.refuseUnwrittenRewrite(rc, err))
		}
	}

	// 3. Say which output checks will read the answer, so the handler gives
	// them the answer the client gets.
	rc.Flags.AnswerReaders |= p.answerReaders(rc, reqPolicy)

	// 4. Fail closed: a request whose answer the gateway will not get (a
	// background response) is refused when a check on the answer would block
	// it, since that check could never run.
	if rc.Flags.AnswerUnavailable {
		if blocking := p.blockingAnswerChecks(rc, keyPolicy, reqPolicy); len(blocking) > 0 {
			return pipeline.ResultError(p.refuseUncheckedAnswer(rc, blocking[0]))
		}
	}
	return pipeline.ResultContinue()
}

// blockingAnswerChecks names the checks on the answer that would refuse it:
// the config file's post-stage rules whose action under the key's and the
// request's policy is block, then the organization's checks that read the
// answer in block mode.
func (p *GuardrailPlugin) blockingAnswerChecks(rc *models.RequestContext, keyPolicy *policy.Policy, reqPolicy policy.RequestPolicy) []string {
	return append(p.engine.BlockingPost(keyPolicy, reqPolicy), p.blockingOrgChecks(rc, StagePost)...)
}

// blockingRequestChecks names the checks on the request that would refuse
// it, as blockingAnswerChecks does for the answer: the config file's
// pre-stage rules, then the organization's checks that read the request.
func (p *GuardrailPlugin) blockingRequestChecks(rc *models.RequestContext, keyPolicy *policy.Policy, reqPolicy policy.RequestPolicy) []string {
	return append(p.engine.BlockingPre(keyPolicy, reqPolicy), p.blockingOrgChecks(rc, StagePre)...)
}

// rewritingRequestChecks names the checks on the request that would rewrite
// it (RequestRewriter), whatever their action: the config file's pre-stage
// rules that run under the key's and the request's policy, then,
// sorted, the organization's enabled checks that read the request.
func (p *GuardrailPlugin) rewritingRequestChecks(rc *models.RequestContext, keyPolicy *policy.Policy, reqPolicy policy.RequestPolicy) []string {
	var org []string
	orgID := rc.Metadata["org_id"]
	p.eachOrgCheck(rc, StagePre, func(name string, check *tenant.GuardrailCheck) bool {
		if rewritesRequest(p.orgCheckGuardrail(orgID, name, check)) {
			org = append(org, name)
		}
		return true
	})
	sort.Strings(org)
	return append(p.engine.RewritingPre(keyPolicy, reqPolicy), org...)
}

// blockingOrgChecks names, sorted, the organization's enabled checks that
// run at stage in block mode.
func (p *GuardrailPlugin) blockingOrgChecks(rc *models.RequestContext, stage Stage) []string {
	var org []string
	p.eachOrgCheck(rc, stage, func(name string, check *tenant.GuardrailCheck) bool {
		if parseAction(check.Action) == ActionBlock {
			org = append(org, name)
		}
		return true
	})
	sort.Strings(org)
	return org
}

// refuseUncheckedAnswer is the refusal of a request whose answer no output
// check can read (rc.Flags.AnswerUnavailable) while check, which would block
// it, applies: 403 answer_unavailable naming the check and the reason, and the
// request's log records the block.
func (p *GuardrailPlugin) refuseUncheckedAnswer(rc *models.RequestContext, check string) *models.APIError {
	reason := rc.Metadata["answer_unavailable"]
	if reason == "" {
		reason = "the gateway cannot read this request's answer"
	}
	return refuseUnchecked(rc, check, "answer_unavailable", "it checks the model's answer, and "+reason)
}

// refuseUnreadableRequest is the refusal of a request whose body, passed
// through to the provider's own API, the gateway cannot read (reason says
// why) while check applies, which does what does says: it would block the
// request ("checks the request") or rewrite it ("rewrites the request").
// 403 request_unavailable, before the provider is called.
func (p *GuardrailPlugin) refuseUnreadableRequest(rc *models.RequestContext, check, does, reason string) *models.APIError {
	return refuseUnchecked(rc, check, "request_unavailable", "it "+does+", and "+reason)
}

// refuseUnchecked is a guardrail refusal (403 code) by check, which would
// block but cannot read what it checks: message says why. The request's log
// records the block.
func refuseUnchecked(rc *models.RequestContext, check, code, message string) *models.APIError {
	rc.Flags.GuardrailTriggered = true
	rc.Metadata["guardrail_name"] = check
	rc.Metadata["guardrail_action"] = "blocked"
	rc.GuardrailResults = append(rc.GuardrailResults, models.GuardrailResult{
		Name: check, Score: 1, Action: actionString(ActionBlock), Message: message,
	})
	return models.ErrGuardrailBlocked(code, fmt.Sprintf("Request blocked by guardrail: %s — %s", check, message))
}

// refuseUnwrittenRewrite is the refusal of a request passed through to the
// provider's own API whose rewrite by a check (pii-detection's mask, redact
// or hash) cannot be written into the body the handler sends (err says
// why): 403 remediation_unavailable, before the provider is called, rather
// than send the request without the rewrite. It names the check that
// rewrites requests.
func (p *GuardrailPlugin) refuseUnwrittenRewrite(rc *models.RequestContext, err error) *models.APIError {
	return refuseUnchecked(rc, p.rewritingCheck(rc), "remediation_unavailable",
		"it rewrites the request, and the gateway cannot write the rewrite into the request it passes through to the provider: "+err.Error())
}

// refuseUnappliedRewrite is the refusal of a request whose check that
// rewrites it (pii-detection's mask, redact or hash) did not finish (reason
// says why: it timed out or failed), so its rewrite was never applied: 403
// remediation_unavailable, before the provider is called, rather than send
// the request without the rewrite, whatever fail_open says (isolate).
func refuseUnappliedRewrite(rc *models.RequestContext, check, reason string) *models.APIError {
	return refuseUnchecked(rc, check, "remediation_unavailable", "it rewrites the request, and "+reason)
}

// rewritingCheck names the check on the request that rewrites it
// (RequestRewriter): a rule in the config file at the pre stage, then an
// organization's check. "request-remediation" when none says it does.
func (p *GuardrailPlugin) rewritingCheck(rc *models.RequestContext) string {
	if p.engine != nil {
		for _, rule := range p.engine.preGuardrails {
			if rewritesRequest(rule.guardrail) {
				return rule.guardrail.Name()
			}
		}
	}
	var names []string
	orgID := rc.Metadata["org_id"]
	p.eachOrgCheck(rc, StagePre, func(name string, check *tenant.GuardrailCheck) bool {
		if rewritesRequest(p.orgCheckGuardrail(orgID, name, check)) {
			names = append(names, name)
		}
		return true
	})
	if len(names) == 0 {
		return "request-remediation"
	}
	sort.Strings(names)
	return names[0]
}

// checkMetadata is the request's metadata as a check's input carries it: a
// copy without the caller's credentials (credentials.WithoutCredentials), as
// the request log's copy leaves out the caller's Authorization header. The
// request's metadata holds that header for the auth plugin, and a webhook
// check sends its input's metadata to the URL its config names, so no check
// gets the caller's key. The copy also keeps a check that outlives its
// timeout from reading the request's own map while the plugin writes it.
func checkMetadata(rc *models.RequestContext) map[string]string {
	return credentials.WithoutCredentials(rc.Metadata)
}

// requestForChecks is the request the checks read: rc.Request, or
// rc.PassThrough in the chat shape (the body the handler passes through to
// the provider's own API, or the request of an API other than chat): what of
// it the gateway can read, nil when it can read none. The first call reads
// it, until ctx ends (models.PassThroughRequest).
func requestForChecks(ctx context.Context, rc *models.RequestContext) *models.ChatCompletionRequest {
	if rc.Request != nil || rc.PassThrough == nil {
		return rc.Request
	}
	req, _ := rc.PassThrough.ChatRequest(ctx)
	return req
}

// requestForAnswerChecks is the request the checks on the answer read with
// it: requestForChecks, except where the request's API answers with
// something other than model text (models.NoChatAnswer: embeddings, images,
// speech, rerank, search, embedContent, ...). There the checks on the
// answer get rc.Request, so no check there records a block it does not
// enforce.
func requestForAnswerChecks(ctx context.Context, rc *models.RequestContext) *models.ChatCompletionRequest {
	if _, ok := rc.PassThrough.(models.NoChatAnswer); ok {
		return rc.Request
	}
	return requestForChecks(ctx, rc)
}

// noAnswerToWithhold says whether the request's API answers with something
// other than model text (models.NoChatAnswer), which the handler serves
// whatever a check on the answer says: the post stage has no answer it can
// withhold, so a check there in block mode records its trigger as logged.
func noAnswerToWithhold(rc *models.RequestContext) bool {
	_, ok := rc.PassThrough.(models.NoChatAnswer)
	return ok
}

// unreadableRequest says why the gateway cannot read the request, all or
// part of it, when a check reads the request (a rule in the config file at
// the pre stage, or an organization's check that runs there), and records
// it in the request's metadata (request_unavailable): the body in
// rc.PassThrough (one that holds a value nested deeper than
// requestview.MaxDepth included), or a tool call's arguments nested deeper
// than the checks read them (UnreadableArguments), in the request the checks
// read. It is "" when the request can be read, and when no check reads it.
// The body is read only here and when a check runs, so a request no check
// reads is never read; its read stops when ctx ends, and a body ctx cut
// short is unread. The arguments read here are kept for the checks (ctx's
// argumentsCache). With rc.Request set too (native /v1/completions, whose
// prompt the provider gets as sent), the body says whether that prompt can
// be read: a prompt of token ids cannot.
func (p *GuardrailPlugin) unreadableRequest(ctx context.Context, rc *models.RequestContext) (context.Context, string) {
	if (rc.Request == nil && rc.PassThrough == nil) || !p.checksTheRequest(rc) {
		return ctx, ""
	}
	var reason string
	if rc.PassThrough != nil {
		_, reason = rc.PassThrough.ChatRequest(ctx)
	}
	if req := requestForChecks(ctx, rc); makesToolCalls(req) {
		ctx = withArgumentsCache(ctx)
		if reason == "" {
			reason = UnreadableArguments(ctx, req)
		}
	}
	if reason != "" {
		rc.Metadata["request_unavailable"] = reason
	}
	return ctx, reason
}

// checksTheRequest says whether a check on the request applies to rc: a rule
// in the config file at the pre stage, or an organization's enabled check
// that runs there.
func (p *GuardrailPlugin) checksTheRequest(rc *models.RequestContext) bool {
	if p.engine.PreCount() > 0 {
		return true
	}
	found := false
	p.eachOrgCheck(rc, StagePre, func(string, *tenant.GuardrailCheck) bool {
		found = true
		return false
	})
	return found
}

// checksTheAnswer says whether a check on the answer applies to rc: a rule
// in the config file at the post stage, or an organization's enabled check
// that runs there.
func (p *GuardrailPlugin) checksTheAnswer(rc *models.RequestContext) bool {
	if p.engine.PostCount() > 0 {
		return true
	}
	found := false
	p.eachOrgCheck(rc, StagePost, func(string, *tenant.GuardrailCheck) bool {
		found = true
		return false
	})
	return found
}

// answerReaders is which output checks will read this request's answer: the
// config file's post-stage rules, unless X-Guardrail-Policy turned the rules
// off for the request, and an organization's enabled checks whose type reads
// the answer.
func (p *GuardrailPlugin) answerReaders(rc *models.RequestContext, reqPolicy policy.RequestPolicy) models.AnswerReaders {
	var readers models.AnswerReaders
	if p.engine.PostCount() > 0 && reqPolicy != policy.RequestPolicyDisabled {
		readers |= models.AnswerReadByRules
	}
	p.eachOrgCheck(rc, StagePost, func(string, *tenant.GuardrailCheck) bool {
		readers |= models.AnswerReadByOrg
		return false
	})
	return readers
}

// eachOrgCheck calls f with each of the request's organization's enabled
// checks that run at stage, until f returns false: at StagePost the checks
// that read the answer, at StagePre those that read the request.
// runOrgGuardrails runs a check at its type's stage, so these are the checks
// whose type runs at stage, other than one a rule in the config file runs
// instead.
func (p *GuardrailPlugin) eachOrgCheck(rc *models.RequestContext, stage Stage, f func(name string, check *tenant.GuardrailCheck) bool) {
	if p.tenantStore == nil {
		return
	}
	orgID := rc.Metadata["org_id"]
	if orgID == "" {
		return
	}
	orgCfg := p.tenantStore.Get(orgID)
	if orgCfg == nil || orgCfg.Guardrails == nil {
		return
	}
	for name, check := range orgCfg.Guardrails.Checks {
		if check == nil || !check.Enabled || (p.engine != nil && p.engine.HasRule(name)) {
			continue
		}
		if g := p.orgCheckGuardrail(orgID, name, check); g != nil && g.Stage() == stage {
			if !f(name, check) {
				return
			}
		}
	}
}

// ProcessResponse runs post-stage guardrails.
func (p *GuardrailPlugin) ProcessResponse(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	// The checks on the answer that read the request share what they read
	// in its tool calls' arguments (argumentsCache).
	if p.checksTheAnswer(rc) && makesToolCalls(requestForAnswerChecks(ctx, rc)) {
		ctx = withArgumentsCache(ctx)
	}

	// 0. Fail closed: an answer the gateway could not read, which a check on
	// the answer would block, is withheld rather than served unchecked.
	if rc.Flags.AnswerUnavailable && rc.Response != nil {
		var keyPolicy *policy.Policy
		reqPolicy := policy.RequestPolicyNone
		if p.engine.PostCount() > 0 {
			kp, rp, err := p.resolvePolicy(rc)
			if err != nil {
				withholdAnswer(rc, err)
				return pipeline.ResultError(err)
			}
			keyPolicy, reqPolicy = kp, rp
		}
		if blocking := p.blockingAnswerChecks(rc, keyPolicy, reqPolicy); len(blocking) > 0 {
			blockErr := p.refuseUncheckedAnswer(rc, blocking[0])
			withholdAnswer(rc, blockErr)
			return pipeline.ResultError(blockErr)
		}
	}

	// 1. Run static engine rules.
	if p.engine != nil && p.engine.PostCount() > 0 {
		keyPolicy, reqPolicy, err := p.resolvePolicy(rc)
		if err != nil {
			// Fail closed. ProcessRequest refuses such a request first; if one
			// gets here anyway, withholding the response is what keeps it
			// from going out unchecked.
			withholdAnswer(rc, err)
			return pipeline.ResultError(err)
		}

		input := &CheckInput{
			Request:  requestForAnswerChecks(ctx, rc),
			Response: rc.Response,
			Metadata: checkMetadata(rc),
		}

		var result *PipelineResult
		if noAnswerToWithhold(rc) {
			result = p.engine.RunPostUnenforced(ctx, input, keyPolicy, reqPolicy)
		} else {
			result = p.engine.RunPost(ctx, input, keyPolicy, reqPolicy)
		}

		if result.Blocked {
			rc.Flags.GuardrailTriggered = true
			if len(result.Triggered) > 0 {
				tg := result.Triggered[0]
				rc.Metadata["guardrail_name"] = tg.Name
				rc.Metadata["guardrail_action"] = "blocked"
			}
			storeGuardrailResults(rc, result)
			blockErr := models.ErrGuardrailBlocked("content_blocked", p.buildBlockMessage(result))
			withholdAnswer(rc, blockErr)
			return pipeline.ResultError(blockErr)
		}

		p.applyMetadata(rc, result)
	}

	// 2. Run dynamic guardrails from org config (managed mode).
	return p.runOrgGuardrails(ctx, rc, StagePost)
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

	result := &PipelineResult{}
	// The request's metadata as the checks get it, copied once, when the
	// first check runs.
	var metadata map[string]string

	for name, check := range orgCfg.Guardrails.Checks {
		if !check.Enabled {
			continue
		}

		// Skip if already handled by static engine rules.
		if p.engine != nil && p.engine.HasRule(name) {
			continue
		}

		// Get or create the guardrail implementation: for the action
		// "mask", the variant that rewrites the request.
		g := p.orgCheckGuardrail(orgID, name, check)
		if g == nil {
			slog.Debug("guardrail implementation not found",
				"name", name,
				"org_id", orgID,
			)
			continue
		}

		// Check stage matches.
		if g.Stage() != stage {
			continue
		}

		// Determine timeout.
		timeout := p.defaultTimeout
		if orgCfg.Guardrails.TimeoutMs > 0 {
			timeout = time.Duration(orgCfg.Guardrails.TimeoutMs) * time.Millisecond
		}

		// Build input. The request is read once, by the first check to run.
		if metadata == nil {
			metadata = checkMetadata(rc)
		}
		input := &CheckInput{
			Request:  requestForChecks(ctx, rc),
			Metadata: metadata,
		}
		if stage == StagePost {
			input.Request = requestForAnswerChecks(ctx, rc)
			input.Response = rc.Response
		}

		// Run with timeout and panic recovery. On the request, a check that
		// rewrites it and did not finish refuses it (isolate).
		cr, unapplied := p.runGuardrailSafe(ctx, g, input, timeout, stage == StagePre)
		if unapplied != "" {
			return pipeline.ResultError(refuseUnappliedRewrite(rc, name, unapplied))
		}
		if cr == nil {
			continue
		}

		// Apply threshold.
		threshold := check.ConfidenceThreshold
		triggered := shouldTrigger(cr, threshold)
		if !triggered {
			continue
		}

		action := orgCheckAction(check, g)
		if stage == StagePost && action == ActionBlock && noAnswerToWithhold(rc) {
			// The handler serves the answer whatever the check says: its
			// trigger is recorded as what is done, logged (RunPostUnenforced).
			action = ActionLog
		}

		tg := TriggeredGuardrail{
			Name:      name,
			Score:     cr.Score,
			Threshold: threshold,
			Action:    action,
			Message:   cr.Message,
		}
		result.Triggered = append(result.Triggered, tg)

		switch action {
		case ActionBlock:
			result.Blocked = true
			storeGuardrailResults(rc, result)
			rc.Flags.GuardrailTriggered = true
			rc.Metadata["guardrail_name"] = name
			rc.Metadata["guardrail_action"] = "blocked"
			blockErr := models.ErrGuardrailBlocked("content_blocked", p.buildBlockMessage(result))
			if stage == StagePost {
				// Withhold the answer, as a static post-stage block does.
				withholdAnswer(rc, blockErr)
			}
			return pipeline.ResultError(blockErr)
		case ActionWarn:
			result.Warnings = append(result.Warnings, fmt.Sprintf("%s: %s", name, cr.Message))
		case ActionLog, ActionMask:
			// A mask rewrote what the check read (its Masked variant) and
			// the request goes on, as in log mode.
			slog.Info("guardrail triggered (log mode)",
				"guardrail", name,
				"org_id", orgID,
				"action", actionString(action),
				"score", cr.Score,
				"threshold", threshold,
				"message", cr.Message,
			)
		}
	}

	// Apply metadata for non-blocking triggers.
	if len(result.Triggered) > 0 {
		p.applyMetadata(rc, result)
	}

	return pipeline.ResultContinue()
}

// actionMask is the action of an organization's check that rewrites what it
// reads instead of blocking it: the check runs as its type's Masked variant,
// and the request goes on, as in log mode. Only a check whose type can
// rewrite the request (Masker: pii-detection) takes it; on any other check
// it is an action parseAction does not know, which runs as log.
const actionMask = "mask"

// maskedCacheKey is the dynamic cache's key for the Masked variant of the
// org's check name. A check name never holds a NUL byte, so it cannot be
// another check's name.
func maskedCacheKey(name string) string { return name + "\x00mask" }

// orgCheckGuardrail is the guardrail an organization's check runs: its
// type's (getGuardrail), or, for the action "mask" on a type that can
// rewrite the request it reads (Masker), the variant that rewrites it,
// cached per org with the org's other dynamic guardrails, so
// InvalidateDynamicCache drops it too. The pass-through write-back and the
// refusals for a check that rewrites the request (step 0 and step 2b of
// ProcessRequest) then take such a check as they take a remediation.
func (p *GuardrailPlugin) orgCheckGuardrail(orgID, name string, check *tenant.GuardrailCheck) Guardrail {
	g := p.getGuardrail(orgID, name, check)
	if g == nil || check == nil || check.Action != actionMask {
		return g
	}
	m, ok := g.(Masker)
	if !ok {
		return g
	}
	key := maskedCacheKey(name)
	if cached := p.getCachedDynamicGuardrail(orgID, key); cached != nil {
		return cached
	}
	masked := m.Masked()
	p.dynamicMu.Lock()
	if _, ok := p.dynamicCache[orgID]; !ok {
		p.dynamicCache[orgID] = make(map[string]Guardrail)
	}
	p.dynamicCache[orgID][key] = masked
	p.dynamicMu.Unlock()
	return masked
}

// orgCheckAction is what an organization's check whose guardrail is g does
// when it triggers: ActionMask for the action "mask" on a type that can
// rewrite the request (g is then its Masked variant), else its action as
// parseAction reads it.
func orgCheckAction(check *tenant.GuardrailCheck, g Guardrail) Action {
	if check.Action == actionMask && CanMask(g) {
		return ActionMask
	}
	return parseAction(check.Action)
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
// With request, as Engine.runSync: a check that rewrites the request runs on
// a copy of its messages (isolate), written back when it returns in time;
// unapplied says why its rewrite was not applied when it did not.
func (p *GuardrailPlugin) runGuardrailSafe(ctx context.Context, g Guardrail, input *CheckInput, timeout time.Duration, request bool) (cr *CheckResult, unapplied string) {
	checkCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	checkInput, commit := input, (func())(nil)
	if request {
		checkInput, commit = isolate(g, input)
	}

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
		cr := g.Check(checkCtx, checkInput)
		ch <- result{cr: cr}
	}()

	select {
	case res := <-ch:
		if res.err != nil {
			slog.Error("dynamic guardrail error",
				"guardrail", g.Name(),
				"error", res.err,
				"fail_open", p.failOpen,
				"rewrites_request", commit != nil,
			)
			if commit != nil {
				return nil, "it failed before it finished rewriting the request (" + res.err.Error() + "), so its rewrite was not applied"
			}
			if p.failOpen {
				return nil, ""
			}
			return &CheckResult{
				Pass:    false,
				Score:   1.0,
				Message: fmt.Sprintf("guardrail %q error: %v", g.Name(), res.err),
			}, ""
		}
		if commit != nil {
			commit()
		}
		return res.cr, ""
	case <-checkCtx.Done():
		slog.Warn("dynamic guardrail timed out",
			"guardrail", g.Name(),
			"timeout", timeout,
			"fail_open", p.failOpen,
			"rewrites_request", commit != nil,
		)
		if commit != nil {
			return nil, fmt.Sprintf("it did not finish rewriting the request within its timeout (%s), so its rewrite was not applied", timeout)
		}
		if p.failOpen {
			return nil, ""
		}
		return &CheckResult{
			Pass:    false,
			Score:   1.0,
			Message: fmt.Sprintf("guardrail %q timed out", g.Name()),
		}, ""
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
			"API key does not allow guardrail policy overrides: per-request overrides are not enabled for this key. Ask an administrator to enable them",
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

// PermittedRequestPolicy is the request's X-Guardrail-Policy when its key has
// allow_policy_override and the value is valid, and RequestPolicyNone
// otherwise. ProcessRequest refuses the other cases before the provider call;
// this is the streaming checker's own guard, so it never takes a policy the
// key may not choose.
func PermittedRequestPolicy(metadata map[string]string) policy.RequestPolicy {
	if metadata["key_allow_policy_override"] != "true" {
		return policy.RequestPolicyNone
	}
	rp, ok := policy.ParseRequestPolicy(metadata["x-guardrail-policy"])
	if !ok {
		return policy.RequestPolicyNone
	}
	return rp
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
	if result.Unapplied != "" {
		reason := result.Triggered[len(result.Triggered)-1].Message
		return pipeline.ResultError(refuseUnappliedRewrite(rc, result.Unapplied, reason))
	}
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

// withholdAnswer is what a post-stage refusal does to the answer it was
// given. The answer is dropped, so it is never served, and the refusal
// becomes the request's error: the pipeline only logs a post-plugin's error,
// so the handler finds it on rc and answers it (a guardrail block: 403
// content_blocked), and the request log records its status. The request is
// flagged AnswerRefused: the answer was given, so its error is not the
// provider's. With no answer (the provider failed, or a pre-plugin refused
// the request) the request keeps the error it already has.
func withholdAnswer(rc *models.RequestContext, err *models.APIError) {
	if rc.Response == nil {
		return
	}
	rc.Response = nil
	rc.Flags.AnswerRefused = true
	rc.AddError(err)
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
	case ActionMask:
		return actionMask
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
