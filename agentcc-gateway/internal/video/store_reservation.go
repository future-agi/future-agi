package video

import (
	"context"
	"encoding/json"
	"errors"
	"strconv"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/redis/go-redis/v9"
)

// Intent records contain recovery coordinates, never a second spend counter or
// request/media data. Budget receipts remain in the shared BudgetStore hashes.
type reservationIntent struct {
	ID, Org, Model string
	At             time.Time
	Micros         int64
	Scopes         []BudgetReservation
}

func (s *RedisStore) reservationKey(id string) string { return s.opts.Prefix + "reservation:" + id }
func (s *RedisStore) reservationGuard(id string) string {
	return s.opts.Prefix + "reservation-guard:" + id
}
func (s *RedisStore) reservationKeys(id string) []string {
	return []string{s.reservationKey(id), s.reservationGuard(id), s.opts.Prefix + "reservations", s.jobKey(id)}
}

const beginReservationScript = `
for i,t in ipairs({'string','string','zset','hash'}) do
 local got=redis.call('TYPE',KEYS[i]).ok
 if got ~= 'none' and got ~= t then return redis.error_reply('reservation key type mismatch') end
end
if redis.call('EXISTS',KEYS[1],KEYS[2],KEYS[4]) > 0 then return {-1} end
redis.call('SET',KEYS[1],ARGV[1])
redis.call('SET',KEYS[2],ARGV[2])
redis.call('ZADD',KEYS[3],ARGV[3],ARGV[4])
return {1}
`
const recoverReservationScript = `
local data=redis.call('GET',KEYS[1])
if not data then return {0} end
-- This check and revocation are atomic with job creation and every debit. A
-- paused acceptor can neither charge another scope nor create after recovery.
if redis.call('EXISTS',KEYS[4]) == 1 then
 redis.call('DEL',KEYS[1],KEYS[2]);redis.call('ZREM',KEYS[3],ARGV[1]);return {0}
end
redis.call('SET',KEYS[2],'recovering')
return {1,data}
`
const finishReservationScript = `
redis.call('DEL',KEYS[1],KEYS[2]);redis.call('ZREM',KEYS[3],ARGV[1]);return {1}
`

func (s *RedisStore) reserveAccept(ctx context.Context, j *VideoJob) (rollback, finish func() error, token string, err error) {
	noop := func() error { return nil }
	b, durable := s.opts.Budget.(*redisstate.BudgetStore)
	if !durable || j.ReservedMicros == 0 || len(j.Reservations) == 0 {
		rollback, err = reserve(s.opts, j)
		return rollback, noop, "", err
	}
	intent := reservationIntent{j.ID, j.OrgID, j.Model, j.CreatedAt, j.ReservedMicros, j.Reservations}
	data, err := json.Marshal(intent)
	if err != nil {
		return nil, nil, "", err
	}
	token = NewID()
	res, err := s.eval(ctx, beginReservationScript, s.reservationKeys(j.ID), string(data), token, s.opts.Now().Add(time.Minute).Unix(), j.ID)
	if err != nil {
		return nil, nil, "", err
	}
	if res[0].(int64) != 1 {
		return nil, nil, "", ErrVideoJobExists
	}
	finish = func() error {
		cleanup, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_, e := s.eval(cleanup, finishReservationScript, s.reservationKeys(j.ID), j.ID)
		return e
	}
	rollback = func() error {
		cleanup, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		return s.recoverReservation(cleanup, j.ID, b)
	}
	reserver := b.WithReservation("video-reserve:"+j.ID, j.CreatedAt, s.reservationGuard(j.ID), token)
	for _, scope := range j.Reservations {
		_, allowed, ok := reserver.CheckAndRecordSpend(j.OrgID, scope.Level, scope.Key, scope.Period, j.Model, float64(j.ReservedMicros)/1e6, scope.Limit, scope.ModelLimit)
		if !ok {
			return nil, nil, "", errors.Join(ErrStoreUnavailable, rollback())
		}
		if !allowed {
			return nil, nil, "", errors.Join(ErrBudgetExceeded, rollback())
		}
	}
	return rollback, finish, token, nil
}

func (s *RedisStore) recoverReservation(ctx context.Context, id string, b *redisstate.BudgetStore) error {
	res, err := s.eval(ctx, recoverReservationScript, s.reservationKeys(id), id)
	if err != nil {
		return err
	}
	if res[0].(int64) == 0 {
		return nil
	}
	var r reservationIntent
	if err = json.Unmarshal([]byte(res[1].(string)), &r); err != nil {
		return ErrStoreUnavailable
	}
	charged := b.WithOperation("video-reserve:"+id, r.At)
	refund := b.WithOperation("video-refund:"+id, r.At)
	for _, scope := range r.Scopes {
		applied, e := charged.OperationApplied(ctx, r.Org, scope.Level, scope.Key, scope.Period)
		if e != nil {
			return errors.Join(ErrCompensationFailed, e)
		}
		if applied {
			if _, ok := refund.RecordSpend(r.Org, scope.Level, scope.Key, scope.Period, r.Model, -float64(r.Micros)/1e6); !ok {
				return ErrCompensationFailed
			}
		}
	}
	_, err = s.eval(ctx, finishReservationScript, s.reservationKeys(id), id)
	return err
}

func (s *RedisStore) recoverReservations(ctx context.Context) error {
	b, ok := s.opts.Budget.(*redisstate.BudgetStore)
	if !ok {
		return nil
	}
	var ids []string
	err := s.do(func(rdb redis.UniversalClient) error {
		var err error
		ids, err = rdb.ZRangeByScore(ctx, s.opts.Prefix+"reservations", &redis.ZRangeBy{Min: "-inf", Max: strconv.FormatInt(s.opts.Now().Unix(), 10), Count: 32}).Result()
		return err
	})
	if err != nil {
		return err
	}
	for _, id := range ids {
		if err = s.recoverReservation(ctx, id, b); err != nil {
			return err
		}
	}
	return nil
}
