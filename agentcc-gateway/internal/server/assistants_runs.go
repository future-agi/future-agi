package server

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"maps"
	"net/http"
	"net/url"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// An Assistants run spends tokens upstream until it finishes, and its usage
// is known only then: on the stream of the call that started or continued
// it, or in the run a later poll reads. The gateway counts a finished run's
// tokens once, into the budgets and spend of the key that started it,
// whichever call sees it finish (runChargeStore hands each run back once).
//
// The run is counted on the call that sees it finish, as on dev: that call's
// own request log entry carries the run's tokens, cost and id (run_id), and
// no entry is added for the run. When another call sees it finish (a poll, a
// tool-outputs submission, another stream), the run's charge moves onto that
// call with the starting call's attribution in place of its own, and the call
// is still answered as its own (answerAsCaller).

// runWatch is the provider an Assistants call that starts, continues or
// reads a run talks to. It passes every answer through as it came, and reads
// each run object in it (observe).
type runWatch struct {
	providers.AssistantsProvider
	h  *Handlers
	rc *models.RequestContext
	// startsRun is set for a call that creates a run: the first run object
	// it relays is recorded for charging when it finishes.
	startsRun bool
	started   map[string]bool
	// caller is this call's own attribution while it carries the charge of
	// a run another call started (carryStartersCharge).
	caller *callerState
}

// callerState is a call's own attribution, put back before it answers.
type callerState struct {
	metadata                               map[string]string
	userID, model, resolvedModel, provider string
	billing                                models.BillingState
}

// watchRuns wraps ap for a call on runs.
func (h *Handlers) watchRuns(rc *models.RequestContext, ap providers.AssistantsProvider, startsRun bool) *runWatch {
	return &runWatch{AssistantsProvider: ap, h: h, rc: rc, startsRun: startsRun}
}

// ProxyAssistantsRequest relays the call and reads the run in its answer.
func (w *runWatch) ProxyAssistantsRequest(ctx context.Context, method, path string, body []byte, queryParams url.Values) ([]byte, int, http.Header, error) {
	respBody, status, headers, err := w.AssistantsProvider.ProxyAssistantsRequest(ctx, method, path, body, queryParams)
	if err == nil && status < http.StatusBadRequest {
		w.observe(ctx, respBody)
	}
	return respBody, status, headers, err
}

// StreamAssistantsRequest relays the stream and reads each run event on it
// as the relay reads it.
func (w *runWatch) StreamAssistantsRequest(ctx context.Context, method, path string, body []byte, queryParams url.Values) (io.ReadCloser, int, http.Header, error) {
	stream, status, headers, err := w.AssistantsProvider.StreamAssistantsRequest(ctx, method, path, body, queryParams)
	if err != nil || stream == nil || status >= http.StatusBadRequest {
		return stream, status, headers, err
	}
	return &runEvents{ReadCloser: stream, ctx: ctx, watch: w}, status, headers, nil
}

// observe reads one relayed object. A run this call started is recorded, so
// it can be charged when it finishes. A finished run is charged on this call
// if its pending charge is still there, with the attribution of the call that
// started it.
func (w *runWatch) observe(ctx context.Context, data []byte) {
	store := w.h.runCharges
	if store == nil {
		return
	}
	var run struct {
		ID     string                     `json:"id"`
		Object string                     `json:"object"`
		Status string                     `json:"status"`
		Model  string                     `json:"model"`
		Usage  *models.AssistantsRunUsage `json:"usage"`
	}
	if json.Unmarshal(data, &run) != nil || run.ID == "" || run.Object != "thread.run" {
		return
	}
	// The store outlives the call: a client that leaves does not cancel it.
	ctx = context.WithoutCancel(ctx)
	if w.startsRun && !w.started[run.ID] {
		if w.started == nil {
			w.started = make(map[string]bool, 1)
		}
		w.started[run.ID] = true
		store.Put(ctx, run.ID, newPendingCharge(w.rc, run.Model))
	}
	if !runFinished(run.Status) || run.Usage == nil || run.Usage.TotalTokens == 0 {
		return
	}
	if w.rc.Response != nil {
		return // this call carries a charge already; the run waits for another
	}
	charge, ok := store.Take(ctx, run.ID)
	if !ok {
		return // not started through the gateway, or counted already
	}
	model := run.Model
	if model == "" {
		model = charge.Model
	}
	usage := &models.Usage{
		PromptTokens:     run.Usage.PromptTokens,
		CompletionTokens: run.Usage.CompletionTokens,
		TotalTokens:      run.Usage.TotalTokens,
	}
	if w.started[run.ID] {
		// This call started the run: its own response plugins count it.
		if w.rc.Model == "" {
			w.rc.Model = model
		}
	} else {
		w.carryStartersCharge(charge, model)
	}
	w.rc.ResolvedModel = model
	w.rc.Metadata["run_id"] = run.ID
	w.rc.Response = &models.ChatCompletionResponse{Model: model, Usage: usage}
}

// carryStartersCharge moves the charge of a run another call started onto
// this call: the starter's attribution replaces this call's rather than
// merging over it, so this call's org, key, team or tags never apply to a run
// it did not start. This call's own holds (pendingChargeDropsPrefixes) stay,
// for its response plugins to settle. Its own attribution is kept for its
// answer (answerAsCaller).
func (w *runWatch) carryStartersCharge(charge pendingCharge, model string) {
	rc := w.rc
	caller := &callerState{metadata: maps.Clone(rc.Metadata), billing: rc.Billing.Clone()}
	caller.userID, caller.model, caller.resolvedModel, caller.provider = rc.UserID, rc.Model, rc.ResolvedModel, rc.Provider
	w.caller = caller
	for key := range rc.Metadata {
		if !hasAnyPrefix(key, pendingChargeDropsPrefixes) {
			delete(rc.Metadata, key)
		}
	}
	maps.Copy(rc.Metadata, charge.Metadata)
	rc.UserID = charge.UserID
	rc.Model = model
	if charge.Provider != "" {
		rc.Provider = charge.Provider
	}
}

// answerAsCaller puts back the call's own attribution once its response
// plugins have counted the starter's charge, before its answer's headers are
// written: the call is answered as its own, as dev answers a poll before it
// charges the run, so it never carries the starter's cost or allowance.
func (w *runWatch) answerAsCaller() {
	caller := w.caller
	if caller == nil {
		return
	}
	w.caller = nil
	rc := w.rc
	clear(rc.Metadata)
	maps.Copy(rc.Metadata, caller.metadata)
	rc.UserID, rc.Model, rc.ResolvedModel, rc.Provider = caller.userID, caller.model, caller.resolvedModel, caller.provider
	rc.Billing = caller.billing
	rc.Response = nil
}

// runFinished reports whether a run's status is final. A run spends tokens
// until then, whatever the outcome.
func runFinished(status string) bool {
	switch status {
	case "completed", "failed", "incomplete", "expired", "cancelled":
		return true
	}
	return false
}

// maxRunEventBytes bounds a stream line runEvents holds while it waits for
// the line's end, as the relay bounds the lines it reads
// (proxyAssistantsStream). A longer line is not read for a run.
const maxRunEventBytes = 1024 * 1024

// runEvents reads a relayed Assistants stream for its run events as the
// relay reads it, without changing a byte of it. Each event's data is
// observed when the blank line that ends the event arrives.
type runEvents struct {
	io.ReadCloser
	ctx   context.Context
	watch *runWatch
	line  []byte // the line being read
	data  []byte // the event's data lines so far
	// skip is set while a line over maxRunEventBytes is passed over; the
	// event it belongs to is not read.
	skip, broken bool
}

func (r *runEvents) Read(p []byte) (int, error) {
	n, err := r.ReadCloser.Read(p)
	r.feed(p[:n])
	if err == io.EOF {
		r.endLine()
		r.endEvent()
	}
	return n, err
}

func (r *runEvents) feed(p []byte) {
	for len(p) > 0 {
		i := bytes.IndexByte(p, '\n')
		if i < 0 {
			r.hold(p)
			return
		}
		r.hold(p[:i])
		p = p[i+1:]
		r.endLine()
		r.line = r.line[:0]
	}
}

func (r *runEvents) hold(p []byte) {
	if r.skip {
		return
	}
	if len(r.line)+len(p) > maxRunEventBytes {
		r.skip, r.broken = true, true
		r.line = r.line[:0]
		return
	}
	r.line = append(r.line, p...)
}

// endLine takes a whole line: a blank one ends the event, a data line adds
// to it, and every other line (event, id, a comment) says nothing about a run.
func (r *runEvents) endLine() {
	if r.skip {
		r.skip = false
		return
	}
	line := bytes.TrimSuffix(r.line, []byte("\r"))
	switch {
	case len(line) == 0:
		r.endEvent()
	case bytes.HasPrefix(line, []byte("data:")):
		value := bytes.TrimPrefix(bytes.TrimPrefix(line, []byte("data:")), []byte(" "))
		if len(r.data)+len(value) > maxRunEventBytes {
			r.broken = true
			return
		}
		if len(r.data) > 0 {
			r.data = append(r.data, '\n')
		}
		r.data = append(r.data, value...)
	}
}

func (r *runEvents) endEvent() {
	if len(r.data) > 0 && !r.broken {
		r.watch.observe(r.ctx, r.data)
	}
	r.data, r.broken = r.data[:0], false
}
