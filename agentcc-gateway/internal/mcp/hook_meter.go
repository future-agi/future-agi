package mcp

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"sync"
	"time"

	"github.com/oklog/ulid/v2"

	"github.com/futureagi/agentcc-gateway/internal/mcp/meter"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// hookRegisterRecord is step 2a of the tools/call chain (chain.go).
const hookRegisterRecord = "registerRecord"

// finalizerCloseRecord closes a call's record cell after its record
// finalizer. Its name is not FinalizerRecord, so it survives Adopt.
const finalizerCloseRecord = "recordClose"

// maxProtocolVersionRunes is M3's protocol_version column (varchar(16)): the
// record carries the caller's MCP-Protocol-Version header, cut to it.
const maxProtocolVersionRunes = 16

// registerRecord registers the record finalizer (FinalizerRecord), so every
// exit from here on, denials in hooks 3 to 16, the -32602 of a hidden tool
// and upstream failures included, emits one mcp-tool-call-v1 record through
// Deps.Events (plan section 6.17, MC.2). It sits right before resolveTenant
// so a policy_unavailable denial is recorded too; the org comes from the
// principal (hook 1). A call hook 2 gave no tool_call_id gets one here, so
// the record and the evidence block always carry it.
//
// The cell is also closed by a second finalizer, registered first so it runs
// after the record one: when a retry adopts a parked call (Adopt drops the
// retry's own record finalizer) and then panics before step 20, the retry's
// own cell would otherwise stay open.
//
// Owner: M15. Keep observeHook as its first statement.
func registerRecord(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookRegisterRecord)
	if cc.ToolCallID == "" {
		cc.ToolCallID = "tc_" + ulid.Make().String()
	}
	cell := &recordCell{s: s, id: cc.ToolCallID}
	openRecords.open(cc, cell)
	cc.Defer(finalizerCloseRecord, func() { openRecords.close(cell) })
	cc.Defer(FinalizerRecord, cell.emit)
	return nil
}

// discardToolStats is Deps.ToolStats' default: nothing is observed.
type discardToolStats struct{}

func (discardToolStats) Observe(*CallContext) {}

// --- The record of one logical call ---

// recordCell is one logical call's record state. Its finalizer reads the
// call context that finishes the call through the cell, not the one it was
// registered on: a parked call adopted by its retry (M28) is recorded once,
// with the retry's fields, under the parked tool_call_id.
type recordCell struct {
	s  *Server
	id string // the logical call's tool_call_id

	// Guarded by openRecords.mu.
	cc          *CallContext // the call context that finishes the call
	respondedAt time.Time    // step 20's clock reading; zero until it runs
}

// recordIndex finds a call's open record cell by its call context, and a
// parked call's by its tool_call_id, which an adopting retry takes over.
type recordIndex struct {
	mu     sync.Mutex
	byCall map[*CallContext]*recordCell
	byID   map[string]*recordCell
}

var openRecords = &recordIndex{byCall: map[*CallContext]*recordCell{}, byID: map[string]*recordCell{}}

func (x *recordIndex) open(cc *CallContext, cell *recordCell) {
	x.mu.Lock()
	defer x.mu.Unlock()
	cell.cc = cc
	x.byCall[cc] = cell
	x.byID[cell.id] = cell
}

// respond binds cc's logical call to cc, which is answering it now (step
// 20), and returns false when cc has no open record. When cc is a retry
// that adopted a parked call, the parked call's cell takes cc as its
// finishing context, and cc's own cell, whose finalizer the adoption
// dropped, is closed.
func (x *recordIndex) respond(cc *CallContext, at time.Time) bool {
	x.mu.Lock()
	defer x.mu.Unlock()
	own := x.byCall[cc]
	cell := own
	if adopted, ok := x.byID[cc.ToolCallID]; ok && adopted != own {
		if own != nil {
			delete(x.byID, own.id)
		}
		delete(x.byCall, adopted.cc)
		adopted.cc = cc
		x.byCall[cc] = adopted
		cell = adopted
	}
	if cell == nil {
		return false
	}
	cell.respondedAt = at
	return true
}

// close removes cell and returns the call context that finishes its call
// and when step 20 answered it. Closing a closed cell changes nothing.
func (x *recordIndex) close(cell *recordCell) (*CallContext, time.Time) {
	x.mu.Lock()
	defer x.mu.Unlock()
	if x.byCall[cell.cc] == cell {
		delete(x.byCall, cell.cc)
	}
	if x.byID[cell.id] == cell {
		delete(x.byID, cell.id)
	}
	return cell.cc, cell.respondedAt
}

// openRecordCount is the number of open record cells (tests).
func openRecordCount() int {
	openRecords.mu.Lock()
	defer openRecords.mu.Unlock()
	return len(openRecords.byCall) + len(openRecords.byID)
}

// emit is the record finalizer: it builds the call's record from the
// finishing call context, emits it, counts it and hands the call to
// Deps.ToolStats. It runs once (Defer, Park).
func (c *recordCell) emit() {
	cc, respondedAt := openRecords.close(c)
	deps := c.s.Deps()
	now := deps.Clock()
	if respondedAt.IsZero() { // step 20 never ran (a panic)
		respondedAt = now
	}
	rec := c.s.buildRecord(cc, respondedAt, now)
	deps.Events(EventKindToolCall, rec)
	meter.Default().Counters().Observe(counterKey(rec), rec.Outcome, rec.LatencyMs, now.UnixMilli())
	deps.ToolStats.Observe(cc)
}

// counterKey is rec's counter key. A call whose tool never resolved to a
// server (unknown or hidden, or denied before hook 6 resolved it) carries
// the name the caller sent, so its org counts it under meter.UnresolvedTool:
// otherwise every probe with a new name would open a counter for good. The
// unknown_tool code is checked too, so the rule holds even if hook 6 sets
// the server before the tool lookup fails.
func counterKey(rec ToolCallRecord) meter.Key {
	if rec.ServerID == "" || rec.ErrorCode == meter.CodeUnknownTool {
		return meter.Key{OrgID: rec.OrgID, Tool: meter.UnresolvedTool}
	}
	return meter.Key{OrgID: rec.OrgID, ServerID: rec.ServerID, Tool: rec.Tool}
}

// recordEvidenceNames are the cc.Evidence entries a record carries (plan
// section 6.34); any other name is not emitted.
var recordEvidenceNames = []string{"identity", "account_label", "policy", "flow", "shaped"}

// buildRecord is cc's mcp-tool-call-v1 record, answered at respondedAt and
// finished at now. is_error is the isError flag of the answer the client
// got: the upstream's, or true for an in-band denial; outcome says which.
//
// tool is the requested name, so a probe for a hidden tool leaves evidence;
// it and upstream_tool are cut to meter.MaxNameRunes, and protocol_version
// (the caller's header) to maxProtocolVersionRunes, NUL replaced
// (meter.Column), so no record exceeds M3's columns and fails the webhook
// batch it shares with other orgs. The agent fields need no cut here: G2's
// validators, which M8 reuses, already hold them to M3's lengths.
func (s *Server) buildRecord(cc *CallContext, respondedAt, now time.Time) ToolCallRecord {
	class := meter.Classify(s.classifyInput(cc))
	mode := recordContentMode(cc)
	redactor := meter.Default().Redactor(cc.Principal.OrgID)
	latency, upstream := callLatencies(cc, respondedAt)
	b := cc.Billing
	rec := ToolCallRecord{
		V: 1, EventID: cc.ToolCallID, ToolCallID: cc.ToolCallID, OrgID: cc.Principal.OrgID, RequestID: cc.RequestID,
		TsMs: now.UnixMilli(), StartedAt: cc.Timings.Started.UTC(),
		APIKeyID: cc.Principal.KeyID, PrincipalKind: string(cc.Principal.Kind), UserID: cc.Principal.UserID, ClientID: cc.Principal.ClientID,
		VirtualServerID: cc.VirtualServer, ServerID: cc.Upstream.ServerID, Tool: meter.CapName(cc.Tool.ExposedName),
		UpstreamTool: meter.CapName(cc.Tool.UpstreamName), ToolRevision: cc.Tool.Revision, Via: callVia(cc),
		Era: cc.Era, ProtocolVersion: meter.Column(cc.ProtocolVersion, maxProtocolVersionRunes),
		SessionHash: sessionHash(cc.SessionID), Agent: recordAgent(cc.Agent), GenerationID: cc.GenerationID,
		Outcome: class.Outcome, IsError: cc.IsError || (cc.Decision != nil && cc.Decision.InBand()),
		ErrorClass: models.ErrorClass(class.ErrorClass), ErrorOrigin: models.ErrorOrigin(class.ErrorOrigin), ErrorCode: class.ErrorCode,
		LatencyMs: latency, UpstreamLatencyMs: upstream, GatewayOverheadMs: latency - upstream,
		BytesIn: firstPositive(cc.Bytes.In, int64(len(cc.Args))), BytesOut: firstPositive(cc.Bytes.Out, int64(len(cc.Result))),
		BillingMode: callBillingMode(cc), CredentialType: recordCredentialType(cc), CredentialSource: recordCredentialSource(cc),
		UpstreamCostMicros: b.UpstreamMicros, BilledCostMicros: b.BilledMicros, MarkupMicros: b.MarkupMicros, MarkupBps: b.MarkupBps,
		CostSource: orDefault(b.CostSource, "none"), UsageEstimated: boolInt(b.UsageEstimated), Dispatched: cc.Dispatched,
		BudgetScope: cc.Budget.Scope,
		Guardrail: ToolCallGuardrail{Checked: cc.Guardrail.Checked, Blocked: cc.Guardrail.Blocked,
			Masked: cc.Guardrail.Masked, Shadow: cc.Guardrail.Shadow},
		ContentMode: mode,
		Arguments:   meter.Content(cc.Args, mode, redactor),
		Result:      meter.Content(cc.Result, mode, redactor),
	}
	if cc.Decision != nil && mode != meter.ModeNone {
		rec.ErrorMessage = meter.ErrorMessage(cc.Decision.Message, errorScrubber(redactor))
	}
	setRecordEvidence(&rec, cc)
	return rec
}

// errorScrubber scrubs a record's error text: the credential patterns the
// gateway scrubs from upstream text (privacy.RedactCredentials, with the
// stdio stderr marker, as transport_stdio.go and routing/errclass.go do since
// M13), then the org's or the global redactor, as the record's arguments and
// result are.
func errorScrubber(r *privacy.Redactor) func(string) string {
	return func(msg string) string {
		msg = privacy.RedactCredentials(msg, stderrMarker)
		if r != nil && r.ShouldRedact() {
			msg = r.Redact(msg)
		}
		return msg
	}
}

// classifyInput is what meter.Classify reads from a finished call.
func (s *Server) classifyInput(cc *CallContext) meter.Input {
	in := meter.Input{
		HasResult: cc.Executed || len(cc.Result) > 0,
		IsError:   cc.IsError,
		Cancelled: errors.Is(cc.Context().Err(), context.Canceled),
	}
	if d := cc.Decision; d != nil {
		in.Denied, in.Code = true, d.Code
		if meta, ok := d.Metadata.(map[string]any); ok {
			in.Attribution, _ = meta["attribution"].(string)
		}
	}
	// The upstream call used its whole time budget, or the caller's deadline
	// passed.
	elapsed := cc.Timings.UpstreamEnded.Sub(cc.Timings.UpstreamStarted)
	in.TimedOut = errors.Is(cc.Context().Err(), context.DeadlineExceeded) ||
		(cc.Dispatched && !cc.Executed && s.toolTimeout > 0 && elapsed >= s.toolTimeout)
	return in
}

// recordContentMode is the record's content mode: none with no org config,
// else D7's resolution of the org, key, header and global sources, then the
// stricter of that and cc.Terms.Retention (plan sections 6.13 and 6.27).
func recordContentMode(cc *CallContext) string {
	src := meter.ModeSources{OrgLoaded: cc.OrgConfig != nil, Retention: string(cc.Terms.Retention)}
	if !src.OrgLoaded {
		return meter.ModeNone
	}
	var header http.Header
	rawKey := ""
	if r := cc.Request(); r != nil {
		header, rawKey = r.Header, requestAPIKey(r)
	}
	src.Org = cc.OrgConfig.ContentMode()
	src.Key = meter.Default().KeyLogContent(cc.Principal.KeyID, rawKey)
	src.Request = privacy.RequestContentMode(header)
	src.Default = privacy.DefaultContentMode()
	return meter.ResolveMode(src)
}

// callLatencies are the call's latency (receipt to response) and the
// upstream's share of it, in ms.
func callLatencies(cc *CallContext, respondedAt time.Time) (latency, upstream int64) {
	latency = max(respondedAt.Sub(cc.Timings.Started).Milliseconds(), 0)
	if !cc.Timings.UpstreamStarted.IsZero() && !cc.Timings.UpstreamEnded.IsZero() {
		upstream = min(max(cc.Timings.UpstreamEnded.Sub(cc.Timings.UpstreamStarted).Milliseconds(), 0), latency)
	}
	return latency, upstream
}

// callBillingMode is cc's billing mode: byok until M18 says otherwise.
func callBillingMode(cc *CallContext) string {
	return orDefault(cc.Billing.Mode, BillingBYOK)
}

func recordCredentialType(cc *CallContext) string {
	if t := cc.Billing.CredentialType; t != "" {
		return t
	}
	switch {
	case callBillingMode(cc) == BillingManaged || cc.Credential.Source == CredentialManaged:
		return BillingManaged
	case cc.Credential.Source == CredentialOrgShared || cc.Credential.Source == CredentialUser:
		return BillingBYOK
	default:
		return string(CredentialNone)
	}
}

func recordCredentialSource(cc *CallContext) string {
	return orDefault(string(cc.Credential.Source), string(CredentialNone))
}

func recordAgent(a models.AgentContext) ToolCallAgent {
	return ToolCallAgent{
		Framework: a.Framework, AgentID: a.AgentID, AgentVersion: a.AgentVersion, RunID: a.RunID,
		ParentRunID: a.ParentRunID, RootRunID: a.RootRunID, TaskID: a.TaskID, TraceID: a.TraceID,
		Workflow: a.Workflow, Step: string(a.Step), Turn: a.Turn, SessionID: a.SessionID, IdentitySource: a.IdentitySource,
	}
}

// setRecordEvidence copies cc's PC.6 evidence entries onto rec.
func setRecordEvidence(rec *ToolCallRecord, cc *CallContext) {
	cc.mu.Lock()
	defer cc.mu.Unlock()
	fields := map[string]*json.RawMessage{
		"identity": &rec.Identity, "account_label": &rec.AccountLabel, "policy": &rec.Policy,
		"flow": &rec.Flow, "shaped": &rec.Shaped,
	}
	for _, name := range recordEvidenceNames {
		if v, ok := cc.Evidence[name]; ok {
			*fields[name] = append(json.RawMessage(nil), v...)
		}
	}
}

// sessionHash is the hex SHA-256 of a session id, or "" without one: a
// record never carries the session id itself.
func sessionHash(id string) string {
	if id == "" {
		return ""
	}
	sum := sha256.Sum256([]byte(id))
	return hex.EncodeToString(sum[:])
}

func firstPositive(a, b int64) int64 {
	if a > 0 {
		return a
	}
	return b
}

func orDefault(v, def string) string {
	if v == "" {
		return def
	}
	return v
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

// --- The record's wire shape ---

// MarshalJSON writes the record as mcp-tool-call-v1 asks: record.go's tags
// leave out an empty error_class, error_origin and agent field (M15 IR-2, at
// the W3b merge), and a nil guardrail list is written as [].
func (r ToolCallRecord) MarshalJSON() ([]byte, error) {
	type plain ToolCallRecord
	p := plain(r)
	for _, list := range []*[]string{&p.Guardrail.Checked, &p.Guardrail.Blocked, &p.Guardrail.Masked, &p.Guardrail.Shadow} {
		if *list == nil {
			*list = []string{}
		}
	}
	return json.Marshal(p)
}
