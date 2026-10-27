package pipeline

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"strconv"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/reflexion"
)

// BufferResponse is deliberately conservative: if any applicable rule opts in,
// the entire candidate must stay private until all synchronous checks pass.
func (e *Engine) BufferResponse(rc *models.RequestContext) bool {
	for _, p := range e.plugins {
		if v, ok := p.(ResponseValidator); ok && v.BufferResponse(rc) {
			return true
		}
	}
	return false
}

func reflectionEndpointError(rc *models.RequestContext, enabled, buffered bool) *models.APIError {
	if !enabled {
		return nil
	}
	switch rc.EndpointType {
	case "anthropic_messages", "genai_generate", "genai_stream", "responses", "completion":
		return models.ErrBadRequest("reflection_endpoint_unsupported", "Response repair is supported only on /v1/chat/completions; use that endpoint or disable this reflection rule")
	case "chat", "":
		if rc.IsStream && !buffered {
			return models.ErrBadRequest("reflection_endpoint_unsupported", "Response repair requires the guarded /v1/chat/completions streaming adapter")
		}
	}
	return nil
}

func (e *Engine) accountAttempt(ctx context.Context, rc *models.RequestContext) {
	if rc.Flags.CacheHit {
		return
	}
	for _, p := range e.postSequential {
		if a, ok := p.(AttemptAccounting); !ok || !a.IsAttemptAccounting() {
			continue
		}
		start := time.Now()
		result := p.ProcessResponse(ctx, rc)
		rc.RecordTiming("post_"+p.Name(), time.Since(start))
		if result.Error != nil {
			slog.Warn("post-plugin error", "plugin", p.Name(), "error", result.Error.Message, "request_id", rc.RequestID)
		}
	}
}

func (e *Engine) validateResponse(ctx context.Context, rc *models.RequestContext) PluginResult {
	if rc.Response == nil {
		return ResultContinue()
	}
	result := ResultContinue()
	candidate := rc.Response
	for _, p := range e.postSequential {
		if _, ok := p.(ResponseValidator); !ok {
			continue
		}
		rc.Response = candidate
		start := time.Now()
		r := p.ProcessResponse(ctx, rc)
		rc.RecordTiming("post_"+p.Name(), time.Since(start))
		if rc.Response != nil {
			candidate = rc.Response
		}
		if r.Error != nil {
			if result.Error == nil {
				result = r
				continue
			}
			if r.Reflection == nil {
				result.Reflection = nil
			} else if result.Reflection != nil {
				result.Reflection.MaxAttempts = min(result.Reflection.MaxAttempts, r.Reflection.MaxAttempts)
				result.Reflection.Reason += "\n" + r.Reflection.Reason
			}
		}
	}
	if result.Error != nil {
		rc.Response = nil
	}
	return result
}

// BeginProviderAttempt and EndProviderAttempt bracket actual upstream calls,
// including transport failover. Ordinary unbuffered streams keep their existing
// lifecycle. A controller-opened first attempt is reused by the handler hook.
func (e *Engine) BeginProviderAttempt(ctx context.Context, rc *models.RequestContext) error {
	l := rc.Attempts
	if l == nil || l.Open {
		return nil
	}
	if l.Denied != nil {
		return l.Denied
	}
	clearAttempt(rc)
	if err := ctx.Err(); err != nil {
		return models.ErrRequestTimeout("request canceled before provider attempt")
	}
	if l.Started > 0 {
		// Non-consuming gates first; RPM is consumed only after spend/credits pass.
		for _, name := range []string{"budget", "credits", "ratelimit"} {
			for _, p := range e.plugins {
				g, ok := p.(RetryAdmission)
				if !ok || !g.IsRetryAdmission() || p.Name() != name {
					continue
				}
				model := rc.Model
				if rc.Request != nil {
					rc.Model = rc.Request.Model
				}
				r := p.ProcessRequest(ctx, rc)
				rc.Model = model
				if r.Error != nil {
					l.Denied = &models.AdmissionError{Err: r.Error}
					return l.Denied
				}
			}
		}
	}
	l.Started++
	l.Open = true
	return nil
}

func clearAttempt(rc *models.RequestContext) {
	rc.Response = nil
	rc.ResolvedModel = ""
	for _, k := range []string{"cost", "cost_source", "credits_used"} {
		delete(rc.Metadata, k)
	}
}

func (e *Engine) EndProviderAttempt(ctx context.Context, rc *models.RequestContext) {
	l := rc.Attempts
	if l == nil || !l.Open {
		return
	}
	l.Open = false
	model := rc.Model
	servingModel := rc.ResolvedModel
	if servingModel == "" && rc.Request != nil {
		servingModel = rc.Request.Model
	}
	if servingModel != "" {
		rc.Model = servingModel
	}
	// Settlement must survive caller cancellation. Never rerun admission here.
	e.accountAttempt(context.WithoutCancel(ctx), rc)
	rc.Model = model
	a := models.ProviderAttempt{Provider: models.RedactCredentials(rc.Provider), Model: models.RedactCredentials(servingModel), Cost: rc.Metadata["cost"], Credits: rc.Metadata["credits_used"], UsageMissing: true}
	if rc.Response != nil && rc.Response.Usage != nil {
		u := rc.Response.Usage
		a.Usage = &models.Usage{PromptTokens: u.PromptTokens, CompletionTokens: u.CompletionTokens, TotalTokens: u.TotalTokens}
		if a.Usage.TotalTokens == 0 {
			a.Usage.TotalTokens = u.PromptTokens + u.CompletionTokens
		}
		a.UsageMissing = false
	}
	l.Attempts = append(l.Attempts, a)
}

func (e *Engine) processCandidates(ctx context.Context, rc *models.RequestContext, call ProviderFunc) error {
	original := rc.Request
	rc.AccountedUsage = nil
	rc.Attempts = &models.AttemptLedger{}
	defer func() { rc.Request = original; rc.Attempts = nil }()
	private, err := cloneRequest(original)
	if err != nil {
		return models.ErrInternal("could not prepare provider request")
	}
	preResults := append([]models.GuardrailResult(nil), rc.GuardrailResults...)
	preMetadata := map[string]string{}
	for _, k := range []string{"guardrail_name", "guardrail_action", "guardrail_warnings"} {
		preMetadata[k] = rc.Metadata[k]
	}
	limit, retries := reflexion.MaxAttempts, 0
	debug := false
	pendingReflection := false
	var finalErr error
	for {
		if pendingReflection {
			clear(rc.GuardrailResults)
			rc.GuardrailResults = append(rc.GuardrailResults[:0], preResults...)
			for k, v := range preMetadata {
				if v == "" {
					delete(rc.Metadata, k)
				} else {
					rc.Metadata[k] = v
				}
			}
		}
		startedBefore := rc.Attempts.Started
		rc.Request = private
		if !rc.AttemptHooks {
			if err := e.BeginProviderAttempt(ctx, rc); err != nil {
				finalErr = err
				break
			}
		}
		start := time.Now()
		err := call(ctx, rc)
		if pendingReflection && rc.Attempts.Started > startedBefore {
			retries++
		}
		if rc.Attempts.Denied != nil {
			err = rc.Attempts.Denied
		}
		rc.RecordTiming("provider", rc.Timings["provider"]+time.Since(start))
		e.EndProviderAttempt(ctx, rc)
		rc.Request = original
		if err != nil {
			finalErr = err
			rc.Response = nil
			break
		}
		if ctx.Err() != nil {
			finalErr = models.ErrRequestTimeout("request canceled during provider attempt")
			rc.Response = nil
			break
		}
		blocked := rc.Response
		if blocked != nil {
			candidate := *blocked
			candidate.Choices = append([]models.Choice(nil), blocked.Choices...)
			for i := range candidate.Choices {
				candidate.Choices[i].Message.Content = append(json.RawMessage(nil), blocked.Choices[i].Message.Content...)
			}
			blocked = &candidate
		}
		result := e.validateResponse(ctx, rc)
		if ctx.Err() != nil {
			finalErr = models.ErrRequestTimeout("request canceled during response validation")
			break
		}
		if result.Error == nil {
			break
		}
		rc.Response = nil
		if result.Reflection == nil {
			finalErr = result.Error
			break
		}
		limit = min(limit, result.Reflection.MaxAttempts)
		debug = debug || result.Reflection.Debug
		if retries >= limit {
			finalErr = result.Error
			break
		}
		private, err = reflectionRequest(original, blocked, result.Reflection.Reason)
		if err != nil {
			finalErr = result.Error
			break
		}
		pendingReflection = true
	}
	rc.Request = original
	if admission, ok := finalErr.(*models.AdmissionError); ok {
		finalErr = admission.Err
	}
	if finalErr != nil {
		if ctx.Err() != nil {
			rc.Flags.Timeout = true
		}
		rc.Response = nil
		delete(rc.Metadata, "guardrail_warnings")
		for i := range rc.GuardrailResults {
			rc.GuardrailResults[i].Message = "response check failed"
		}
		rc.AddError(finalErr)
	}
	publishAttempts(rc, retries, debug)
	e.runFinalPlugins(context.WithoutCancel(ctx), rc)
	return finalErr
}

func cloneRequest(req *models.ChatCompletionRequest) (*models.ChatCompletionRequest, error) {
	if req == nil {
		return nil, nil
	}
	b, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	var copy models.ChatCompletionRequest
	if err := json.Unmarshal(b, &copy); err != nil {
		return nil, err
	}
	return &copy, nil
}

func reflectionRequest(original *models.ChatCompletionRequest, response *models.ChatCompletionResponse, reason string) (*models.ChatCompletionRequest, error) {
	// Do not synthesize tool results or collapse alternative choices into one
	// assistant turn. Those responses remain blocked, with their spend settled.
	if original == nil || response == nil || len(response.Choices) != 1 {
		return nil, fmt.Errorf("unsupported candidate")
	}
	if original.N != nil && *original.N != 1 {
		return nil, fmt.Errorf("unsupported candidate")
	}
	if finish := response.Choices[0].FinishReason; finish == "tool_calls" || finish == "function_call" {
		return nil, fmt.Errorf("unsupported candidate")
	}
	m := response.Choices[0].Message
	if len(m.ToolCalls) != 0 || m.ToolCallID != "" || len(m.ThinkingBlocks) != 0 || m.ReasoningContent != "" {
		return nil, fmt.Errorf("unsupported candidate")
	}
	var text string
	if json.Unmarshal(m.Content, &text) != nil || len(text) > 2<<20 || len(reason) > 16<<10 {
		return nil, fmt.Errorf("unsupported candidate")
	}
	req, err := cloneRequest(original)
	if err != nil {
		return nil, err
	}
	feedback, _ := json.Marshal("The preceding answer failed the response checks below. Produce a corrected answer to the original request. Do not repeat the rejected answer.\n" + reason)
	content, _ := json.Marshal(text)
	req.Messages = append(req.Messages, models.Message{Role: "assistant", Content: content}, models.Message{Role: "user", Content: feedback})
	return req, nil
}

func publishAttempts(rc *models.RequestContext, retries int, debug bool) {
	l := rc.Attempts
	if l == nil || len(l.Attempts) == 0 {
		return
	}
	var total models.Usage
	var cost, credits float64
	costKnown, creditsKnown := false, false
	missing := false
	for _, a := range l.Attempts {
		missing = missing || a.UsageMissing
		if a.Usage != nil {
			total.PromptTokens += a.Usage.PromptTokens
			total.CompletionTokens += a.Usage.CompletionTokens
			total.TotalTokens += a.Usage.TotalTokens
		}
		c, _ := strconv.ParseFloat(a.Cost, 64)
		cost += c
		costKnown = costKnown || a.Cost != ""
		cr, _ := strconv.ParseFloat(a.Credits, 64)
		credits += cr
		creditsKnown = creditsKnown || a.Credits != ""
	}
	rc.AccountedUsage = &total
	// Preserve provider-specific detail for the normal single-attempt response.
	if rc.Response != nil && len(l.Attempts) > 1 {
		rc.Response.Usage = &total
	}
	rc.Metadata["provider_attempts"] = strconv.Itoa(l.Started)
	rc.Metadata["reflexion_attempts"] = strconv.Itoa(retries)
	if missing {
		rc.Metadata["usage_incomplete"] = "true"
	}
	if costKnown {
		rc.Metadata["cost"] = fmt.Sprintf("%.6f", cost)
	}
	if creditsKnown {
		rc.Metadata["credits_used"] = fmt.Sprintf("%.6f", credits)
	}
	if len(l.Attempts) > 1 || debug {
		b, _ := json.Marshal(l.Attempts)
		rc.Metadata["provider_attempt_usage"] = string(b)
	}
	if debug {
		rc.Metadata["reflexion_debug"] = "true"
	}
}
