package mock

import (
	"encoding/json"
	"net/http"
	"strings"
	"sync"
	"time"
)

// maxReceived bounds the requests /__received keeps; older ones are dropped.
const maxReceived = 2000

// State is everything the mock remembers between requests. It is shared with
// the ext package, which builds on the charges and the account balance.
type State struct {
	mu            sync.Mutex
	seq           int64
	received      []Received
	charges       []Charge
	logs          []json.RawMessage
	logBatches    int
	initialCredit int64 // micros; total_credits after a reset
	creditMicros  int64 // total_credits: the account balance plus everything charged
	counters      map[string]int
	armed         *ArmedBehavior
	onReset       []func()
}

// Received is one request the mock was sent, as /__received reports it.
type Received struct {
	Seq     int64             `json:"seq"`
	Time    time.Time         `json:"time"`
	Method  string            `json:"method"`
	Path    string            `json:"path"`
	Query   string            `json:"query,omitempty"`
	Headers map[string]string `json:"headers"`
	Body    json.RawMessage   `json:"body,omitempty"`
}

// Charge is one upstream charge: what a successful inference call cost.
type Charge struct {
	Bearer string `json:"-"`
	Model  string `json:"model"`
	Route  Route  `json:"route"`
	Micros int64  `json:"micros"`
}

// ArmedBehavior is an x-mock-behavior value applied to inference calls that
// carry no header of their own, optionally only for one upstream model. It is
// how a scenario reaches the mock through the gateway, which does not forward
// client headers upstream.
type ArmedBehavior struct {
	Behavior string `json:"behavior"`
	Model    string `json:"model,omitempty"`
}

// NewState returns the state for an account holding balanceMicros.
func NewState(balanceMicros int64) *State {
	return &State{initialCredit: balanceMicros, creditMicros: balanceMicros, counters: map[string]int{}}
}

// OnReset registers fn to run on every POST /__reset, after the state clears.
func (s *State) OnReset(fn func()) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.onReset = append(s.onReset, fn)
}

// Reset clears every request, charge, log and counter, and restores the
// account balance from the fixtures.
func (s *State) Reset() {
	s.mu.Lock()
	s.received, s.charges, s.logs = nil, nil, nil
	s.logBatches = 0
	s.creditMicros = s.initialCredit
	s.counters = map[string]int{}
	s.armed = nil
	hooks := append([]func(){}, s.onReset...)
	s.mu.Unlock()
	for _, fn := range hooks {
		fn()
	}
}

func (s *State) recordRequest(r *http.Request, body []byte) {
	headers := make(map[string]string, len(r.Header))
	for k, v := range r.Header {
		headers[strings.ToLower(k)] = strings.Join(v, ", ")
	}
	rec := Received{Time: time.Now().UTC(), Method: r.Method, Path: r.URL.Path, Query: r.URL.RawQuery, Headers: headers}
	if len(body) > 0 {
		if json.Valid(body) {
			rec.Body = append(json.RawMessage(nil), body...)
		} else {
			rec.Body, _ = json.Marshal(string(body))
		}
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.seq++
	rec.Seq = s.seq
	s.received = append(s.received, rec)
	if len(s.received) > maxReceived {
		s.received = append([]Received(nil), s.received[len(s.received)-maxReceived:]...)
	}
}

// ReceivedRequests returns the last n received requests whose path contains
// pathFilter, oldest first. n <= 0 returns all of them.
func (s *State) ReceivedRequests(n int, pathFilter string) []Received {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []Received
	for _, r := range s.received {
		if strings.Contains(r.Path, pathFilter) {
			out = append(out, r)
		}
	}
	if n > 0 && len(out) > n {
		out = out[len(out)-n:]
	}
	return append([]Received{}, out...)
}

// RecordCharge records what an upstream call cost, against the bearer that made it.
func (s *State) RecordCharge(c Charge) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.charges = append(s.charges, c)
}

// TotalChargeMicros is the sum of every charge since the last reset.
func (s *State) TotalChargeMicros() (total int64, count int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.totalLocked(), len(s.charges)
}

func (s *State) totalLocked() (total int64) {
	for _, c := range s.charges {
		total += c.Micros
	}
	return total
}

// ChargesByBearer sums the charges per bearer token. Its values add up to
// TotalChargeMicros.
func (s *State) ChargesByBearer() map[string]int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := map[string]int64{}
	for _, c := range s.charges {
		out[c.Bearer] += c.Micros
	}
	return out
}

// BalanceMicros is the account's remaining credit: total credits less charges.
func (s *State) BalanceMicros() int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.creditMicros - s.totalLocked()
}

// CreditMicros is the account's total credits (balance plus everything charged).
func (s *State) CreditMicros() int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.creditMicros
}

// SetBalanceMicros makes the remaining balance b, keeping the charges.
func (s *State) SetBalanceMicros(b int64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.creditMicros = b + s.totalLocked()
}

// AppendLogs stores a log-webhook delivery: {"logs": [...]} adds each entry,
// any other JSON value is stored as one entry.
func (s *State) AppendLogs(body []byte) error {
	var batch struct {
		Logs []json.RawMessage `json:"logs"`
	}
	entries := []json.RawMessage{append(json.RawMessage(nil), body...)}
	if err := json.Unmarshal(body, &batch); err == nil && batch.Logs != nil {
		entries = batch.Logs
	} else if !json.Valid(body) {
		return errInvalidJSON
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.logs = append(s.logs, entries...)
	s.logBatches++
	return nil
}

// Logs returns the last n stored log entries (all when n <= 0) and the number
// of deliveries received.
func (s *State) Logs(n int) ([]json.RawMessage, int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := s.logs
	if n > 0 && len(out) > n {
		out = out[len(out)-n:]
	}
	return append([]json.RawMessage{}, out...), s.logBatches
}

// Arm sets the behaviour applied to header-less inference calls, with fresh
// counters for its directives. nil disarms.
func (s *State) Arm(b *ArmedBehavior) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.armed = b
	if b != nil {
		for _, tok := range splitDirectives(b.Behavior) {
			delete(s.counters, tok)
		}
	}
}

// Armed returns the armed behaviour, or nil.
func (s *State) Armed() *ArmedBehavior {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.armed == nil {
		return nil
	}
	cp := *s.armed
	return &cp
}

// countUse counts one more request carrying directive token tok and returns
// how many have, this one included.
func (s *State) countUse(tok string) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.counters[tok]++
	return s.counters[tok]
}
