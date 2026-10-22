// Package capmock is a stand-in for the capability vendors the gateway's
// capability packages call (search, fetch, documents, speech, embeddings and rerank,
// browsers, sandbox usage, media jobs, connectors, payments), serving
// vendor-shaped fixtures with no real key. Each vendor is a fake under its
// own path prefix (/exa/search, /azure-di/...); GET /__fakes lists them.
//
// Control API:
//
//	GET    /__health              {ok: true}
//	GET    /__fakes               {fakes: [{name, routes, cost_path, async}]}
//	GET    /__received?fake=&last=n   what the fakes saw, oldest first; credential
//	                              headers are kept as sha256 only
//	POST   /__arm                 {fake, behavior, calls?}: the directives (see
//	                              behavior) for the fake's next calls without a
//	                              header (calls 0: until cleared)
//	DELETE /__arm[?fake=]         clear one fake's arming, or all
//	POST   /__reset               forget calls, arming, jobs, files and sessions
//	GET    /__files/{id}          a file URL a fake returned (403 once expired)
package capmock

import (
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

//go:embed fixtures/*.json
var fixtures embed.FS

// Received is one data-plane request a fake saw.
type Received struct {
	Seq     int               `json:"seq"`
	Fake    string            `json:"fake"`
	Method  string            `json:"method"`
	Path    string            `json:"path"`
	Query   string            `json:"query,omitempty"`
	Headers map[string]string `json:"headers"`
	Body    any               `json:"body"`
}

type armed struct {
	behavior string
	calls    int // 0: until cleared
}

// Mock is the capability mock. Its zero value is not usable; call New.
type Mock struct {
	mu       sync.Mutex
	base     string
	now      func() time.Time
	seq      int
	received []Received
	armed    map[string]armed
	calls    map[string]int
	jobs     map[string]*job
	files    map[string]file
	sessions map[string]*session
	fakes    map[string]*fake
	mux      *http.ServeMux
}

// New returns a mock whose minted URLs start with base.
func New(base string) *Mock {
	m := &Mock{base: strings.TrimSuffix(base, "/"), now: time.Now, mux: http.NewServeMux(), fakes: map[string]*fake{}}
	m.reset()
	m.mux.HandleFunc("GET /__health", func(w http.ResponseWriter, _ *http.Request) { writeJSON(w, 200, map[string]bool{"ok": true}) })
	m.mux.HandleFunc("GET /__fakes", m.handleFakes)
	m.mux.HandleFunc("GET /__received", m.handleReceived)
	m.mux.HandleFunc("POST /__arm", m.handleArm)
	m.mux.HandleFunc("DELETE /__arm", m.handleDisarm)
	m.mux.HandleFunc("POST /__reset", func(w http.ResponseWriter, _ *http.Request) {
		m.reset()
		writeJSON(w, 200, map[string]bool{"reset": true})
	})
	m.mux.HandleFunc("GET /__files/{id}", m.handleFile)
	for _, f := range allFakes() {
		m.fakes[f.name] = f
		for _, rt := range f.routes {
			m.mux.HandleFunc(rt.pattern, m.serveFake(f, rt.h))
		}
	}
	return m
}

// SetBase sets the base URL minted URLs start with (the bound address).
func (m *Mock) SetBase(base string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.base = strings.TrimSuffix(base, "/")
}

// Handler serves the fakes and the control API.
func (m *Mock) Handler() http.Handler { return http.HandlerFunc(m.serveHTTP) }

// serveHTTP routes through the mux, except Jina Reader's paths, which embed
// a URL (/jina/https://...) whose double slash the mux would clean away.
func (m *Mock) serveHTTP(w http.ResponseWriter, r *http.Request) {
	if target, ok := strings.CutPrefix(r.URL.Path, "/jina/"); ok && r.Method == http.MethodGet {
		r.SetPathValue("target", target)
		m.serveFake(m.fakes["jina"], jinaReader)(w, r)
		return
	}
	m.mux.ServeHTTP(w, r)
}

func (m *Mock) reset() {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.received, m.armed, m.calls = nil, map[string]armed{}, map[string]int{}
	m.jobs, m.files, m.sessions = map[string]*job{}, map[string]file{}, map[string]*session{}
}

// serveFake records the call, applies its directives and then runs h.
func (m *Mock) serveFake(f *fake, h handler) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(io.LimitReader(r.Body, 10<<20))
		if err != nil {
			writeJSON(w, http.StatusBadRequest, vendorError(400, "unreadable body"))
			return
		}
		directives, n := m.record(f, r, body)
		b, err := parseBehavior(directives, f)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": map[string]string{"code": "invalid_capmock_directive", "message": err.Error()}})
			return
		}
		if b.delay > 0 {
			select {
			case <-time.After(b.delay):
			case <-r.Context().Done():
				return
			}
		}
		if b.drop {
			dropConnection(w)
			return
		}
		if code, fail := b.failing(n); fail {
			if code == http.StatusTooManyRequests {
				w.Header().Set("Retry-After", "1")
			}
			writeJSON(w, code, vendorError(code, fmt.Sprintf("capmock: %s answered %d by directive", f.name, code)))
			return
		}
		h(m, &call{w: w, r: r, fake: f, body: body, b: b, id: m.nextID(f.name)})
	}
}

// record keeps the request and returns its directives (its header, else
// the fake's arming) and the fake's call count including it.
func (m *Mock) record(f *fake, r *http.Request, body []byte) (string, int) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.seq++
	m.calls[f.name]++
	m.received = append(m.received, Received{Seq: m.seq, Fake: f.name, Method: r.Method, Path: r.URL.Path,
		Query: r.URL.RawQuery, Headers: redactHeaders(r.Header), Body: decodeBody(body)})
	if v := r.Header.Get(BehaviorHeader); v != "" {
		return v, m.calls[f.name]
	}
	a, ok := m.armed[f.name]
	if !ok {
		return "", m.calls[f.name]
	}
	if a.calls > 0 {
		if a.calls--; a.calls == 0 {
			delete(m.armed, f.name)
		} else {
			m.armed[f.name] = a
		}
	}
	return a.behavior, m.calls[f.name]
}

func (m *Mock) nextID(prefix string) string {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.seq++
	return fmt.Sprintf("%s-%06d", strings.ReplaceAll(prefix, "-", ""), m.seq)
}

func (m *Mock) baseURL() string {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.base
}

func (m *Mock) handleFakes(w http.ResponseWriter, _ *http.Request) {
	type row struct {
		Name     string   `json:"name"`
		Routes   []string `json:"routes"`
		CostPath string   `json:"cost_path,omitempty"`
		Async    bool     `json:"async"`
	}
	var out []row
	for _, f := range allFakes() {
		r := row{Name: f.name, CostPath: f.costPath, Async: f.failWord != ""}
		for _, rt := range f.routes {
			r.Routes = append(r.Routes, rt.pattern)
		}
		out = append(out, r)
	}
	writeJSON(w, 200, map[string]any{"fakes": out})
}

func (m *Mock) handleReceived(w http.ResponseWriter, r *http.Request) {
	fakeName := r.URL.Query().Get("fake")
	last, _ := strconv.Atoi(r.URL.Query().Get("last"))
	m.mu.Lock()
	var out []Received
	for _, rec := range m.received {
		if fakeName == "" || rec.Fake == fakeName {
			out = append(out, rec)
		}
	}
	m.mu.Unlock()
	if last > 0 && len(out) > last {
		out = out[len(out)-last:]
	}
	if out == nil {
		out = []Received{}
	}
	writeJSON(w, 200, map[string]any{"received": out})
}

func (m *Mock) handleArm(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Fake     string `json:"fake"`
		Behavior string `json:"behavior"`
		Calls    int    `json:"calls"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Calls < 0 {
		writeJSON(w, 400, map[string]string{"error": "want {fake, behavior, calls >= 0}"})
		return
	}
	f, ok := m.fakes[req.Fake]
	if !ok {
		writeJSON(w, 400, map[string]string{"error": "unknown fake " + strconv.Quote(req.Fake)})
		return
	}
	if _, err := parseBehavior(req.Behavior, f); err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	m.mu.Lock()
	m.armed[req.Fake] = armed{behavior: req.Behavior, calls: req.Calls}
	m.calls[req.Fake] = 0
	m.mu.Unlock()
	writeJSON(w, 200, map[string]any{"armed": req.Fake, "behavior": req.Behavior, "calls": req.Calls})
}

func (m *Mock) handleDisarm(w http.ResponseWriter, r *http.Request) {
	m.mu.Lock()
	if name := r.URL.Query().Get("fake"); name != "" {
		delete(m.armed, name)
	} else {
		m.armed = map[string]armed{}
	}
	m.mu.Unlock()
	writeJSON(w, 200, map[string]bool{"cleared": true})
}

// credentialWords mark a header kept as its sha256 only.
var credentialWords = []string{"auth", "key", "token", "secret", "signature", "cookie", "credential", "passw"}

func redactHeaders(h http.Header) map[string]string {
	out := map[string]string{}
	for name, vals := range h {
		v := strings.Join(vals, ", ")
		lower := strings.ToLower(name)
		for _, word := range credentialWords {
			if strings.Contains(lower, word) {
				sum := sha256.Sum256([]byte(v))
				v = "sha256:" + hex.EncodeToString(sum[:])
				break
			}
		}
		out[lower] = v
	}
	return out
}

func decodeBody(body []byte) any {
	if len(body) == 0 {
		return nil
	}
	var v any
	if json.Unmarshal(body, &v) == nil {
		return v
	}
	return string(body)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// vendorError is the error body a directive's failure answers with.
func vendorError(code int, msg string) map[string]any {
	return map[string]any{"error": map[string]any{"code": code, "type": http.StatusText(code), "message": msg}}
}

func dropConnection(w http.ResponseWriter) {
	hj, ok := w.(http.Hijacker)
	if !ok {
		panic(http.ErrAbortHandler)
	}
	conn, _, err := hj.Hijack()
	if err == nil {
		conn.Close()
	}
}
