package server

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/streaming"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/futureagi/agentcc-gateway/internal/tokenizer"
)

func (h *Handlers) handleStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, provider providers.Provider, orgCfg *tenant.OrgConfig) {
	sseWriter := streaming.NewSSEWriter(w)
	if sseWriter == nil {
		models.WriteError(w, models.ErrInternal("streaming not supported by server"))
		return
	}

	var chunks <-chan models.StreamChunk
	var errCh <-chan error
	var firstChunk *models.StreamChunk
	var servingProvider providers.Provider
	var upstreamStreamCancel context.CancelFunc
	var upstreamStreamCtx context.Context // the serving attempt's upstream call (streamCutError)
	defer func() {
		if upstreamStreamCancel != nil {
			upstreamStreamCancel()
		}
	}()
	effectiveFailover := h.effectiveFailover(orgCfg)

	// openStream makes one stream attempt (call-order steps 14 and 15): it
	// ends when the first chunk arrives or the attempt fails. prev is the
	// previous attempt's error, nil for the first.
	//
	// callCtx bounds an attempt only until its first chunk. The stream itself
	// lives on the request context: failover cancels callCtx as soon as the
	// attempt returns, which would otherwise end the stream after one chunk.
	openStream := func(callCtx context.Context, callProvider providers.Provider, providerID string, kind models.AttemptKind, prev error) error {
		beginCtx, finish, refusal := h.startAttempt(callCtx, rc, kind, h.attemptReason(prev), providerID, rc.Request.Model, credentialType(rc))
		if refusal != "" { // an attempt past the deadline is never dispatched
			return routing.SkipHop(prev, refusal)
		}
		h.rewriteUpstreamRouting(rc, callProvider, rc.Request)
		attemptCtx, attemptCancel := streamAttemptContext(ctx, rc) // the deadline cuts a stream that started
		candidateChunks, candidateErrCh := callProvider.StreamChatCompletion(attemptCtx, rc.Request)
		if candidateChunks == nil {
			attemptCancel()
			err := models.ErrUpstreamProvider(http.StatusBadGateway, "upstream provider returned no stream")
			finish(nil, nil, err)
			return err
		}

		chunk, err := waitForFirstStreamChunk(beginCtx, candidateChunks, candidateErrCh)
		finish(nil, nil, err)
		if err != nil {
			cancelAndDrainStream(attemptCancel, candidateChunks)
			return err
		}

		if upstreamStreamCancel != nil {
			cancelAndDrainStream(upstreamStreamCancel, chunks)
		}
		upstreamStreamCancel = attemptCancel
		upstreamStreamCtx = attemptCtx
		servingProvider = callProvider
		chunks = candidateChunks
		errCh = candidateErrCh
		firstChunk = chunk
		return nil
	}

	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		ctx, release := withRequestDeadline(ctx, rc) // a retry backoff ends at the request deadline
		defer release()
		originalModel := rc.Request.Model
		orgID := rc.Metadata["org_id"]
		h.planHedge(rc, orgCfg)

		tryModelFallbacks := func(firstErr error) error {
			if !shouldTryModelFallback(effectiveFailover, rc, firstErr) {
				return firstErr
			}

			var modelChain []string
			if len(rc.OrgModelFallbacks) > 0 {
				// Keyed by the routed model: resolveManaged may have rewritten
				// rc.Request.Model to the upstream's ID.
				modelChain = rc.OrgModelFallbacks[rc.Model]
				if len(modelChain) == 0 {
					modelChain = rc.OrgModelFallbacks[fallbackChainModel(rc, originalModel)]
				}
			} else if mf := h.modelFallbacks.Load(); mf != nil {
				// A chain is configured under the name the caller asked for
				// (rc.Model): a target's model_override rewrites the model sent
				// upstream, which no chain is keyed by. Else by the model the
				// request was routed with, as the org path reads it.
				modelChain = mf.GetChain(rc.Model)
				if len(modelChain) == 0 {
					modelChain = mf.GetChain(fallbackChainModel(rc, originalModel))
				}
			}

			lastErr := firstErr
			for _, fbModel := range modelChain {
				fallbackModel := fbModel
				fbProvider, fbProviderID, resolvedModel, ok := h.resolveFallbackProvider(ctx, orgID, orgCfg, rc, fallbackModel)
				if !ok || !h.admitFallbackHop(rc, orgCfg, fbProviderID, resolvedModel) {
					continue
				}

				rc.Provider = fbProviderID
				rc.Request.Model = resolvedModel
				err := openStream(ctx, fbProvider, fbProviderID, fallbackKind(rc), lastErr)
				if err == nil {
					rc.Flags.FallbackUsed = true
					rc.Metadata["original_model"] = originalModel
					rc.Metadata["fallback_model"] = resolvedModel
					return nil
				}
				lastErr = err
			}

			rc.Request.Model = originalModel
			return firstErr
		}

		// The failover chatFailover chose: with of, a customer key's over its
		// org's own providers (target ids map to the org's instances).
		if fo, of, key := h.chatFailover(ctx, rc, orgCfg, effectiveFailover); fo != nil {
			hops := failoverHops{routed: of.routedProvider(rc)}
			result, err := fo.Execute(ctx, key, func(callCtx context.Context, providerID string, modelOverride string) error {
				model := originalModel
				if modelOverride != "" {
					model = modelOverride
				}
				var p providers.Provider
				targetID := providerID
				if of != nil {
					var err error
					if p, providerID, err = of.target(h, targetID, model); err != nil {
						return err
					}
				} else {
					var ok bool
					if p, ok = h.registry.GetProvider(providerID); !ok {
						return models.ErrUpstreamProvider(http.StatusBadGateway, fmt.Sprintf("provider %q not found", providerID))
					}
					if shouldApplyOrgProviderOverride(rc) {
						p = h.applyOrgProviderOverride(orgID, orgCfg, providerID, p)
					}
				}
				kind, admitted := hops.next(h, rc, orgCfg, providerID, model)
				if !admitted {
					return hops.lastErr
				}
				rc.Request.Model = model
				var started time.Time
				if of != nil {
					started = time.Now()
				}
				err := openStream(callCtx, p, providerID, kind, hops.lastErr)
				hops.lastErr = err
				if of != nil {
					of.record(targetID, started, err)
				}
				return err
			})
			if err != nil {
				return tryModelFallbacks(err)
			}
			if result != nil {
				rc.Provider = of.recorded(result.ProviderID)
				if result.ModelOverride != "" {
					rc.Request.Model = result.ModelOverride
				}
				if result.FallbackUsed {
					rc.Flags.FallbackUsed = true
					rc.Metadata["original_provider"] = of.recorded(result.OriginalProvider)
					rc.Metadata["fallback_provider"] = of.recorded(result.ProviderID)
				}
				if result.StrategyName != "" {
					rc.Metadata["routing_strategy"] = result.StrategyName
				}
				if of != nil {
					of.stampStrategy(rc)
				}
			}
			return nil
		}

		if err := openStream(ctx, provider, rc.Provider, models.AttemptPrimary, nil); err != nil {
			return tryModelFallbacks(err)
		}
		return nil
	}

	// Run pre-plugins before opening the upstream stream.
	if err := h.engine.Process(ctx, rc, providerCall); err != nil {
		h.failChat(w, rc, h.attemptLimitError(w, rc, err)) // the deadline answers as on the non-stream path
		return
	}

	// A pre-plugin answered (a cache hit) and an output check withheld it.
	if apiErr := withheldAnswerError(rc); apiErr != nil {
		h.failChat(w, rc, apiErr)
		return
	}

	if rc.Flags.ShortCircuited && rc.Response != nil {
		// Short-circuited (e.g., cache hit) - return as JSON even if stream was requested.
		h.writeChatResponse(w, rc, false, nil)
		return
	}

	if chunks == nil {
		h.failChat(w, rc, models.ErrInternal("no stream from provider"))
		return
	}

	// Set Prism headers after pre-plugins have run but before the streaming
	// body starts (call-order steps 18 and 19).
	h.idempotencyFinish(rc, http.StatusOK, w.Header(), nil)
	h.setAgentccHeadersForProvider(w, rc, servingProvider)
	sseWriter.WriteHeaders()

	// Create a cancellable context for stream guardrail checks.
	// Cancelling this ensures spawned guardrail goroutines are stopped on exit.
	streamCtx, streamCancel := context.WithCancel(ctx)
	defer streamCancel()

	// Track the last usage from stream chunks so we can populate rc.Response
	// after the stream completes (providers send usage in the final chunk).
	var lastUsage *models.Usage
	var streamID string
	var streamCreated int64
	var streamed streamedOutput

	// Set up streaming guardrail checker if enabled.
	var streamChecker *guardrails.StreamGuardrailChecker
	if h.streamGuardrailCfg.Enabled && h.guardrailEngine != nil && h.guardrailEngine.PostCount() > 0 {
		var keyPolicy *policy.Policy
		if keyID := rc.Metadata["auth_key_id"]; keyID != "" && h.policyStore != nil {
			keyPolicy = h.policyStore.Get(keyID)
		}
		// The request's X-Guardrail-Policy only when its key has
		// allow_policy_override (guardrails.PermittedRequestPolicy).
		streamChecker = guardrails.NewStreamChecker(
			h.guardrailEngine, h.streamGuardrailCfg,
			keyPolicy, guardrails.PermittedRequestPolicy(rc.Metadata), rc.Request, rc.Metadata,
		)
	}

	// The answer the stream sends, assembled for the request log and for an
	// output check that reads it at the stream's end, whether or not the log
	// keeps bodies. The streaming checker reads the stream itself for the
	// config file's rules, so with it on only an organization's check needs
	// the assembly.
	readers := rc.Flags.AnswerReaders
	if streamChecker != nil {
		readers &^= models.AnswerReadByRules
	}
	capture := newStreamCapture(h.captureStreamContent || readers != 0)

	// finalizeStream populates rc.Response with accumulated usage and runs
	// post-plugins (cost, credits, logging, otel, prometheus). Must be called
	// before every return from the streaming loop. When detach is true, uses
	// a background context so post-plugins run even after client disconnect.
	finalizeStream := func(detach bool) *models.StreamChunk {
		rc.Response = &models.ChatCompletionResponse{
			ID:      streamID,
			Object:  "chat.completion",
			Created: streamCreated,
			Model:   rc.ResolvedModel,
			Usage:   lastUsage, // nil is OK — means provider didn't send usage
		}
		if lastUsage == nil {
			rc.Response.Usage = estimatedManagedStreamUsage(rc, &streamed)
		}
		capture.applyTo(rc.Response)
		pluginCtx := ctx
		if detach {
			pluginCtx = context.Background()
		}
		h.engine.RunPostPlugins(pluginCtx, rc)
		h.recordGenerationFor(rc)
		if detach {
			return nil
		}
		return h.buildStreamingMetadataChunk(rc, streamID, streamCreated, lastUsage)
	}

	for {
		if firstChunk != nil {
			chunk := *firstChunk
			firstChunk = nil
			if streamID == "" && chunk.ID != "" {
				streamID = chunk.ID
			}
			if streamCreated == 0 && chunk.Created != 0 {
				streamCreated = chunk.Created
			}
			if len(chunk.Choices) > 0 && rc.ResolvedModel == "" {
				rc.ResolvedModel = chunk.Model
			}
			if chunk.Usage != nil {
				lastUsage = chunk.Usage
				chunk.Usage = clientStreamUsage(rc, chunk.Usage)
			}
			capture.observe(chunk)
			streamed.observe(chunk)

			if streamChecker != nil {
				if res := streamChecker.ProcessChunk(streamCtx, chunk); res.Blocked {
					blockStream(sseWriter, rc, res.Message)
					finalizeStream(false)
					return
				}
			}

			if err := sseWriter.WriteChunk(chunk); err != nil {
				slog.Warn("error writing stream chunk",
					"request_id", rc.RequestID,
					"error", err,
				)
				finalizeStream(false)
				return
			}
			continue
		}

		select {
		case chunk, ok := <-chunks:
			if !ok && streamCutError(upstreamStreamCtx, rc) != nil {
				// The deadline cut the stream; the provider closed it
				// without an error. Never a [DONE] for a cut answer.
				sseWriter.WriteError(streamCutError(upstreamStreamCtx, rc))
				finalizeStream(false)
				return
			}
			if !ok {
				// Stream complete — run final guardrail check. answered is
				// set once the checker ended the stream with its error or
				// its disclaimer.
				answered := false
				if streamChecker != nil {
					if res := streamChecker.Finish(streamCtx); res.Blocked {
						blockStream(sseWriter, rc, res.Message)
						answered = true
					} else if res.Disclaimer != "" {
						disclaimer := res.Disclaimer
						disclaimerChunk := models.StreamChunk{
							Choices: []models.StreamChoice{
								{Delta: models.Delta{Content: &disclaimer}},
							},
						}
						sseWriter.WriteChunk(disclaimerChunk)
						answered = true
					}
				}
				metadataChunk := finalizeStream(false)
				if !answered {
					// The output checks the post-plugins ran on the assembled
					// answer (a static rule, or an org's check) refused it:
					// the stream ends with that guardrail's error, after the
					// text already sent.
					if apiErr := withheldAnswerError(rc); apiErr != nil {
						sseWriter.WriteError(apiErr)
					}
				}
				if metadataChunk != nil {
					if err := sseWriter.WriteChunk(*metadataChunk); err != nil {
						slog.Warn("error writing final stream metadata chunk",
							"request_id", rc.RequestID,
							"error", err,
						)
					}
				}
				sseWriter.WriteDone()
				return
			}
			if streamID == "" && chunk.ID != "" {
				streamID = chunk.ID
			}
			if streamCreated == 0 && chunk.Created != 0 {
				streamCreated = chunk.Created
			}
			if len(chunk.Choices) > 0 && rc.ResolvedModel == "" {
				rc.ResolvedModel = chunk.Model
			}
			if chunk.Usage != nil {
				lastUsage = chunk.Usage
				chunk.Usage = clientStreamUsage(rc, chunk.Usage)
			}
			capture.observe(chunk)
			streamed.observe(chunk)

			// Run streaming guardrail check.
			if streamChecker != nil {
				if res := streamChecker.ProcessChunk(streamCtx, chunk); res.Blocked {
					blockStream(sseWriter, rc, res.Message)
					finalizeStream(false)
					return
				}
			}

			if err := sseWriter.WriteChunk(chunk); err != nil {
				slog.Warn("error writing stream chunk",
					"request_id", rc.RequestID,
					"error", err,
				)
				finalizeStream(false)
				return
			}

		case err, ok := <-errCh:
			if ok && err != nil {
				if apiErr, isAPI := err.(*models.APIError); isAPI {
					sseWriter.WriteError(apiErr)
				} else {
					sseWriter.WriteError(models.ErrInternal(err.Error()))
				}
				finalizeStream(false)
				return
			}

		case <-ctx.Done():
			// Client disconnected — streamCancel() in defer will stop guardrail goroutines.
			// Drain remaining chunks to unblock the provider goroutine.
			go func() {
				for range chunks {
				}
			}()
			// Run post-plugins synchronously with detached context to avoid
			// data race with defer rc.Release() in the caller.
			finalizeStream(true)
			return
		}
	}
}

// blockStream ends a stream the streaming guardrail checker stopped: the
// client gets the guardrail's error event (stream_blocked), and the request
// records the block, so its log entry carries that status (403) and the
// guardrail action. Called before finalizeStream runs the post-plugins.
func blockStream(sse *streaming.SSEWriter, rc *models.RequestContext, message string) {
	apiErr := models.ErrGuardrailBlocked("stream_blocked", message)
	rc.Flags.GuardrailTriggered = true
	rc.Flags.AnswerRefused = true
	rc.SetMetadata("guardrail_action", "blocked")
	rc.AddError(apiErr)
	sse.WriteError(apiErr)
}

func waitForFirstStreamChunk(ctx context.Context, chunks <-chan models.StreamChunk, errCh <-chan error) (*models.StreamChunk, error) {
	for {
		select {
		case chunk, ok := <-chunks:
			if !ok {
				return nil, models.ErrUpstreamProvider(http.StatusBadGateway, "upstream stream closed before sending any chunks")
			}
			return &chunk, nil
		case err, ok := <-errCh:
			if !ok {
				errCh = nil
				continue
			}
			if err != nil {
				return nil, err
			}
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
}

func cancelAndDrainStream(cancel context.CancelFunc, chunks <-chan models.StreamChunk) {
	if cancel != nil {
		cancel()
	}
	if chunks == nil {
		return
	}
	go func() {
		for range chunks {
		}
	}()
}

// streamedOutput counts what a stream has sent the client, so a managed
// stream that ends without the upstream's usage chunk can still be billed.
type streamedOutput struct{ chars int }

func (o *streamedOutput) observe(chunk models.StreamChunk) {
	for _, c := range chunk.Choices {
		if c.Delta.Content != nil {
			o.chars += len(*c.Delta.Content)
		}
		if c.Delta.ReasoningContent != nil {
			o.chars += len(*c.Delta.ReasoningContent)
		}
		for _, tc := range c.Delta.ToolCalls {
			if tc.Function != nil {
				o.chars += len(tc.Function.Name) + len(tc.Function.Arguments)
			}
		}
	}
}

// estimatedManagedStreamUsage bills a managed stream that ended before the
// upstream reported usage — the client disconnected, the upstream failed, or
// a guardrail cut it off. The upstream has still charged for the prompt and
// for what it generated, so the request is billed on an estimate (prompt
// tokens from the messages, output at ~4 characters a token) rather than not
// at all. Other streams keep nil usage.
func estimatedManagedStreamUsage(rc *models.RequestContext, o *streamedOutput) *models.Usage {
	if rc.Metadata["billing_mode"] != billingManaged || rc.Request == nil {
		return nil
	}
	prompt := tokenizer.EstimateTokens(rc.Request.Messages)
	completion := (o.chars + 3) / 4
	rc.Metadata["usage_estimated"] = "true"
	return &models.Usage{PromptTokens: prompt, CompletionTokens: completion, TotalTokens: prompt + completion}
}
