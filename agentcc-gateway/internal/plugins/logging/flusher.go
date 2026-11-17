package logging

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"reflect"
	"strconv"
	"sync"
	"syscall"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/logspool"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

const maxFlushRetries = 3 // drop records after this many consecutive failures

// Enqueue refuses records while the buffer holds twice maxBuffer, and warns
// about it at most this often.
const overflowWarnInterval = 10 * time.Second

// On shutdown, Close tries each batch up to finalFlushAttempts times, this far
// apart, before its deadline: a backend that is restarting may be back.
var (
	finalFlushAttempts  = 3
	finalFlushRetryWait = time.Second
)

type LogFlusher struct {
	buffer []TraceRecord
	// states holds, by request id, the v2 state captured with each buffered
	// or in-flight record that EnqueueRequest took, until the record is sent
	// or dropped for good. Under mu.
	states map[string]*requestState
	// events are the records EnqueueEvent took, sent with the next batch.
	// Under mu.
	events           []json.RawMessage
	mu               sync.Mutex
	overflow         int       // under mu: records Enqueue refused because the buffer was full
	overflowWarned   time.Time // under mu: when that was last logged
	closed           bool      // under mu: Close has begun, so Enqueue refuses records
	lost             int       // under mu: records refused or dropped since then; Close reports them
	lostEvents       int       // under mu: events refused or dropped since Close began; Close reports them
	webhookURL       string
	webhookSecret    string
	interval         time.Duration
	maxBuffer        int
	client           *http.Client
	consecutiveFails int

	// kick asks Run for a flush once the buffer reaches maxBuffer. It holds
	// one request, so the records that arrive during a slow send wait for
	// the next flush instead of each starting one.
	kick chan struct{}

	// sendMu lets one send run at a time, so Close never sends a batch that a
	// flush is still sending. A flush sends with sendCtx, which Close cancels
	// once its deadline passes. Close closes stop to end Run.
	sendMu      sync.Mutex
	sendCtx     context.Context
	cancelSends context.CancelFunc
	stop        chan struct{}

	// spool keeps the batches the webhook could not take (SetSpool), so they
	// are delivered later instead of dropped; nil keeps the records' old fate.
	// spill holds, under mu, records refused or left over while the spool is
	// set (the buffer was full, a re-enqueue overflowed, Close had begun),
	// spooled at the next flush or by Close.
	spool     *logspool.Store
	spoolKey  []byte
	spill     []TraceRecord
	drainKick chan struct{}
	spoolMu   sync.Mutex
	spooled   int64            // under spoolMu: batches written to the spool
	dropped   map[string]int64 // under spoolMu: spool batches dropped, by reason
	spoolSeen logspool.Stats   // under spoolMu: the spool's size at its last read
}

// spoolDrainInterval is how often a gateway with spooled batches tries to
// deliver them while it runs, besides after each delivered flush.
var spoolDrainInterval = 30 * time.Second

// spoolOldestWarn is the age past which the oldest spooled batch is logged
// as a warning at every drain.
const spoolOldestWarn = time.Hour

// Spool drop reason for entries that carry no request id.
const reasonNoRequestID = "no_request_id"

// logFlushPayload is one webhook batch. Logs holds log entries and, next to
// them, events (records with a kind); it is written from logBatch.
type logFlushPayload struct {
	Logs []logEntry `json:"logs"`
}

// logBatch is the webhook batch as sent: log entries, then events.
type logBatch struct {
	Logs []any `json:"logs"`
}

// logEntry is one request's entry in the webhook batch (log-entry-v2, plan
// section 6.3): the v1 fields, then the v2 fields, every one of which is
// omitted when empty. The four cost amounts are pointers so a priced zero is
// sent as 0: they are set, 0 included, on every entry whose cost_source names
// a price, and left out when cost_source is unpriced or empty (setCosts).
type logEntry struct {
	RequestID          string            `json:"request_id"`
	Timestamp          time.Time         `json:"timestamp"`
	Model              string            `json:"model"`
	ResolvedModel      string            `json:"resolved_model,omitempty"`
	Provider           string            `json:"provider"`
	PromptTokens       int               `json:"prompt_tokens"`
	CompletionTokens   int               `json:"completion_tokens"`
	TotalTokens        int               `json:"total_tokens"`
	StatusCode         int               `json:"status_code"`
	LatencyMs          int64             `json:"latency_ms"`
	IsStream           bool              `json:"is_stream"`
	IsError            bool              `json:"is_error"`
	ErrorMessage       string            `json:"error_message,omitempty"`
	ErrorCode          string            `json:"error_code,omitempty"`
	ErrorSource        string            `json:"error_source,omitempty"`
	CacheHit           bool              `json:"cache_hit"`
	FallbackUsed       bool              `json:"fallback_used"`
	GuardrailTriggered bool              `json:"guardrail_triggered"`
	GuardrailResults   json.RawMessage   `json:"guardrail_results,omitempty"`
	Cost               float64           `json:"cost"`
	AuthKeyID          string            `json:"auth_key_id,omitempty"`
	UserID             string            `json:"user_id,omitempty"`
	SessionID          string            `json:"session_id,omitempty"`
	RoutingStrategy    string            `json:"routing_strategy,omitempty"`
	Metadata           map[string]string `json:"metadata,omitempty"`
	RequestBody        json.RawMessage   `json:"request_body,omitempty"`
	ResponseBody       json.RawMessage   `json:"response_body,omitempty"`
	RequestHeaders     json.RawMessage   `json:"request_headers,omitempty"`

	Attempts               []models.Attempt `json:"attempts,omitempty"`
	AttemptCount           int              `json:"attempt_count,omitempty"`
	CachedTokens           int              `json:"cached_tokens,omitempty"`
	CacheWriteTokens       int              `json:"cache_write_tokens,omitempty"`
	ReasoningTokens        int              `json:"reasoning_tokens,omitempty"`
	BillingMode            string           `json:"billing_mode,omitempty"`
	CredentialType         string           `json:"credential_type,omitempty"`
	CostUpstream           *float64         `json:"cost_upstream,omitempty"`
	CostMarkup             *float64         `json:"cost_markup,omitempty"`
	CostMarkupBps          *int             `json:"cost_markup_bps,omitempty"`
	CostSource             string           `json:"cost_source,omitempty"`
	UsageEstimated         bool             `json:"usage_estimated,omitempty"`
	CostLanded             *float64         `json:"cost_landed,omitempty"` // staff only
	ServedUpstreamProvider string           `json:"served_upstream_provider,omitempty"`
	UpstreamKeyID          string           `json:"upstream_key_id,omitempty"`
	ContentMode            string           `json:"content_mode,omitempty"`
	GenerationID           string           `json:"generation_id,omitempty"`
	Agent                  *logAgent        `json:"agent,omitempty"`
	Routing                *logRouting      `json:"routing,omitempty"`
	App                    *logApp          `json:"app,omitempty"`
	Tags                   []string         `json:"tags,omitempty"`
	EndUser                string           `json:"end_user,omitempty"`
	Guardrail              *logGuardrail    `json:"guardrail,omitempty"`
	// Evidence, agent.identity and EvalExport come from the request's log
	// evidence (rc.SetLogEvidence).
	Evidence   map[string]json.RawMessage `json:"evidence,omitempty"`
	EvalExport string                     `json:"eval_export,omitempty"`
}

// logAgent is a log entry's agent{}. The session is top-level session_id.
type logAgent struct {
	Framework       string   `json:"framework,omitempty"`
	AgentID         string   `json:"agent_id,omitempty"`
	AgentVersion    string   `json:"agent_version,omitempty"`
	RunID           string   `json:"run_id,omitempty"`
	ParentRunID     string   `json:"parent_run_id,omitempty"`
	RootRunID       string   `json:"root_run_id,omitempty"`
	TaskID          string   `json:"task_id,omitempty"`
	TraceID         string   `json:"trace_id,omitempty"`
	Workflow        string   `json:"workflow,omitempty"`
	Step            string   `json:"step,omitempty"`
	StepSource      string   `json:"step_source,omitempty"`
	Turn            *int     `json:"turn,omitempty"`
	Env             string   `json:"env,omitempty"`
	BudgetScope     string   `json:"budget_scope,omitempty"`
	AttemptID       string   `json:"attempt_id,omitempty"`
	IdentitySource  string   `json:"identity_source,omitempty"`
	AliasOverridden []string `json:"alias_overridden,omitempty"`
	// Identity is the identity chain, from log evidence "identity".
	Identity json.RawMessage `json:"identity,omitempty"`
}

// logRouting is a log entry's routing{}.
type logRouting struct {
	PolicyVersion     string `json:"policy_version,omitempty"`
	PolicyVariant     string `json:"policy_variant,omitempty"`
	CatalogSnapshotID string `json:"catalog_snapshot_id,omitempty"`
	Auto              bool   `json:"auto,omitempty"`
	AutoStep          string `json:"auto_step,omitempty"`
	Affinity          string `json:"affinity,omitempty"`
	Preset            string `json:"preset,omitempty"`
	PresetSource      string `json:"preset_source,omitempty"`
}

type logApp struct {
	Title   string `json:"title,omitempty"`
	Referer string `json:"referer,omitempty"`
}

type logGuardrail struct {
	ToolCalls *logToolCalls `json:"tool_calls,omitempty"`
}

type logToolCalls struct {
	Checked  int      `json:"checked"`
	Blocked  []string `json:"blocked"`
	Stripped []string `json:"stripped"`
}

// LogEntryOptions say what a request's log entry may carry.
type LogEntryOptions struct {
	// IncludeBodies keeps the request and response bodies and the request
	// headers the record holds. A content mode of metadata or none drops
	// them whatever this says.
	IncludeBodies bool
	// ContentMode is the request's content mode. Empty
	// takes the one the request resolved (rc.Privacy), and none of them
	// means full. The entry records it as content_mode.
	ContentMode models.ContentMode
}

// DefaultLogEntryOptions keep everything the record holds.
func DefaultLogEntryOptions() LogEntryOptions {
	return LogEntryOptions{IncludeBodies: true}
}

// requestState is what EnqueueRequest captured from the request with its
// record: the v2 state, the response's usage and the entry options.
type requestState struct {
	v2    models.RequestV2
	usage *models.Usage
	opts  LogEntryOptions
}

func NewLogFlusher(webhookURL, webhookSecret string, interval time.Duration, maxBuffer int) *LogFlusher {
	if interval <= 0 {
		interval = 30 * time.Second
	}
	if maxBuffer <= 0 {
		maxBuffer = 5000
	}
	sendCtx, cancelSends := context.WithCancel(context.Background())
	return &LogFlusher{
		kick:          make(chan struct{}, 1),
		drainKick:     make(chan struct{}, 1),
		sendCtx:       sendCtx,
		cancelSends:   cancelSends,
		stop:          make(chan struct{}),
		webhookURL:    webhookURL,
		webhookSecret: webhookSecret,
		interval:      interval,
		maxBuffer:     maxBuffer,
		client:        &http.Client{Timeout: 15 * time.Second},
	}
}

// SetSpool makes the flusher keep, in store, the batches the webhook could not
// take, sealed with key, and deliver them later. Call before Run. A nil store
// or an empty key leaves the spool off.
func (f *LogFlusher) SetSpool(store *logspool.Store, key []byte) {
	if store == nil || len(key) == 0 {
		return
	}
	f.spool, f.spoolKey = store, key
}

// Spool is the flusher's spool, nil when off.
func (f *LogFlusher) Spool() *logspool.Store { return f.spool }

func (f *LogFlusher) Run(ctx context.Context) {
	ticker := time.NewTicker(f.interval)
	defer ticker.Stop()
	drainTicker := time.NewTicker(spoolDrainInterval)
	defer drainTicker.Stop()
	if f.spool != nil {
		// Batches a previous run left are delivered first.
		f.requestDrain()
	}

	slog.Info("log flusher started",
		"interval", f.interval.String(),
		"webhook_url", f.webhookURL,
		"max_buffer", f.maxBuffer,
	)

	for {
		select {
		case <-ctx.Done():
			f.flush()
			slog.Info("log flusher stopped")
			return
		case <-f.stop:
			return // Close makes the last flush
		case <-ticker.C:
			f.flush()
		case <-f.kick:
			f.flush()
		case <-drainTicker.C:
			f.drain(ctx)
		case <-f.drainKick:
			f.drain(ctx)
		}
	}
}

// Enqueue buffers a record for the webhook. Its entry carries the v1 fields
// only; EnqueueRequest adds the request's v2 state.
func (f *LogFlusher) Enqueue(rec TraceRecord) {
	f.enqueue(rec, nil)
}

// EnqueueRequest buffers the record of the request rc, with rc's v2 state
// (attempts, agent, billing, routing evidence, content mode, usage details),
// so its entry is a full log-entry-v2. It copies what it
// needs from rc before it returns, reading only, so a post-plugin may call it
// in the parallel window.
func (f *LogFlusher) EnqueueRequest(rec TraceRecord, rc *models.RequestContext, opts LogEntryOptions) {
	st := &requestState{v2: rc.SnapshotV2(), opts: opts}
	if usage := answerUsage(rc); usage != nil {
		u := *usage
		st.usage = &u
	}
	f.enqueue(rec, st)
}

// EnqueueEvent adds an event, {"kind": kind, ...payload}, to the next webhook
// batch, next to the log entries (models.EventSink). payload must marshal to
// a JSON object; its own kind key, if any, is replaced. An event is dropped
// once the flusher is closing, when maxBuffer events are already waiting, or
// when its batch is dropped: money never depends on this path.
func (f *LogFlusher) EnqueueEvent(kind string, payload any) {
	raw, err := json.Marshal(payload)
	var fields map[string]json.RawMessage
	if err == nil {
		err = json.Unmarshal(raw, &fields)
	}
	if err != nil || fields == nil {
		slog.Warn("log flusher: event payload is not a JSON object, dropping it", "kind", kind, "error", err)
		return
	}
	fields["kind"], _ = json.Marshal(kind)
	event, err := json.Marshal(fields)
	if err != nil {
		return
	}
	f.mu.Lock()
	if f.closed || len(f.events) >= f.maxBuffer {
		if f.closed {
			f.lostEvents++
		}
		f.mu.Unlock()
		slog.Warn("log flusher: dropping event", "kind", kind)
		return
	}
	f.events = append(f.events, event)
	shouldFlush := len(f.events) >= f.maxBuffer
	f.mu.Unlock()
	if shouldFlush {
		f.requestFlush()
	}
}

func (f *LogFlusher) enqueue(rec TraceRecord, st *requestState) {
	f.mu.Lock()
	if f.spool != nil && (f.closed || len(f.buffer) >= 2*f.maxBuffer) && len(f.spill) < 2*f.maxBuffer {
		// The buffer cannot take it (full, or Close has begun): spooled
		// instead, at the next flush or by Close.
		f.spill = append(f.spill, rec)
		f.keepStateLocked(rec, st)
		f.mu.Unlock()
		f.requestFlush()
		return
	}
	if f.closed {
		f.lost++
		f.mu.Unlock()
		return
	}
	if len(f.buffer) >= 2*f.maxBuffer {
		// The webhook is not keeping up: refuse the record rather than let
		// the buffer grow without bound.
		f.overflow++
		overflow := f.overflow
		warn := time.Since(f.overflowWarned) >= overflowWarnInterval
		if warn {
			f.overflowWarned = time.Now()
		}
		f.mu.Unlock()
		if warn {
			slog.Warn("log flusher: buffer full, dropping new records",
				"dropped_total", overflow,
				"buffered", 2*f.maxBuffer,
			)
		}
		return
	}
	f.buffer = append(f.buffer, rec)
	f.keepStateLocked(rec, st)
	shouldFlush := len(f.buffer) >= f.maxBuffer
	f.mu.Unlock()

	if shouldFlush {
		f.requestFlush()
	}
}

// keepStateLocked keeps st as rec's captured v2 state. Under mu.
func (f *LogFlusher) keepStateLocked(rec TraceRecord, st *requestState) {
	if st != nil && rec.RequestID != "" {
		if f.states == nil {
			f.states = make(map[string]*requestState)
		}
		f.states[rec.RequestID] = st
	}
}

// requestFlush asks Run for a flush.
func (f *LogFlusher) requestFlush() {
	select {
	case f.kick <- struct{}{}:
	default: // a flush is already pending
	}
}

// encodeLogs builds the webhook payload for records, with their captured v2
// state, followed by events.
func (f *LogFlusher) encodeLogs(records []TraceRecord, events []json.RawMessage) ([]byte, error) {
	f.mu.Lock()
	states := make([]*requestState, len(records))
	for i, rec := range records {
		states[i] = f.states[rec.RequestID]
	}
	f.mu.Unlock()

	items := make([]any, 0, len(records)+len(events))
	for i, rec := range records {
		var entry logEntry
		if st := states[i]; st != nil {
			entry = buildLogEntry(rec, &st.v2, st.usage, st.opts)
		} else {
			entry = buildLogEntryV1(rec)
		}
		items = append(items, entry)
	}
	for _, e := range events {
		items = append(items, e)
	}
	return json.Marshal(logBatch{Logs: items})
}

// buildLogEntry is a request's log-entry-v2: the v1 fields from rec, and the
// v2 fields from the request's v2 state and the usage of
// its response. Empty values are omitted (a priced entry's cost amounts are
// kept even at 0, see setCosts), and rec's metadata keys are kept.
// The content mode (opts, else v2.Privacy) decides what content stays: metadata and none drop the bodies and request headers; none
// also drops error text and the caller's own metadata keys, keeping the
// gateway's, and attempts keep their error class and status only.
func buildLogEntry(rec TraceRecord, v2 *models.RequestV2, usage *models.Usage, opts LogEntryOptions) logEntry {
	mode := opts.ContentMode
	if mode == "" {
		mode = v2.Privacy.ContentMode
	}
	if !opts.IncludeBodies || mode == models.ContentModeMetadata || mode == models.ContentModeNone {
		rec.RequestBody, rec.ResponseBody = nil, nil
		rec.RequestBodyJSON, rec.ResponseBodyJSON = nil, nil
		rec.RequestHeaders = nil
	}
	if mode == models.ContentModeNone {
		rec.ErrorMessage = ""
		rec.Metadata = withoutKeys(rec.Metadata, v2.CustomMetadataKeys)
	}

	e := buildLogEntryV1(rec)
	e.GenerationID = rec.RequestID
	e.ContentMode = string(mode)
	if e.SessionID == "" {
		e.SessionID = v2.Agent.SessionID
	}

	if len(v2.Attempts) > 0 {
		e.Attempts = append([]models.Attempt(nil), v2.Attempts...)
		if mode == models.ContentModeNone {
			for i := range e.Attempts {
				e.Attempts[i].ErrorMessage = ""
			}
		}
		e.AttemptCount = len(e.Attempts)
	}
	if usage != nil {
		u := *usage
		u.ParseDetails()
		e.CachedTokens = u.PromptDetails.CachedTokens
		e.CacheWriteTokens = u.PromptDetails.CacheWriteTokens
		e.ReasoningTokens = u.CompletionDetails.ReasoningTokens
	}

	b := v2.Billing
	e.BillingMode = b.Mode
	e.CredentialType = string(b.CredentialType)
	setCosts(&e, &b)
	e.CostSource = string(b.CostSource)
	e.UsageEstimated = b.UsageEstimated
	e.ServedUpstreamProvider = b.ServedUpstreamProvider
	e.UpstreamKeyID = b.UpstreamKeyID

	e.Agent = logAgentOf(&v2.Agent)
	e.Routing = logRoutingOf(&v2.Evidence, rec.Metadata["auto_requested"] != "")
	if a := v2.Agent; a.AppTitle != "" || a.AppReferer != "" {
		e.App = &logApp{Title: a.AppTitle, Referer: a.AppReferer}
	}
	if len(v2.Agent.Tags) > 0 {
		e.Tags = append([]string(nil), v2.Agent.Tags...)
	}
	e.EndUser = v2.Agent.EndUser
	if tc := v2.ToolCalls; v2.ToolCallGuard || tc.Checked > 0 || len(tc.Blocked) > 0 || len(tc.Stripped) > 0 {
		e.Guardrail = &logGuardrail{ToolCalls: &logToolCalls{
			Checked:  tc.Checked,
			Blocked:  append([]string{}, tc.Blocked...),
			Stripped: append([]string{}, tc.Stripped...),
		}}
	}
	applyLogEvidence(&e, v2.LogEvidence)
	return e
}

// Log evidence names written outside the evidence object.
const (
	logEvidenceIdentity   = "identity"
	logEvidenceEvalExport = "eval_export"
)

// applyLogEvidence writes the request's log evidence into e: each
// entry as evidence.<name>, except identity (agent.identity) and eval_export
// (the top-level eval_export, a string). Every content mode keeps it.
func applyLogEvidence(e *logEntry, ev map[string]json.RawMessage) {
	for name, raw := range ev {
		switch name {
		case logEvidenceIdentity:
			if e.Agent == nil {
				e.Agent = &logAgent{}
			}
			e.Agent.Identity = raw
		case logEvidenceEvalExport:
			var v string
			if json.Unmarshal(raw, &v) == nil {
				e.EvalExport = v
			}
		default:
			if e.Evidence == nil {
				e.Evidence = make(map[string]json.RawMessage, len(ev))
			}
			e.Evidence[name] = raw
		}
	}
}

func logAgentOf(a *models.AgentContext) *logAgent {
	la := logAgent{
		Framework:       a.Framework,
		AgentID:         a.AgentID,
		AgentVersion:    a.AgentVersion,
		RunID:           a.RunID,
		ParentRunID:     a.ParentRunID,
		RootRunID:       a.RootRunID,
		TaskID:          a.TaskID,
		TraceID:         a.TraceID,
		Workflow:        a.Workflow,
		Step:            string(a.Step),
		StepSource:      a.StepSource,
		Turn:            a.Turn,
		Env:             a.Env,
		BudgetScope:     a.BudgetScope,
		AttemptID:       a.AttemptID,
		IdentitySource:  a.IdentitySource,
		AliasOverridden: a.AliasOverridden,
	}
	if la.Turn == nil && len(la.AliasOverridden) == 0 {
		empty := la
		empty.AliasOverridden = nil
		if reflect.DeepEqual(empty, logAgent{}) {
			return nil
		}
	}
	return &la
}

func logRoutingOf(ev *models.RoutingEvidence, auto bool) *logRouting {
	lr := logRouting{
		PolicyVersion:     ev.PolicyVersion,
		PolicyVariant:     ev.PolicyVariant,
		CatalogSnapshotID: ev.CatalogSnapshotID,
		Auto:              auto,
		AutoStep:          string(ev.AutoStep),
		Affinity:          ev.Affinity,
		Preset:            ev.Preset,
		PresetSource:      ev.PresetSource,
	}
	if lr == (logRouting{}) {
		return nil
	}
	return &lr
}

// setCosts writes b's cost amounts into e when b names the price they came
// from: cost_upstream, cost_markup, cost_markup_bps and cost_landed are then
// all present, 0 included (a managed call at no markup, a BYOK call at zero),
// so Django never reads a priced 0 as missing. An unpriced request
// (cost_source unpriced) has no amounts to report, and one with no cost
// source (refused before dispatch, a cache hit, nothing billable) priced
// nothing: both leave the four out, and Django stores the unpriced one's as
// NULL and the other's as 0.
func setCosts(e *logEntry, b *models.BillingState) {
	if b.CostSource == "" || b.CostSource == models.CostSourceUnpriced {
		return
	}
	upstream := microsToUSD(b.UpstreamMicros)
	markup := microsToUSD(b.MarkupMicros)
	bps := b.MarkupBps
	landed := microsToUSD(b.LandedMicros)
	e.CostUpstream, e.CostMarkup, e.CostMarkupBps, e.CostLanded = &upstream, &markup, &bps, &landed
}

// microsToUSD converts integer micros to USD.
func microsToUSD(micros int64) float64 {
	return float64(micros) / 1e6
}

// withoutKeys returns m without keys, copying it only when it has one of them.
func withoutKeys(m map[string]string, keys []string) map[string]string {
	if len(m) == 0 || len(keys) == 0 {
		return m
	}
	out := make(map[string]string, len(m))
	for k, v := range m {
		out[k] = v
	}
	for _, k := range keys {
		delete(out, k)
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

// buildLogEntryV1 is the v1 part of a log entry, from rec alone.
func buildLogEntryV1(rec TraceRecord) logEntry {
	var grJSON json.RawMessage
	if len(rec.GuardrailResults) > 0 {
		grJSON, _ = json.Marshal(rec.GuardrailResults)
	}
	var reqBody, respBody json.RawMessage
	if len(rec.RequestBodyJSON) > 0 {
		reqBody = rec.RequestBodyJSON
	} else if rec.RequestBody != nil {
		reqBody, _ = json.Marshal(rec.RequestBody)
	}
	if len(rec.ResponseBodyJSON) > 0 {
		respBody = rec.ResponseBodyJSON
	} else if rec.ResponseBody != nil {
		respBody, _ = json.Marshal(rec.ResponseBody)
	}
	var reqHeaders json.RawMessage
	if len(rec.RequestHeaders) > 0 {
		reqHeaders, _ = json.Marshal(rec.RequestHeaders)
	}
	// error_code never carries a provider's own code as it is: Django reads a
	// budget code there as the gateway's refusal (log_ingestion.py
	// _budget_error_code), and the openai and azure adapters pass a
	// provider's error.code through unchanged (parseProviderError), so a
	// provider answering budget_exceeded must not raise budget.exceeded.
	// Those errors set UpstreamStatus, as does every error
	// models.ErrUpstreamProvider makes with a nonzero status, and their code
	// is left out. error_code is not only the gateway's own codes, though: on
	// this release the anthropic, gemini, bedrock and cohere adapters' parsed
	// errors, the speech relay's refusals and a provider that never answered
	// (provider_0) leave UpstreamStatus at 0, so their provider_ codes reach
	// error_code. None of them can equal a budget code.
	errorCode := rec.ErrorCode
	if rec.ErrorFromUpstream {
		errorCode = ""
	}
	// Extract cost from metadata to promote as top-level field.
	var cost float64
	if costStr, ok := rec.Metadata["cost"]; ok {
		cost, _ = strconv.ParseFloat(costStr, 64)
	}
	return logEntry{
		RequestID:          rec.RequestID,
		Timestamp:          rec.Timestamp,
		Model:              rec.Model,
		ResolvedModel:      rec.ResolvedModel,
		Provider:           rec.Provider,
		PromptTokens:       rec.PromptTokens,
		CompletionTokens:   rec.CompletionTokens,
		TotalTokens:        rec.TotalTokens,
		StatusCode:         rec.StatusCode,
		LatencyMs:          rec.LatencyMs,
		IsStream:           rec.IsStream,
		IsError:            rec.StatusCode >= 400,
		ErrorMessage:       rec.ErrorMessage,
		ErrorCode:          errorCode,
		ErrorSource:        rec.ErrorSource,
		CacheHit:           rec.CacheHit,
		FallbackUsed:       rec.FallbackUsed,
		GuardrailTriggered: rec.GuardrailTriggered,
		GuardrailResults:   grJSON,
		Cost:               cost,
		AuthKeyID:          rec.AuthKeyID,
		UserID:             rec.UserID,
		SessionID:          rec.SessionID,
		RoutingStrategy:    rec.Metadata["routing_strategy"],
		Metadata:           rec.Metadata,
		RequestBody:        reqBody,
		ResponseBody:       respBody,
		RequestHeaders:     reqHeaders,
	}
}

func (f *LogFlusher) flush() {
	f.sendMu.Lock()
	defer f.sendMu.Unlock()

	f.spoolSpill()
	records := f.take()
	events := f.takeEvents()
	if len(records) == 0 && len(events) == 0 {
		return
	}

	body, err := f.encodeLogs(records, events)
	if err != nil {
		slog.Error("log flusher: marshal failed", "error", err, "count", len(records), "events", len(events))
		f.drop(records)
		f.countLostEvents(len(events))
		return
	}

	status, code, err := f.post(f.sendCtx, body)
	if err != nil && f.sendCtx.Err() != nil {
		// Close's deadline passed during the send; Close counts these.
		f.reEnqueue(records)
		f.requeueEvents(events)
		return
	}
	if retryable(status, err) {
		cause := slog.Any("error", err)
		if err == nil {
			cause = slog.Int("status", status)
		}
		f.consecutiveFails++
		if f.consecutiveFails > maxFlushRetries && !f.closing() {
			if f.spoolRecords(records, events) {
				slog.Warn("log flusher: webhook unavailable, spooled records for later delivery",
					cause, "count", len(records), "consecutive_failures", f.consecutiveFails)
				f.consecutiveFails = 0
				return
			}
			slog.Error("log flusher: max retries exceeded, dropping records",
				cause,
				"count", len(records),
				"consecutive_failures", f.consecutiveFails,
			)
			f.consecutiveFails = 0
			f.drop(records)
			f.countLostEvents(len(events))
			return
		}
		slog.Error("log flusher: send failed, re-enqueuing records",
			cause,
			"count", len(records),
			"retry", f.consecutiveFails,
		)
		f.reEnqueue(records)
		f.requeueEvents(events)
		return
	}
	if status >= 400 {
		// Any refusal but a malformed batch may pass later (a secret being
		// rotated, say): with the spool on it is kept.
		if !permanentRefusal(status, code) && f.spoolRecords(records, events) {
			slog.Warn("log flusher: webhook refused the batch, spooled it for later delivery",
				"status", status, "count", len(records))
			f.consecutiveFails = 0
			return
		}
		// Client error — retrying won't help, drop immediately.
		slog.Error("log flusher: webhook returned client error, dropping records",
			"status", status,
			"count", len(records),
		)
		f.consecutiveFails = 0
		f.drop(records)
		f.countLostEvents(len(events))
		return
	}

	// Success — reset retry counter.
	f.consecutiveFails = 0
	f.forget(records)
	// The webhook takes batches again: deliver any the spool holds.
	f.requestDrain()

	slog.Debug("log flusher: sent records",
		"count", len(records),
		"status", status,
	)
}

// Close stops accepting records and makes a last attempt to deliver the
// buffered ones, trying each batch up to finalFlushAttempts times until ctx
// ends. A flush already sending finishes first (or is cut off when ctx ends),
// so no batch is sent twice. It logs how many records were not delivered, and
// how many it did deliver.
func (f *LogFlusher) Close(ctx context.Context) {
	f.mu.Lock()
	if f.closed {
		f.mu.Unlock()
		return
	}
	f.closed = true
	f.mu.Unlock()
	close(f.stop)
	defer f.cancelSends()
	defer context.AfterFunc(ctx, f.cancelSends)()

	f.sendMu.Lock()
	defer f.sendMu.Unlock()

	records := f.take()
	var undelivered int
	var err error
	if len(records) > 0 {
		var left []TraceRecord
		left, err = f.deliverLeft(ctx, records)
		undelivered = len(left)
		if undelivered > 0 && f.spoolRecords(left, nil) {
			slog.Info("log flusher: spooled undelivered request logs at shutdown", "count", undelivered)
			undelivered = 0
		}
	}
	f.spoolSpill()
	if f.hasEvents() {
		// Events with no records left to ride with go on their own.
		if evErr := f.deliverBatch(ctx, nil); err == nil {
			err = evErr
		}
	}
	delivered := len(records) - undelivered

	f.mu.Lock()
	undelivered += f.lost + len(f.spill) // spilled after the last spool write
	f.spill = nil
	lostEvents := f.lostEvents + len(f.events)
	f.events, f.states = nil, nil
	f.mu.Unlock()
	if lostEvents > 0 {
		slog.Warn("log flusher: events not delivered before shutdown", "events", lostEvents)
	}
	switch {
	case undelivered > 0:
		attrs := []any{"undelivered", undelivered}
		if delivered > 0 {
			attrs = append(attrs, "delivered", delivered)
		}
		level := slog.LevelError
		if err != nil {
			attrs = append(attrs, "error", err)
			if backendGone(err) {
				// Stop order often stops the backend first (compose does).
				level = slog.LevelWarn
			}
		}
		slog.Log(context.Background(), level, "log flusher: request logs not delivered before shutdown", attrs...)
	case len(records) > 0:
		slog.Info("log flusher: delivered buffered request logs before shutdown", "count", len(records))
	}
	slog.Info("log flusher stopped")
}

// deliver sends records in batches of at most maxBuffer, so one failed send
// does not lose the whole backlog: up to twice maxBuffer buffered records, plus
// as many from a flush that gave its batch back. A batch the webhook refuses
// with a client error is not delivered, but the batches after it are still
// sent, as a flush would. Any other failure stops it: the batches after would
// meet it too. It returns how many records it did not deliver, and why.
func (f *LogFlusher) deliver(ctx context.Context, records []TraceRecord) (int, error) {
	left, err := f.deliverLeft(ctx, records)
	return len(left), err
}

// deliverLeft is deliver, returning the records it did not deliver (none
// that the webhook refused for good).
func (f *LogFlusher) deliverLeft(ctx context.Context, records []TraceRecord) ([]TraceRecord, error) {
	var undelivered []TraceRecord
	var lastErr error
	for start := 0; start < len(records); start += f.maxBuffer {
		batch := records[start:min(start+f.maxBuffer, len(records))]
		err := f.deliverBatch(ctx, batch)
		if err == nil {
			continue
		}
		var status statusError
		if !errors.As(err, &status) || retryable(status.status, nil) {
			return append(undelivered, records[start:]...), err
		}
		if !permanentRefusal(status.status, status.code) {
			undelivered = append(undelivered, batch...)
		} else {
			f.forget(batch)
		}
		lastErr = err
	}
	return undelivered, lastErr
}

// deliverBatch sends one batch, trying again after a failed send or a server
// error, up to finalFlushAttempts sends in all while ctx lasts.
func (f *LogFlusher) deliverBatch(ctx context.Context, records []TraceRecord) (err error) {
	if err := ctx.Err(); err != nil {
		return err // a send cut off at the deadline left these: don't encode them
	}
	// Waiting events ride with the batch; a batch that is not delivered
	// loses them.
	events := f.takeEvents()
	body, err := f.encodeLogs(records, events)
	if err != nil {
		f.requeueEvents(events) // never sent: the next batch may carry them
		return err
	}
	defer func() {
		if err != nil {
			// Count once after all attempts, not on encode failure: those
			// events were requeued and may still be delivered by Close.
			f.mu.Lock()
			f.lostEvents += len(events)
			f.mu.Unlock()
		}
	}()
	for attempt := 1; ; attempt++ {
		if ctxErr := ctx.Err(); ctxErr != nil {
			if err == nil {
				err = ctxErr
			}
			return err
		}
		status, code, postErr := f.post(ctx, body)
		if postErr == nil && status < 400 {
			f.forget(records)
			return nil
		}
		err = postErr
		if err == nil {
			err = statusError{status: status, code: code}
		}
		if !retryable(status, postErr) || attempt >= finalFlushAttempts {
			return err
		}
		select {
		case <-time.After(finalFlushRetryWait):
		case <-ctx.Done():
			return err
		}
	}
}

// statusError is the error for a send the webhook answered with this status
// (and, for a 400, its error code).
type statusError struct {
	status int
	code   string
}

func (e statusError) Error() string {
	return fmt.Sprintf("webhook returned status %d", e.status)
}

// invalidLogBatch is the webhook's code for a malformed batch: the one
// refusal a redelivery would meet again, so the only one never spooled.
const invalidLogBatch = "invalid_log_batch"

// permanentRefusal reports whether the webhook refused a batch for good.
func permanentRefusal(status int, code string) bool {
	return status == http.StatusBadRequest && code == invalidLogBatch
}

// retryable reports whether a send that returned status and err may succeed
// if tried again: it failed before the webhook answered, or the webhook had a
// server error. A client error will not change on a retry.
func retryable(status int, err error) bool {
	return err != nil || status >= 500
}

// backendGone reports whether err says there is no backend to send to: it
// refused the connection, or its name no longer resolves, as a stopped compose
// service's does.
func backendGone(err error) bool {
	var dnsErr *net.DNSError
	return errors.Is(err, syscall.ECONNREFUSED) || (errors.As(err, &dnsErr) && dnsErr.IsNotFound)
}

// closing reports whether Close has begun. A flush then re-enqueues the
// records it would drop after maxFlushRetries, for Close's last attempt.
func (f *LogFlusher) closing() bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.closed
}

// countLost notes that a flush dropped n records for good. Once Close has
// begun, Close reports them as undelivered.
func (f *LogFlusher) countLost(n int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.closed {
		f.lost += n
	}
}

// countLostEvents notes events dropped for good after Close began, just as
// countLost does for records.
func (f *LogFlusher) countLostEvents(n int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.closed {
		f.lostEvents += n
	}
}

// take empties the buffer and returns what was in it.
func (f *LogFlusher) take() []TraceRecord {
	f.mu.Lock()
	defer f.mu.Unlock()
	records := f.buffer
	f.buffer = nil
	return records
}

// takeEvents empties the event queue and returns what was in it.
func (f *LogFlusher) takeEvents() []json.RawMessage {
	f.mu.Lock()
	defer f.mu.Unlock()
	events := f.events
	f.events = nil
	return events
}

// hasEvents reports whether events are waiting.
func (f *LogFlusher) hasEvents() bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.events) > 0
}

// requeueEvents puts events that were not sent back in front of the queue,
// keeping at most maxBuffer.
func (f *LogFlusher) requeueEvents(events []json.RawMessage) {
	if len(events) == 0 {
		return
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.events = append(events, f.events...)
	if len(f.events) > f.maxBuffer {
		slog.Warn("log flusher: event queue full, dropping events", "dropped", len(f.events)-f.maxBuffer)
		if f.closed {
			f.lostEvents += len(f.events) - f.maxBuffer
		}
		f.events = f.events[:f.maxBuffer]
	}
}

// forget drops the captured state of records that left the flusher for good.
func (f *LogFlusher) forget(records []TraceRecord) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.forgetLocked(records)
}

func (f *LogFlusher) forgetLocked(records []TraceRecord) {
	if len(f.states) == 0 {
		return
	}
	for _, rec := range records {
		delete(f.states, rec.RequestID)
	}
}

// drop counts records as lost for good and forgets their state.
func (f *LogFlusher) drop(records []TraceRecord) {
	f.countLost(len(records))
	f.forget(records)
}

// Webhook signing headers: the unix time of the send and the hex
// HMAC-SHA256 of "<timestamp>.<body>" under the webhook secret. The control
// plane accepts a signed send only inside a short window; every send,
// a spooled batch's replay included, is signed when it is made.
const (
	webhookTimestampHeader = "X-Webhook-Timestamp"
	webhookSignatureHeader = "X-Webhook-Signature"
)

// webhookSignature signs one send of body at ts.
func webhookSignature(secret, ts string, body []byte) string {
	m := hmac.New(sha256.New, []byte(secret))
	m.Write([]byte(ts))
	m.Write([]byte("."))
	m.Write(body)
	return hex.EncodeToString(m.Sum(nil))
}

// post sends one batch to the webhook and returns the response status and,
// for a 400, the refusal's error code.
func (f *LogFlusher) post(ctx context.Context, body []byte) (int, string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, f.webhookURL, bytes.NewReader(body))
	if err != nil {
		return 0, "", fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	if f.webhookSecret != "" {
		// The static header stays for a control plane older than the
		// signature; a newer one judges the signature alone.
		req.Header.Set("X-Webhook-Secret", f.webhookSecret)
		ts := strconv.FormatInt(time.Now().Unix(), 10)
		req.Header.Set(webhookTimestampHeader, ts)
		req.Header.Set(webhookSignatureHeader, webhookSignature(f.webhookSecret, ts, body))
	}

	resp, err := f.client.Do(req)
	if err != nil {
		return 0, "", err
	}
	defer resp.Body.Close()
	var code string
	if resp.StatusCode == http.StatusBadRequest {
		var refusal struct {
			Code string `json:"code"`
		}
		if json.NewDecoder(io.LimitReader(resp.Body, 4096)).Decode(&refusal) == nil {
			code = refusal.Code
		}
	}
	return resp.StatusCode, code, nil
}

// reEnqueue puts failed records back into the buffer (up to maxBuffer).
// This prevents data loss when the webhook endpoint is temporarily unavailable.
// Once Close has begun it keeps them all for Close's last attempt: Enqueue
// refuses records by then, so the buffer cannot grow further.
func (f *LogFlusher) reEnqueue(records []TraceRecord) {
	f.mu.Lock()
	defer f.mu.Unlock()

	capacity := f.maxBuffer - len(f.buffer)
	if f.closed {
		capacity = len(records)
	}
	if capacity <= 0 {
		if f.spool != nil {
			f.spill = append(f.spill, records...) // spooled at the next flush
			return
		}
		slog.Warn("log flusher: buffer full, dropping records",
			"dropped", len(records),
		)
		f.forgetLocked(records)
		return
	}
	if len(records) > capacity {
		if f.spool != nil {
			f.spill = append(f.spill, records[capacity:]...)
		} else {
			slog.Warn("log flusher: partial re-enqueue, dropping excess",
				"re_enqueued", capacity,
				"dropped", len(records)-capacity,
			)
			f.forgetLocked(records[capacity:])
		}
		records = records[:capacity]
	}
	f.buffer = append(f.buffer, records...)
}

// spoolEntries are records' log entries as the spool keeps them: never the
// request or response bodies or the request headers.
func (f *LogFlusher) spoolEntries(records []TraceRecord) ([]json.RawMessage, error) {
	f.mu.Lock()
	states := make([]*requestState, len(records))
	for i, rec := range records {
		states[i] = f.states[rec.RequestID]
	}
	f.mu.Unlock()
	out := make([]json.RawMessage, 0, len(records))
	for i, rec := range records {
		var entry logEntry
		if st := states[i]; st != nil {
			entry = buildLogEntry(rec, &st.v2, st.usage, st.opts)
		} else {
			entry = buildLogEntryV1(rec)
		}
		entry.RequestBody, entry.ResponseBody, entry.RequestHeaders = nil, nil, nil
		raw, err := json.Marshal(entry)
		if err != nil {
			return nil, err
		}
		out = append(out, raw)
	}
	return out, nil
}

// spoolRecords writes records (and events) to the spool as one sealed batch,
// and reports whether it did; on false the caller treats them as before.
func (f *LogFlusher) spoolRecords(records []TraceRecord, events []json.RawMessage) bool {
	if f.spool == nil || (len(records) == 0 && len(events) == 0) {
		return false
	}
	entries, err := f.spoolEntries(records)
	if err != nil {
		slog.Error("log flusher: spool encode failed", "error", err, "count", len(records))
		return false
	}
	batch, skipped, err := logspool.NewBatch(f.spoolKey, time.Now(), entries, events)
	if err != nil {
		slog.Error("log flusher: spool batch failed", "error", err)
		return false
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	evicted, err := f.spool.Put(ctx, batch)
	if err != nil {
		slog.Error("log flusher: spool write failed", "error", err, "count", len(records))
		return false
	}
	if skipped > 0 {
		slog.Warn("log flusher: entries without a request id are not spooled", "count", skipped)
		f.countLost(skipped)
		f.countDropped(reasonNoRequestID, int64(skipped))
	}
	if evicted > 0 {
		slog.Warn("log flusher: spool full, oldest batches dropped (their spend is kept for the budget seed)",
			"dropped_batches", evicted)
		f.countDropped(logspool.ReasonSize, int64(evicted))
	}
	f.spoolMu.Lock()
	f.spooled++
	f.spoolMu.Unlock()
	f.forget(records)
	return true
}

// spoolSpill spools the records the buffer could not take.
func (f *LogFlusher) spoolSpill() {
	f.mu.Lock()
	spill := f.spill
	f.spill = nil
	f.mu.Unlock()
	if len(spill) == 0 {
		return
	}
	if !f.spoolRecords(spill, nil) {
		slog.Error("log flusher: could not spool records the buffer refused, dropping them", "count", len(spill))
		f.drop(spill)
	}
}

func (f *LogFlusher) countDropped(reason string, n int64) {
	f.spoolMu.Lock()
	defer f.spoolMu.Unlock()
	if f.dropped == nil {
		f.dropped = map[string]int64{}
	}
	f.dropped[reason] += n
}

// requestDrain asks Run to deliver the spooled batches.
func (f *LogFlusher) requestDrain() {
	if f.spool == nil {
		return
	}
	select {
	case f.drainKick <- struct{}{}:
	default:
	}
}

// drainBatches is how many spooled batches one drain reads at a time, and
// drainRounds how many times it reads before it yields to the flushes.
const (
	drainBatches = 20
	drainRounds  = 10
)

// drain delivers spooled batches, oldest first, under the spool's lease, so
// replicas never deliver the same batch at once. A delivered batch is marked
// and removed by id in one script; one the webhook refuses for good is
// removed; a batch whose MAC fails is dropped unsent and uncounted; one past
// its age is dropped and its spend kept. Any other failure stops the drain
// until the next one.
func (f *LogFlusher) drain(ctx context.Context) {
	if f.spool == nil {
		return
	}
	dctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	stats, err := f.spool.Stats(dctx)
	if err != nil {
		return
	}
	f.noteStats(stats)
	if stats.Pending == 0 {
		return
	}
	lease, err := f.spool.Acquire(dctx)
	if err != nil || lease == nil {
		return
	}
	defer lease.Release(context.Background())
	for round := 0; round < drainRounds; round++ {
		batches, err := f.spool.Oldest(dctx, drainBatches)
		if err != nil || len(batches) == 0 {
			break
		}
		for _, st := range batches {
			if !lease.Renew(dctx) {
				return
			}
			if !f.drainOne(dctx, lease, st) {
				f.refreshStats(dctx)
				return
			}
		}
	}
	f.refreshStats(dctx)
}

// drainOne handles one spooled batch, and reports whether the drain goes on.
func (f *LogFlusher) drainOne(ctx context.Context, lease *logspool.Lease, st logspool.Stored) bool {
	switch {
	case st.Corrupt || st.Batch == nil || !st.Batch.Verify(f.spoolKey):
		if lease.Drop(ctx, st.ID) == nil {
			f.countDropped(logspool.ReasonMAC, 1)
			slog.Error("log flusher: a spooled batch failed its integrity check and was dropped")
		}
		return true
	case st.Delivered:
		return lease.Remove(ctx, st.ID) == nil
	case !st.ExpireAt.IsZero() && time.Now().After(st.ExpireAt):
		if lease.Evict(ctx, st.ID) == nil {
			f.countDropped(logspool.ReasonAge, 1)
			slog.Warn("log flusher: a spooled batch passed its age bound and was dropped (its spend is kept for the budget seed)")
		}
		return true
	}
	body, err := st.Batch.Body()
	if err != nil {
		return false
	}
	status, code, err := f.post(ctx, body)
	switch {
	case err == nil && status < 400:
		return lease.Delivered(ctx, st.ID) == nil
	case err == nil && permanentRefusal(status, code):
		if lease.Remove(ctx, st.ID) == nil {
			f.countDropped(logspool.ReasonRefused, 1)
			slog.Error("log flusher: the webhook refused a spooled batch as malformed; it was removed", "status", status)
		}
		return true
	}
	st.Batch.Attempts++
	st.Batch.Seal(f.spoolKey)
	_ = lease.Update(ctx, st.Batch)
	return false
}

func (f *LogFlusher) refreshStats(ctx context.Context) {
	if stats, err := f.spool.Stats(ctx); err == nil {
		f.noteStats(stats)
	}
}

func (f *LogFlusher) noteStats(stats logspool.Stats) {
	f.spoolMu.Lock()
	f.spoolSeen = stats
	f.spoolMu.Unlock()
	if stats.Pending > 0 && !stats.Oldest.IsZero() && time.Since(stats.Oldest) > spoolOldestWarn {
		slog.Warn("log flusher: the oldest spooled batch is over an hour old",
			"pending_batches", stats.Pending, "oldest_age_seconds", int64(time.Since(stats.Oldest).Seconds()))
	}
}

// SpoolMetrics are the spool's gauges and counters (a metrics collector):
// batches waiting, their bytes, the oldest one's age, batches spooled, and
// batches dropped by reason. Read from the last drain, never from Redis at
// scrape time.
func (f *LogFlusher) SpoolMetrics() []metrics.Family {
	f.spoolMu.Lock()
	defer f.spoolMu.Unlock()
	age := 0.0
	if f.spoolSeen.Pending > 0 && !f.spoolSeen.Oldest.IsZero() {
		age = time.Since(f.spoolSeen.Oldest).Seconds()
	}
	dropped := metrics.Family{Name: "agentcc_logspool_dropped_batches_total", Type: "counter",
		Help: "Spooled request-log batches dropped undelivered, by reason"}
	for reason, n := range f.dropped {
		dropped.Samples = append(dropped.Samples, metrics.Sample{Labels: map[string]string{"reason": reason}, Value: float64(n)})
	}
	return []metrics.Family{
		{Name: "agentcc_logspool_pending_batches", Type: "gauge", Help: "Request-log batches waiting in the spool",
			Samples: []metrics.Sample{{Value: float64(f.spoolSeen.Pending)}}},
		{Name: "agentcc_logspool_bytes", Type: "gauge", Help: "Bytes the spooled request-log batches take",
			Samples: []metrics.Sample{{Value: float64(f.spoolSeen.Bytes)}}},
		{Name: "agentcc_logspool_oldest_age_seconds", Type: "gauge", Help: "Age of the oldest spooled request-log batch",
			Samples: []metrics.Sample{{Value: age}}},
		{Name: "agentcc_logspool_spooled_batches_total", Type: "counter", Help: "Request-log batches written to the spool",
			Samples: []metrics.Sample{{Value: float64(f.spooled)}}},
		dropped,
	}
}

func FormatLogsWebhookURL(controlPlaneURL string) string {
	return fmt.Sprintf("%s/agentcc/webhook/logs/", controlPlaneURL)
}
