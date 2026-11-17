package redisstate

import (
	"context"
	"time"

	"github.com/redis/go-redis/v9"
)

// EnvelopeNonceStore keeps the delegated envelopes' consumed nonces in Redis,
// so every replica refuses an envelope another one served. A nonce is kept
// one envelope life (EnvelopeNonceSlack) past its envelope's expiry, so a
// replica whose clock runs behind the one that consumed it still finds it.
type EnvelopeNonceStore struct {
	client *Client
	prefix string
}

// NewEnvelopeNonceStore stores nonces under prefix.
func NewEnvelopeNonceStore(client *Client, prefix string) *EnvelopeNonceStore {
	return &EnvelopeNonceStore{client: client, prefix: prefix}
}

// EnvelopeNonceSlack is how long past its envelope's expiry a nonce is kept:
// one envelope life, far above the clock skew between replicas.
const EnvelopeNonceSlack = 60 * time.Second

// Consume sets id once, until exp (Unix seconds) plus EnvelopeNonceSlack:
// true when it was not set before. An unavailable Redis (or an open circuit)
// is an error, and the envelope is refused.
func (s *EnvelopeNonceStore) Consume(ctx context.Context, id string, exp, now int64) (bool, error) {
	ttl := time.Duration(exp-now+1)*time.Second + EnvelopeNonceSlack
	var set bool
	err := s.client.Do(func(rdb redis.UniversalClient) error {
		var err error
		set, err = rdb.SetNX(ctx, s.prefix+id, exp, ttl).Result()
		return err
	})
	return set, err
}
