package auth

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"strconv"
	"strings"
	"testing"
	"time"
)

// Test signing uses the explicit single-block RFC 5869 operations rather than
// the verifier's HKDF library. All inputs here are synthetic fixture values.
func signedGrantFields(fields []string, pepper string) string {
	canonical := strings.Join(fields, "\n")
	extract := hmac.New(sha256.New, []byte("fe1"))
	extract.Write([]byte(pepper))
	expand := hmac.New(sha256.New, extract.Sum(nil))
	expand.Write([]byte(fields[3] + ">" + fields[4] + "|" + fields[2]))
	expand.Write([]byte{1})
	mac := hmac.New(sha256.New, expand.Sum(nil))
	mac.Write([]byte(canonical))
	return "fe1." + base64.RawURLEncoding.EncodeToString([]byte(canonical)) + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

func sandboxFields(now time.Time) []string {
	return []string{"fe1", "grant", "k1", "fa", "collector-ingest", "-", "-", strconv.FormatInt(now.Unix(), 10), strconv.FormatInt(now.Unix()+600, 10), strings.Repeat("a", 32), "sandbox", routeOrg, routeWorkspace, "33333333-3333-4333-8333-333333333333", "person", "attempt:" + routeAgent + ":44444444-4444-4444-8444-444444444444", "traces", "-", "-"}
}

func TestCollectorGrantEnvelopeContract(t *testing.T) {
	now := time.Unix(1800000000, 0)
	fields := sandboxFields(now)
	api := "fit_" + fields[9]
	peppers := mustPeppers(t, goldenPepper)
	verify := func(f []string) error {
		_, err := VerifyCollectorGrant(api, signedGrantFields(f, goldenPepper), peppers, []string{"k1"}, now)
		return err
	}
	if err := verify(fields); err != nil {
		t.Fatal(err)
	}
	token := signedGrantFields(fields, goldenPepper)
	// Every canonical slot is inside the MAC, even slots whose alternate value
	// might remain syntactically valid.
	for i := range fields {
		t.Run("mac-field-"+strconv.Itoa(i), func(t *testing.T) {
			changed := append([]string(nil), fields...)
			changed[i] += "x"
			parts := strings.Split(token, ".")
			parts[1] = base64.RawURLEncoding.EncodeToString([]byte(strings.Join(changed, "\n")))
			if _, err := VerifyCollectorGrant(api, strings.Join(parts, "."), peppers, []string{"k1"}, now); err == nil {
				t.Fatal("changed field accepted")
			}
		})
	}
	cases := []struct {
		name  string
		index int
		value string
	}{
		{"mode", 1, "request"}, {"kid", 2, "k9"}, {"issuer", 3, "gateway"}, {"audience", 4, "collector-emit"},
		{"method", 5, "POST"}, {"route", 6, "/v1/traces"}, {"time-syntax", 7, "01800000000"}, {"id", 9, strings.Repeat("b", 32)},
		{"kind", 10, "ee-serve"}, {"org", 11, "-"}, {"workspace", 12, "not-a-uuid"}, {"user", 13, "-"}, {"via", 14, "svc"},
		{"subject", 15, "emit:sim"}, {"scope", 16, "eval"}, {"body", 17, strings.Repeat("0", 64)}, {"grant", 18, strings.Repeat("0", 64)},
		{"subject-extra", 15, fields[15] + ":extra"}, {"subject-missing", 15, "attempt:" + routeAgent},
		{"lifetime", 8, strconv.FormatInt(now.Unix()+86401, 10)}, {"expired", 8, strconv.FormatInt(now.Unix(), 10)},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := append([]string(nil), fields...)
			f[c.index] = c.value
			if verify(f) == nil {
				t.Fatal("invalid typed grant accepted")
			}
		})
	}
	for _, at := range []time.Time{now.Add(600 * time.Second), now.Add(601 * time.Second), now.Add(-6 * time.Second)} {
		if _, err := VerifyCollectorGrant(api, token, peppers, []string{"k1"}, at); err == nil {
			t.Fatal("grant accepted outside time window")
		}
	}
	for _, bad := range []string{token + "=", "fe1." + strings.Repeat("a", 2048) + ".x", token + ".extra"} {
		if _, err := VerifyCollectorGrant(api, bad, peppers, []string{"k1"}, now); err == nil {
			t.Fatal("invalid wire accepted")
		}
	}
}

func TestCollectorPlatformGrantAndPepperOverlap(t *testing.T) {
	now := time.Now()
	f := sandboxFields(now)
	f[4] = "collector-emit"
	f[8] = strconv.FormatInt(now.Unix()+60, 10)
	f[10] = "platform-emit"
	f[13] = "-"
	f[14] = "svc"
	f[15] = "emit:sim"
	next := "synthetic-next-pepper-for-grant-tests-only"
	p, err := ResolveSecretPeppers(next, []string{goldenPepper}, "test", "")
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{next, goldenPepper} {
		if _, err := VerifyCollectorGrant("fit_"+f[9], signedGrantFields(f, key), p, []string{"k1"}, now); err != nil {
			t.Fatal(err)
		}
	}
	for _, c := range []struct {
		i     int
		value string
	}{{13, routeOrg}, {15, "attempt:" + routeAgent + ":" + routeOrg}, {8, strconv.FormatInt(now.Unix()+61, 10)}, {4, "collector-ingest"}} {
		bad := append([]string(nil), f...)
		bad[c.i] = c.value
		if _, err := VerifyCollectorGrant("fit_"+f[9], signedGrantFields(bad, next), p, []string{"k1"}, now); err == nil {
			t.Fatal("invalid platform grant accepted")
		}
	}
}

func TestGrantCacheChecksTokenAndExpiryOnEveryHit(t *testing.T) {
	now := time.Now()
	f := sandboxFields(now)
	api := "fit_" + f[9]
	token := signedGrantFields(f, goldenPepper)
	p := mustPeppers(t, goldenPepper)
	claims, err := VerifyCollectorGrant(api, token, p, []string{"k1"}, now)
	if err != nil {
		t.Fatal(err)
	}
	a := &Authenticator{cfg: Config{Peppers: p, EnvelopeKids: []string{"k1"}}, cache: newCache(time.Minute, time.Hour)}
	a.cache.putPositive(CacheKey(api, token), &ResolveResult{Principal: PrincipalSandbox, Grant: claims})
	if _, err := a.Authenticate(context.Background(), api, token); err != nil {
		t.Fatal(err)
	}
	if _, err := a.Authenticate(context.Background(), api, token+"x"); err == nil {
		t.Fatal("invalid token reused cached grant")
	}
	expired := *claims
	expired.Exp = now.Unix() - 1
	a.cache.putPositive(CacheKey(api, token), &ResolveResult{Principal: PrincipalSandbox, Grant: &expired})
	if _, status := a.cache.get(CacheKey(api, token)); status != "miss" {
		t.Fatal("expired grant served from cache")
	}
}
