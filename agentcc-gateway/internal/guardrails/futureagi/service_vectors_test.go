package futureagi

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
)

// Independent FA stub MAC implementation. Its known-answer inputs are L1b's
// committed vectors, not values produced by the gateway signer under test.
func serviceMAC(secret string, fields []string) []byte {
	extract := hmac.New(sha256.New, []byte("fe1"))
	extract.Write([]byte(secret))
	expand := hmac.New(sha256.New, extract.Sum(nil))
	expand.Write([]byte(fields[3] + ">" + fields[4] + "|" + fields[2]))
	expand.Write([]byte{1})
	mac := hmac.New(sha256.New, expand.Sum(nil))
	mac.Write([]byte(strings.Join(fields, "\n")))
	return mac.Sum(nil)
}

func verifyServiceWire(secret, token string) ([]string, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 || parts[0] != "fe1" || len(token) > 2048 {
		return nil, fmt.Errorf("wire rejected")
	}
	raw, err := base64.RawURLEncoding.Strict().DecodeString(parts[1])
	if err != nil || base64.RawURLEncoding.EncodeToString(raw) != parts[1] {
		return nil, fmt.Errorf("canonical rejected")
	}
	fields := strings.Split(string(raw), "\n")
	if len(fields) != 19 || fields[0] != "fe1" {
		return nil, fmt.Errorf("fields rejected")
	}
	mac, err := base64.RawURLEncoding.Strict().DecodeString(parts[2])
	if err != nil || !hmac.Equal(mac, serviceMAC(secret, fields)) {
		return nil, fmt.Errorf("MAC rejected")
	}
	return fields, nil
}

func checkServiceVectors(t *testing.T) string {
	t.Helper()
	raw, err := os.ReadFile("../../auth/testdata/service_envelope_vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Secret string `json:"test_only_pair_secret"`
		Valid  []struct {
			Token string `json:"token"`
			MAC   string `json:"mac_value"`
		} `json:"valid_macs"`
	}
	if err := json.Unmarshal(raw, &corpus); err != nil {
		t.Fatal(err)
	}
	evalVectors := 0
	for _, v := range corpus.Valid {
		f, err := verifyServiceWire(corpus.Secret, v.Token)
		if err != nil || "mac:"+hex.EncodeToString(serviceMAC(corpus.Secret, f)) != v.MAC {
			t.Fatal("FA stub disagrees with pinned vector")
		}
		if f[4] == "fa-eval" {
			evalVectors++
		}
	}
	if evalVectors != 2 {
		t.Fatalf("expected both key and grant eval vectors, got %d", evalVectors)
	}
	return corpus.Secret
}

func assertServiceWindow(t *testing.T, f []string) {
	t.Helper()
	iat, err1 := strconv.ParseInt(f[7], 10, 64)
	exp, err2 := strconv.ParseInt(f[8], 10, 64)
	now := time.Now().Unix()
	if err1 != nil || err2 != nil || exp != iat+60 || now < iat-5 || now > exp {
		t.Error("invalid envelope time window")
	}
	id, err := hex.DecodeString(f[9])
	if err != nil || len(id) != 16 || strings.ToLower(f[9]) != f[9] {
		t.Error("invalid envelope nonce")
	}
}

func serviceGrant(t *testing.T, secret, kind string) (string, auth.EnvelopeClaims) {
	t.Helper()
	now := time.Now().Unix()
	id, err := auth.NewEnvelopeID()
	if err != nil {
		t.Fatal(err)
	}
	sub := "chat:44444444-4444-4444-8444-444444444444"
	if kind == "batch" {
		sub = "batch:44444444-4444-4444-8444-444444444444"
	}
	f := []string{"fe1", "grant", "k1", "fa", "fa-eval-grant", "-", "-", strconv.FormatInt(now, 10), strconv.FormatInt(now+120, 10), id, kind, serviceOrg, serviceWorkspace, serviceUser, "person", sub, "eval", "-", "-"}
	wire := "fe1." + base64.RawURLEncoding.EncodeToString([]byte(strings.Join(f, "\n"))) + "." + base64.RawURLEncoding.EncodeToString(serviceMAC(secret, f))
	return wire, auth.EnvelopeClaims{Mode: "grant", Kid: "k1", Iss: "fa", Aud: "fa-eval-grant", Method: "-", Route: "-", Iat: now, Exp: now + 120, ID: id, Kind: kind, Org: serviceOrg, Workspace: serviceWorkspace, User: serviceUser, Via: "person", Sub: sub, Scope: "eval", Body: "-", Grant: "-"}
}
