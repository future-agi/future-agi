package keyscope

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"
)

// refusalsPath is the control plane's route for a scoped key's refusals,
// which Django writes to the org's audit log with the key's workspace and EE
// as the actor (futureagi/agentcc/views/ee_agent_key_policy.py).
const refusalsPath = "/agentcc/mcp/service/agent-key-refusals/"

// maxPending bounds the refusals held between flushes: a key hammering a
// refused route adds to one entry's count, and distinct entries past the
// bound are dropped and counted.
const maxPending = 1000

// maxEntriesPerKey bounds one key's distinct entries per flush window, so a
// key that varies the path cannot write hundreds of audit rows every flush:
// past it, the key's further refusals with the same code add to one entry,
// whose path is foldedPath.
const maxEntriesPerKey = 16

// foldedPath is the path of the entry a key's refusals past
// maxEntriesPerKey add to.
const foldedPath = "(further routes, folded: over 16 distinct in this window)"

// What the control plane accepts (futureagi/agentcc/views/
// ee_agent_key_policy.py RefusalEventSerializer): a method of at most 16
// characters and a path of 1 to 300 characters with no NUL. A batch with one
// event outside that would be refused whole, so each refusal is cleaned here.
const maxPathRunes = 300

// knownMethods are the methods a refusal keeps; any other is sent as
// otherMethod. PIPELINE is the keyscope plugin's (the pipeline does not keep
// the HTTP method).
var knownMethods = map[string]bool{
	http.MethodGet: true, http.MethodHead: true, http.MethodPost: true, http.MethodPut: true,
	http.MethodPatch: true, http.MethodDelete: true, http.MethodOptions: true,
	http.MethodConnect: true, http.MethodTrace: true, "PIPELINE": true,
}

const otherMethod = "OTHER"

// Refusal is one refused request of a scoped or budgeted key.
type Refusal struct {
	KeyID  string
	Code   string // CodeScopeDenied or CodeBudgetExhausted
	Method string
	Path   string
}

type refusalEntry struct {
	AgentKeyID string    `json:"agent_key_id"`
	Code       string    `json:"code"`
	Method     string    `json:"method"`
	Path       string    `json:"path"`
	Count      int       `json:"count"`
	FirstAt    time.Time `json:"first_at"`
	LastAt     time.Time `json:"last_at"`
}

// Reporter sends a scoped key's refusals to the control plane, so each is
// audited with the key's workspace. Refusals are grouped by (key, code,
// method, path) between flushes; a flush that fails keeps them for the next.
// A nil *Reporter reports nothing (no control plane).
type Reporter struct {
	url, token string
	client     *http.Client
	now        func() time.Time

	mu      sync.Mutex
	pending map[Refusal]*refusalEntry
	perKey  map[string]int // distinct entries per key in pending
	dropped int
}

// NewReporter returns a reporter to the control plane at baseURL, or nil
// when there is none.
func NewReporter(baseURL, adminToken string) *Reporter {
	if baseURL == "" {
		return nil
	}
	return &Reporter{
		url:     baseURL + refusalsPath,
		token:   adminToken,
		client:  &http.Client{Timeout: 10 * time.Second},
		now:     time.Now,
		pending: make(map[Refusal]*refusalEntry),
		perKey:  make(map[string]int),
	}
}

// clean makes a refusal one the control plane accepts: a method it does not
// know becomes OTHER; the path loses control and format characters
// (unicode Cc and Cf: NUL, CR, LF, bidi overrides) and invalid UTF-8, is cut
// to 300 characters, and is "-" when nothing is left. The method and the
// path come from the refused request (the model name, for the pipeline's
// refusals), so a client chooses them.
func clean(ref Refusal) Refusal {
	if !knownMethods[ref.Method] {
		ref.Method = otherMethod
	}
	path := strings.Map(func(r rune) rune {
		if unicode.In(r, unicode.Cc, unicode.Cf) {
			return -1
		}
		return r
	}, strings.ToValidUTF8(ref.Path, string(utf8.RuneError)))
	if utf8.RuneCountInString(path) > maxPathRunes {
		path = string([]rune(path)[:maxPathRunes])
	}
	if strings.TrimSpace(path) == "" {
		path = "-"
	}
	ref.Path = path
	return ref
}

// Report records a refusal for the next flush. It never blocks on the
// network.
// It logs a refusal once per flush window (when a new entry starts), so a
// key that hammers a refused route adds to a count, not to the log; with no
// control plane every refusal is logged, as nothing else records it.
// The refusal is cleaned first (clean), and a key past maxEntriesPerKey
// distinct entries in the window adds to its folded entry.
func (r *Reporter) Report(ref Refusal) {
	ref = clean(ref)
	if r == nil {
		logRefusal(ref)
		return
	}
	now := r.now().UTC()
	r.mu.Lock()
	defer r.mu.Unlock()
	r.add(ref, &refusalEntry{Count: 1, FirstAt: now, LastAt: now}, true)
}

// add puts e's count and times under ref's entry, folding ref when its key
// has maxEntriesPerKey distinct entries already. Called with mu held.
func (r *Reporter) add(ref Refusal, e *refusalEntry, log bool) {
	cur, ok := r.pending[ref]
	if !ok && r.perKey[ref.KeyID] >= maxEntriesPerKey {
		ref = Refusal{KeyID: ref.KeyID, Code: ref.Code, Method: otherMethod, Path: foldedPath}
		cur, ok = r.pending[ref]
	}
	if ok {
		cur.Count += e.Count
		if e.FirstAt.Before(cur.FirstAt) {
			cur.FirstAt = e.FirstAt
		}
		if e.LastAt.After(cur.LastAt) {
			cur.LastAt = e.LastAt
		}
		return
	}
	if len(r.pending) >= maxPending {
		r.dropped++
		return
	}
	if log {
		logRefusal(ref)
	}
	r.pending[ref] = &refusalEntry{AgentKeyID: ref.KeyID, Code: ref.Code, Method: ref.Method, Path: ref.Path,
		Count: e.Count, FirstAt: e.FirstAt, LastAt: e.LastAt}
	r.perKey[ref.KeyID]++
}

func logRefusal(ref Refusal) {
	slog.Warn("scoped key refused", "key_id", ref.KeyID, "code", ref.Code, "method", ref.Method, "path", ref.Path)
}

// Run flushes every interval until ctx ends, then once more.
func (r *Reporter) Run(ctx context.Context, interval time.Duration) {
	if r == nil {
		return
	}
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			flushCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			if err := r.Flush(flushCtx); err != nil {
				slog.Warn("scoped key refusals: final flush failed", "error", err)
			}
			cancel()
			return
		case <-t.C:
			if err := r.Flush(ctx); err != nil {
				slog.Warn("scoped key refusals: flush failed; kept for the next", "error", err)
			}
		}
	}
}

// Flush sends what is pending. On failure the refusals are kept (within the
// bound) for the next flush.
func (r *Reporter) Flush(ctx context.Context) error {
	if r == nil {
		return nil
	}
	r.mu.Lock()
	batch, dropped := r.pending, r.dropped
	r.pending, r.perKey, r.dropped = make(map[Refusal]*refusalEntry), make(map[string]int), 0
	r.mu.Unlock()
	if dropped > 0 {
		slog.Warn("scoped key refusals: dropped distinct refusals over the bound", "dropped", dropped, "bound", maxPending)
	}
	if len(batch) == 0 {
		return nil
	}
	events := make([]*refusalEntry, 0, len(batch))
	for _, e := range batch {
		events = append(events, e)
	}
	retry, err := r.post(ctx, events)
	if err != nil && retry {
		r.requeue(batch)
	}
	if err == nil {
		slog.Info("scoped key refusals sent to the control plane", "entries", len(events))
	}
	return err
}

func (r *Reporter) requeue(batch map[Refusal]*refusalEntry) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for ref, e := range batch {
		r.add(ref, e, false)
	}
}

// post sends one batch. retry is true when the batch may succeed later (the
// control plane was unreachable or answered 5xx); a 4xx would refuse it
// again, so it is dropped with the error.
func (r *Reporter) post(ctx context.Context, events []*refusalEntry) (retry bool, err error) {
	body, err := json.Marshal(map[string]any{"events": events})
	if err != nil {
		return false, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, r.url, bytes.NewReader(body))
	if err != nil {
		return false, err
	}
	req.Header.Set("Content-Type", "application/json")
	if r.token != "" {
		req.Header.Set("Authorization", "Bearer "+r.token)
	}
	resp, err := r.client.Do(req)
	if err != nil {
		return true, err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
	if resp.StatusCode/100 != 2 {
		return resp.StatusCode >= 500, fmt.Errorf("control plane answered %d to %d refusals", resp.StatusCode, len(events))
	}
	return false, nil
}
