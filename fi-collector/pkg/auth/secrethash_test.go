package auth

import (
	"context"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
)

// Written by Django (futureagi/accounts/api_secrets.py) and checked against
// openssl: HMAC-SHA256(pepper, secret) and the pepper fingerprint. The same
// values are pinned in futureagi/accounts/tests/test_api_secret_hashing.py.
const (
	goldenPepper    = "golden-vector-pepper-0123456789abcdef"
	goldenSecret    = "golden-secret-4f1c2b"
	goldenHash      = "hmac-sha256$5c1fdc64$0fe42b43c7243f5c62a9d9eec31c02b5813c7f702d0ff8b90d2ab549b59d32d9"
	goldenLocalKey  = "golden-secret-key"
	goldenLocalHash = "hmac-sha256$b9df05ee$d577a3daef0ced43290fb3a11c8d9ab5f76bb70d7729894d3950c937d8152f17"
	goldenAPIKey    = "golden-api-key"
	goldenRevoke    = "apikey:71a099d844d0c4e0540283ac182aeb5d01991f97fa705e686bbf2d232b59b9f6"
)

func mustPeppers(t *testing.T, configured string, retired ...string) *SecretPeppers {
	t.Helper()
	p, err := ResolveSecretPeppers(configured, retired, "production", "")
	if err != nil {
		t.Fatalf("resolve peppers: %v", err)
	}
	return p
}

func TestSecretHashGoldenVector(t *testing.T) {
	p := mustPeppers(t, goldenPepper)
	if got := p.HashSecret(goldenSecret); got != goldenHash {
		t.Fatalf("hash drifted from Django's:\n got %s\nwant %s", got, goldenHash)
	}
	if !p.VerifySecret(goldenHash, "", goldenSecret) {
		t.Fatal("Django's stored hash must verify")
	}
	local, err := ResolveSecretPeppers("", nil, "", goldenLocalKey)
	if err != nil {
		t.Fatalf("local pepper: %v", err)
	}
	if got := local.HashSecret(goldenSecret); got != goldenLocalHash {
		t.Fatalf("local derivation drifted from Django's:\n got %s\nwant %s", got, goldenLocalHash)
	}
	if got := RevocationPrefix + APIKeyDigest(goldenAPIKey); got != goldenRevoke {
		t.Fatalf("revocation payload drifted: %s", got)
	}
}

func TestVerifySecretRefusesEveryWrongForm(t *testing.T) {
	p := mustPeppers(t, goldenPepper)
	cases := map[string]struct{ stored, legacy, presented string }{
		"wrong secret":       {goldenHash, "", goldenSecret + "x"},
		"empty presented":    {goldenHash, "", ""},
		"no such key":        {"", "", goldenSecret},
		"malformed hash":     {"hmac-sha256$5c1fdc64$abc", "", goldenSecret},
		"other scheme":       {strings.Replace(goldenHash, "hmac-sha256", "sha256", 1), "", goldenSecret},
		"unknown pepper id":  {strings.Replace(goldenHash, "5c1fdc64", "00000000", 1), "", goldenSecret},
		"hash wins on both":  {goldenHash, goldenSecret + "x", goldenSecret + "x"},
		"legacy wrong":       {"", goldenSecret, goldenSecret + "x"},
		"legacy is a prefix": {"", goldenSecret, goldenSecret[:5]},
	}
	for name, c := range cases {
		if p.VerifySecret(c.stored, c.legacy, c.presented) {
			t.Errorf("%s: accepted", name)
		}
	}
	if !p.VerifySecret("", goldenSecret, goldenSecret) {
		t.Error("a legacy plaintext row (one Django's migration 0027 has not sealed yet) must still verify")
	}
}

func TestRetiredPepperStillVerifies(t *testing.T) {
	rotated := mustPeppers(t, "a-brand-new-pepper-from-the-secret-store-0001", goldenPepper)
	if !rotated.VerifySecret(goldenHash, "", goldenSecret) {
		t.Fatal("a hash under a retired pepper must verify until Django re-hashes it")
	}
	if rotated.CurrentID() == "5c1fdc64" {
		t.Fatal("the configured pepper must be current, not the retired one")
	}
	dropped := mustPeppers(t, "a-brand-new-pepper-from-the-secret-store-0001")
	if dropped.VerifySecret(goldenHash, "", goldenSecret) {
		t.Fatal("a pepper no longer listed must not verify")
	}
}

func TestResolveSecretPeppersFailsClosed(t *testing.T) {
	for _, env := range []string{"production", "prod", "staging", "dev"} {
		if _, err := ResolveSecretPeppers("", nil, env, "a-secret-key"); err != ErrPepperMissing {
			t.Errorf("ENV_TYPE=%s without a pepper: got %v, want ErrPepperMissing", env, err)
		}
	}
	if _, err := ResolveSecretPeppers("", nil, "local", ""); err != ErrPepperMissing {
		t.Errorf("local without SECRET_KEY: got %v, want ErrPepperMissing", err)
	}
	if _, err := ResolveSecretPeppers("too-short", nil, "production", ""); err == nil {
		t.Error("a short pepper must be refused")
	}
	if _, err := NewPGResolver(context.Background(), Config{PGWrite: "postgres://u@127.0.0.1:1/db"}); err != ErrPepperMissing {
		t.Errorf("NewPGResolver without peppers: got %v, want ErrPepperMissing", err)
	}
}

// Django refuses the published pepper outside ENV_TYPE=local
// (_api_secret_start_problem in futureagi/tfc/settings/settings.py). The
// collector verifies collector grants under the same pepper, so it refuses it
// the same way; futureagi/tfc/tests/test_settings_env.py pins the value.
func TestResolveSecretPeppersRefusesThePublishedPepperOutsideLocal(t *testing.T) {
	const published = "local-dev-only-api-secret-pepper-replace-me"
	for _, env := range []string{"production", "prod", "staging", "dev", " Production "} {
		_, err := ResolveSecretPeppers(published, nil, env, "")
		if err == nil {
			t.Errorf("ENV_TYPE=%q with the published pepper: started, want refused", env)
		} else if strings.Contains(err.Error(), published) {
			t.Errorf("ENV_TYPE=%q: the error echoes the configured value", env)
		}
	}
	for _, env := range []string{"", "local", "test", "LOCAL"} {
		if _, err := ResolveSecretPeppers(published, nil, env, ""); err != nil {
			t.Errorf("ENV_TYPE=%q with the published pepper: %v, want local installs unchanged", env, err)
		}
	}
	if _, err := ResolveSecretPeppers(strings.Repeat("9f", 32), nil, "production", ""); err != nil {
		t.Errorf("a random 64-hex pepper in production: %v", err)
	}
}

func TestValidateKeyQueryNeverMatchesTheSecretInSQL(t *testing.T) {
	if strings.Contains(validateKeyQuery, "secret_key =") || strings.Contains(validateKeyQuery, "$2") {
		t.Fatal("the secret must be compared in constant time in Go, not in SQL")
	}
	if !strings.Contains(validateKeyQuery, "to_jsonb(k)->>'secret_key_hash'") {
		t.Fatal("the hash column must be read so the collector can roll out before the migration")
	}
}

func TestRevocationByAPIKeyDigestEvictsEveryEntryOfTheKey(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	t.Cleanup(func() { rdb.Close() })

	a := &Authenticator{cache: newCache(5*time.Minute, time.Hour), rdb: rdb, log: slog.Default()}
	startFreshnessWatcher(t, a)
	revoked := APIKeyDigest(goldenAPIKey)
	a.cache.putPositive(CacheKey(goldenAPIKey, goldenSecret), &ResolveResult{OrgID: "o", APIKeyDigest: revoked, Projects: map[string]string{}})
	a.cache.putPositive(CacheKey("other-api-key", "s"), &ResolveResult{OrgID: "o", APIKeyDigest: APIKeyDigest("other-api-key"), Projects: map[string]string{}})

	ctx := context.Background()
	if err := rdb.Publish(ctx, revocationChannel, goldenRevoke).Err(); err != nil {
		t.Fatalf("publish: %v", err)
	}
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if _, status := a.cache.get(CacheKey(goldenAPIKey, goldenSecret)); status == "miss" {
			if _, other := a.cache.get(CacheKey("other-api-key", "s")); other != "fresh" {
				t.Fatal("another key's entry was evicted")
			}
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("the revoked key's entry was not evicted")
}
