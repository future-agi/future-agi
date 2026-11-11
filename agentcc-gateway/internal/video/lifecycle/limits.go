package lifecycle

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/redis/go-redis/v9"
	"time"
)

func accountKey(service, account, model string) string {
	return fmt.Sprintf("%x", sha256.Sum256([]byte(service+"\x00"+account+"\x00"+model)))
}
func (s *Service) rate(ctx context.Context, j *video.VideoJob, kind string) (bool, time.Duration, error) {
	p := s.cfg.Providers[j.Service]
	limit := p.Limits.PollQPS
	window := time.Second
	model := ""
	if kind == "submit" {
		limit = p.Limits.SubmitRPM
		window = time.Minute
		model = j.ModelID
	}
	if s.redis == nil && s.cfg.Store == "memory" {
		return true, 0, nil
	}
	ok, d, e := s.limiter.AllowWindow(ctx, accountKey(j.Service, j.AccountRef, model)+":"+kind, limit, window)
	if e != nil {
		return false, 0, video.ErrStoreUnavailable
	}
	return ok, d, nil
}

// ListLimit supplies BytePlus's cross-replica, account-scoped one-QPS gate.
func (s *Service) ListLimit(ctx context.Context, account string) error {
	for {
		ok, d, e := s.limiter.AllowWindow(ctx, accountKey("byteplus", account, "")+":list", 1, time.Second)
		if e != nil {
			return video.ErrStoreUnavailable
		}
		if ok {
			return nil
		}
		t := time.NewTimer(d)
		select {
		case <-ctx.Done():
			t.Stop()
			return ctx.Err()
		case <-t.C:
		}
	}
}

var acquirePermit = redis.NewScript(`
local t=redis.call('TIME');local now=tonumber(t[1])*1000+math.floor(tonumber(t[2])/1000)
for i,k in ipairs(KEYS) do redis.call('ZREMRANGEBYSCORE',k,'-inf',now);if redis.call('ZCARD',k)>=tonumber(ARGV[i+2]) then return 0 end end
for _,k in ipairs(KEYS) do redis.call('ZADD',k,now+tonumber(ARGV[2]),ARGV[1]);redis.call('PEXPIRE',k,tonumber(ARGV[2])*2) end
return 1`)
var renewPermit = redis.NewScript(`
local t=redis.call('TIME');local now=tonumber(t[1])*1000+math.floor(tonumber(t[2])/1000)
for _,k in ipairs(KEYS) do local score=redis.call('ZSCORE',k,ARGV[1]);if not score or tonumber(score)<=now then return 0 end end
for _,k in ipairs(KEYS) do redis.call('ZADD',k,now+tonumber(ARGV[2]),ARGV[1]);redis.call('PEXPIRE',k,tonumber(ARGV[2])*2) end
return 1`)
var errLimited = errors.New("video concurrency limited")

func (s *Service) withPermit(ctx context.Context, j *video.VideoJob, kind string, fn func(context.Context) error) error {
	if s.redis == nil {
		if s.cfg.Store == "memory" {
			return fn(ctx)
		}
		return video.ErrStoreUnavailable
	}
	keys := []string{s.prefix + "semaphore:total", s.prefix + "semaphore:" + accountKey(j.Service, j.AccountRef, "")}
	limits := []int{s.cfg.Poll.MaxConcurrentTotal, s.cfg.Poll.PerProviderMaxConcurrent}
	if kind == "copy" {
		keys = append(keys, s.prefix+"semaphore:copy")
		limits = append(limits, s.cfg.Copy.MaxConcurrent)
	}
	token := video.NewID()
	ttl := s.cfg.Submit.LeaseTTL
	args := []any{token, ttl.Milliseconds()}
	for _, n := range limits {
		args = append(args, n)
	}
	var held int64
	err := s.redis.Do(func(c redis.UniversalClient) error {
		var e error
		held, e = acquirePermit.Run(ctx, c, keys, args...).Int64()
		return e
	})
	if err != nil {
		return video.ErrStoreUnavailable
	}
	if held != 1 {
		return errLimited
	}
	ctx, cancel := context.WithCancel(ctx)
	done := make(chan struct{})
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		t := time.NewTicker(max(ttl/3, time.Millisecond))
		defer t.Stop()
		for {
			select {
			case <-done:
				return
			case <-ctx.Done():
				return
			case <-t.C:
				var n int64
				e := s.redis.Do(func(c redis.UniversalClient) error {
					var e error
					n, e = renewPermit.Run(ctx, c, keys, token, ttl.Milliseconds()).Int64()
					return e
				})
				if e != nil || n != 1 {
					cancel()
					return
				}
			}
		}
	}()
	defer func() {
		close(done)
		cancel()
		<-stopped
		cleanup, stop := context.WithTimeout(context.Background(), time.Second)
		defer stop()
		_ = s.redis.Do(func(c redis.UniversalClient) error {
			p := c.Pipeline()
			for _, k := range keys {
				p.ZRem(cleanup, k, token)
			}
			_, e := p.Exec(cleanup)
			return e
		})
	}()
	return fn(ctx)
}
