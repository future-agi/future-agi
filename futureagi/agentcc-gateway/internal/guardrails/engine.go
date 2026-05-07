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
	guardrail          Guardrail
	mode               string // "sync" or "async"
	action             Action
	threshold          float64
	timeout            time.Duration
	onBlock            string
	reflectMaxAttempts int
	reflectTempBump    float64
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
			guardrail:          g,
			mode:               rule.Mode,
			action:             parseAction(rule.Action),
			threshold:          rule.Threshold,
			timeout:            timeout,
			onBlock:            rule.OnBlock,
			reflectMaxAttempts: rule.ReflectMaxAttempts,
			reflectTempBump:    rule.ReflectTempBump,
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
	return e.run(ctx, e.preGuardrails, input, p, rp, nil)
}

// RunPost executes all post-stage guardrails with optional per-key policy and reflexion.
func (e *Engine) RunPost(ctx context.Context, input *CheckInput, p *policy.Policy, rp policy.RequestPolicy, regenerateFn func(context.Context, string) error) *PipelineResult {
	return e.run(ctx, e.postGuardrails, input, p, rp, regenerateFn)
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

func (e *Engine) run(ctx context.Context, rules []resolvedRule, input *CheckInput, p *policy.Policy, rp policy.RequestPolicy, regenerateFn func(context.Context, string) error) *PipelineResult {
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

		maxAttempts := 0
		if rule.onBlock == "reflect" && effectiveAction == ActionBlock && regenerateFn != nil {
			maxAttempts = rule.reflectMaxAttempts
			if maxAttempts <= 0 {
				maxAttempts = 1
			}
		}

		var checkResult *CheckResult
		var triggered bool

		for attempt := 0; attempt <= maxAttempts; attempt++ {
			// Sync execution with per-guardrail timeout.
			checkResult = e.runSync(ctx, rule, input)
			if checkResult == nil {
				triggered = false
				break
			}

			// Apply threshold.
			triggered = shouldTrigger(checkResult, effectiveThreshold)
			if !triggered {
				if attempt > 0 {
					result.ReflexionSucceeded = true
				}
				break // Passed!
			}

			// If triggered and we have attempts left, trigger reflexion loop
			if attempt < maxAttempts {
				result.ReflexionAttempts++
				slog.Info("guardrail triggered, reflecting", "guardrail", rule.guardrail.Name(), "attempt", attempt+1)

				// Invoke callback to prompt the LLM to correct itself
				if err := regenerateFn(ctx, checkResult.Message); err != nil {
					slog.Warn("reflexion regeneration failed", "error", err)
					break // Break out of loop, fall through to standard block
				}
			}
		}

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

	select {
	case res := <-ch:
		if res.err != nil {
			return e.handleError(rule, res.err)
		}
		return res.cr
	case <-checkCtx.Done():
		slog.Warn("guardrail timed out",
			"guardrail", rule.guardrail.Name(),
			"timeout", rule.timeout,
			"fail_open", e.failOpen,
		)
		if e.failOpen {
			return nil // pass
		}
		return &CheckResult{
			Pass:    false,
			Score:   1.0,
			Action:  rule.action,
			Message: fmt.Sprintf("guardrail %q timed out", rule.guardrail.Name()),
		}
	}
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
