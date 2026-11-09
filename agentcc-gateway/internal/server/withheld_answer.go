package server

import (
	"log/slog"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A post-stage refusal (an output check's block) withholds the answer: the
// guardrail plugin drops rc.Response and records its error on rc, because
// the pipeline only logs a post-plugin's error. Every handler that serves an
// answer once the post-plugins have run reads it back with pipelineErr, and
// answers it as it answers a refusal before the provider: with the
// guardrail's status, in the surface's own format. The request log reads the
// same error, so it records the status the client got.

// withheldAnswerError is the error a post-plugin recorded when it withheld
// the answer (a guardrail's post-stage block: 403 content_blocked), or nil
// when no answer was withheld (models.WithheldAnswerError, which the a2a
// server's own pipeline path reads too). Call it only when the pipeline
// returned no error.
func withheldAnswerError(rc *models.RequestContext) *models.APIError {
	return models.WithheldAnswerError(rc)
}

// pipelineErr is the error a handler answers once Process has run: err, the
// one Process returned, or else the error of an output check that withheld
// the answer, or nil when there is an answer to serve. The result is a plain
// error so that "no error" stays a nil interface.
func pipelineErr(rc *models.RequestContext, err error) error {
	if err != nil {
		return err
	}
	if apiErr := withheldAnswerError(rc); apiErr != nil {
		return apiErr
	}
	return nil
}

// endWithheldStream ends a stream whose text has gone out but whose answer an
// output check refused once the post-plugins ran at its end: it sends the
// guardrail's error event, frame(err) in the stream's own format, as a chat
// stream does, so the client learns of the block the request log records.
// It sends nothing when no answer was withheld. Call it only after a stream
// that ended normally, once its post-plugins have run.
func endWithheldStream(w http.ResponseWriter, flusher http.Flusher, rc *models.RequestContext, frame func(*models.APIError) []byte) {
	apiErr := withheldAnswerError(rc)
	if apiErr == nil {
		return
	}
	if _, err := w.Write(frame(apiErr)); err != nil {
		slog.Warn("error writing the guardrail's stream error event", "request_id", rc.RequestID, "error", err)
		return
	}
	flusher.Flush()
}

// recordProviderHealth records the request's outcome against its provider,
// for adaptive routing and model "auto": an error when the request failed,
// a success otherwise. A guardrail's refusal of an answer it was given
// (Flags.AnswerRefused) is a success: the answer came, and counting the
// refusal would steer an org's traffic away from a healthy provider.
func (h *Handlers) recordProviderHealth(rc *models.RequestContext) {
	if h.healthMonitor == nil || rc.Provider == "" {
		return
	}
	if len(rc.Errors) > 0 && !rc.Flags.AnswerRefused {
		h.healthMonitor.RecordError(rc.Provider, rc.Errors[0])
		return
	}
	h.healthMonitor.RecordSuccess(rc.Provider)
}
