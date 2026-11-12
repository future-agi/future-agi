package credentials

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// Microsoft's hold: Django holds a token back by policy and answers HTTP 409
// with error microsoft_coming_soon and, optionally, the sentence the caller's
// refusal is to carry (mcp-upstream-token-v1 $defs/Refusal message).
const holdReason = "microsoft_coming_soon"

// holdBody is the hold's answer body with fields besides its error.
func holdBody(t *testing.T, fields map[string]any) string {
	t.Helper()
	body := map[string]any{"error": holdReason}
	for k, v := range fields {
		body[k] = v
	}
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

// goldenHold is the golden's hold and its sentence.
func goldenHold(t *testing.T) (map[string]any, string) {
	t.Helper()
	hold, _ := schematest.ReadExample(t, "mcp-upstream-token-v1.json")["hold"].(map[string]any)
	message, _ := hold["message"].(string)
	if hold["error"] != holdReason || message == "" {
		t.Fatalf("the golden hold %v is not microsoft_coming_soon with a message", hold)
	}
	return hold, message
}

// requireUnavailable checks d is policy_unavailable, the refusal of a call
// that is not made, valid against mcp-denial-v1's $defs/Error, with no
// metadata (so no connect link) and message as its text.
func requireUnavailable(t *testing.T, d *mcp.Denial, message string) {
	t.Helper()
	if d == nil || d.Code != mcp.CodePolicyUnavailable || d.Type != mcp.CredentialUnavailable().Type {
		t.Fatalf("got %v, want policy_unavailable", d)
	}
	obj := errorObject(t, d)
	if errs := schematest.ValidateDef(t, "mcp-denial-v1.schema.json", "Error", obj); len(errs) > 0 {
		t.Fatalf("the refusal breaks mcp-denial-v1 $defs/Error: %v", errs)
	}
	if meta, _ := obj["metadata"].(map[string]any); len(meta) != 0 {
		t.Fatalf("metadata %v, want none", meta)
	}
	if d.Message != message {
		t.Fatalf("message %q, want %q", d.Message, message)
	}
}

// Django's hold is its answer, not a failure of the channel: a typed refusal
// with no connect hint (the hold stops a sign-in from starting too), whatever
// else the body carries, and its error names the reason only. Any other 409,
// and a hold past the answer cap, stay failures of the channel. The golden's
// hold is a $defs/Refusal.
func TestMicrosoftHoldIsARefusal(t *testing.T) {
	hold, message := goldenHold(t)
	d := newFakeDjango(t)
	c := tokenClientFor(t, d)
	d.answerRaw(keyA1, http.StatusConflict, holdBody(t, map[string]any{
		"message": message, "connect_hint": testConnectHost + ConnectStartPath("issues"),
	}))
	_, err := c.Fetch(context.Background(), keyA1)
	var r *Refusal
	if !errors.As(err, &r) || r.Reason != holdReason || r.ConnectHint != "" || errors.Is(err, ErrTokenChannel) {
		t.Fatalf("the hold read as %v, want the refusal %s with no connect hint", err, holdReason)
	}
	if strings.Contains(err.Error(), message) {
		t.Fatalf("the error %q carries the caller's sentence", err)
	}
	for name, body := range map[string]string{
		"another 409":  `{"error":"conflict","message":"Not the hold."}`,
		"not JSON":     "Microsoft connections are coming soon.",
		"past the cap": holdBody(t, map[string]any{"message": strings.Repeat("x", maxTokenAnswerBytes)}),
	} {
		d.answerRaw(keyA1, http.StatusConflict, body)
		_, err := c.Fetch(context.Background(), keyA1)
		var other *Refusal
		if !errors.Is(err, ErrTokenChannel) || errors.As(err, &other) {
			t.Fatalf("%s: %v, want a channel failure", name, err)
		}
	}
	if errs := schematest.ValidateDef(t, "mcp-upstream-token-v1.schema.json", "Refusal", hold); len(errs) > 0 {
		t.Fatalf("the golden hold breaks $defs/Refusal: %v", errs)
	}
}

// A call Django holds back is refused in Django's own sentence: the same
// policy_unavailable refusal as when no credential can be resolved, with no
// connect link, its text the hold's message made plain (control and format
// characters dropped, each run of whitespace one space, trimmed) and cut to
// 300 characters. A hold with no usable message keeps today's text, and no
// other field of the answer reaches the caller.
func TestMicrosoftHoldSaysDjangosSentence(t *testing.T) {
	_, sentence := goldenHold(t)
	today := mcp.CredentialUnavailable().Message
	for _, c := range []struct {
		name   string
		fields map[string]any
		want   string
	}{
		{"the golden's sentence", map[string]any{"message": sentence}, sentence},
		{"the plain hold", map[string]any{"message": "Microsoft connections are coming soon."}, "Microsoft connections are coming soon."},
		{"no other field", map[string]any{
			"message": "Held.", "connect_hint": testConnectHost + ConnectStartPath("issues"),
			"access_token": "m25-test-token-not-handed-out", "token_type": "Bearer",
		}, "Held."},
		{"plain text", map[string]any{"message": "  Line one.\r\n\tLine\u00a0two.\u0000\u202e\u200b \u2028"}, "Line one. Line two."},
		{"cut to 300 characters", map[string]any{"message": strings.Repeat("é", 301)}, strings.Repeat("é", 300)},
		{"no message", nil, today},
		{"an empty message", map[string]any{"message": ""}, today},
		{"nothing left", map[string]any{"message": " \u0000\t\u202e "}, today},
		{"not a string", map[string]any{"message": 7}, today},
	} {
		t.Run(c.name, func(t *testing.T) {
			d := newFakeDjango(t)
			r, _ := testResolver(t, d, nil, "", newTestClock())
			d.answerRaw(keyA1, http.StatusConflict, holdBody(t, c.fields))
			auth, _, den := r.resolveUser(context.Background(), oauthUser("org-a", "u1"), "org-a", "issues")
			if auth != nil {
				t.Fatal("a held credential produced an override")
			}
			requireUnavailable(t, den, c.want)
			raw, _ := json.Marshal(den.Object())
			if strings.Contains(string(raw), "connect.example.test") || strings.Contains(string(raw), "m25-test-token") {
				t.Fatalf("the refusal %s carries another field of the answer", raw)
			}
		})
	}
}

// The organization's shared account is held the same way, in Django's words.
func TestSharedMicrosoftHoldSaysDjangosSentence(t *testing.T) {
	_, sentence := goldenHold(t)
	d := newFakeDjango(t)
	r, _ := testResolver(t, d, nil, "", newTestClock())
	d.answerRaw(sharedA, http.StatusConflict, holdBody(t, map[string]any{"message": sentence}))
	_, den := r.resolveShared(context.Background(), "org-a", "crm")
	requireUnavailable(t, den, sentence)
	d.answerRaw(sharedA, http.StatusConflict, holdBody(t, nil))
	_, den = r.resolveShared(context.Background(), "org-a", "crm")
	requireUnavailable(t, den, mcp.CredentialUnavailable().Message)
}

// Only the hold's message is read: any other refusal keeps its own words,
// and one the user remedies by connecting keeps its connect link.
func TestOnlyTheHoldsMessageIsRead(t *testing.T) {
	d := newFakeDjango(t)
	r, _ := testResolver(t, d, nil, "", newTestClock())
	hint := testConnectHost + ConnectStartPath("issues")
	d.answerRaw(keyA1, http.StatusOK, `{"error":"needs_reauth","connect_hint":"`+hint+`","message":"Not for the caller."}`)
	_, _, den := r.resolveUser(context.Background(), oauthUser("org-a", "u1"), "org-a", "issues")
	meta := requireAuthRequired(t, den, mcp.AuthReasonNeedsReauth)
	if want := mcp.AuthRequired("issues", mcp.AuthReasonNeedsReauth, "").Message; den.Message != want {
		t.Fatalf("message %q, want %q", den.Message, want)
	}
	link, _ := meta["connect_url"].(string)
	openConnectLink(t, link, hint, ConnectBinding{V: 1, OrgID: "org-a", ServerID: "issues", UserID: "u1"})

	d.answerRaw(keyA1, http.StatusOK, `{"error":"unavailable","message":"Not for the caller."}`)
	_, _, den = r.resolveUser(context.Background(), oauthUser("org-a", "u1"), "org-a", "issues")
	requireUnavailable(t, den, mcp.CredentialUnavailable().Message)
}
