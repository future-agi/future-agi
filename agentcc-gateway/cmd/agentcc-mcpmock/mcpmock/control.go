package mcpmock

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"strconv"
	"strings"
)

// registerControl mounts the control API. It takes no credential: the mock
// is a loopback test tool.
//
//	POST /__control/tools     replace the tool set, then send tools list_changed
//	POST /__control/behavior  replace the armed Behavior; drop_session and notify run once
//	GET  /__control/behavior  the armed Behavior
//	POST /__control/reset     clear /__received, /__charges and the behaviour
//	POST /__control/oauth/token  mint an access token (OAuth mode)
//	GET  /__received?last=N&method=M
//	GET  /__charges           the cost log; total_micros (the money oracle) and billable_count leave out input_required rounds
//	GET  /__health
func (m *Mock) registerControl(mux *http.ServeMux) {
	mux.HandleFunc("POST /__control/tools", m.controlTools)
	mux.HandleFunc("POST /__control/behavior", m.controlBehavior)
	mux.HandleFunc("GET /__control/behavior", func(w http.ResponseWriter, _ *http.Request) {
		_ = writeJSON(w, http.StatusOK, map[string]any{"behavior": m.state.Behavior()})
	})
	mux.HandleFunc("POST /__control/reset", func(w http.ResponseWriter, _ *http.Request) {
		m.state.Reset()
		_ = writeJSON(w, http.StatusOK, map[string]any{"ok": true})
	})
	mux.HandleFunc("POST /__control/oauth/token", m.controlMintToken)
	mux.HandleFunc("GET /__received", m.controlReceived)
	mux.HandleFunc("GET /__charges", func(w http.ResponseWriter, _ *http.Request) {
		charges, total := m.state.Charges()
		billable := 0
		for _, ch := range charges {
			if ch.billable() {
				billable++
			}
		}
		_ = writeJSON(w, http.StatusOK, map[string]any{"charges": charges, "total_micros": total,
			"count": len(charges), "billable_count": billable})
	})
	mux.HandleFunc("GET /__health", m.controlHealth)
}

func controlError(w http.ResponseWriter, status int, msg string) {
	_ = writeJSON(w, status, map[string]any{"ok": false, "error": msg})
}

func (m *Mock) controlTools(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, maxBodyBytes))
	if err != nil {
		controlError(w, http.StatusBadRequest, "cannot read the body")
		return
	}
	tools, err := ParseTools(body)
	if err != nil {
		controlError(w, http.StatusBadRequest, err.Error())
		return
	}
	m.state.SetTools(tools)
	m.Broadcast("notifications/tools/list_changed", nil)
	_ = writeJSON(w, http.StatusOK, map[string]any{"ok": true, "tools": len(tools)})
}

func (m *Mock) controlBehavior(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		controlError(w, http.StatusBadRequest, "cannot read the body")
		return
	}
	var b Behavior
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.DisallowUnknownFields()
	if len(bytes.TrimSpace(body)) > 0 {
		if err := dec.Decode(&b); err != nil {
			controlError(w, http.StatusBadRequest, "behavior: "+err.Error())
			return
		}
	}
	if b.LatencyMS < 0 || b.FailNext < 0 || b.ResultBytes < 0 || b.PageSize < 0 || b.LogMessages < 0 || b.Progress < 0 {
		controlError(w, http.StatusBadRequest, "behavior: counts and sizes must be >= 0")
		return
	}
	if b.CostUSD != nil && *b.CostUSD < 0 {
		controlError(w, http.StatusBadRequest, "behavior: cost_usd must be >= 0")
		return
	}
	dropped := 0
	if b.DropSession {
		dropped = m.sessions.dropAll()
	}
	notified := []string{}
	for _, n := range b.Notify {
		if !strings.HasPrefix(n, "notifications/") {
			n = "notifications/" + n
		}
		m.Broadcast(n, nil)
		notified = append(notified, n)
	}
	m.state.SetBehavior(b)
	_ = writeJSON(w, http.StatusOK, map[string]any{"ok": true, "behavior": m.state.Behavior(),
		"dropped_sessions": dropped, "notified": notified})
}

func (m *Mock) controlReceived(w http.ResponseWriter, r *http.Request) {
	last := 0
	if v := r.URL.Query().Get("last"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 0 {
			controlError(w, http.StatusBadRequest, "last must be an integer >= 0")
			return
		}
		last = n
	}
	_ = writeJSON(w, http.StatusOK, map[string]any{"received": m.state.ReceivedLast(last, r.URL.Query().Get("method"))})
}

func (m *Mock) controlHealth(w http.ResponseWriter, _ *http.Request) {
	auth := m.cfg.Auth
	if strings.HasPrefix(auth, "bearer:") {
		auth = "bearer" // never the token
	}
	mode := "http"
	if m.cfg.Stdio {
		mode = "stdio"
	}
	_ = writeJSON(w, http.StatusOK, map[string]any{
		"ok": true, "mode": mode, "era": m.cfg.Era.Name, "path": m.cfg.Path, "auth": auth,
		"tools": len(m.listedTools()), "sessions": m.sessions.count(), "listeners": m.listenerCount(),
	})
}
