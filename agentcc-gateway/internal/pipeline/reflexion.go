package pipeline

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
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
				// Admission uses the caller's logical model, as on the first attempt.
				r := p.ProcessRequest(ctx, rc)
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
	servingModel := rc.ResolvedModel
	if servingModel == "" && rc.Request != nil {
		servingModel = rc.Request.Model
	}
	// Preserve rc.Model for pricing fallback and budget identity, including when
	// routing rewrote Request.Model. The serving model is the preferred price;
	// a response that omits its model is priced by the upstream request model.
	// Settlement must survive caller cancellation. Never rerun admission here.
	resolved := rc.ResolvedModel
	rc.ResolvedModel = servingModel
	e.accountAttempt(context.WithoutCancel(ctx), rc)
	rc.ResolvedModel = resolved
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
	for _, p := range e.plugins {
		if v, ok := p.(ResponseValidator); ok && v.ReflectionDebug(rc) {
			debug = true
			break
		}
	}
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
		if err == nil && !rc.IsStream {
			err = candidateResponseError(rc.Response)
		}
		e.EndProviderAttempt(ctx, rc)
		rc.Request = original
		if err != nil {
			if pendingReflection {
				err = sanitizeReflectionError(err)
			}
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

func candidateResponseError(response *models.ChatCompletionResponse) error {
	if response == nil || len(response.Choices) == 0 {
		return models.ErrUpstreamProvider(http.StatusBadGateway, "upstream response has no choices")
	}
	if raw, ok := response.Extra["error"]; ok && !bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
		return models.ErrUpstreamProvider(http.StatusBadGateway, "upstream response returned an error")
	}
	for _, choice := range response.Choices {
		if choice.FinishReason == "error" {
			return models.ErrUpstreamProvider(http.StatusBadGateway, "upstream response returned an error")
		}
	}
	return nil
}

// Upstream validation errors can echo the private answer and evaluator feedback.
func sanitizeReflectionError(err error) error {
	var admission *models.AdmissionError
	if errors.As(err, &admission) || err == context.Canceled || err == context.DeadlineExceeded {
		return err
	}
	const message = "Provider request failed during response repair"
	var apiErr *models.APIError
	if errors.As(err, &apiErr) {
		// Upstream type/code/param are arbitrary text too. Derive identifiers
		// from the status using only gateway constructors, never upstream fields.
		safe := models.ErrUpstreamProvider(apiErr.Status, message)
		switch apiErr.Status {
		case http.StatusBadRequest:
			safe = models.ErrBadRequest(safe.Code, message)
		case http.StatusUnauthorized:
			safe = models.ErrUnauthorized(message)
		case http.StatusForbidden:
			safe = models.ErrForbidden(message)
		case http.StatusNotFound:
			safe = models.ErrNotFound(safe.Code, message)
		case http.StatusRequestTimeout:
			safe = models.ErrRequestTimeout(message)
		case http.StatusTooManyRequests:
			safe = models.ErrTooManyRequests(message)
		case http.StatusInternalServerError:
			safe = models.ErrInternal(message)
		case http.StatusServiceUnavailable:
			safe = models.ErrServiceUnavailable(message)
		case http.StatusGatewayTimeout:
			safe = models.ErrGatewayTimeout(message)
		}
		safe.Status = apiErr.Status
		return safe
	}
	return models.ErrUpstreamProvider(http.StatusBadGateway, message)
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
	feedback, _ := json.Marshal("The preceding answer failed the response checks below. Produce a corrected answer to the original request. Comply with the intent of these checks. Do not use alternate spellings, lookalike or invisible characters, spacing, encodings, or other obfuscation to bypass them. If the original request cannot be answered within these checks, say so briefly instead. Do not repeat the rejected answer.\n" + reason)
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
