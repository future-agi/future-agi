package server

import (
	"bytes"
	"context"
	"encoding/base64"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
)

// Ruling 733 (J-SEC-KEYS L6.06), at the server boundary: OTLP/HTTP /v1/traces
// answers a retired key with Django's APIKeyRetired refusal, written out from
// futureagi/accounts/authentication.py:1105-1174 and tfc/utils/api_errors.py:
// 195-244 (DRF compact JSONRenderer), and counts it with the other 401s.
const djangoRetiredBody = `{"status":false,"type":"authentication_error","code":"api_key_retired",` +
	`"detail":"Organization keys were retired. Create a personal key.",` +
	`"message":"Organization keys were retired. Create a personal key.",` +
	`"error":"Organization keys were retired. Create a personal key.",` +
	`"result":"Organization keys were retired. Create a personal key.",` +
	`"details":{"detail":["Organization keys were retired. Create a personal key."]},` +
	`"create_url":"/dashboard/keys"}`

// retiredKeyStore is persistence only: no key is eligible, and rows holds each
// key's retirement columns and stored hash as the database would.
type retiredKeyStore struct {
	rows map[string][4]string // type, retired_at, retired_reason, secret_key_hash
}

func (*retiredKeyStore) ValidateKey(context.Context, string, string) (*auth.ResolveResult, error) {
	return nil, nil
}

func (s *retiredKeyStore) LookupRetirement(_ context.Context, api string) (keyType, retiredAt, retiredReason, secretKeyHash string, err error) {
	row := s.rows[api]
	return row[0], row[1], row[2], row[3], nil
}

func (*retiredKeyStore) ResolveProjects(context.Context, string, string, []string) (map[string]string, error) {
	return map[string]string{}, nil
}

func (*retiredKeyStore) GetOrCreateProject(context.Context, string, string, string, string) (string, error) {
	return "", errors.New("retirement tests create no projects")
}

func TestOTLPHTTPRetiredKeyGetsDjangoTypedRefusal(t *testing.T) {
	peppers, err := auth.ResolveSecretPeppers("", nil, "test", "fi-collector-test-secret-key")
	if err != nil {
		t.Fatal(err)
	}
	const secret = "synthetic-retired-secret-733"
	hash := peppers.HashSecret(secret)
	store := &retiredKeyStore{rows: map[string][4]string{
		"pk-system":         {"system", "", "", hash},
		"pk-retired-at":     {"user", "2026-10-30T12:00:00+00:00", "", hash},
		"pk-retired-reason": {"user", "", "733", hash},
	}}
	a, err := auth.NewWithResolver(auth.Config{Peppers: peppers}, store, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	s := New(Config{}, nil, a, nil, nil)
	h := s.countAuthRefusals(a.HTTPMiddleware(http.HandlerFunc(s.handleHTTPTraces)))
	post := func(set func(*http.Request)) *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(emptySpans(t, 1)))
		req.Header.Set("Content-Type", "application/x-protobuf")
		set(req)
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		return rec
	}
	refusals := 0
	for _, api := range []string{"pk-system", "pk-retired-at", "pk-retired-reason"} {
		for transport, set := range map[string]func(*http.Request){
			"x-api-key": func(r *http.Request) { r.Header.Set("X-Api-Key", api); r.Header.Set("X-Secret-Key", secret) },
			"basic": func(r *http.Request) {
				r.Header.Set("Authorization", "Basic "+base64.StdEncoding.EncodeToString([]byte(api+":"+secret)))
			},
		} {
			t.Run(api+"/"+transport, func(t *testing.T) {
				rec := post(set)
				refusals++
				if rec.Code != http.StatusUnauthorized || rec.Body.String() != djangoRetiredBody {
					t.Fatalf("status %d body %q; want 401 %q", rec.Code, rec.Body.String(), djangoRetiredBody)
				}
				if rec.Header().Get("Content-Type") != "application/json" || rec.Header().Get("WWW-Authenticate") != "ApiKey" {
					t.Errorf("headers %v; want application/json with the ApiKey challenge", rec.Header())
				}
				if strings.Contains(rec.Body.String(), api) || strings.Contains(rec.Body.String(), secret) {
					t.Error("refusal echoes key material")
				}
			})
		}
	}
	t.Run("wrong_secret_keeps_current_refusal", func(t *testing.T) {
		rec := post(func(r *http.Request) {
			r.Header.Set("X-Api-Key", "pk-system")
			r.Header.Set("X-Secret-Key", "not-the-secret")
		})
		refusals++
		if rec.Code != http.StatusUnauthorized || rec.Body.String() != "{\"error\":\"authentication failed\"}\n" {
			t.Fatalf("status %d body %q; want the unchanged generic 401", rec.Code, rec.Body.String())
		}
		if rec.Header().Get("WWW-Authenticate") != "" {
			t.Error("generic refusal gained a challenge header")
		}
	})
	if got := s.IngestStats().Rejected["http/auth/401"]; got != int64(refusals) {
		t.Errorf("auth refusals counted %d, want %d (%v)", got, refusals, s.IngestStats().Rejected)
	}
}
