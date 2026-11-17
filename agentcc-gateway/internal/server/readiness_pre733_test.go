package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// pre733ReadyzAnswers are the key sync answers of each control-plane
// generation (internal/auth's sequence matrix has the full set of events).
func pre733ReadyzAnswers() map[string]map[string]interface{} {
	person := map[string]interface{}{"id": "person", "name": "person", "key_hash": auth.HashKey("sk-readyz-person"),
		"metadata": map[string]string{"org_id": "org-readyz", "user_id": "user-readyz"}}
	legacy := map[string]interface{}{"id": "legacy", "name": "legacy", "key_hash": auth.HashKey("sk-readyz-legacy"),
		"metadata": map[string]string{"org_id": "org-readyz"}}
	answer733 := func(result []interface{}, complete bool, issuedEver int) map[string]interface{} {
		return map[string]interface{}{"status": true, "result": result,
			"snapshot": map[string]interface{}{"complete": complete, "issued_ever": issuedEver},
			"retired":  []interface{}{}, "config_conflicts": []string{}}
	}
	return map[string]map[string]interface{}{
		"pre733":               {"status": true, "result": []interface{}{person, legacy}},
		"733":                  answer733([]interface{}{person}, true, 2),
		"733-fresh-empty":      answer733([]interface{}{}, true, 0),
		"733-incomplete-empty": answer733([]interface{}{}, false, 2),
	}
}

// 733 add.11 (a): a gateway whose control plane is pre-733 says so in its
// readiness answer ("control_plane": "pre-733" next to "status": "ready"),
// as its log does once (internal/auth's
// Test733Pre733ControlPlaneIsLoggedUntilThe733Latch), until the first 733
// answer latches 733 semantics; after that the field is gone for good, even
// if a later answer lacks the snapshot. Before the first list the gateway is
// not ready and reports no generation. Every ordering of up to three lists.
func Test733ReadyzReportsAPre733ControlPlaneUntilThe733Latch(t *testing.T) {
	answers := pre733ReadyzAnswers()
	steps := []string{"pre733", "733", "733-fresh-empty", "733-incomplete-empty"}
	var sequences [][]string
	var grow func(prefix []string)
	grow = func(prefix []string) {
		if len(prefix) > 0 {
			sequences = append(sequences, prefix)
		}
		if len(prefix) == 3 {
			return
		}
		for _, step := range steps {
			grow(append(append([]string(nil), prefix...), step))
		}
	}
	grow(nil)
	if want := 4 + 4*4 + 4*4*4; len(sequences) != want {
		t.Fatalf("sequences = %d, want %d", len(sequences), want)
	}

	var mu sync.Mutex
	var body map[string]interface{}
	app := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		b := body
		mu.Unlock()
		json.NewEncoder(w).Encode(b)
	}))
	defer app.Close()

	for _, seq := range sequences {
		t.Run(strings.Join(seq, ">"), func(t *testing.T) {
			cfg := config.DefaultConfig()
			cfg.ControlPlane = config.ControlPlaneConfig{URL: "http://app.invalid", SyncOnStartup: true}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatal(err)
			}
			// As main.go makes it: control-plane mode when a control plane URL is set.
			ks := auth.NewKeyStore(cfg.Auth, cfg.ControlPlane.SyncsKeys())
			srv := New(cfg, "", registry, pipeline.NewEngine(), ks, nil, nil, nil, testModelDBPtr(), nil, nil)
			readyEpoch733(srv)
			srv.ready.Store(true)
			readyz := func() (int, map[string]interface{}) {
				w := httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/readyz", nil))
				var got map[string]interface{}
				if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
					t.Fatalf("/readyz body %q: %v", w.Body.String(), err)
				}
				return w.Code, got
			}

			code, got := readyz()
			if _, reported := got["control_plane"]; code != http.StatusServiceUnavailable || reported {
				t.Fatalf("before the first list: /readyz = %d %v, want 503 without control_plane", code, got)
			}
			sawPre733, latched := false, false
			for i, step := range seq {
				mu.Lock()
				body = answers[step]
				mu.Unlock()
				if err := auth.SyncKeysFromControlPlane(context.Background(), app.URL, "", ks); err != nil {
					t.Fatalf("event %d (%s): key sync: %v", i+1, step, err)
				}
				if step == "pre733" {
					sawPre733 = true
				} else {
					latched = true
				}
				code, got := readyz()
				if code != http.StatusOK || got["status"] != "ready" {
					t.Errorf("event %d (%s): /readyz = %d %v, want 200 ready", i+1, step, code, got)
				}
				value, reported := got["control_plane"]
				if want := sawPre733 && !latched; reported != want || (want && value != "pre-733") {
					t.Errorf("event %d (%s): /readyz control_plane = %v (reported %v), want reported %v as \"pre-733\" (latched=%v)",
						i+1, step, value, reported, want, latched)
				}
			}
		})
	}

	// A gateway with no control plane is ready at once and reports none.
	t.Run("no control plane", func(t *testing.T) {
		cfg := config.DefaultConfig()
		registry, err := providers.NewRegistry(cfg)
		if err != nil {
			t.Fatal(err)
		}
		ks := auth.NewKeyStore(cfg.Auth, cfg.ControlPlane.SyncsKeys())
		srv := New(cfg, "", registry, pipeline.NewEngine(), ks, nil, nil, nil, testModelDBPtr(), nil, nil)
		readyEpoch733(srv)
		srv.ready.Store(true)
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/readyz", nil))
		if w.Code != http.StatusOK || strings.Contains(w.Body.String(), "control_plane") {
			t.Fatalf("/readyz = %d %s, want 200 without control_plane", w.Code, w.Body.String())
		}
	})
}
