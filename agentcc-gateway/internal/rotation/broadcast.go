package rotation

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strconv"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

// ChannelSuffix is the Redis pub/sub channel a promote is broadcast on,
// after the gateway's Redis key prefix.
const ChannelSuffix = "provider:rotate"

// MaxMessageAge is how old (or how far ahead) a broadcast promote may be and
// still be applied.
const MaxMessageAge = 2 * time.Minute

// Broadcaster tells the other replicas about a promote over Redis pub/sub,
// so every replica sends the new key from then on. A message is transient:
// pub/sub stores nothing, and a replica that is not subscribed at that moment
// misses it (it keeps its deployment config's key until restart). The key in
// a message is sealed with AES-256-GCM under a key derived from the gateway's
// admin token, which every replica of one deployment shares, so a Redis
// subscriber without the admin token reads no provider key, and a message
// from another deployment on the same Redis is ignored.
type Broadcaster struct {
	rdb     redis.UniversalClient
	channel string
	aead    cipher.AEAD
	origin  string // this replica, to skip its own messages

	mu   sync.Mutex
	last map[string]int64 // provider -> issued_at (ms) of the last message applied
}

// message is one broadcast promote.
type message struct {
	V        int    `json:"v"`
	Origin   string `json:"origin"`
	Provider string `json:"provider"`
	IssuedAt int64  `json:"issued_at"` // unix ms
	Nonce    string `json:"nonce"`
	Sealed   string `json:"sealed"` // the key, sealed; AAD binds v, origin, provider and issued_at
}

// NewBroadcaster builds a broadcaster on rdb for the deployment whose admin
// token is secret; channelPrefix is the gateway's Redis key prefix.
func NewBroadcaster(rdb redis.UniversalClient, secret, channelPrefix string) (*Broadcaster, error) {
	if rdb == nil {
		return nil, errors.New("rotation broadcast: no Redis client")
	}
	if secret == "" {
		return nil, errors.New("rotation broadcast: no admin token to seal the key with")
	}
	key, err := hkdf.Key(sha256.New, []byte(secret), nil, "agentcc provider key rotation broadcast v1", 32)
	if err != nil {
		return nil, err
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	var id [12]byte
	if _, err := rand.Read(id[:]); err != nil {
		return nil, err
	}
	return &Broadcaster{
		rdb:     rdb,
		channel: channelPrefix + ChannelSuffix,
		aead:    aead,
		origin:  hex.EncodeToString(id[:]),
		last:    make(map[string]int64),
	}, nil
}

// Channel is the pub/sub channel this broadcaster uses.
func (b *Broadcaster) Channel() string { return b.channel }

func aad(v int, origin, provider string, issuedAt int64) []byte {
	return []byte(strconv.Itoa(v) + "\x00" + origin + "\x00" + provider + "\x00" + strconv.FormatInt(issuedAt, 10))
}

// Publish broadcasts that providerID now uses key.
func (b *Broadcaster) Publish(ctx context.Context, providerID, key string) error {
	msg := message{V: 1, Origin: b.origin, Provider: providerID, IssuedAt: time.Now().UnixMilli()}
	nonce := make([]byte, b.aead.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return err
	}
	sealed := b.aead.Seal(nil, nonce, []byte(key), aad(msg.V, msg.Origin, msg.Provider, msg.IssuedAt))
	msg.Nonce = base64.StdEncoding.EncodeToString(nonce)
	msg.Sealed = base64.StdEncoding.EncodeToString(sealed)
	payload, err := json.Marshal(msg)
	if err != nil {
		return err
	}
	return b.rdb.Publish(ctx, b.channel, payload).Err()
}

// open reads one message: the provider and key it carries, or why it is not
// applied. self is true for this replica's own message.
func (b *Broadcaster) open(payload string, now time.Time) (provider, key string, self bool, err error) {
	var msg message
	if err := json.Unmarshal([]byte(payload), &msg); err != nil {
		return "", "", false, fmt.Errorf("unreadable message: %w", err)
	}
	if msg.Origin == b.origin {
		return msg.Provider, "", true, nil
	}
	if msg.V != 1 || msg.Provider == "" {
		return "", "", false, errors.New("unsupported message")
	}
	age := now.Sub(time.UnixMilli(msg.IssuedAt))
	if age > MaxMessageAge || age < -MaxMessageAge {
		return msg.Provider, "", false, fmt.Errorf("message issued %s from now; refused (more than %s)", age.Round(time.Second), MaxMessageAge)
	}
	nonce, err1 := base64.StdEncoding.DecodeString(msg.Nonce)
	sealed, err2 := base64.StdEncoding.DecodeString(msg.Sealed)
	if err1 != nil || err2 != nil || len(nonce) != b.aead.NonceSize() {
		return msg.Provider, "", false, errors.New("malformed sealed key")
	}
	plain, err := b.aead.Open(nil, nonce, sealed, aad(msg.V, msg.Origin, msg.Provider, msg.IssuedAt))
	if err != nil {
		return msg.Provider, "", false, errors.New("sealed by another admin token (another deployment?) or altered")
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	if msg.IssuedAt <= b.last[msg.Provider] {
		return msg.Provider, "", false, errors.New("older than a message already applied (replayed?)")
	}
	b.last[msg.Provider] = msg.IssuedAt
	return msg.Provider, string(plain), false, nil
}

// Subscribe applies every promote another replica broadcasts, until ctx ends;
// it reconnects after a lost subscription. Run it in a goroutine.
func (b *Broadcaster) Subscribe(ctx context.Context, apply func(providerID, key string)) {
	for {
		if ctx.Err() != nil {
			return
		}
		b.subscribeOnce(ctx, apply)
		select {
		case <-ctx.Done():
			return
		case <-time.After(2 * time.Second):
			slog.Warn("provider key rotation subscription lost, reconnecting", "channel", b.channel)
		}
	}
}

func (b *Broadcaster) subscribeOnce(ctx context.Context, apply func(providerID, key string)) {
	b.run(ctx, apply, nil)
}

// run subscribes, reports on ready (when non-nil) whether Redis confirmed the
// subscription, then applies messages until ctx ends or the subscription is
// lost.
func (b *Broadcaster) run(ctx context.Context, apply func(providerID, key string), ready chan<- error) {
	sub := b.rdb.Subscribe(ctx, b.channel)
	defer sub.Close()
	if _, err := sub.Receive(ctx); err != nil {
		if ready != nil {
			ready <- err
		}
		return
	}
	if ready != nil {
		ready <- nil
	}
	ch := sub.Channel()
	for {
		select {
		case <-ctx.Done():
			return
		case msg, ok := <-ch:
			if !ok {
				return
			}
			provider, key, self, err := b.open(msg.Payload, time.Now())
			if self {
				continue
			}
			if err != nil {
				slog.Warn("provider key rotation broadcast not applied", "provider", provider, "reason", err.Error())
				continue
			}
			apply(provider, key)
		}
	}
}

// SubscribeReady is Subscribe that reports, on ready, when the first
// subscription is live (nil) or failed (its error). Later reconnects are
// silent.
func (b *Broadcaster) SubscribeReady(ctx context.Context, apply func(providerID, key string), ready chan<- error) {
	b.run(ctx, apply, ready)
	b.Subscribe(ctx, apply)
}
