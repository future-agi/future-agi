package auth

import (
	"context"
	"encoding/base64"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

// Ruling 733 (J-SEC-KEYS L6.06): a retired key presented with its own secret
// is refused on OTLP/HTTP exactly as Django refuses it. Django raises
// APIKeyRetired (futureagi/accounts/api_key_policy.py:16-19, 104-110) and
// custom_exception_handler renders it (futureagi/accounts/authentication.py:
// 1105-1174, tfc/utils/api_errors.py:195-244) as a 401 carrying the ApiKey
// challenge of the default authenticator (authentication.py:202-203). The body
// below is written out from that handler with DRF's compact JSONRenderer, not
// taken from the collector's encoder.
const djangoAPIKeyRetiredBody = `{"status":false,"type":"authentication_error","code":"api_key_retired",` +
	`"detail":"Organization keys were retired. Create a personal key.",` +
	`"message":"Organization keys were retired. Create a personal key.",` +
	`"error":"Organization keys were retired. Create a personal key.",` +
	`"result":"Organization keys were retired. Create a personal key.",` +
	`"details":{"detail":["Organization keys were retired. Create a personal key."]},` +
	`"create_url":"/dashboard/keys"}`

// The collector's existing refusals, which retirement must not change.
const (
	genericRefusalBody = "{\"error\":\"authentication failed\"}\n"
	missingCredsBody   = "{\"error\":\"missing credentials: provide X-Api-Key/X-Secret-Key or Authorization: Basic\"}\n"
	plainTextType      = "text/plain; charset=utf-8"
)

const (
	retireOrg       = "7f3c2a10-0000-4000-8000-000000000733"
	retireWorkspace = "7f3c2a10-0000-4000-8000-000000000734"
	retireSecret    = "synthetic-retired-secret-733"
)

// retirementRow is one accounts_orgapikey row as the database holds it.
type retirementRow struct{ keyType, retiredAt, retiredReason, secretKeyHash string }

// retirementStore is persistence only. Eligibility (live) is what the primary's
// validateKeyQuery admits; rows is every row by api key whatever its state. The
// retirement predicate, the possession proof and the refusal are production code.
type retirementStore struct {
	live                 map[string]string
	rows                 map[string]retirementRow
	validations, lookups atomic.Int64
}

func (s *retirementStore) ValidateKey(_ context.Context, api, secret string) (*ResolveResult, error) {
	s.validations.Add(1)
	if want, ok := s.live[api]; !ok || want != secret {
		return nil, nil
	}
	return &ResolveResult{OrgID: retireOrg, WorkspaceID: retireWorkspace, KeyType: "user",
		Principal: PrincipalPersonal, Projects: map[string]string{}, APIKeyDigest: APIKeyDigest(api)}, nil
}

func (s *retirementStore) LookupRetirement(_ context.Context, api string) (keyType, retiredAt, retiredReason, secretKeyHash string, err error) {
	s.lookups.Add(1)
	row := s.rows[api]
	return row.keyType, row.retiredAt, row.retiredReason, row.secretKeyHash, nil
}

func (s *retirementStore) ResolveProjects(context.Context, string, string, []string) (map[string]string, error) {
	return map[string]string{}, nil
}

func (s *retirementStore) GetOrCreateProject(context.Context, string, string, string, string) (string, error) {
	return "", errors.New("retirement tests create no projects")
}

func retirementAuth(t *testing.T, store *retirementStore) (*Authenticator, *SecretPeppers) {
	t.Helper()
	peppers := mustPeppers(t, goldenPepper)
	a, err := NewWithResolver(Config{Peppers: peppers}, store, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	return a, peppers
}

// postTraces sends one OTLP/HTTP export through the real middleware. The next
// handler stands in for the trace handler; a refusal must never reach it.
func postTraces(t *testing.T, a *Authenticator, set func(*http.Request)) (*httptest.ResponseRecorder, bool) {
	t.Helper()
	var reached atomic.Bool
	h := a.HTTPMiddleware(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		reached.Store(true)
		w.WriteHeader(http.StatusOK)
	}))
	req := httptest.NewRequest(http.MethodPost, "/v1/traces", strings.NewReader("\x0a\x00"))
	req.Header.Set("Content-Type", "application/x-protobuf")
	set(req)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec, reached.Load()
}

func withKeyHeaders(api, secret string) func(*http.Request) {
	return func(r *http.Request) {
		r.Header.Set("X-Api-Key", api)
		r.Header.Set("X-Secret-Key", secret)
	}
}

func withBasic(api, secret string) func(*http.Request) {
	return func(r *http.Request) {
		r.Header.Set("Authorization", "Basic "+base64.StdEncoding.EncodeToString([]byte(api+":"+secret)))
	}
}

func assertRetiredRefusal(t *testing.T, a *Authenticator, rec *httptest.ResponseRecorder, reached bool, api, secret string) {
	t.Helper()
	if reached {
		t.Fatal("a retired key reached the trace handler")
	}
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status %d, want 401 (Django APIKeyRetired)", rec.Code)
	}
	if got := rec.Body.String(); got != djangoAPIKeyRetiredBody {
		t.Fatalf("body\n got %q\nwant %q", got, djangoAPIKeyRetiredBody)
	}
	if got := rec.Header().Get("Content-Type"); got != "application/json" {
		t.Errorf("Content-Type %q, want application/json", got)
	}
	if got := rec.Header().Get("WWW-Authenticate"); got != "ApiKey" {
		t.Errorf("WWW-Authenticate %q, want ApiKey", got)
	}
	for _, private := range []string{api, secret, retireOrg, retireWorkspace} {
		if strings.Contains(rec.Body.String(), private) || strings.Contains(strings.Join(headerValues(rec.Header()), "\n"), private) {
			t.Errorf("refusal echoes %q", private)
		}
	}
	if entry, state := a.cache.get(CacheKey(api, secret)); entry != nil || state != "miss" {
		t.Errorf("retired key cached as %s", state)
	}
}

func assertGenericRefusal(t *testing.T, rec *httptest.ResponseRecorder, reached bool, body string) {
	t.Helper()
	if reached {
		t.Fatal("a refused key reached the trace handler")
	}
	if rec.Code != http.StatusUnauthorized || rec.Body.String() != body {
		t.Fatalf("status %d body %q, want 401 %q", rec.Code, rec.Body.String(), body)
	}
	if got := rec.Header().Get("Content-Type"); got != plainTextType {
		t.Errorf("Content-Type %q, want unchanged %q", got, plainTextType)
	}
	if got := rec.Header().Get("WWW-Authenticate"); got != "" {
		t.Errorf("WWW-Authenticate %q added to an unchanged refusal", got)
	}
}

func headerValues(h http.Header) []string {
	var out []string
	for _, v := range h {
		out = append(out, v...)
	}
	return out
}

// Each clause of Django's is_retired_key (api_key_policy.py:22-27, with
// RETIRED_KEY_TYPES from api_key_constants.py:4) yields the typed refusal, on
// both credential transports, and on every repeat: nothing is cached.
func TestRetiredKeyGetsDjangoTypedRefusalOnOTLPHTTP(t *testing.T) {
	cases := map[string]retirementRow{
		"type_system":    {keyType: "system"},
		"type_mcp":       {keyType: "mcp"},
		"type_ingest":    {keyType: "ingest"},
		"retired_at":     {keyType: "user", retiredAt: "2026-10-30T12:00:00.000000+00:00"},
		"retired_reason": {keyType: "user", retiredReason: "733"},
		// Django checks retirement before enabled/deleted/membership
		// (api_key_policy.py:104-110), so the row's other state is irrelevant.
		"system_retired_at_and_reason": {keyType: "system", retiredAt: "2026-10-30T12:00:00+00:00", retiredReason: "733"},
	}
	for name, row := range cases {
		for transport, creds := range map[string]func(string, string) func(*http.Request){"x-api-key": withKeyHeaders, "basic": withBasic} {
			t.Run(name+"/"+transport, func(t *testing.T) {
				api := "pk-retired-" + name
				store := &retirementStore{rows: map[string]retirementRow{}}
				a, peppers := retirementAuth(t, store)
				row.secretKeyHash = peppers.HashSecret(retireSecret)
				store.rows[api] = row
				for attempt := 0; attempt < 2; attempt++ {
					rec, reached := postTraces(t, a, creds(api, retireSecret))
					assertRetiredRefusal(t, a, rec, reached, api, retireSecret)
				}
				if got := store.validations.Load(); got != 2 {
					t.Errorf("primary validations %d, want 2: a retired key must never be served from cache", got)
				}
			})
		}
	}
}

// Retirement is revealed only to a holder of the key's secret, proven against
// its stored hash (never a legacy plaintext), within Django's length bound
// (api_secrets.py:38), and never for a grant id. Everything else keeps the
// collector's current refusal byte for byte.
func TestRetirementKeepsOtherRefusalsUnchanged(t *testing.T) {
	store := &retirementStore{live: map[string]string{"pk-live": retireSecret}, rows: map[string]retirementRow{}}
	a, peppers := retirementAuth(t, store)
	other := mustPeppers(t, "a-pepper-the-collector-does-not-know-0123456789")
	long := strings.Repeat("s", 256)
	longKey := strings.Repeat("k", 256)
	grantID := "fit_" + strings.Repeat("ab", 16)
	store.rows["pk-retired"] = retirementRow{keyType: "system", secretKeyHash: peppers.HashSecret(retireSecret)}
	store.rows["pk-retired-legacy"] = retirementRow{keyType: "ingest"}
	store.rows["pk-retired-foreign-pepper"] = retirementRow{keyType: "mcp", secretKeyHash: other.HashSecret(retireSecret)}
	store.rows["pk-retired-long-secret"] = retirementRow{keyType: "system", secretKeyHash: peppers.HashSecret(long)}
	store.rows[longKey] = retirementRow{keyType: "system", secretKeyHash: peppers.HashSecret(retireSecret)}
	store.rows[grantID] = retirementRow{keyType: "system", secretKeyHash: peppers.HashSecret(retireSecret)}
	store.rows["pk-live"] = retirementRow{keyType: "user", secretKeyHash: peppers.HashSecret(retireSecret)}

	cases := map[string]struct {
		set  func(*http.Request)
		body string
	}{
		"retired_wrong_secret":   {withKeyHeaders("pk-retired", "not-the-secret"), genericRefusalBody},
		"retired_legacy_only":    {withKeyHeaders("pk-retired-legacy", retireSecret), genericRefusalBody},
		"retired_unknown_pepper": {withKeyHeaders("pk-retired-foreign-pepper", retireSecret), genericRefusalBody},
		"retired_secret_too_long": {withKeyHeaders("pk-retired-long-secret", long),
			genericRefusalBody},
		"retired_key_too_long": {withKeyHeaders(longKey, retireSecret), genericRefusalBody},
		"retired_grant_id":     {withKeyHeaders(grantID, retireSecret), genericRefusalBody},
		"live_wrong_secret":    {withKeyHeaders("pk-live", "not-the-secret"), genericRefusalBody},
		"unknown_key":          {withKeyHeaders("pk-unknown", retireSecret), genericRefusalBody},
		"missing_secret":       {func(r *http.Request) { r.Header.Set("X-Api-Key", "pk-retired") }, missingCredsBody},
		"missing_credentials":  {func(*http.Request) {}, missingCredsBody},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			rec, reached := postTraces(t, a, c.set)
			assertGenericRefusal(t, rec, reached, c.body)
		})
	}
	// Positive control: an eligible personal key still authenticates.
	if rec, reached := postTraces(t, a, withKeyHeaders("pk-live", retireSecret)); !reached || rec.Code != http.StatusOK {
		t.Fatalf("eligible personal key: status %d reached=%v", rec.Code, reached)
	}
}

// A key retired while cached is evicted by the existing revocation notice and
// its next request receives the typed refusal; gRPC keeps its current status.
func TestRetiredAfterRevocationNoticeAndGRPCUnchanged(t *testing.T) {
	store := &retirementStore{live: map[string]string{"pk-to-retire": retireSecret}, rows: map[string]retirementRow{}}
	a, peppers := retirementAuth(t, store)
	if rec, reached := postTraces(t, a, withKeyHeaders("pk-to-retire", retireSecret)); !reached || rec.Code != http.StatusOK {
		t.Fatalf("before retirement: status %d reached=%v", rec.Code, reached)
	}
	delete(store.live, "pk-to-retire")
	store.rows["pk-to-retire"] = retirementRow{keyType: "user", retiredReason: "733", secretKeyHash: peppers.HashSecret(retireSecret)}
	a.evictCredential(RevocationPrefix + APIKeyDigest("pk-to-retire"))
	rec, reached := postTraces(t, a, withKeyHeaders("pk-to-retire", retireSecret))
	assertRetiredRefusal(t, a, rec, reached, "pk-to-retire", retireSecret)

	ctx := metadata.NewIncomingContext(context.Background(), metadata.Pairs("x-api-key", "pk-to-retire", "x-secret-key", retireSecret))
	_, err := a.AuthenticateGRPC(ctx)
	if st, _ := status.FromError(err); st.Code() != codes.Unauthenticated || st.Message() != "authentication failed" {
		t.Fatalf("gRPC refusal changed: %v", err)
	}
}

// The primary's eligibility statement refuses every retirement clause, so a
// retired key never authenticates (fail closed), including retired_reason.
func TestValidateKeyQueryRefusesEveryRetirementClause(t *testing.T) {
	for _, clause := range []string{
		"k.type = 'user'",
		"COALESCE(to_jsonb(k)->>'retired_at', '') = ''",
		"COALESCE(to_jsonb(k)->>'retired_reason', '') = ''",
	} {
		if !strings.Contains(validateKeyQuery, clause) {
			t.Errorf("validateKeyQuery lacks %q", clause)
		}
	}
}

// Against PostgreSQL (OBS_TEST_PG_DSN): the real resolver refuses each retired
// row and the middleware renders Django's refusal, before the retirement
// columns are read as real columns and without any disabled-state precedence.
func TestRetiredKeyOnPrimaryGetsDjangoTypedRefusal(t *testing.T) {
	pool := routingDatabase(t)
	f := newEligibilityFixture()
	seedEligibility(t, pool, f)
	peppers := mustPeppers(t, goldenPepper)
	exec := routingFixture{t, pool}.exec
	exec(`ALTER TABLE accounts_orgapikey ADD COLUMN secret_key_hash varchar(128) NOT NULL DEFAULT ''`)
	exec(`ALTER TABLE accounts_orgapikey ADD COLUMN retired_at timestamptz NULL`)
	exec(`ALTER TABLE accounts_orgapikey ADD COLUMN retired_reason varchar(64) NULL`)
	exec(`UPDATE accounts_orgapikey SET secret_key='', secret_key_hash=$1`, peppers.HashSecret(f.secret))
	cfg := Config{PGWrite: pool.Config().ConnString(), PGRead: pool.Config().ConnString(), Peppers: peppers}
	a, err := New(context.Background(), cfg, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	reset := `UPDATE accounts_orgapikey SET type='user', enabled=true, retired_at=NULL, retired_reason=NULL`
	if rec, reached := postTraces(t, a, withKeyHeaders(f.api, f.secret)); !reached || rec.Code != http.StatusOK {
		t.Fatalf("eligible key: status %d reached=%v body=%q", rec.Code, reached, rec.Body.String())
	}
	for name, change := range map[string]string{
		"retired_reason":  `UPDATE accounts_orgapikey SET retired_reason='733'`,
		"retired_at":      `UPDATE accounts_orgapikey SET retired_at=now()`,
		"system_disabled": `UPDATE accounts_orgapikey SET type='system', enabled=false`,
		"ingest":          `UPDATE accounts_orgapikey SET type='ingest'`,
	} {
		t.Run(name, func(t *testing.T) {
			exec(reset)
			exec(change)
			a.evictCredential(RevocationPrefix + APIKeyDigest(f.api))
			r := &PGResolver{read: pool, write: pool, peppers: peppers}
			if res, err := r.ValidateKey(context.Background(), f.api, f.secret); err != nil || res != nil {
				t.Fatalf("retired key admitted by the primary statement: %v %v", res, err)
			}
			rec, reached := postTraces(t, a, withKeyHeaders(f.api, f.secret))
			assertRetiredRefusal(t, a, rec, reached, f.api, f.secret)
			rec, reached = postTraces(t, a, withKeyHeaders(f.api, "not-the-secret"))
			assertGenericRefusal(t, rec, reached, genericRefusalBody)
		})
	}
}
