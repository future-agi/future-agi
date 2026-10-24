package capability

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strconv"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Response headers every capability response carries.
const (
	HeaderProvider    = "x-agentcc-capability-provider"
	HeaderBillingMode = "x-agentcc-billing-mode"
)

// MetaBillingKey is the MCP _meta key of a built-in call's billing, which
// the MCP layer copies into its tool-call record and strips.
const MetaBillingKey = "io.futureagi/billing"

// logEvalExport is the log-evidence name the request log writes as the top
// level eval_export.
const logEvalExport = "eval_export"

// MetaNoPerfDisclosure is the request metadata key (value "true") set when a
// call tried a vendor whose terms forbid disclosing its performance
// (no_perf_disclosure). The request's latency is then that vendor's, so the
// request log and analytics must not show it; the flag rides in the log
// entry's open metadata object (log-entry-v2's evidence object is closed),
// which the request log keeps with the entry.
const MetaNoPerfDisclosure = "no_perf_disclosure"

// markNoPerfDisclosure flags rc as having tried a no_perf_disclosure vendor.
func markNoPerfDisclosure(rc *models.RequestContext) {
	rc.Metadata[MetaNoPerfDisclosure] = "true"
}

// calls holds the capability call record of each request in flight. A record
// lives from Do until the request's finalizers run, so the post plugins (the
// capability publisher, then logging and tracing) read it while the request
// is still open.
var calls sync.Map // *models.RequestContext -> *Call

// Call is what a capability call recorded about itself: its attempts and
// billing, which Publish writes onto the request, and its content, which the
// request log and spans keep only as RetentionFor allows.
type Call struct {
	mu                sync.Mutex
	capability        string
	retentionOverride string
	card              *Card // of the vendor that served, else the last one tried
	request           json.RawMessage
	response          json.RawMessage
	usage             *Usage
	attempts          []models.Attempt
	billing           callBilling
	published         int // attempts already on the request
	next              int // the last attempt index nextIndex handed out
}

// callBilling is the request-level billing of a call, as Publish writes it on
// rc.Billing.
type callBilling struct {
	mode             string
	credentialType   models.CredentialType
	upstreamMicros   int64
	billedMicros     int64
	landedMicros     int64
	markupBps        int
	processingFeeBps int
	costSource       models.CostSource
	estimated        bool
}

// CallOf is rc's capability call record, if Do ran on rc.
func CallOf(rc *models.RequestContext) (*Call, bool) {
	call := callOf(rc)
	return call, call != nil
}

func callOf(rc *models.RequestContext) *Call {
	if v, ok := calls.Load(rc); ok {
		return v.(*Call)
	}
	return nil
}

// attachCall returns rc's call record, creating it (and the finalizer that
// drops it) on the request's first capability call.
func attachCall(rc *models.RequestContext, capability string, req Request) *Call {
	call := &Call{capability: capability, request: req.Body, retentionOverride: req.Org.ToolsPolicy().RetentionOverride}
	if prev, loaded := calls.LoadOrStore(rc, call); loaded {
		call = prev.(*Call)
		call.mu.Lock()
		call.capability, call.request = capability, req.Body
		call.mu.Unlock()
		return call
	}
	rc.AddFinalizer(func() { calls.Delete(rc) })
	return call
}

// Card is the card of the vendor whose result the call returned (else the
// last vendor tried), or nil.
func (c *Call) Card() *Card {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.card
}

// Request is the normalized request as it was sent.
func (c *Call) Request() json.RawMessage {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.request
}

// Response is the vendor's response, nil when no vendor answered.
func (c *Call) Response() json.RawMessage {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.response
}

// MetadataRequest describes the request without its content: the capability
// and the size of the body, and nothing derived from the body's content.
func (c *Call) MetadataRequest() json.RawMessage {
	c.mu.Lock()
	defer c.mu.Unlock()
	return marshal(map[string]any{
		"capability":     c.capability,
		"body_bytes":     len(c.request),
		"retention":      string(models.ContentModeMetadata),
		"content_elided": true,
	})
}

// MetadataResponse describes the response without its content: the vendor,
// the units and the size of the result.
func (c *Call) MetadataResponse() json.RawMessage {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.response == nil {
		return nil
	}
	out := map[string]any{"result_bytes": len(c.response), "content_elided": true}
	if c.usage != nil {
		out["provider"], out["units"] = c.usage.Provider, c.usage.Units
	}
	return marshal(out)
}

func (c *Call) addAttempt(a models.Attempt) {
	c.mu.Lock()
	c.attempts = append(c.attempts, a)
	c.mu.Unlock()
}

// served records the vendor that answered, its result and the call's usage
// and billing.
func (c *Call) served(card *Card, response json.RawMessage, usage Usage, b callBilling) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.card, c.response, c.usage, c.billing = card, response, &usage, b
}

// tried records the card of a vendor that failed, when none served.
func (c *Call) tried(card *Card, b callBilling) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.usage == nil {
		c.card, c.billing = card, b
	}
}

// Publish writes rc's capability call onto the request: its attempts (in
// order, with their ids) and the request's billing. The capability post
// plugin calls it after the cost plugin, which leaves a request with no
// attempts and no chat response alone, so the request log and spans read
// the core's figures; a caller outside the pipeline (an MCP built-in) calls
// it itself. Calling it again writes only what is new.
func Publish(rc *models.RequestContext) {
	call := callOf(rc)
	if call == nil {
		return
	}
	call.mu.Lock()
	pending := append([]models.Attempt(nil), call.attempts[call.published:]...)
	call.published = len(call.attempts)
	b := call.billing
	call.mu.Unlock()
	if len(pending) == 0 {
		return // refused before any attempt, or already published
	}
	for _, a := range pending {
		rc.AddAttempt(a)
	}
	rb := &rc.Billing
	rb.Mode, rb.CredentialType = b.mode, b.credentialType
	rb.UpstreamMicros, rb.BilledMicros, rb.LandedMicros = b.upstreamMicros, b.billedMicros, b.landedMicros
	rb.MarkupMicros, rb.MarkupBps = max(b.billedMicros-b.upstreamMicros, 0), b.markupBps
	rb.UpstreamFeeBps, rb.ProcessingFeeBps = 0, b.processingFeeBps
	rb.CostSource, rb.UsageEstimated = b.costSource, b.estimated
	publishCost(rc, b)
}

// publishCost writes the request's cost metadata as the cost plugin writes
// a model call's, so budgets, alerts, metrics, spans and the request log
// count capability spend: cost is what the org was billed for a managed
// call, and the upstream estimate for a BYOK one. A request whose key is a
// prepaid key (key_type managed) gets no cost: the core settles the call on
// the org's ledger.
func publishCost(rc *models.RequestContext, b callBilling) {
	if b.costSource != "" {
		rc.Metadata["cost_source"] = string(b.costSource)
	}
	if rc.Metadata["key_type"] == "managed" {
		return
	}
	if b.mode == BillingManaged {
		rc.Metadata["cost"] = formatMicros(b.billedMicros)
		rc.Metadata["cost_base"] = formatMicros(b.upstreamMicros)
		rc.Metadata["cost_markup_percent"] = strconv.FormatFloat(float64(b.markupBps)/100, 'f', -1, 64)
		return
	}
	rc.Metadata["cost"] = formatMicros(b.upstreamMicros)
}

// formatMicros is micros in dollars with six decimals, as the cost plugin writes cost.
func formatMicros(m int64) string {
	return fmt.Sprintf("%d.%06d", m/1_000_000, m%1_000_000)
}

// recordServed puts what the client and the request log see on rc: the
// provider and model, the two capability headers, and the eval_export flag
// of a vendor whose terms forbid analysing its results.
func recordServed(rc *models.RequestContext, capability string, cand candidate, mode string) {
	rc.Provider = cand.vendor
	rc.Model = capability + "/" + cand.sku.SKU
	rc.ResolvedModel = rc.Model
	rc.SetResponseHeader(HeaderProvider, cand.vendor)
	rc.SetResponseHeader(HeaderBillingMode, mode)
	if cand.card.EvalExport == EvalExportForbidden {
		rc.SetLogEvidence(logEvalExport, EvalExportForbidden)
	}
}

// billingMeta is the MCP billing meta of a served call.
func billingMeta(usage Usage, upstreamMicros, billedMicros int64) map[string]any {
	return map[string]any{MetaBillingKey: map[string]any{
		"billing_mode":    usage.BillingMode,
		"upstream_micros": upstreamMicros,
		"billed_micros":   billedMicros,
		"cost_source":     usage.CostSource,
	}}
}

func microsUSD(micros int64) float64 { return billing.MicrosToUSD(micros) }

func sha256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func marshal(v any) json.RawMessage {
	b, err := json.Marshal(v)
	if err != nil {
		return nil
	}
	return b
}
