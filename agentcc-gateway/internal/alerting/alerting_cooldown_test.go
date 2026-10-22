package alerting

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// A rule's severity is in every alert it sends (webhook JSON, Slack text),
// and a rule's cooldown state can outlive the manager it was built in, so
// rebuilding an org's rules on a config push keeps each rule inside its
// cooldown.

type alertCapture struct {
	srv    *httptest.Server
	mu     sync.Mutex
	bodies []string
}

func newAlertCapture(t *testing.T) *alertCapture {
	t.Helper()
	c := &alertCapture{}
	c.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		c.mu.Lock()
		c.bodies = append(c.bodies, string(b))
		c.mu.Unlock()
	}))
	t.Cleanup(c.srv.Close)
	return c
}

func (c *alertCapture) Bodies() []string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]string(nil), c.bodies...)
}

func TestAlertRuleCooldownAndSeverity(t *testing.T) {
	t.Run("severity in the webhook JSON and the Slack text", func(t *testing.T) {
		hook, chat := newAlertCapture(t), newAlertCapture(t)
		rule := NewRule("errors", "error_count", ">=", 1, time.Minute, time.Hour, []string{"hook", "chat"}).
			WithSeverity("critical")
		m := NewManagerWithChannels([]*Rule{rule}, map[string]Channel{
			"hook": NewWebhookChannel(hook.srv.URL, nil),
			"chat": NewSlackChannel(chat.srv.URL),
		})
		m.Record("error_count", 1)
		m.Evaluate()
		m.Evaluate()

		hooks, chats := hook.Bodies(), chat.Bodies()
		if len(hooks) != 1 || len(chats) != 1 {
			t.Fatalf("two evaluations inside a 1h cooldown sent %d webhook and %d Slack alerts; want 1 each", len(hooks), len(chats))
		}
		var alert Alert
		if err := json.Unmarshal([]byte(hooks[0]), &alert); err != nil {
			t.Fatal(err)
		}
		if alert.Severity != "critical" || !strings.Contains(hooks[0], `"severity":"critical"`) {
			t.Fatalf("webhook payload %s: want \"severity\":\"critical\"", hooks[0])
		}
		var slack map[string]string
		_ = json.Unmarshal([]byte(chats[0]), &slack)
		if !strings.Contains(slack["text"], "Severity: critical") {
			t.Fatalf("slack text %q does not name the severity", slack["text"])
		}
	})

	t.Run("the Slack text's emoji and Severity line follow the severity", func(t *testing.T) {
		for sev, emoji := range map[string]string{
			"info":     ":information_source:",
			"warning":  ":warning:",
			"critical": ":rotating_light:",
			"":         ":rotating_light:",
		} {
			text := slackText(Alert{Name: "r", Severity: sev, Metric: "error_count", Condition: ">=", Window: "1m0s"})
			if !strings.HasPrefix(text, emoji+" *Alert: r*") {
				t.Errorf("severity %q: slack text %q does not start with %s", sev, text, emoji)
			}
			if got := strings.Contains(text, "Severity:"); got != (sev != "") {
				t.Errorf("severity %q: Severity line present = %v; text %q", sev, got, text)
			}
		}
	})

	t.Run("a rule with no severity (operator config.yaml) sends none", func(t *testing.T) {
		hook := newAlertCapture(t)
		rule := NewRule("ops", "request_count", ">=", 1, time.Minute, time.Hour, []string{"hook"})
		m := NewManagerWithChannels([]*Rule{rule}, map[string]Channel{"hook": NewWebhookChannel(hook.srv.URL, nil)})
		m.Record("request_count", 1)
		m.Evaluate()
		hooks := hook.Bodies()
		if len(hooks) != 1 || strings.Contains(hooks[0], "severity") {
			t.Fatalf("operator rule payloads %v: want one alert without a severity field", hooks)
		}
	})

	t.Run("a cooldown state shared with a rebuilt rule holds the cooldown", func(t *testing.T) {
		ch := &captureChannel{}
		state := NewCooldownState()
		first := NewRule("r", "request_count", ">=", 1, time.Minute, time.Hour, []string{"c"}).WithCooldownState(state)
		m1 := NewManagerWithChannels([]*Rule{first}, map[string]Channel{"c": ch})
		m1.Record("request_count", 1)
		m1.Evaluate()

		rebuilt := NewRule("r", "request_count", ">=", 1, time.Minute, time.Hour, []string{"c"}).WithCooldownState(state)
		m2 := NewManagerWithChannels([]*Rule{rebuilt}, map[string]Channel{"c": ch})
		m2.Record("request_count", 1)
		m2.Evaluate()

		if n := len(ch.Alerts()); n != 1 {
			t.Fatalf("the rebuilt rule fired again inside the cooldown: %d alerts, want 1", n)
		}
	})

	t.Run("concurrent evaluations past the threshold fire once inside the cooldown", func(t *testing.T) {
		ch := &captureChannel{}
		rule := NewRule("r", "request_count", ">=", 1, time.Minute, time.Hour, []string{"c"})
		m := NewManagerWithChannels([]*Rule{rule}, map[string]Channel{"c": ch})
		var wg sync.WaitGroup
		for i := 0; i < 32; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				m.Record("request_count", 1)
				m.Evaluate()
			}()
		}
		wg.Wait()
		if n := len(ch.Alerts()); n != 1 {
			t.Fatalf("32 concurrent evaluations fired %d alerts inside a 1h cooldown; want 1", n)
		}
	})
}
