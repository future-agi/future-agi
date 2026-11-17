package redisstate

import (
	"context"
	"errors"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/batch"
)

// BatchStore keeps batches in Redis, so every replica of a cluster gets,
// lists and cancels a batch another one runs (batch.SharedStore). Under
// prefix, a batch id keys its record (<id>), its item results by index
// (<id>:results), its owner's lease (<id>:lease) and a cancel request
// (<id>:cancel); org:<org> is the set of an org's batch ids and all the set
// of every batch id. Each write keeps what it touches for its ttl. An
// unavailable Redis (or an open circuit) is an error, so no batch starts
// that other replicas cannot see, and no lookup answers not found for a
// batch it could not read.
type BatchStore struct {
	client *Client
	prefix string
}

// NewBatchStore keeps batches under prefix.
func NewBatchStore(client *Client, prefix string) *BatchStore {
	return &BatchStore{client: client, prefix: prefix}
}

var _ batch.SharedStore = (*BatchStore)(nil)

func (s *BatchStore) recordKey(id string) string  { return s.prefix + id }
func (s *BatchStore) resultsKey(id string) string { return s.prefix + id + ":results" }
func (s *BatchStore) leaseKey(id string) string   { return s.prefix + id + ":lease" }
func (s *BatchStore) cancelKey(id string) string  { return s.prefix + id + ":cancel" }
func (s *BatchStore) orgKey(orgID string) string  { return s.prefix + "org:" + orgID }
func (s *BatchStore) allKey() string              { return s.prefix + "all" }

// Create writes a new batch's record, its owner's lease and its listing
// entries in one transaction.
func (s *BatchStore) Create(ctx context.Context, id, orgID, owner string, record []byte, lease, ttl time.Duration) error {
	return s.client.Do(func(rdb redis.UniversalClient) error {
		_, err := rdb.TxPipelined(ctx, func(p redis.Pipeliner) error {
			p.Set(ctx, s.recordKey(id), record, ttl)
			p.Set(ctx, s.leaseKey(id), owner, lease)
			p.SAdd(ctx, s.orgKey(orgID), id)
			p.PExpire(ctx, s.orgKey(orgID), ttl)
			p.SAdd(ctx, s.allKey(), id)
			p.PExpire(ctx, s.allKey(), ttl)
			return nil
		})
		return err
	})
}

// batchSaveScript writes a batch's record and results while its owner holds
// the lease (KEYS: record, results, lease, cancel, org set, all set; ARGV:
// owner, ttl in ms, final, status, record, lost record, then index and
// result pairs). Without the lease the stored outcome stands: a processing
// record becomes the lost record, an ended one is written only when its
// status is the same, and an expired batch is left alone. It answers 1 when
// the stored status is the given one.
var batchSaveScript = redis.NewScript(`
if not redis.call('GET', KEYS[1]) then
  return 0
end
local record = ARGV[5]
local kept = 1
if redis.call('GET', KEYS[3]) ~= ARGV[1] then
  local status = cjson.decode(redis.call('GET', KEYS[1]))['status']
  if status == 'processing' then
    record = ARGV[6]
    if ARGV[4] ~= 'failed' then
      kept = 0
    end
  elseif status ~= ARGV[4] then
    record = false
    kept = 0
  end
end
if record then
  redis.call('SET', KEYS[1], record, 'PX', ARGV[2])
end
for i = 7, #ARGV, 2 do
  redis.call('HSET', KEYS[2], ARGV[i], ARGV[i + 1])
end
redis.call('PEXPIRE', KEYS[2], ARGV[2])
redis.call('PEXPIRE', KEYS[5], ARGV[2])
redis.call('PEXPIRE', KEYS[6], ARGV[2])
if ARGV[3] == '1' then
  redis.call('DEL', KEYS[3], KEYS[4])
end
return kept
`)

// Save writes w as one script, fenced by the owner's lease
// (batch.SharedStore.Save).
func (s *BatchStore) Save(ctx context.Context, w batch.Write) (bool, error) {
	keys := []string{s.recordKey(w.ID), s.resultsKey(w.ID), s.leaseKey(w.ID), s.cancelKey(w.ID), s.orgKey(w.OrgID), s.allKey()}
	final := "0"
	if w.Final {
		final = "1"
	}
	args := make([]any, 0, 6+2*len(w.Results))
	args = append(args, w.Owner, w.TTL.Milliseconds(), final, w.Status, w.Record, w.Lost)
	for index, result := range w.Results {
		args = append(args, strconv.Itoa(index), result)
	}
	kept := false
	err := s.client.Do(func(rdb redis.UniversalClient) error {
		n, err := batchSaveScript.Run(ctx, rdb, keys, args...).Int()
		kept = n == 1
		return err
	})
	return kept, err
}

// Renew extends owner's lease only while it exists (SET XX): a lease that
// has expired stays gone, and every replica keeps reading the batch as lost.
// It also reads the cancel request, and keeps the batch for ttl.
func (s *BatchStore) Renew(ctx context.Context, id, orgID, owner string, lease, ttl time.Duration) (held, cancel bool, err error) {
	err = s.client.Do(func(rdb redis.UniversalClient) error {
		var renewed *redis.BoolCmd
		var requested *redis.IntCmd
		_, err := rdb.TxPipelined(ctx, func(p redis.Pipeliner) error {
			renewed = p.SetXX(ctx, s.leaseKey(id), owner, lease)
			requested = p.Exists(ctx, s.cancelKey(id))
			p.PExpire(ctx, s.recordKey(id), ttl)
			p.PExpire(ctx, s.resultsKey(id), ttl)
			p.PExpire(ctx, s.orgKey(orgID), ttl)
			p.PExpire(ctx, s.allKey(), ttl)
			return nil
		})
		if err != nil {
			return err
		}
		held, cancel = renewed.Val(), requested.Val() > 0
		return nil
	})
	return held, cancel, err
}

// Load reads one batch, its results and its lease in one transaction. A
// batch that does not exist is an answer, not a failure: it never counts
// against the circuit the gateway's other Redis state shares.
func (s *BatchStore) Load(ctx context.Context, id string) (batch.StoredBatch, bool, error) {
	var stored batch.StoredBatch
	found := false
	err := s.client.Do(func(rdb redis.UniversalClient) error {
		var record *redis.StringCmd
		var results *redis.MapStringStringCmd
		var lease *redis.IntCmd
		_, err := rdb.TxPipelined(ctx, func(p redis.Pipeliner) error {
			record = p.Get(ctx, s.recordKey(id))
			results = p.HGetAll(ctx, s.resultsKey(id))
			lease = p.Exists(ctx, s.leaseKey(id))
			return nil
		})
		if errors.Is(record.Err(), redis.Nil) {
			return nil
		}
		if err != nil {
			return err
		}
		stored = batch.StoredBatch{
			Record:    []byte(record.Val()),
			Results:   make(map[int][]byte, len(results.Val())),
			LeaseHeld: lease.Val() > 0,
		}
		for field, value := range results.Val() {
			if index, err := strconv.Atoi(field); err == nil {
				stored.Results[index] = []byte(value)
			}
		}
		found = true
		return nil
	})
	return stored, found, err
}

// RequestCancel sets the batch's cancel request, which its owner reads at
// its next lease renewal.
func (s *BatchStore) RequestCancel(ctx context.Context, id string, ttl time.Duration) error {
	return s.client.Do(func(rdb redis.UniversalClient) error {
		return rdb.Set(ctx, s.cancelKey(id), "1", ttl).Err()
	})
}

// List reads every batch of orgID with its lease, without results.
func (s *BatchStore) List(ctx context.Context, orgID string) ([]batch.StoredBatch, error) {
	return s.listSet(ctx, s.orgKey(orgID))
}

// ListAll reads every org's batches with their leases, without results.
func (s *BatchStore) ListAll(ctx context.Context) ([]batch.StoredBatch, error) {
	return s.listSet(ctx, s.allKey())
}

// listSet reads the batches whose ids the set at key holds, each record
// with its lease, in one transaction. An id whose record has expired leaves
// the set.
func (s *BatchStore) listSet(ctx context.Context, key string) ([]batch.StoredBatch, error) {
	var out []batch.StoredBatch
	err := s.client.Do(func(rdb redis.UniversalClient) error {
		ids, err := rdb.SMembers(ctx, key).Result()
		if err != nil || len(ids) == 0 {
			return err
		}
		records := make([]*redis.StringCmd, len(ids))
		leases := make([]*redis.IntCmd, len(ids))
		if _, err := rdb.TxPipelined(ctx, func(p redis.Pipeliner) error {
			for i, id := range ids {
				records[i] = p.Get(ctx, s.recordKey(id))
				leases[i] = p.Exists(ctx, s.leaseKey(id))
			}
			return nil
		}); err != nil && !errors.Is(err, redis.Nil) {
			return err
		}
		var expired []any
		for i, id := range ids {
			if err := records[i].Err(); errors.Is(err, redis.Nil) {
				expired = append(expired, id)
				continue
			} else if err != nil {
				return err
			}
			if err := leases[i].Err(); err != nil {
				return err
			}
			out = append(out, batch.StoredBatch{Record: []byte(records[i].Val()), LeaseHeld: leases[i].Val() > 0})
		}
		if len(expired) > 0 {
			return rdb.SRem(ctx, key, expired...).Err()
		}
		return nil
	})
	return out, err
}
