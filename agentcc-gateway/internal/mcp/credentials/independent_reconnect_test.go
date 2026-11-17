package credentials

import (
	"context"
	"testing"
	"time"
)

// A real subscriber disconnect, not an in-process local invalidation cache.
func TestIndependentR4SubscriberReconnectPollBound(t *testing.T) {
	for _, shared := range []bool{false, true} {
		name := "personal"
		if shared {
			name = "shared-oauth"
		}
		t.Run(name, func(t *testing.T) {
			r, purger, source := stoppingResolvers(t)
			k := keyA1
			if shared {
				k = SharedKey("org-a", "issues")
			}
			stoppingSeed(source, k, 1)
			auth := stoppingGrant(t, r, k)
			if _, err := r.cache.client.Redis().ClientKillByFilter(context.Background(), "TYPE", "pubsub", "SKIPME", "yes").Result(); err != nil {
				t.Fatal(err)
			}
			epoch := r.cache.personalEpochKey(k)
			if shared {
				epoch = r.cache.sharedEpochKey(k)
			}
			start := time.Now()
			if err := r.cache.client.Redis().Eval(context.Background(), "redis.call('INCR',KEYS[1]); return redis.call('DEL',KEYS[2])", []string{epoch, r.cache.RedisKey(k)}).Err(); err != nil {
				t.Fatal(err)
			}
			stoppingAwait(t, auth.Lifetime.Done(), start, 5*time.Second)
			stoppingSeed(source, k, 2)
			fresh := stoppingGrant(t, r, k)
			if fresh.Check(context.Background()) != nil {
				t.Fatal("new grant was cancelled by prior reconnect invalidation")
			}
			start = time.Now()
			if _, err := purger.PurgeCredentials(context.Background(), k.OrgID, k.UserID, k.ServerID); err != nil {
				t.Fatal(err)
			}
			stoppingAwait(t, fresh.Lifetime.Done(), start, 5*time.Second)
		})
	}
}
