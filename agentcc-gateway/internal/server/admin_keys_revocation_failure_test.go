package server

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

func TestAdminRevokeWithoutRedis(t *testing.T) {
	for _, tc := range []struct {
		name           string
		keyID          string
		singleInstance bool
		unauthorized   bool
		status         int
		code           string
		revoked        bool
	}{
		{"held", "synthetic-person-id", false, false, http.StatusServiceUnavailable, "revocation_not_broadcast", true},
		{"absent", "synthetic-absent-id", false, false, http.StatusServiceUnavailable, "revocation_not_broadcast", false},
		{"config", "key_1", false, false, http.StatusConflict, "config_key_not_revocable", false},
		{"missing_id", "", false, false, http.StatusBadRequest, "missing_key_id", false},
		{"unauthorized", "synthetic-person-id", false, true, http.StatusUnauthorized, "", false},
		{"single_instance", "synthetic-person-id", true, false, http.StatusOK, "", true},
		{"single_instance_absent", "synthetic-absent-id", true, false, http.StatusNotFound, "key_not_found", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			adminToken := strings.Join([]string{"synthetic", "r3", "admin"}, "-")
			rawKey := strings.Join([]string{"synthetic", "r3", "person"}, "-")
			rawOther := strings.Join([]string{"synthetic", "r3", "other"}, "-")
			rawConfig := strings.Join([]string{"synthetic", "r3", "config"}, "-")
			ks := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Name: "synthetic-config", Key: rawConfig, KeyType: "internal"}}}, true)
			metadata := map[string]string{"org_id": "synthetic-org", "user_id": "01234567-89ab-4cde-8fab-0123456789ab"}
			if ks.LoadFromHashes([]auth.SyncedKey{
				{ID: "synthetic-person-id", KeyHash: auth.HashKey(rawKey), Metadata: metadata},
				{ID: "synthetic-other-id", KeyHash: auth.HashKey(rawOther), Metadata: metadata},
			}) != 2 || ks.Authenticate(rawKey) == nil || ks.Authenticate(rawOther) == nil {
				t.Fatal("synthetic key fixtures must authenticate before DELETE")
			}
			if !ks.ConfigKeyNotRevocable("key_1") {
				t.Fatal("config fixture must exercise the protected-key guard")
			}
			h := NewKeyHandlers(ks, adminToken)
			if !tc.singleInstance {
				h.SetRevocationPublisher(redisstate.NewKeyRevocationBroadcaster(nil))
			}
			// Retry a partial failure: the local tombstone must remain in place
			// and the unavailable broadcast must still be reported, never 200.
			attempts := 1
			if tc.name == "held" {
				attempts = 2
			}
			for i := 0; i < attempts; i++ {
				req := httptest.NewRequest(http.MethodDelete, "/-/keys/"+tc.keyID+"?key_id="+tc.keyID, nil)
				if !tc.unauthorized {
					req.Header.Set("Authorization", "Bearer "+adminToken)
				}
				w := httptest.NewRecorder()
				h.RevokeKey(w, req)
				if w.Code != tc.status {
					t.Fatalf("DELETE status = %d, want %d", w.Code, tc.status)
				}
				if tc.code != "" {
					var body struct {
						Error struct {
							Code    string `json:"code"`
							Message string `json:"message"`
						} `json:"error"`
					}
					if json.Unmarshal(w.Body.Bytes(), &body) != nil || body.Error.Code != tc.code {
						t.Error("DELETE returned the wrong structured error code")
					}
					if tc.code == "revocation_not_broadcast" && body.Error.Message != "revocation not broadcast" {
						t.Error("broadcast failure response must use the value-free message")
					}
				}
				if (ks.Authenticate(rawKey) == nil) != tc.revoked {
					t.Error("local revocation state disagrees with the DELETE outcome")
				}
				if ks.Authenticate(rawOther) == nil {
					t.Error("unrelated key must remain active")
				}
				if key := ks.Get("key_1"); key == nil || !key.IsActive() {
					t.Error("protected config key must remain active")
				}
			}
		})
	}
}

type revocationResultPublisher struct {
	publish     func() error
	resultCalls int
	legacyCalls int
}

func (p *revocationResultPublisher) Publish(context.Context, string) { p.legacyCalls++ }
func (p *revocationResultPublisher) PublishWithError(context.Context, string) error {
	p.resultCalls++
	return p.publish()
}

func TestAdminRevokeUsesPublishResult(t *testing.T) {
	for _, fail := range []bool{false, true} {
		name := "success"
		if fail {
			name = "failure"
		}
		t.Run(name, func(t *testing.T) {
			const adminToken = "synthetic-r3-admin"
			const rawKey = "synthetic-r3-person"
			ks := auth.NewKeyStore(config.AuthConfig{}, true)
			if ks.LoadFromHashes([]auth.SyncedKey{{
				ID: "synthetic-person-id", KeyHash: auth.HashKey(rawKey),
				Metadata: map[string]string{"org_id": "synthetic-org", "user_id": "01234567-89ab-4cde-8fab-0123456789ab"},
			}}) != 1 || ks.Authenticate(rawKey) == nil {
				t.Fatal("synthetic fixture must authenticate before DELETE")
			}
			detail := strings.Join([]string{"synthetic", "private", "publisher", "detail"}, "-")
			pub := &revocationResultPublisher{publish: func() error {
				if ks.Authenticate(rawKey) != nil {
					t.Error("local store must revoke before calling the publisher")
				}
				if fail {
					return errors.New(detail)
				}
				return nil
			}}
			h := NewKeyHandlers(ks, adminToken)
			h.SetRevocationPublisher(pub)
			req := httptest.NewRequest(http.MethodDelete, "/-/keys/synthetic-person-id?key_id=synthetic-person-id", nil)
			req.Header.Set("Authorization", "Bearer "+adminToken)
			w := httptest.NewRecorder()
			h.RevokeKey(w, req)
			want := http.StatusOK
			if fail {
				want = http.StatusServiceUnavailable
			}
			if w.Code != want || pub.resultCalls != 1 || pub.legacyCalls != 0 {
				t.Errorf("status=%d result_calls=%d legacy_calls=%d; want %d/1/0", w.Code, pub.resultCalls, pub.legacyCalls, want)
			}
			if strings.Contains(w.Body.String(), detail) {
				t.Error("response leaked synthetic publisher error detail")
			}
			if ks.Authenticate(rawKey) != nil {
				t.Error("publish outcome must not undo local revocation")
			}
		})
	}
}
