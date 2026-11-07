package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

const (
	testAKID   = "AKIDEXAMPLE0000000000"
	testSecret = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
)

// verifySigV4 checks r's AWS Signature Version 4 the way AWS does: it
// rebuilds the canonical request from what arrived and recomputes the
// signature. It is written from the SigV4 spec, independent of the bedrock
// package's signer.
func verifySigV4(r *http.Request, body []byte, secret string) error {
	auth := r.Header.Get("Authorization")
	rest, ok := strings.CutPrefix(auth, "AWS4-HMAC-SHA256 ")
	if !ok {
		return fmt.Errorf("not a SigV4 Authorization header: %q", auth)
	}
	fields := map[string]string{}
	for _, part := range strings.Split(rest, ", ") {
		k, v, _ := strings.Cut(part, "=")
		fields[k] = v
	}
	scope := strings.SplitN(fields["Credential"], "/", 2)
	if len(scope) != 2 {
		return fmt.Errorf("bad Credential %q", fields["Credential"])
	}
	date, region, service := "", "", ""
	if s := strings.Split(scope[1], "/"); len(s) == 4 {
		date, region, service = s[0], s[1], s[2]
	}

	payloadHash := sha256Hex(body)
	if got := r.Header.Get("X-Amz-Content-Sha256"); got != payloadHash {
		return fmt.Errorf("x-amz-content-sha256 %s does not match the body received (%s)", got, payloadHash)
	}
	var headers strings.Builder
	signed := strings.Split(fields["SignedHeaders"], ";")
	for _, h := range signed {
		v := r.Header.Get(h)
		if h == "host" {
			v = r.Host
		}
		headers.WriteString(h + ":" + strings.TrimSpace(v) + "\n")
	}
	segs := strings.Split(r.URL.EscapedPath(), "/")
	for i, s := range segs {
		segs[i] = uriEncode(s)
	}
	canonical := strings.Join([]string{
		r.Method, strings.Join(segs, "/"), r.URL.Query().Encode(),
		headers.String(), fields["SignedHeaders"], payloadHash,
	}, "\n")
	toSign := strings.Join([]string{
		"AWS4-HMAC-SHA256", r.Header.Get("X-Amz-Date"), scope[1], sha256Hex([]byte(canonical)),
	}, "\n")
	key := []byte("AWS4" + secret)
	for _, s := range []string{date, region, service, "aws4_request"} {
		key = hmacSHA256(key, s)
	}
	if want := hex.EncodeToString(hmacSHA256(key, toSign)); !hmac.Equal([]byte(want), []byte(fields["Signature"])) {
		return fmt.Errorf("signature mismatch")
	}
	return nil
}

// uriEncode is SigV4's UriEncode: everything but A-Za-z0-9-._~ as %XX.
func uriEncode(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		c := s[i]
		if 'a' <= c && c <= 'z' || 'A' <= c && c <= 'Z' || '0' <= c && c <= '9' || strings.IndexByte("-._~", c) >= 0 {
			b.WriteByte(c)
		} else {
			fmt.Fprintf(&b, "%%%02X", c)
		}
	}
	return b.String()
}

func sha256Hex(b []byte) string {
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

func hmacSHA256(key []byte, s string) []byte {
	m := hmac.New(sha256.New, key)
	m.Write([]byte(s))
	return m.Sum(nil)
}

const converseBody = `{"output":{"message":{"role":"assistant","content":[{"text":"Hi."}]}},"stopReason":"end_turn",` +
	`"usage":{"inputTokens":5,"outputTokens":2,"totalTokens":7}}`

// TestRecordBedrockSigV4SurvivesProxy is why the recorder dials a proxy over
// plain http instead of pointing the adapter at a different host: the
// signature covers host, path, headers and body, and all of them must reach
// upstream exactly as signed.
func TestRecordBedrockSigV4SurvivesProxy(t *testing.T) {
	var mu sync.Mutex
	var verified []string
	var verr error
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		err := verifySigV4(r, body, testSecret)
		// Negative control: the verifier must reject a body that differs
		// from the one signed, or a pass would mean nothing.
		tampered := verifySigV4(r, append(body, ' '), testSecret)
		mu.Lock()
		if err == nil && tampered == nil {
			err = fmt.Errorf("verifier accepted a tampered body")
		}
		if err != nil {
			verr = err
		} else {
			verified = append(verified, r.URL.EscapedPath())
		}
		mu.Unlock()
		if err != nil {
			w.WriteHeader(http.StatusForbidden)
			_, _ = io.WriteString(w, `{"message":"The request signature we calculated does not match the signature you provided."}`)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, converseBody)
	}))
	t.Cleanup(srv.Close)

	const nova = "us.amazon.nova-2-lite-v1:0"
	f := newFixtureTree(t)
	f.model("bedrock", nova, "text_basic", "with_patch")
	f.write("bedrock/testdata/contracts/"+"us.amazon.nova-2-lite-v1%3A0"+"/with_patch/upstream_patch.json", `{"inferenceConfig":{"maxTokens":8}}`)

	env := map[string]string{"AWS_ACCESS_KEY_ID": testAKID, "AWS_SECRET_ACCESS_KEY": testSecret, "AWS_REGION": "us-east-1"}
	var out strings.Builder
	opts, err := parseFlags([]string{"-providers-dir", f.root, "-provider", "bedrock", "-base-url", srv.URL}, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	r := &recorder{opts: opts, out: &out, env: func(k string) string { return env[k] }, now: fixedNow, client: srv.Client()}
	code := r.run(t.Context())

	mu.Lock()
	defer mu.Unlock()
	if verr != nil {
		t.Fatalf("upstream rejected the proxied request: %v\n%s", verr, out.String())
	}
	if len(verified) != 1 || verified[0] != "/model/us.amazon.nova-2-lite-v1:0/converse" {
		t.Errorf("verified requests = %q, want the one converse call", verified)
	}
	if got := f.read("bedrock/testdata/contracts/us.amazon.nova-2-lite-v1%3A0/text_basic/response.json"); got != converseBody {
		t.Errorf("response.json = %s", got)
	}
	// A patch would change a signed body, so the case fails without a call.
	if code != 1 || !strings.Contains(out.String(), "with_patch: FAILED: upstream_patch.json cannot be applied") {
		t.Errorf("exit %d; want the patched case refused:\n%s", code, out.String())
	}
	if strings.Contains(out.String(), testSecret) || strings.Contains(out.String(), testAKID) {
		t.Errorf("credentials in output:\n%s", out.String())
	}
}
