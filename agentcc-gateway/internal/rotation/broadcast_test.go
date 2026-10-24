package rotation

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// capturePublish is a redis.UniversalClient whose Publish records the payload
// instead of sending it.
type capturePublish struct {
	redis.UniversalClient
	payloads []string
}

func (c *capturePublish) Publish(ctx context.Context, channel string, message interface{}) *redis.IntCmd {
	c.payloads = append(c.payloads, string(message.([]byte)))
	return redis.NewIntResult(1, nil)
}

const (
	broadcastTokenA = "deployment-a-admin-token-0123456789abcdef0123456789abcdef"
	broadcastTokenB = "deployment-b-admin-token-0123456789abcdef0123456789abcdef"
	broadcastKey    = "sk-broadcast-provider-key-0123456789"
)

// TestRotationBroadcastSealsTheKey: a broadcast promote carries the key
// sealed (never in the clear), opens only under the same admin token, is
// refused when old or replayed, and is skipped by the replica that sent it.
func TestRotationBroadcastSealsTheKey(t *testing.T) {
	pub := &capturePublish{}
	sender, err := NewBroadcaster(pub, broadcastTokenA, "agentcc:")
	if err != nil {
		t.Fatal(err)
	}
	if sender.Channel() != "agentcc:provider:rotate" {
		t.Errorf("channel = %q", sender.Channel())
	}
	if err := sender.Publish(context.Background(), "openai", broadcastKey); err != nil {
		t.Fatal(err)
	}
	payload := pub.payloads[0]
	if strings.Contains(payload, broadcastKey) || strings.Contains(payload, broadcastKey[:8]) {
		t.Fatalf("the broadcast payload carries the key in the clear: %s", payload)
	}

	now := time.Now()
	if _, _, self, _ := sender.open(payload, now); !self {
		t.Error("the sending replica does not skip its own message")
	}

	receiver, _ := NewBroadcaster(pub, broadcastTokenA, "agentcc:")
	provider, key, self, err := receiver.open(payload, now)
	if err != nil || self || provider != "openai" || key != broadcastKey {
		t.Fatalf("same deployment: open = %q %q self=%v err=%v; want openai and the key", provider, key, self, err)
	}
	if _, _, _, err := receiver.open(payload, now); err == nil {
		t.Error("a replayed message was applied a second time")
	}

	other, _ := NewBroadcaster(pub, broadcastTokenB, "agentcc:")
	if _, key, _, err := other.open(payload, now); err == nil || key != "" {
		t.Errorf("another admin token opened the message: key %q err %v", key, err)
	}

	late, _ := NewBroadcaster(pub, broadcastTokenA, "agentcc:")
	if _, key, _, err := late.open(payload, now.Add(MaxMessageAge+time.Second)); err == nil || key != "" {
		t.Errorf("a message older than %s was applied: key %q err %v", MaxMessageAge, key, err)
	}

	// Altering the provider the message names breaks its seal.
	var msg message
	_ = json.Unmarshal([]byte(payload), &msg)
	msg.Provider = "anthropic"
	altered, _ := json.Marshal(msg)
	fresh, _ := NewBroadcaster(pub, broadcastTokenA, "agentcc:")
	if _, key, _, err := fresh.open(string(altered), now); err == nil || key != "" {
		t.Errorf("a message whose provider was altered was applied: key %q err %v", key, err)
	}
}

// TestRotationApplyRemote: a key another replica promoted is applied
// (draining, source replica) without validation, and a key already primary
// changes nothing.
func TestRotationApplyRemote(t *testing.T) {
	var applied []string
	validated := 0
	m := NewManager(time.Minute, nil)
	m.SetHooks(Hooks{
		Validate: func(context.Context, string, string) (string, error) { validated++; return "", nil },
		Apply: func(id, key string) (func(), error) {
			applied = append(applied, id+"="+key)
			return nil, nil
		},
	})
	m.RegisterProviderWithDrain("openai", "sk-old", 0)
	if ok, err := m.ApplyRemote("openai", "sk-new"); !ok || err != nil {
		t.Fatalf("ApplyRemote = %v, %v", ok, err)
	}
	st, _ := m.GetStatus("openai")
	if st.Primary != "sk-new" || st.Status != StatusDraining || st.Source != "replica" || validated != 0 {
		t.Errorf("after ApplyRemote: %+v, validated %d times", st, validated)
	}
	if ok, _ := m.ApplyRemote("openai", "sk-new"); ok {
		t.Error("a key already primary was applied again")
	}
	if len(applied) != 1 || applied[0] != "openai=sk-new" {
		t.Errorf("applied = %v", applied)
	}
	if _, err := m.ApplyRemote("unregistered", "k"); err == nil {
		t.Error("ApplyRemote for a provider without rotation succeeded")
	}
}
