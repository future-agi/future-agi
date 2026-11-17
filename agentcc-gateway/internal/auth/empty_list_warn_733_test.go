package auth

import (
	"bytes"
	"log/slog"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// An empty key list that is not an authoritative snapshot keeps the keys the
// store holds. Only a store that holds synced keys has anything to keep, so an
// app with no keys yet (a fresh install answers issued_ever 0, an older app no
// snapshot) is not a WARN on every periodic sync, in either mode. A store that
// keeps a synced key still warns, and keeps it.
func Test733NonAuthoritativeEmptyListWarnsOnlyWhenKeepingSyncedKeys(t *testing.T) {
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, &slog.HandlerOptions{Level: slog.LevelWarn})))
	t.Cleanup(func() { slog.SetDefault(prev) })
	const warning = "non-authoritative empty list"
	fresh := map[string]interface{}{
		"status": true, "result": []interface{}{},
		"snapshot": map[string]interface{}{"complete": true, "issued_ever": 0},
		"retired":  []interface{}{}, "config_conflicts": []string{},
	}
	legacy := map[string]interface{}{"status": true, "result": []interface{}{}}

	for _, controlPlane := range []bool{false, true} {
		buf.Reset()
		ks := NewKeyStore(config.AuthConfig{}, controlPlane)
		pull733(t, ks, fresh)
		pull733(t, ks, legacy)
		if strings.Contains(buf.String(), warning) {
			t.Fatalf("control_plane=%v: an empty store warned on an empty list: %s", controlPlane, buf.String())
		}
		if !ks.KeyListLoaded() {
			t.Fatalf("control_plane=%v: an empty first list did not count as loaded", controlPlane)
		}

		_, raw := ks.Create("person", "person", nil, nil, map[string]string{"org_id": "org", "user_id": "user"})
		if raw == "" {
			t.Fatalf("control_plane=%v: a person-owned Create was refused", controlPlane)
		}
		pull733(t, ks, fresh)
		if !strings.Contains(buf.String(), warning) {
			t.Fatalf("control_plane=%v: keeping a synced key on a non-authoritative empty list did not warn", controlPlane)
		}
		if ks.Authenticate(raw) == nil {
			t.Fatalf("control_plane=%v: a non-authoritative empty list dropped a kept key", controlPlane)
		}
	}
}
