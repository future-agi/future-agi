package auth

import (
	"crypto/hmac"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// L1b publication pinned at 39a67f7da5b98d341d716bd6b4a6995d85f6cd0e.
// Corpus SHA256: sha256:67cc19a792c94e60842a42e8c9f2939963f4b8c132f66e9277e4814be23b8931.
// Gateway verifies the request MAC, NOT the forwarded grant MAC. Generic MAC
// vectors below test the common primitive only, never install grant auth.
type fe1Vector struct {
	Name     string            `json:"name"`
	Source   string            `json:"source"`
	Claims   map[string]string `json:"claims"`
	Token    string            `json:"token"`
	Digest   string            `json:"canonical_digest"`
	MAC      string            `json:"mac_value"`
	PublicID string            `json:"public_id"`
	Now      int64             `json:"now"`
	Body     string            `json:"body"`
	Grant    string            `json:"accompanying_grant"`
	Audience string            `json:"audience"`
	Route    string            `json:"route"`
	Method   string            `json:"method"`
	Row      map[string]any    `json:"row"`
	Liveness map[string]any    `json:"liveness"`
	Expected bool              `json:"expected"`
}

func TestEnvelope733L1bPublishedVectors(t *testing.T) {
	raw, err := os.ReadFile("testdata/service_envelope_vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	if "sha256:"+EnvelopeDigest(raw) != "sha256:67cc19a792c94e60842a42e8c9f2939963f4b8c132f66e9277e4814be23b8931" {
		t.Fatal("L1b corpus changed without repinning")
	}
	var corpus struct {
		Secret     string      `json:"test_only_pair_secret"`
		Valid      []fe1Vector `json:"valid_macs"`
		Rejections []fe1Vector `json:"rejections"`
		Registry   []fe1Vector `json:"registry_cases"`
	}
	if err = json.Unmarshal(raw, &corpus); err != nil {
		t.Fatal(err)
	}
	sources := map[string]fe1Vector{}
	for _, v := range corpus.Valid {
		sources[v.Name] = v
		t.Run("valid/"+v.Name, func(t *testing.T) {
			c, canonical, mac, err := parseEnvelope(v.Token)
			if err != nil {
				t.Fatal(err)
			}
			if !hmac.Equal(mac, envelopeMAC(corpus.Secret, c, canonical)) || v.Digest != "sha256:"+EnvelopeDigest(canonical) || v.MAC != "mac:"+hex.EncodeToString(mac) {
				t.Fatal("cross-language MAC mismatch")
			}
			if c.Mode == "request" {
				got, err := SignEnvelope(corpus.Secret, c)
				if err != nil || got != v.Token {
					t.Fatal("request signer differs from L1b")
				}
			}
			if c.Aud == "gw-delegated" {
				verifier := NewEnvelopeVerifier(corpus.Secret, time.Unix(v.Now-10, 0))
				if _, _, err := verifier.VerifyDelegated(v.Token, v.Grant, c.Method, c.Route, []byte(v.Body), time.Unix(v.Now, 0)); err != nil {
					t.Fatal(err)
				}
			}
		})
	}
	// Exercise all syntax and MAC rejection vectors with the common parser and
	// primitive, and real delegated binding for its request-specific cases.
	for _, v := range corpus.Rejections {
		t.Run("reject/"+v.Name, func(t *testing.T) {
			src, ok := sources[v.Source]
			if !ok {
				t.Fatal("unknown source")
			}
			token := v.Token
			if token == "" {
				token = src.Token
			}
			// Gateway vectors must reach the real verifier, including malformed
			// claims. A test-side method/route comparison must not short-circuit
			// the rejection or substitute for the production error class.
			if src.Claims["aud"] == "gw-delegated" {
				method, route, body := src.Claims["method"], src.Claims["route"], src.Body
				if v.Method != "" {
					method = v.Method
				}
				if v.Route != "" {
					route = v.Route
				}
				if v.Body != "" {
					body = v.Body
				}
				now := src.Now
				if v.Now != 0 {
					now = v.Now
				}
				var reasons []string
				verifier := NewEnvelopeVerifier(corpus.Secret, time.Unix(src.Now-10, 0), func(reason string) {
					reasons = append(reasons, reason)
				})
				_, _, err := verifier.VerifyDelegated(token, src.Grant, method, route, []byte(body), time.Unix(now, 0))
				if !errors.Is(err, ErrEnvelopeRejected) {
					t.Fatalf("gateway rejection class = %v, want %v", err, ErrEnvelopeRejected)
				}
				if len(reasons) != 1 || reasons[0] != "request_binding" {
					t.Fatalf("gateway rejection reasons = %v, want [request_binding]", reasons)
				}
				return
			}
			c, canonical, mac, err := parseEnvelope(token)
			accepted := err == nil && c.Kid == EnvelopeKid && hmac.Equal(mac, envelopeMAC(corpus.Secret, c, canonical))
			now := src.Now
			if v.Now != 0 {
				now = v.Now
			}
			if accepted {
				accepted = now >= c.Iat-5 && now <= c.Exp
			}
			if v.PublicID != "" {
				accepted = accepted && v.PublicID == "fit_"+c.ID
			}
			if v.Audience != "" {
				accepted = accepted && c.Aud == v.Audience
			}
			if v.Route != "" {
				accepted = accepted && c.Route == v.Route
			}
			if v.Method != "" {
				accepted = accepted && c.Method == v.Method
			}
			if v.Body != "" {
				accepted = accepted && c.Body == EnvelopeDigest([]byte(v.Body))
			}
			if accepted && c.Mode == "request" && strings.HasPrefix(c.Via, "grant:") {
				_, err = boundGrant(c, src.Grant, time.Unix(now, 0))
				accepted = err == nil
			}
			if accepted && c.Aud == "gw-delegated" {
				verifier := NewEnvelopeVerifier(corpus.Secret, time.Unix(src.Now-10, 0))
				_, _, err = verifier.VerifyDelegated(token, src.Grant, c.Method, c.Route, []byte(src.Body), time.Unix(now, 0))
				accepted = err == nil
			}
			if accepted {
				t.Fatal("rejection vector accepted")
			}
		})
	}
	// Registry ownership stays with FA. The gateway has no grant signing key
	// and no registry; only the batch membership/liveness projection is local.
	// Row-equality cases are explicitly NOT claimed as gateway proof.
	applicable := 0
	for _, v := range corpus.Registry {
		if !strings.HasPrefix(v.Name, "batch-") || strings.Contains(v.Name, "row") || v.Name == "batch-revoked" {
			continue
		}
		t.Run("registry-membership/"+v.Name, func(t *testing.T) {
			batchVector := sources["batch"]
			c, _, _, err := parseEnvelope(batchVector.Token)
			if err != nil {
				t.Fatal(err)
			}
			members := []DelegatedMember{}
			live := func(name string) bool { return v.Liveness[name] == true }
			if live("org_active") && live("user_active") && live("org_member") {
				members = append(members, DelegatedMember{Org: c.Org, User: c.User, Workspace: "-"})
				if live("workspace_active") && live("workspace_member") {
					members = append(members, DelegatedMember{Org: c.Org, User: c.User, Workspace: c.Workspace})
				}
			}
			now := time.Unix(batchVector.Now, 0)
			ks := NewKeyStore(config.AuthConfig{})
			ks.SyncDelegations(&DelegationSnapshot{Complete: true, Members: members}, now)
			if got := ks.DelegatedEligible(c, now); got != v.Expected {
				t.Fatalf("membership eligible=%v want=%v", got, v.Expected)
			}
		})
		applicable++
	}
	if applicable != 6 {
		t.Fatalf("batch membership corpus coverage drift: %d", applicable)
	}
}
