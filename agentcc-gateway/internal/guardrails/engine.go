package guardrails

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
)

// resolvedRule is a guardrail bound to its config-driven parameters.
type resolvedRule struct {
	guardrail Guardrail
	mode      string // "sync" or "async"
	action    Action
	threshold float64
	timeout   time.Duration
}

// maxAsyncGuardrails limits the number of concurrent async guardrail goroutines.
const maxAsyncGuardrails = 64

// Engine manages guardrail execution.
type Engine struct {
	preGuardrails  []resolvedRule
	postGuardrails []resolvedRule
	failOpen       bool
	defaultTimeout time.Duration
	asyncSem       chan struct{} // bounds async goroutine count
}

// NewEngine creates a guardrail engine from config and a registry of guardrail implementations.
func NewEngine(cfg config.GuardrailsConfig, registry map[string]Guardrail) *Engine {
	e := &Engine{
		failOpen:       cfg.FailOpen,
		defaultTimeout: cfg.DefaultTimeout,
		asyncSem:       make(chan struct{}, maxAsyncGuardrails),
	}
	if e.defaultTimeout <= 0 {
		e.defaultTimeout = 30 * time.Second
	}

	for _, rule := range cfg.Rules {
		g, ok := registry[rule.Name]
		if !ok {
			slog.Warn("guardrail not found in registry, skipping",
				"name", rule.Name,
			)
			continue
		}

		timeout := rule.Timeout
		if timeout <= 0 {
			timeout = e.defaultTimeout
		}

		resolved := resolvedRule{
			guardrail: g,
			mode:      rule.Mode,
			action:    parseAction(rule.Action),
			threshold: rule.Threshold,
			timeout:   timeout,
		}

		switch rule.Stage {
		case "pre":
			e.preGuardrails = append(e.preGuardrails, resolved)
		case "post":
			e.postGuardrails = append(e.postGuardrails, resolved)
		default:
			slog.Warn("unknown guardrail stage, skipping",
				"name", rule.Name,
				"stage", rule.Stage,
			)
		}
	}

	return e
}

// RunPre executes all pre-stage guardrails with optional per-key policy.
func (e *Engine) RunPre(ctx context.Context, input *CheckInput, p *policy.Policy, rp policy.RequestPolicy) *PipelineResult {
	return e.run(ctx, StagePre, e.preGuardrails, input, p, rp, false)
}

// RunPost executes all post-stage guardrails with optional per-key policy.
func (e *Engine) RunPost(ctx context.Context, input *CheckInput, p *policy.Policy, rp policy.RequestPolicy) *PipelineResult {
	return e.run(ctx, StagePost, e.postGuardrails, input, p, rp, false)
}

// RunPostUnenforced runs the post-stage guardrails on the answer of a
// request whose answer the gateway cannot withhold (models.NoChatAnswer:
// an embedding, an image, audio, a ranking, search results, a token
// count, ...), which the handler serves whatever a rule says: as RunPost,
// except that a rule whose action is block records its trigger as what is
// done, log, and the rules after it run. So the request log and the
// response headers never say a rule blocked an answer that was served.
func (e *Engine) RunPostUnenforced(ctx context.Context, input *CheckInput, p *policy.Policy, rp policy.RequestPolicy) *PipelineResult {
	return e.run(ctx, StagePost, e.postGuardrails, input, p, rp, true)
}

// PreCount returns the number of pre-stage guardrails.
func (e *Engine) PreCount() int {
	if e == nil {
		return 0
	}
	return len(e.preGuardrails)
}

// PostCount returns the number of post-stage guardrails.
func (e *Engine) PostCount() int {
	if e == nil {
		return 0
	}
	return len(e.postGuardrails)
}

func (e *Engine) run(ctx context.Context, stage Stage, rules []resolvedRule, input *CheckInput, p *policy.Policy, rp policy.RequestPolicy, unenforced bool) *PipelineResult {
	result := &PipelineResult{}

	// Per-request policy: disabled skips all guardrails.
	if rp == policy.RequestPolicyDisabled {
		return result
	}

	for _, rule := range rules {
		effectiveAction, effectiveThreshold, skip := effectiveRule(rule, p, rp)
		if skip {
			continue // the key's policy disables this guardrail
		}
		if unenforced && effectiveAction == ActionBlock {
			effectiveAction = ActionLog // RunPostUnenforced
		}

		if rule.mode == "async" {
			e.runAsync(ctx, rule, input)
			continue
		}

		// Sync execution with per-guardrail timeout. On the request, a rule
		// that rewrites it and did not finish refuses it (isolate).
		checkResult, unapplied := e.runSync(ctx, rule, input, stage == StagePre)
		if unapplied != "" {
			result.Triggered = append(result.Triggered, TriggeredGuardrail{
				Name:      rule.guardrail.Name(),
				Score:     1,
				Threshold: effectiveThreshold,
				Action:    ActionBlock,
				Message:   unapplied,
			})
			result.Blocked = true
			result.Unapplied = rule.guardrail.Name()
			return result
		}
		if checkResult == nil {
			continue
		}

		// Apply threshold.
		triggered := shouldTrigger(checkResult, effectiveThreshold)
		if !triggered {
			continue
		}

		tg := TriggeredGuardrail{
			Name:      rule.guardrail.Name(),
			Score:     checkResult.Score,
			Threshold: effectiveThreshold,
			Action:    effectiveAction,
			Message:   checkResult.Message,
		}
		result.Triggered = append(result.Triggered, tg)

		switch effectiveAction {
		case ActionBlock:
			result.Blocked = true
			return result // Short-circuit on block.
		case ActionWarn:
			result.Warnings = append(result.Warnings, fmt.Sprintf("%s: %s", rule.guardrail.Name(), checkResult.Message))
		case ActionLog:
			slog.Info("guardrail triggered (log mode)",
				"guardrail", rule.guardrail.Name(),
				"score", checkResult.Score,
				"threshold", effectiveThreshold,
				"message", checkResult.Message,
			)
		}
	}

	return result
}

// effectiveRule is how rule runs for a request: its action and threshold
// after the key's policy overrides and then the request's
// X-Guardrail-Policy, which takes precedence; skip when the key's policy
// disables the rule.
func effectiveRule(rule resolvedRule, p *policy.Policy, rp policy.RequestPolicy) (action Action, threshold float64, skip bool) {
	action, threshold = rule.action, rule.threshold
	if p != nil {
		if ov, ok := p.Overrides[rule.guardrail.Name()]; ok {
			if ov.Disabled {
				return action, threshold, true
			}
			if ov.Action != "" {
				action = parseAction(ov.Action)
			}
			if ov.Threshold != nil {
				threshold = *ov.Threshold
			}
		}
	}
	switch rp {
	case policy.RequestPolicyLogOnly:
		action = ActionLog
	case policy.RequestPolicyStrict:
		action, threshold = ActionBlock, 0
	}
	return action, threshold, false
}

// BlockingPost names the post-stage rules that would refuse a request's
// answer under the key's policy p and the request's X-Guardrail-Policy rp:
// the sync rules whose action, after both, is block. An async rule only logs.
func (e *Engine) BlockingPost(p *policy.Policy, rp policy.RequestPolicy) []string {
	if e == nil {
		return nil
	}
	return blocking(e.postGuardrails, p, rp)
}

// BlockingPre names the pre-stage rules that would refuse a request, as
// BlockingPost does for the answer.
func (e *Engine) BlockingPre(p *policy.Policy, rp policy.RequestPolicy) []string {
	if e == nil {
		return nil
	}
	return blocking(e.preGuardrails, p, rp)
}

// RewritingPre names the pre-stage rules that would rewrite a request
// (RequestRewriter: pii-detection's mask, redact or hash) under the key's
// policy p and the request's X-Guardrail-Policy rp: every rule that runs on
// the request and rewrites what it reads, sync or async, whatever its
// action. A rule the key's policy disables does not run, and no rule runs
// when rp is disabled.
func (e *Engine) RewritingPre(p *policy.Policy, rp policy.RequestPolicy) []string {
	if e == nil || rp == policy.RequestPolicyDisabled {
		return nil
	}
	var names []string
	for _, rule := range e.preGuardrails {
		if _, _, skip := effectiveRule(rule, p, rp); !skip && rewritesRequest(rule.guardrail) {
			names = append(names, rule.guardrail.Name())
		}
	}
	return names
}

// blocking names the sync rules whose action under p and rp is block.
func blocking(rules []resolvedRule, p *policy.Policy, rp policy.RequestPolicy) []string {
	if rp == policy.RequestPolicyDisabled {
		return nil
	}
	var names []string
	for _, rule := range rules {
		if rule.mode == "async" {
			continue
		}
		if action, _, skip := effectiveRule(rule, p, rp); !skip && action == ActionBlock {
			names = append(names, rule.guardrail.Name())
		}
	}
	return names
}

// runSync runs rule's check and waits for it up to its timeout. With
// request, the check reads the request before the provider gets it: one that
// rewrites the request runs on a copy of its messages (isolate), written
// back when the check returns in time. unapplied, when it is not "", says
// why such a check's rewrite was not applied: it timed out or panicked. The
// copy it may still write is its own, and fail_open does not apply to it.
func (e *Engine) runSync(ctx context.Context, rule resolvedRule, input *CheckInput, request bool) (cr *CheckResult, unapplied string) {
	checkCtx, cancel := context.WithTimeout(ctx, rule.timeout)
	defer cancel()

	checkInput, commit := input, (func())(nil)
	if request {
		checkInput, commit = isolate(rule.guardrail, input)
	}

	// Run check with panic recovery.
	type result struct {
		cr  *CheckResult
		err error
	}
	ch := make(chan result, 1)

	go func() {
		defer func() {
			if r := recover(); r != nil {
				slog.Error("guardrail panicked",
					"guardrail", rule.guardrail.Name(),
					"panic", r,
				)
				ch <- result{err: fmt.Errorf("guardrail panicked: %v", r)}
			}
		}()
		cr := rule.guardrail.Check(checkCtx, checkInput)
		ch <- result{cr: cr}
	}()

	select {
	case res := <-ch:
		if res.err != nil {
			if commit != nil {
				slog.Error("guardrail that rewrites the request failed; its rewrite is not applied",
					"guardrail", rule.guardrail.Name(), "error", res.err)
				return nil, "it failed before it finished rewriting the request (" + res.err.Error() + "), so its rewrite was not applied"
			}
			return e.handleError(rule, res.err), ""
		}
		if commit != nil {
			commit()
		}
		return res.cr, ""
	case <-checkCtx.Done():
		slog.Warn("guardrail timed out",
			"guardrail", rule.guardrail.Name(),
			"timeout", rule.timeout,
			"fail_open", e.failOpen,
			"rewrites_request", commit != nil,
		)
		if commit != nil {
			return nil, fmt.Sprintf("it did not finish rewriting the request within its timeout (%s), so its rewrite was not applied", rule.timeout)
		}
		if e.failOpen {
			return nil, "" // pass
		}
		return &CheckResult{
			Pass:    false,
			Score:   1.0,
			Action:  rule.action,
			Message: fmt.Sprintf("guardrail %q timed out", rule.guardrail.Name()),
		}, ""
	}
}

// runAsync runs rule's check in the background, on a copy of the messages
// it reads (snapshot): the request does not wait for it, so nothing it reads
// may change under it, and nothing it writes reaches the request or the
// answer. A rule that rewrites what it reads only logs in async mode. It
// shares what the request's checks read in its tool calls' arguments
// (ctx's argumentsCache), and none of ctx's deadline or cancellation.
func (e *Engine) runAsync(ctx context.Context, rule resolvedRule, input *CheckInput) {
	// Acquire semaphore slot to bound concurrency. Drop if full.
	select {
	case e.asyncSem <- struct{}{}:
	default:
		slog.Warn("async guardrail dropped, concurrency limit reached",
			"guardrail", rule.guardrail.Name(),
			"limit", maxAsyncGuardrails,
		)
		return
	}

	input = snapshot(input)
	detached := withCache(context.Background(), cacheOf(ctx))
	go func() {
		defer func() { <-e.asyncSem }()
		defer func() {
			if r := recover(); r != nil {
				slog.Error("async guardrail panicked",
					"guardrail", rule.guardrail.Name(),
					"panic", r,
				)
			}
		}()

		ctx, cancel := context.WithTimeout(detached, rule.timeout)
		defer cancel()

		cr := rule.guardrail.Check(ctx, input)
		if cr != nil && !cr.Pass {
			slog.Info("async guardrail triggered",
				"guardrail", rule.guardrail.Name(),
				"score", cr.Score,
				"message", cr.Message,
			)
		}
	}()
}

func (e *Engine) handleError(rule resolvedRule, err error) *CheckResult {
	slog.Error("guardrail error",
		"guardrail", rule.guardrail.Name(),
		"error", err,
		"fail_open", e.failOpen,
	)
	if e.failOpen {
		return nil
	}
	return &CheckResult{
		Pass:    false,
		Score:   1.0,
		Action:  rule.action,
		Message: fmt.Sprintf("guardrail %q error: %v", rule.guardrail.Name(), err),
	}
}

func parseAction(s string) Action {
	switch s {
	case "block":
		return ActionBlock
	case "warn":
		return ActionWarn
	case "log":
		return ActionLog
	default:
		return ActionLog
	}
}

// HasRule returns true if the engine has a static rule with the given name.
func (e *Engine) HasRule(name string) bool {
	if e == nil {
		return false
	}
	for _, r := range e.preGuardrails {
		if r.guardrail.Name() == name {
			return true
		}
	}
	for _, r := range e.postGuardrails {
		if r.guardrail.Name() == name {
			return true
		}
	}
	return false
}

// FailOpen returns the fail-open setting.
func (e *Engine) FailOpen() bool {
	if e == nil {
		return false
	}
	return e.failOpen
}

// DefaultTimeout returns the default timeout for guardrail checks.
func (e *Engine) DefaultTimeout() time.Duration {
	if e == nil {
		return 30 * time.Second
	}
	return e.defaultTimeout
}
