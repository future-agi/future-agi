package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

type credentialPublisherSpy struct {
	publish func(string, string) error
	legacy  int
}

func (p *credentialPublisherSpy) Publish(context.Context, string) { p.legacy++ }
func (p *credentialPublisherSpy) PublishWithError(context.Context, string) error {
	p.legacy++
	return nil
}
func (p *credentialPublisherSpy) PublishCredentialWithError(_ context.Context, id, hash string) error {
	return p.publish(id, hash)
}

func TestAdminRevokeCapturesCredentialIdentity(t *testing.T) {
	for _, known := range []bool{true, false} {
		ks := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Key: "synthetic-admin-identity"}}}, false)
		id := "key_1"
		wantStatus := http.StatusOK
		if !known {
			id, wantStatus = "synthetic-unknown", http.StatusServiceUnavailable
		}
		calls := 0
		pub := &credentialPublisherSpy{publish: func(gotID, hash string) error {
			calls++
			if gotID != id {
				t.Error("publisher diagnostic ID changed")
			}
			if known {
				if hash != auth.HashKey("synthetic-admin-identity") || ks.Authenticate("synthetic-admin-identity") != nil {
					t.Error("credential publisher requires atomic identity capture and local-first revocation")
				}
				return nil
			}
			if hash != "" {
				t.Error("unknown admin ID fabricated an identity")
			}
			return &redisstate.RevocationPublishError{}
		}}
		h := NewKeyHandlers(ks, "synthetic-identity-admin")
		h.SetRevocationPublisher(pub)
		req := httptest.NewRequest(http.MethodDelete, "/-/keys/"+id+"?key_id="+id, nil)
		req.Header.Set("Authorization", "Bearer synthetic-identity-admin")
		w := httptest.NewRecorder()
		h.RevokeKey(w, req)
		if w.Code != wantStatus || calls != 1 || pub.legacy != 0 {
			t.Errorf("identity dispatch: status=%d calls=%d legacy=%d", w.Code, calls, pub.legacy)
		}
		if !known && ks.Authenticate("synthetic-admin-identity") == nil {
			t.Error("unknown ID must not disable configured credential")
		}
	}
}
