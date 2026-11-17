package redisstate

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"

	"github.com/redis/go-redis/v9"
)

// Only synthetic IDs are retained; no socket or provider is used.
type revocationFloorRedis struct {
	redis.UniversalClient
	revoked                       map[string]bool
	actions                       []string
	storeErr, publishErr, readErr error
	wrongKey                      bool
	published                     interface{}
}

func (r *revocationFloorRedis) SAdd(_ context.Context, key string, members ...interface{}) *redis.IntCmd {
	r.actions = append(r.actions, "persist")
	r.wrongKey = r.wrongKey || key != "agentcc:key:revoked:sha256:v1"
	if r.storeErr != nil {
		return redis.NewIntResult(0, r.storeErr)
	}
	for _, member := range members {
		r.revoked[member.(string)] = true
	}
	return redis.NewIntResult(1, nil)
}

func (r *revocationFloorRedis) SIsMember(_ context.Context, key string, member interface{}) *redis.BoolCmd {
	r.wrongKey = r.wrongKey || key != "agentcc:key:revoked:sha256:v1"
	return redis.NewBoolResult(r.revoked[member.(string)], r.readErr)
}

func (r *revocationFloorRedis) Publish(_ context.Context, channel string, message interface{}) *redis.IntCmd {
	r.actions = append(r.actions, "publish")
	r.published = message
	r.wrongKey = r.wrongKey || channel != keyRevocationChannel
	return redis.NewIntResult(1, r.publishErr)
}

func newRevocationFloorFixture() (*KeyRevocationBroadcaster, *revocationFloorRedis) {
	r := &revocationFloorRedis{revoked: make(map[string]bool)}
	return NewKeyRevocationBroadcaster(&Client{rdb: r, breaker: newCircuitBreaker(100, 0)}), r
}

func TestRevocationPersistsBeforePublish(t *testing.T) {
	for _, name := range []string{"healthy", "publish_failure", "persistence_failure"} {
		t.Run(name, func(t *testing.T) {
			b, r := newRevocationFloorFixture()
			detail := errors.New("synthetic-transport-failure")
			if name == "publish_failure" {
				r.publishErr = detail
			}
			if name == "persistence_failure" {
				r.storeErr = detail
			}
			_ = b.PublishCredentialWithError(context.Background(), "synthetic-revoked-id", strings.Repeat("a", 64))
			want := []string{"persist", "publish"}
			if name == "persistence_failure" {
				want = []string{"persist"}
			}
			if !reflect.DeepEqual(r.actions, want) {
				t.Errorf("durable revocation must precede delivery: actions=%v want=%v", r.actions, want)
			}
			if r.revoked[strings.Repeat("a", 64)] != (name != "persistence_failure") {
				t.Error("durable revocation must survive PUBLISH failure")
			}
			if r.wrongKey {
				t.Error("revocation persistence/channel namespace mismatch")
			}
		})
	}
}

type revocationAuthority interface {
	CheckKeyRevocation(context.Context, string) (bool, error)
}

func TestRevocationAuthorityRefusesUnknownAndReplays(t *testing.T) {
	b, r := newRevocationFloorFixture()
	reader, ok := interface{}(b).(revocationAuthority)
	if !ok {
		t.Fatal("cached-key revocation authority is absent")
	}
	for _, id := range []string{strings.Repeat("a", 64), strings.Repeat("b", 64)} {
		if revoked, err := reader.CheckKeyRevocation(context.Background(), id); revoked || err != nil {
			t.Fatal("unrevoked synthetic positive control refused")
		}
	}
	_ = b.PublishCredentialWithError(context.Background(), "synthetic-target", strings.Repeat("a", 64))
	// A new broadcaster has no pub/sub history or in-memory revocation state.
	fresh := NewKeyRevocationBroadcaster(b.client)
	reader = interface{}(fresh).(revocationAuthority)
	if revoked, err := reader.CheckKeyRevocation(context.Background(), strings.Repeat("a", 64)); !revoked || err != nil {
		t.Error("new replica must recover a missed revocation from authority")
	}
	if revoked, err := reader.CheckKeyRevocation(context.Background(), strings.Repeat("b", 64)); revoked || err != nil {
		t.Error("unrelated key must remain usable after missed delivery")
	}
	r.readErr = errors.New("synthetic-private-transport-detail")
	if _, err := reader.CheckKeyRevocation(context.Background(), strings.Repeat("b", 64)); err == nil || err.Error() != "revocation freshness unavailable" || errors.Unwrap(err) != nil {
		t.Error("unknown authority must return a value-free non-wrapping error")
	}
	r.readErr = nil
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := reader.CheckKeyRevocation(ctx, strings.Repeat("b", 64)); err == nil {
		t.Error("cancelled read must not assert freshness")
	}
	for _, missing := range []*KeyRevocationBroadcaster{nil, NewKeyRevocationBroadcaster(nil)} {
		if _, err := interface{}(missing).(revocationAuthority).CheckKeyRevocation(context.Background(), strings.Repeat("b", 64)); err == nil {
			t.Error("missing Redis must not assert freshness")
		}
	}
	if r.wrongKey {
		t.Error("authority reader must use publisher tombstones")
	}
}
