package server

import (
	"context"
	"log/slog"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/streaming"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func (h *Handlers) handleStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, provider providers.Provider, orgCfg *tenant.OrgConfig) {
	sseWriter := streaming.NewSSEWriter(w)
	if sseWriter == nil {
		models.WriteError(w, models.ErrInternal("streaming not supported by server"))
		return
	}

	opened, err := h.openRoutedChatStream(ctx, rc, provider, orgCfg)
	if err != nil {
		h.failChat(w, rc, h.attemptLimitError(w, rc, err))
		return
	}
	defer func() {
		if opened != nil && opened.cancel != nil {
			opened.cancel()
		}
	}()

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

	if opened == nil {
		h.failChat(w, rc, models.ErrInternal("no stream from provider"))
		return
	}
	chunks, errCh, firstChunk, streamPrefixes := opened.chunks, opened.errors, opened.first, opened.prefixes
	servingProvider, upstreamStreamCtx := opened.provider, opened.ctx

	if chunks == nil {
		h.failChat(w, rc, models.ErrInternal("no stream from provider"))
		return
	}

	// Set Prism headers after pre-plugins have run but before the streaming
	// body starts (call-order steps 18 and 19).
	h.idempotencyFinish(rc, http.StatusOK, w.Header(), nil)
	h.setAgentccHeadersForProvider(w, rc, servingProvider)

	// Set up streaming guardrail checker if enabled.
	var streamChecker *guardrails.StreamGuardrailChecker
	if h.streamGuardrailCfg.Enabled && h.guardrailEngine != nil && h.guardrailEngine.PostCount() > 0 {
		// The rules run under the policy the guardrail plugin runs them
		// under: the organization's with the key's overrides on top
		// (guardrails.RulePolicy), and the request's X-Guardrail-Policy only
		// when its key has allow_policy_override
		// (guardrails.PermittedRequestPolicy). So the checker blocks, and the
		// stream is held, for exactly the rules the plugin would block.
		streamChecker = guardrails.NewStreamChecker(
			h.guardrailEngine, h.streamGuardrailCfg,
			guardrails.RulePolicy(h.tenantStore, h.policyStore, rc.Metadata),
			guardrails.PermittedRequestPolicy(rc.Metadata), rc.Request, rc.Metadata,
		)
	}

	// A stream that a blocking check on the answer reads is held until the
	// checks have passed the whole answer (heldStream): the client gets its
	// chunks, as they came, only then, and none of a blocked answer.
	checkerBlocks := streamChecker.Blocks()
	var held *heldStream
	if chatStreamHolds(rc, streamChecker, checkerBlocks) {
		if held = newHeldStream(w); held != nil {
			sseWriter = streaming.NewSSEWriter(held)
		}
	}
	sseWriter.WriteHeaders()

	// Create a cancellable context for stream guardrail checks.
	// Cancelling this ensures spawned guardrail goroutines are stopped on exit.
	streamCtx, streamCancel := context.WithCancel(ctx)
	defer streamCancel()
	// checkerCtx is the context the streaming checker's checks run on. On a
	// held stream it is one the request's end does not cancel (as
	// checksContext gives the post-plugins): a check cut off by the request's
	// deadline, or a client gone, fails, and under guardrails.fail_open a
	// failed check would pass text the client then gets unread. Each check
	// keeps its own timeout.
	checkerCtx := streamCtx
	if held != nil {
		checkerCtx = context.WithoutCancel(ctx)
	}

	// Track the last usage from stream chunks so we can populate rc.Response
	// after the stream completes (providers send usage in the final chunk).
	var lastUsage *models.Usage
	var streamID string
	var streamCreated int64
	var streamed streamedOutput
	// Freeze the owning attempt before consuming its terminal/error events.
	streamAttemptID := latestStreamAttempt(rc)

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
	if held != nil {
		capture.holdAll()
	}
	// heldErr is the refusal of a held stream whose whole answer the checks
	// could not read (refuseUnreadHeldChat), set by finalizeStream.
	var heldErr *models.APIError

	// finalizeStream populates rc.Response with accumulated usage and runs
	// post-plugins (cost, credits, logging, otel, prometheus). Must be called
	// before every return from the streaming loop. When detach is true, uses
	// a background context so post-plugins run even after client disconnect.
	// It returns the stream's final chunk (empty choices, the billed usage,
	// the metadata when asked for), or nil when the client gets none: it
	// asked for neither usage nor metadata (wantsFinalStreamChunk), or the
	// request's context ended (detach). usage is the usage the client sees,
	// the one billed: the upstream's, or a managed or budgeted key's stream's
	// estimate when the upstream sent none (nil when neither is known).
	finalizeStream := func(detach bool) (final *models.StreamChunk, usage *models.Usage) {
		bindStreamEvidence(rc, streamAttemptID, streamed.generation, lastUsage)
		metered := lastUsage // nil is OK — means provider didn't send usage
		if metered.NeedsTokenEstimate() {
			metered = billing.CompleteUsage(metered, estimatedStreamUsage(rc, &streamed, h.keyBudgeted(rc)))
		}
		if metered != nil && metered.TotalTokens == 0 {
			// Split reports may measure both dimensions without sending a
			// total. Match Messages: settle and return their sum, without
			// mutating the provider's shared usage object.
			usage := *metered
			usage.TotalTokens = usage.PromptTokens + usage.CompletionTokens
			metered = &usage
		}
		if metered != nil && metered.Estimated {
			rc.Metadata["usage_estimated"] = "true"
		}
		rc.Response = &models.ChatCompletionResponse{
			ID:               streamID,
			Object:           "chat.completion",
			Created:          streamCreated,
			Model:            rc.ResolvedModel,
			Usage:            metered,
			UpstreamProvider: streamed.provider,
		}
		capture.applyTo(rc.Response)
		heldErr = refuseUnreadHeldChat(rc, held, capture)
		h.engine.RunPostPlugins(checksContext(ctx, held, detach), rc)
		h.recordGenerationFor(rc)
		if detach {
			return nil, billedUsage(rc, metered)
		}
		if wantsFinalStreamChunk(rc) {
			final = h.buildStreamingMetadataChunk(rc, streamID, streamCreated, metered)
		}
		return final, billedUsage(rc, metered)
	}

	// For a client that did not ask for usage, each choice's finish chunk
	// waits for the upstream's end and goes out with the billed usage
	// (finishHold). send writes every other chunk as it comes.
	hold := newFinishHold(rc)
	send := func(chunk models.StreamChunk) error {
		release, held := hold.take(chunk)
		for _, c := range release {
			if err := sseWriter.WriteChunk(c); err != nil {
				return err
			}
		}
		if held {
			return nil
		}
		return sseWriter.WriteChunk(chunk)
	}
	writeHeld := func(usage *models.Usage) {
		for _, c := range hold.drain(usage) {
			if err := sseWriter.WriteChunk(c); err != nil {
				slog.Warn("error writing held stream finish chunk",
					"request_id", rc.RequestID,
					"error", err,
				)
				return
			}
		}
	}
	// heldRefusal is the error a held stream ends with instead of its
	// answer, once its post-plugins have run, or nil when the answer goes
	// out: blockErr (the streaming checker's block), heldErr, or the error
	// of an output check that withheld the answer.
	heldRefusal := func(blockErr *models.APIError) *models.APIError {
		switch {
		case blockErr != nil:
			return blockErr
		case heldErr != nil:
			return heldErr
		}
		return withheldAnswerError(rc)
	}
	// refuseHeld ends a held stream that is refused: none of its answer goes
	// out (what it holds and the finish chunks finishHold keeps are
	// dropped), only apiErr's error event.
	refuseHeld := func(apiErr *models.APIError) {
		held.discard()
		hold.drain(nil)
		sseWriter.WriteError(apiErr)
	}
	// checkHeldTail runs the streaming checker's final check on a held
	// stream that the checker holds and that ends before the provider's
	// stream does (an upstream error, the deadline, a client gone). The
	// checker checks every check_interval characters, so the text after its
	// last check has not been read yet; released unread, it would hand the
	// client a blocked answer's tail. It returns the checker's block
	// (markStreamBlocked, so it is called before finalizeStream runs the
	// post-plugins), or nil when the checker passes the whole of what the
	// stream read, or the stream is not held by the checker. A disclaimer is
	// not added to a stream that failed.
	checkHeldTail := func() *models.APIError {
		if held == nil || !checkerBlocks {
			return nil
		}
		if res := streamChecker.Finish(checkerCtx); res.Blocked {
			return markStreamBlocked(rc, res.Message)
		}
		return nil
	}
	// failStream ends the stream with apiErr's error event and no [DONE]
	// (an upstream error, the deadline). A held finish chunk goes out
	// first, where it went before, with the billed usage, so the
	// post-plugins then run before the error event; with nothing held the
	// error event goes out first, as before. A held stream is checked
	// first, the streaming checker's final check and then its post-plugins:
	// what it holds goes out before the error event, or, when the checks
	// refuse it, only their error event.
	failStream := func(apiErr *models.APIError) {
		// The error channel can win a select race against its queued evidence
		// chunk. The adapter carries the same evidence on the error as well.
		if apiErr.BillingEvidence != nil {
			streamed.generation.Observe(apiErr.BillingEvidence.Generation)
			lastUsage = models.MergeUsage(lastUsage, apiErr.BillingEvidence.Usage)
			if provider := apiErr.BillingEvidence.Generation.Provider; provider != "" {
				streamed.provider = provider
			}
		}
		if held != nil {
			blockErr := checkHeldTail()
			_, usage := finalizeStream(false)
			if refusal := heldRefusal(blockErr); refusal != nil {
				refuseHeld(refusal)
				return
			}
			held.release()
			writeHeld(usage)
			sseWriter.WriteError(apiErr)
			return
		}
		if !hold.pending() {
			sseWriter.WriteError(apiErr)
			finalizeStream(false)
			return
		}
		_, usage := finalizeStream(false)
		writeHeld(usage)
		sseWriter.WriteError(apiErr)
	}
	// stopStream ends the stream the streaming checker stopped mid-stream,
	// with its error event (markStreamBlocked) and no [DONE]: as failStream
	// does, except that a held stream sends none of its answer.
	stopStream := func(apiErr *models.APIError) {
		if held == nil {
			failStream(apiErr)
			return
		}
		finalizeStream(false)
		refuseHeld(apiErr)
	}

	var terminalErr *models.APIError
	for {
		// After observing a terminal error, consume queued chunks before
		// settlement. Their identity and usage precede that error upstream.
		// Never wait for more data once the ready queue is exhausted.
		if firstChunk == nil && terminalErr != nil {
			select {
			case chunk, ok := <-chunks:
				if ok {
					firstChunk = &chunk
				}
			default:
			}
			if firstChunk == nil {
				failStream(terminalErr)
				return
			}
		}
		if firstChunk != nil {
			chunk := *firstChunk
			if len(streamPrefixes) > 0 {
				chunk, streamPrefixes = streamPrefixes[0], streamPrefixes[1:]
			} else {
				firstChunk = nil
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
				lastUsage = models.MergeUsage(lastUsage, chunk.Usage)
				chunk.Usage = nil // metered, never forwarded (see the loop below)
			}
			capture.observe(chunk)
			streamed.observe(chunk)

			if streamChecker != nil {
				if res := streamChecker.ProcessChunk(checkerCtx, chunk); res.Blocked {
					stopStream(markStreamBlocked(rc, res.Message))
					return
				}
			}

			if !clientGetsStreamChunk(chunk) {
				continue
			}
			if err := send(chunk); err != nil {
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
				// I3: the deadline cut the stream; the provider closed it
				// without an error. Never a [DONE] for a cut answer.
				failStream(streamCutError(upstreamStreamCtx, rc))
				return
			}
			if !ok {
				// Adapters queue the terminal error before closing chunks. A
				// ready close must not turn that error into a successful [DONE].
				select {
				case err := <-errCh:
					if err != nil {
						apiErr, isAPI := err.(*models.APIError)
						if !isAPI {
							apiErr = models.ErrInternal(err.Error())
						}
						failStream(apiErr)
						return
					}
				default:
				}
				// Stream complete — run final guardrail check. answered is
				// set once the checker ended the stream with its error or
				// its disclaimer. A block's error event follows the held
				// finish chunk, when there is one, as it did before.
				answered := false
				var blockErr *models.APIError
				if streamChecker != nil {
					if res := streamChecker.Finish(checkerCtx); res.Blocked {
						blockErr = markStreamBlocked(rc, res.Message)
						if held == nil && !hold.pending() {
							sseWriter.WriteError(blockErr)
							blockErr = nil
						}
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
				metadataChunk, usage := finalizeStream(false)
				if held != nil {
					// The checks have read the whole answer. A refused one
					// ends with only the guardrail's error event, then the
					// usage chunk when the client asked for usage (C79), then
					// [DONE]; a passing one goes out as it came.
					if refusal := heldRefusal(blockErr); refusal != nil {
						refuseHeld(refusal)
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
					held.release()
				}
				writeHeld(usage)
				if blockErr != nil {
					sseWriter.WriteError(blockErr)
				}
				if !answered {
					// The output checks the post-plugins ran on the assembled
					// answer (a static rule, or an org's check) refused it:
					// the stream ends with that guardrail's error. This
					// stream was not held, so no check that refuses it was
					// expected to block.
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
			// A provider's usage is metered here and never forwarded, on
			// whatever chunk it rides: its usage chunk, its finish chunk (the
			// Anthropic adapter), Bedrock Converse's metadata chunk (one
			// empty choice and the usage), or every chunk (Gemini's
			// cumulative usageMetadata). The client gets the billed usage
			// once, from the gateway: on the final chunk when it asked
			// (wantsFinalStreamChunk), else on the held finish chunk.
			if chunk.Usage != nil {
				lastUsage = models.MergeUsage(lastUsage, chunk.Usage)
				chunk.Usage = nil
			}
			capture.observe(chunk)
			streamed.observe(chunk)

			// Run streaming guardrail check.
			if streamChecker != nil {
				if res := streamChecker.ProcessChunk(checkerCtx, chunk); res.Blocked {
					stopStream(markStreamBlocked(rc, res.Message))
					return
				}
			}

			// A chunk with no choice (the upstream's usage chunk) is metered
			// above and not sent.
			if !clientGetsStreamChunk(chunk) {
				continue
			}
			if err := send(chunk); err != nil {
				slog.Warn("error writing stream chunk",
					"request_id", rc.RequestID,
					"error", err,
				)
				finalizeStream(false)
				return
			}

		case err, ok := <-errCh:
			if !ok {
				errCh = nil
				continue
			}
			if err != nil {
				apiErr, isAPI := err.(*models.APIError)
				if !isAPI {
					apiErr = models.ErrInternal(err.Error())
				}
				terminalErr = apiErr
				errCh = nil
			}

		case <-ctx.Done():
			// The request's context ended before the upstream's stream did:
			// the client disconnected, or a server-side timeout fired while
			// it may still be reading (x-agentcc-request-timeout,
			// x-agentcc-timeout, the org's or the model's timeout, the
			// ceiling). streamCancel() in defer will stop guardrail
			// goroutines. Drain remaining chunks to unblock the provider
			// goroutine.
			go func() {
				for range chunks {
				}
			}()
			// Run post-plugins synchronously with detached context to avoid
			// data race with defer rc.Release() in the caller. A held finish
			// chunk is then written, best effort, with the billed usage: a
			// client still reading gets the finish_reason it got live before
			// the hold; for a client that is gone the write fails. The stream
			// ends there, with neither [DONE] nor an error event, as before,
			// unless it is held and the checks refuse it: then it is only
			// their error event (written best effort too). A held stream gets
			// the streaming checker's final check first (checkHeldTail).
			blockErr := checkHeldTail()
			_, usage := finalizeStream(true)
			if held != nil {
				if refusal := heldRefusal(blockErr); refusal != nil {
					refuseHeld(refusal)
					return
				}
				held.release()
			}
			writeHeld(usage)
			return
		}
	}
}

// chatStreamHolds says whether a chat stream is held (heldStream): an
// organization's check on the answer would block it, a rule in the config
// file at the post stage would (and no streaming checker reads the stream
// for the rules instead), or the streaming checker stops streams
// (checkerBlocks, checker.Blocks()). In disclaimer mode the checker does
// not block, and the stream is not held for its rules.
func chatStreamHolds(rc *models.RequestContext, checker *guardrails.StreamGuardrailChecker, checkerBlocks bool) bool {
	if rc.Flags.AnswerBlockers&models.AnswerReadByOrg != 0 || checkerBlocks {
		return true
	}
	return checker == nil && rc.Flags.AnswerBlockers&models.AnswerReadByRules != 0
}

// refuseUnreadHeldChat is called right before the post-plugins of a held
// chat stream. When the checks cannot read its whole answer (it went over
// maxHeldStreamBytes, or the assembly dropped a choice), the guardrail
// plugin refuses it if one of its checks would block (heldStream.
// beforeChecks), and nil is returned; when only the streaming checker held
// it, it is refused here, as the checker's own block is, and the refusal is
// returned. nil when the checks can read it, or the stream is not held.
func refuseUnreadHeldChat(rc *models.RequestContext, held *heldStream, capture *streamCapture) *models.APIError {
	if held == nil || (!held.over && capture.complete()) {
		return nil
	}
	if rc.Flags.AnswerBlockers != 0 {
		held.beforeChecks(rc, capture.complete())
		return nil
	}
	reason := answerPartlyRead
	if held.over {
		reason = answerOverHoldBound
	}
	apiErr := models.ErrGuardrailBlocked("answer_unavailable",
		"Request blocked by guardrail: the streaming guardrail checks the model's answer, and "+reason)
	rc.Flags.GuardrailTriggered = true
	rc.Flags.AnswerRefused = true
	rc.SetMetadata("guardrail_action", "blocked")
	rc.SetMetadata("answer_unavailable", reason)
	rc.AddError(apiErr)
	return apiErr
}

// markStreamBlocked records that the streaming guardrail checker stopped the
// stream, so its log entry carries that status (403) and the guardrail
// action, and returns the error event the client gets (stream_blocked).
// Called before finalizeStream runs the post-plugins.
func markStreamBlocked(rc *models.RequestContext, message string) *models.APIError {
	apiErr := models.ErrGuardrailBlocked("stream_blocked", message)
	rc.Flags.GuardrailTriggered = true
	rc.Flags.AnswerRefused = true
	rc.SetMetadata("guardrail_action", "blocked")
	rc.AddError(apiErr)
	return apiErr
}

func waitForFirstStreamChunk(ctx context.Context, chunks <-chan models.StreamChunk, errCh <-chan error) (*models.StreamChunk, error) {
	first, _, err := awaitCanonicalStreamStart(ctx, chunks, errCh)
	return first, err
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

// streamedOutput counts what a stream has sent the client, so a managed or
// budgeted key's stream that ends without the upstream's usage chunk can
// still be billed, retaining the upstream provider identity.
type streamedOutput struct {
	chars      int
	provider   string
	generation models.GenerationEvidence
}

func (o *streamedOutput) observe(chunk models.StreamChunk) {
	o.generation.Observe(models.GenerationFromResponse(&chunk))
	if chunk.UpstreamProvider != "" {
		o.provider = chunk.UpstreamProvider
	}
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

// estimatedStreamUsage bills a managed stream that ended before the
// upstream reported usage — the client disconnected, the upstream failed, or
// a guardrail cut it off. The upstream has still charged for the prompt and
// for what it generated, so the request is billed on an estimate (prompt
// tokens from the messages, output at ~4 characters a token) rather than not
// at all. A budgeted key's stream on the org's own provider (BYOK) is
// estimated the same way (CN22c R-CN22c-3), so the key's budget moves when a
// client drops each stream before its usage chunk. Other streams keep nil
// usage, as before.
func estimatedStreamUsage(rc *models.RequestContext, o *streamedOutput, budgetedKey bool) *models.Usage {
	if (rc.Metadata["billing_mode"] != billingManaged && !budgetedKey) || rc.Request == nil {
		return nil
	}
	return billing.EstimateUsage(rc.Request, o.chars)
}

// estimatedManagedStreamUsage preserves the managed-only callers' contract.
func estimatedManagedStreamUsage(rc *models.RequestContext, o *streamedOutput) *models.Usage {
	return estimatedStreamUsage(rc, o, false)
}
