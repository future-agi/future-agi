package mcpmock

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"math"
	"net/http"
	"strings"
	"sync"
	"time"
)

// maxReceived bounds what /__received keeps; older entries are dropped.
const maxReceived = 5000

// Behavior shapes the mock's answers until the next POST /__control/behavior
// replaces it. FailNext counts down; DropSession and Notify are actions that
// run once when posted and are never stored.
type Behavior struct {
	LatencyMS   int      `json:"latency_ms,omitempty"`   // delay before a tools/call answers
	FailNext    int      `json:"fail_next,omitempty"`    // the next n tools/call fail (HTTP 502, -32603)
	IsError     bool     `json:"is_error,omitempty"`     // tools/call results carry isError: true
	ResultBytes int      `json:"result_bytes,omitempty"` // pad the result text to this many bytes
	SSE         bool     `json:"sse,omitempty"`          // answer POSTs as an SSE stream
	PageSize    int      `json:"page_size,omitempty"`    // list pages of this size, with nextCursor
	Elicit      bool     `json:"elicit,omitempty"`       // tools/call asks for input once
	CostUSD     *float64 `json:"cost_usd,omitempty"`     // _meta cost on results, and the charged price
	DropSession bool     `json:"drop_session,omitempty"` // action: forget every legacy session now
	Notify      []string `json:"notify,omitempty"`       // action: send these notifications now
	LogMessages int      `json:"log_messages,omitempty"` // notifications/message sent before a result
	Progress    int      `json:"progress,omitempty"`     // notifications/progress sent before a result
}

// CredSeen is what the mock keeps of a credential header: whether it was
// sent and the SHA-256 of its value, never the value.
type CredSeen struct {
	Present bool   `json:"present"`
	SHA256  string `json:"sha256,omitempty"`
}

// Received is one message (or bare HTTP request) the mock was sent.
type Received struct {
	Seq         int64               `json:"seq"`
	Time        time.Time           `json:"time"`
	Transport   string              `json:"transport"` // http, sse (2024-11-05), stdio
	HTTPMethod  string              `json:"http_method,omitempty"`
	Path        string              `json:"path,omitempty"`
	Method      string              `json:"method,omitempty"` // JSON-RPC method; empty for a bare GET or DELETE
	ID          json.RawMessage     `json:"id,omitempty"`
	Name        string              `json:"name,omitempty"` // tools/call and prompts/get name, resources/read uri
	Era         string              `json:"era,omitempty"`  // the protocol version the message was served under
	SessionID   string              `json:"session_id,omitempty"`
	Headers     map[string]string   `json:"headers,omitempty"` // credential headers excluded
	Meta        json.RawMessage     `json:"_meta,omitempty"`
	Auth        CredSeen            `json:"auth"`                  // the --auth-header (default Authorization)
	Credentials map[string]CredSeen `json:"credentials,omitempty"` // other credential headers seen
	Principal   string              `json:"principal,omitempty"`   // the OAuth token's subject
	Rejected    string              `json:"rejected,omitempty"`    // why the mock refused it before serving, if it did
}

// Charge is one tools/call the mock received, as a paid API charges it: at
// receipt, whether or not the caller ever reads the result.
//
// The money oracle is /__charges's total_micros: the sum of price_usd over
// the billable entries. A 2026 round that answered input_required did no
// billable work: it is logged with input_required true, price_usd 0 and
// completed false (no tool result was delivered), and total_micros and
// billable_count leave it out. So a 2026 call that needed input is charged
// once, on the retry that completes it.
type Charge struct {
	Seq               int64           `json:"seq"`
	Tool              string          `json:"tool"`
	RequestIDSeen     json.RawMessage `json:"request_id_seen,omitempty"`
	ProgressTokenSeen json.RawMessage `json:"progress_token_seen,omitempty"`
	PriceUSD          float64         `json:"price_usd"` // 0 on an input_required round
	Completed         bool            `json:"completed"` // the tool's result was delivered
	InputRequired     bool            `json:"input_required,omitempty"`
}

func (c Charge) billable() bool { return !c.InputRequired }

// credentialWords: a header whose lower-case name contains one of these is a
// credential, kept as presence and a hash only. The gateway's api_key auth
// sends its key in any header the config names (auth.header), so the mock
// matches credential words in any header name; --credential-header adds names these miss.
var credentialWords = []string{"auth", "key", "token", "secret", "passw", "cookie", "credential", "signature"}

// verbatimHeaders match a credential word but carry no secret: kept verbatim.
var verbatimHeaders = map[string]bool{"idempotency-key": true}

// headerPolicy decides which request headers are credentials and which one
// /__received reports as auth (--auth-header, default Authorization).
type headerPolicy struct {
	authHeader string          // lower case
	extra      map[string]bool // --credential-header names, lower case
}

func newHeaderPolicy(authHeader string, extra []string) headerPolicy {
	p := headerPolicy{authHeader: strings.ToLower(authHeader), extra: map[string]bool{}}
	if p.authHeader == "" {
		p.authHeader = "authorization"
	}
	for _, name := range extra {
		p.extra[strings.ToLower(name)] = true
	}
	return p
}

func (p headerPolicy) isCredential(lower string) bool {
	if lower == p.authHeader || p.extra[lower] {
		return true
	}
	if verbatimHeaders[lower] {
		return false
	}
	for _, w := range credentialWords {
		if strings.Contains(lower, w) {
			return true
		}
	}
	return false
}

// State is everything the mock remembers between requests.
type State struct {
	mu        sync.Mutex
	seq       int64
	tools     []Tool
	resources []Resource
	prompts   []Prompt
	behavior  Behavior
	received  []Received
	charges   []*Charge
}

// NewState starts the mock on its fixtures.
func NewState(fix *Fixtures) *State {
	return &State{tools: fix.Tools, resources: fix.Resources, prompts: fix.Prompts}
}

// Tools returns the current tool set.
func (s *State) Tools() []Tool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]Tool(nil), s.tools...)
}

// SetTools replaces the tool set.
func (s *State) SetTools(tools []Tool) {
	s.mu.Lock()
	s.tools = tools
	s.mu.Unlock()
}

func (s *State) tool(name string) (Tool, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, t := range s.tools {
		if t.Name == name {
			return t, true
		}
	}
	return Tool{}, false
}

func (s *State) resourceList() []Resource {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]Resource(nil), s.resources...)
}

func (s *State) promptList() []Prompt {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]Prompt(nil), s.prompts...)
}

// Behavior returns the armed behaviour.
func (s *State) Behavior() Behavior {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.behavior
}

// SetBehavior replaces the armed behaviour; the one-shot actions are not kept.
func (s *State) SetBehavior(b Behavior) {
	b.DropSession, b.Notify = false, nil
	s.mu.Lock()
	s.behavior = b
	s.mu.Unlock()
}

// takeFailure reports whether this tools/call is one of the armed failures,
// counting it down.
func (s *State) takeFailure() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.behavior.FailNext <= 0 {
		return false
	}
	s.behavior.FailNext--
	return true
}

func (s *State) record(r Received) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.seq++
	r.Seq, r.Time = s.seq, time.Now().UTC()
	s.received = append(s.received, r)
	if len(s.received) > maxReceived {
		s.received = s.received[len(s.received)-maxReceived:]
	}
}

// ReceivedLast returns the last n entries (all when n <= 0) whose method
// matches method (any when empty).
func (s *State) ReceivedLast(n int, method string) []Received {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Received, 0, len(s.received))
	for _, r := range s.received {
		if method == "" || r.Method == method {
			out = append(out, r)
		}
	}
	if n > 0 && len(out) > n {
		out = out[len(out)-n:]
	}
	return out
}

// charge writes a cost-log entry at receipt and returns it, so the caller can
// mark it completed once the result has been delivered. An input_required
// round is logged at price 0 (see Charge).
func (s *State) charge(tool string, price float64, id, progressToken json.RawMessage, inputRequired bool) *Charge {
	if inputRequired {
		price = 0
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.seq++
	c := &Charge{Seq: s.seq, Tool: tool, RequestIDSeen: id, ProgressTokenSeen: progressToken,
		PriceUSD: price, InputRequired: inputRequired}
	s.charges = append(s.charges, c)
	return c
}

// complete marks a charge's tool result delivered. An input_required round
// delivered no tool result, so it stays incomplete.
func (s *State) complete(c *Charge) {
	if c == nil || c.InputRequired {
		return
	}
	s.mu.Lock()
	c.Completed = true
	s.mu.Unlock()
}

// Charges returns a copy of the cost log and its total in micros, each price
// rounded half-up to a micro. input_required rounds are priced 0, so the
// total is the billable entries' sum.
func (s *State) Charges() ([]Charge, int64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Charge, len(s.charges))
	var total int64
	for i, c := range s.charges {
		out[i] = *c
		total += int64(math.Floor(c.PriceUSD*1e6 + 0.5))
	}
	return out, total
}

// Reset clears the received and cost logs and the behaviour; the tool set stays.
func (s *State) Reset() {
	s.mu.Lock()
	s.received, s.charges, s.behavior = nil, nil, Behavior{}
	s.mu.Unlock()
}

// record splits request headers into the ones kept verbatim and the
// credential headers, kept as presence and hash: the auth header as auth, the
// others under credentials.
func (p headerPolicy) record(h http.Header) (map[string]string, CredSeen, map[string]CredSeen) {
	kept := map[string]string{}
	var auth CredSeen
	var creds map[string]CredSeen
	for name, values := range h {
		lower := strings.ToLower(name)
		value := strings.Join(values, ", ")
		if !p.isCredential(lower) {
			kept[lower] = value
			continue
		}
		seen := CredSeen{Present: true, SHA256: hashHex(value)}
		if lower == p.authHeader {
			auth = seen
			continue
		}
		if creds == nil {
			creds = map[string]CredSeen{}
		}
		creds[lower] = seen
	}
	return kept, auth, creds
}

func hashHex(v string) string {
	sum := sha256.Sum256([]byte(v))
	return hex.EncodeToString(sum[:])
}
