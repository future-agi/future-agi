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
	unresolved     []config.GuardrailRuleConfig
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
			e.unresolved = append(e.unresolved, rule)
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
			e.unresolved = append(e.unresolved, rule)
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
	return e.run(ctx, e.preGuardrails, input, p, rp)
}

// RunPost executes all post-stage guardrails with optional per-key policy.
func (e *Engine) RunPost(ctx context.Context, input *CheckInput, p *policy.Policy, rp policy.RequestPolicy) *PipelineResult {
	return e.run(ctx, e.postGuardrails, input, p, rp)
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

func (e *Engine) run(ctx context.Context, rules []resolvedRule, input *CheckInput, p *policy.Policy, rp policy.RequestPolicy) *PipelineResult {
	result := &PipelineResult{}

	// Per-request policy: disabled skips all guardrails.
	if rp == policy.RequestPolicyDisabled {
		return result
	}

	for _, rule := range rules {
		// Apply per-key policy overrides.
		effectiveAction := rule.action
		effectiveThreshold := rule.threshold

		if p != nil {
			if ov, ok := p.Overrides[rule.guardrail.Name()]; ok {
				if ov.Disabled {
					continue // skip this guardrail for this key
				}
				if ov.Action != "" {
					effectiveAction = parseAction(ov.Action)
				}
				if ov.Threshold != nil {
					effectiveThreshold = *ov.Threshold
				}
			}
		}

		// Apply per-request policy overrides (take precedence over per-key).
		switch rp {
		case policy.RequestPolicyLogOnly:
			effectiveAction = ActionLog
		case policy.RequestPolicyStrict:
			effectiveAction = ActionBlock
			effectiveThreshold = 0
		}

		if rule.mode == "async" {
			e.runAsync(rule, input)
			continue
		}

		// Sync execution with per-guardrail timeout.
		checkResult := e.runSync(ctx, rule, input)
		if checkResult == nil {
			continue
		}

		// Failure policy precedes scoring; admission remains an auditable outcome.
		if checkResult.FailureAdmitted {
			effectiveAction = ActionLog
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
			result.Unavailable = checkResult.Err != nil
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

func (e *Engine) runSync(ctx context.Context, rule resolvedRule, input *CheckInput) *CheckResult {
	checkCtx, cancel := context.WithTimeout(ctx, rule.timeout)
	defer cancel()

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
		cr := rule.guardrail.Check(checkCtx, input)
		ch <- result{cr: cr}
	}()

	var res result
	select {
	case res = <-ch:
	case <-checkCtx.Done():
	}
	if checkCtx.Err() != nil {
		return e.handleError(rule, checkCtx.Err())
	}
	if res.err == nil {
		res.err = checkResultError(res.cr)
	}
	if res.err != nil {
		return e.handleError(rule, res.err)
	}
	return res.cr
}

func (e *Engine) runAsync(rule resolvedRule, input *CheckInput) {
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

		ctx, cancel := context.WithTimeout(context.Background(), rule.timeout)
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
	return &CheckResult{
		Pass: false, Score: 1.0, Action: rule.action,
		Err: err, FailureAdmitted: e.failOpen,
		Message: fmt.Sprintf("guardrail_unavailable: %q", rule.guardrail.Name()),
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
