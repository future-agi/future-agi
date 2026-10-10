package video

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"strconv"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/redis/go-redis/v9"
)

// RedisStore uses the shared Redis client and budget counters. No memory fallback
// is permitted. The multi-key scripts target the gateway's single Redis primary;
// Redis Cluster cross-slot transactions are not supported by this v1 schema.
type RedisStore struct {
	client *redisstate.Client
	opts   StoreOptions
}

func NewRedisStore(client *redisstate.Client, opts StoreOptions) *RedisStore {
	return &RedisStore{client: client, opts: defaults(opts)}
}
func (s *RedisStore) jobKey(id string) string   { return s.opts.Prefix + "job:" + id }
func (s *RedisStore) leaseKey(id string) string { return s.opts.Prefix + "lease:" + id }
func (s *RedisStore) orgKey(org string) string {
	return s.opts.Prefix + "org:" + url.QueryEscape(org) + ":jobs"
}
func (s *RedisStore) idemKey(j *VideoJob) string {
	return s.opts.Prefix + "idem:" + url.QueryEscape(j.OrgID) + ":" + url.QueryEscape(j.IdemOp) + ":" + j.IdemDigest
}
func (s *RedisStore) keys(j *VideoJob) []string {
	return []string{s.jobKey(j.ID), s.leaseKey(j.ID), s.orgKey(j.OrgID), s.idemKey(j), s.opts.Prefix + "submit", s.opts.Prefix + "due", s.opts.Prefix + "reconcile", s.opts.Prefix + "expire", s.opts.Prefix + "active:org:" + url.QueryEscape(j.OrgID), s.opts.Prefix + "active:account:" + url.QueryEscape(j.Service+":"+j.AccountRef+":"+j.ModelID), s.reservationGuard(j.ID)}
}
func (s *RedisStore) do(fn func(redis.UniversalClient) error) error {
	if s.client == nil {
		return ErrStoreUnavailable
	}
	if err := s.client.Do(fn); err != nil {
		return fmt.Errorf("%w: %v", ErrStoreUnavailable, err)
	}
	return nil
}
func (s *RedisStore) eval(ctx context.Context, script string, keys []string, args ...any) ([]any, error) {
	var out []any
	err := s.do(func(rdb redis.UniversalClient) error {
		var err error
		out, err = redis.NewScript(script).Run(ctx, rdb, keys, args...).Slice()
		return err
	})
	return out, err
}

// Scripts validate key types BEFORE writing: Lua execution errors do not roll
// back earlier writes. Every multi-key mutation either bootstraps a fresh fence
// for a new record or validates both the live lease and persisted fencing token.
const redisWritePrelude = `
local expected_types = {'hash','string','zset','string','zset','zset','zset','zset','zset','zset','string'}
for i,k in ipairs(KEYS) do
 local t = redis.call('TYPE',k).ok
 if t ~= 'none' and t ~= expected_types[i] then return redis.error_reply('video key type mismatch') end
end
local function protected(d)
 local terminal = d.status == 'completed' or d.status == 'failed' or d.status == 'cancelled'
 return not terminal or d.settlement_state == 'reserved' or d.settlement_state == 'unsettled' or d.reconcile_state == 'pending' or d.reconcile_state == 'unresolved'
end
local function indexes(d)
 local id = d.id
 redis.call('ZREM',KEYS[3],id)
 redis.call('ZREM',KEYS[5],id)
 redis.call('ZREM',KEYS[6],id)
 redis.call('ZREM',KEYS[7],id)
 local expiry = math.max(tonumber(d.expires_at or '0'),tonumber(d.idempotency_expires_at or '0'))
 if expiry > 0 then redis.call('ZADD',KEYS[8],expiry,id) else redis.call('ZREM',KEYS[8],id) end
 if d.org_id == '' then return end
 local terminal = d.status == 'completed' or d.status == 'failed' or d.status == 'cancelled'
 if not terminal or d.upstream_may_continue == 'true' then
  redis.call('ZADD',KEYS[9],0,id); redis.call('ZADD',KEYS[10],0,id)
 else redis.call('ZREM',KEYS[9],id); redis.call('ZREM',KEYS[10],id) end
 local deleted = d.deleted_at ~= nil and d.deleted_at ~= ''
 local active = not deleted or protected(d)
 local next_at = tonumber(d.next_poll_at or '0')
 if not deleted then redis.call('ZADD',KEYS[3],tonumber(d.created_at or '0'),id) end
 if d.status == 'submitting' and not deleted then
  local score = next_at
  if score == 0 then score = tonumber(d.created_at or '0') end
  redis.call('ZADD',KEYS[5],score,id)
 end
 if active and (d.status == 'submission_unknown' or d.reconcile_state == 'pending' or d.reconcile_state == 'unresolved') then
  redis.call('ZADD',KEYS[7],next_at,id)
 elseif active and (d.status == 'queued' or d.status == 'running' or ((d.status == 'completed' or d.status == 'failed' or d.status == 'cancelled') and (d.settlement_state == 'reserved' or d.settlement_state == 'unsettled'))) then
  redis.call('ZADD',KEYS[6],next_at,id)
 end
 if d.idem_digest ~= nil and d.idem_digest ~= '' then
  local value=redis.call('GET',KEYS[4])
  if value then
   local ok,binding=pcall(cjson.decode,value)
   if ok and binding.id == id then
    local idem_expiry=tonumber(d.idempotency_expires_at or '0')
    if protected(d) then redis.call('PERSIST',KEYS[4])
    elseif idem_expiry > 0 then redis.call('PEXPIREAT',KEYS[4],idem_expiry*1000) end
   end
  end
 end
end
local function write(d)
 for k,v in pairs(d) do redis.call('HSET',KEYS[1],k,v) end
 indexes(d)
end
local function fenced(owner,fence)
 return redis.call('HGET',KEYS[1],'org_id') ~= '' and
 redis.call('HGET',KEYS[1],'lease_fence') == fence and
 redis.call('GET',KEYS[2]) == owner .. ':' .. fence
end
`
const redisCreateScript = redisWritePrelude + `
local d = cjson.decode(ARGV[1])
local use_idem = ARGV[2] == '1'
if ARGV[5] ~= '' and redis.call('GET',KEYS[11]) ~= ARGV[5] then return {-3,''} end
if use_idem then
 local existing = redis.call('GET',KEYS[4])
 if existing then return {0,existing} end
end
if redis.call('EXISTS',KEYS[1]) == 1 then return {-2,''} end
if use_idem then
 if tonumber(d.org_max_active or '0') > 0 and redis.call('ZCARD',KEYS[9]) >= tonumber(d.org_max_active) then return {-6,''} end
 if tonumber(d.account_max_active or '0') > 0 and redis.call('ZCARD',KEYS[10]) >= tonumber(d.account_max_active) then return {-6,''} end
end
if not redis.call('SET',KEYS[2],ARGV[3] .. ':1','NX','PX',5000) then return {-3,''} end
-- The acceptor owns the initial fence; no worker can claim until this script ends.
d.lease_fence = '1'
d.lease_owner = ARGV[3]
redis.call('HSET',KEYS[1],'lease_fence','1','org_id',d.org_id)
if redis.call('GET',KEYS[2]) ~= ARGV[3] .. ':1' then return {-1,''} end
if use_idem then redis.call('SET',KEYS[4],ARGV[4],'NX') end
write(d)
redis.call('DEL',KEYS[2])
redis.call('HSET',KEYS[1],'lease_released','true')
return {1,d.id}
`
const redisSaveScript = redisWritePrelude + `
if not fenced(ARGV[2],ARGV[3]) then return {-1} end
local d = cjson.decode(ARGV[1])
for _,field in ipairs({'id','org_id','service','model_id','region','account_ref','model','capability_revision','fingerprint','idem_digest','idem_op','key_id','creator','tariff_revision','reserved_micros','created_at','submit_by'}) do
 if (redis.call('HGET',KEYS[1],field) or '') ~= (d[field] or '') then return {-5} end
end
local run_by = redis.call('HGET',KEYS[1],'run_by')
if run_by and run_by ~= '0' and run_by ~= d.run_by then return {-5} end
local provider_id = redis.call('HGET',KEYS[1],'provider_job_id')
if provider_id and provider_id ~= '' and provider_id ~= d.provider_job_id then return {-5} end
local old = redis.call('HGET',KEYS[1],'status')
local phase = redis.call('HGET',KEYS[1],'phase')
local status = d.status
local allowed = false
if old == status then
 allowed = old ~= 'submitting' or phase == d.phase or (phase == 'prepared' and d.phase == 'calling')
elseif old == 'submitting' and phase == 'prepared' then allowed = status == 'failed' or status == 'cancelled'
elseif old == 'submitting' and phase == 'calling' then allowed = status == 'queued' or status == 'running' or status == 'completed' or status == 'failed' or status == 'submission_unknown' or status == 'cancelled'
elseif old == 'submission_unknown' then allowed = status == 'queued' or status == 'running' or status == 'completed' or status == 'failed' or status == 'cancelled'
elseif old == 'queued' then allowed = status == 'running' or status == 'completed' or status == 'failed' or status == 'cancelled'
elseif old == 'running' then allowed = status == 'completed' or status == 'failed' or status == 'cancelled' end
if old == 'submission_unknown' and status == 'submitting' and d.phase == 'prepared' and d.resubmit_authorized == 'true' then allowed = true end
if not allowed then return {-4} end
local deleted = redis.call('HGET',KEYS[1],'deleted_at')
if deleted and deleted ~= '' then d.deleted_at = deleted end
if d.deleted_at and d.deleted_at ~= '' then
 d.prompt=''; d.request_canonical='null'; d.metadata_json='null'; d.artifacts_json='null'; d.videos='null'; d.remix_source=''
end
d.lease_owner=ARGV[2]; d.lease_fence=ARGV[3]
write(d)
return {1}
`
const redisLeaseScript = `
if redis.call('HGET',KEYS[1],'org_id') == false or redis.call('HGET',KEYS[1],'org_id') == '' then return {0} end
if ARGV[3] == '1' then
 local score = redis.call('ZSCORE',KEYS[3],ARGV[4])
 if not score or tonumber(score) > tonumber(ARGV[5]) then return {0} end
end
if redis.call('EXISTS',KEYS[2]) == 1 then return {-2} end
local takeover=0
if redis.call('HGET',KEYS[1],'lease_released')=='false' then takeover=1 end
local fence = redis.call('HINCRBY',KEYS[1],'lease_fence',1)
if not redis.call('SET',KEYS[2],ARGV[1] .. ':' .. fence,'NX','PX',ARGV[2]) then return {-2} end
redis.call('HSET',KEYS[1],'lease_owner',ARGV[1],'lease_released','false')
return {1,fence,takeover}
`
const redisRenewScript = `
if redis.call('GET',KEYS[2]) ~= ARGV[1] .. ':' .. ARGV[2] or redis.call('HGET',KEYS[1],'lease_fence') ~= ARGV[2] or (redis.call('HGET',KEYS[1],'org_id') or '') == '' then return {-1} end
redis.call('PEXPIRE',KEYS[2],ARGV[3])
return {1}
`
const redisReleaseScript = `
if redis.call('GET',KEYS[2]) ~= ARGV[1] .. ':' .. ARGV[2] or redis.call('HGET',KEYS[1],'lease_fence') ~= ARGV[2] or (redis.call('HGET',KEYS[1],'org_id') or '') == '' then return {-1} end
redis.call('DEL',KEYS[2])
redis.call('HSET',KEYS[1],'lease_released','true')
return {1}
`
const redisGCScript = redisWritePrelude + `
if not fenced(ARGV[1],ARGV[2]) then return {-1} end
local raw = redis.call('HGETALL',KEYS[1]); local d = {}
for i=1,#raw,2 do d[raw[i]]=raw[i+1] end
if protected(d) then return {0} end
local expiry = math.max(tonumber(d.expires_at or '0'),tonumber(d.idempotency_expires_at or '0'))
if expiry <= 0 or expiry > tonumber(ARGV[3]) then return {0} end
-- Only delete the idempotency binding that still belongs to this job.
local value=redis.call('GET',KEYS[4])
local same_binding = false
if value then local binding=cjson.decode(value);same_binding = binding.id == d.id end
for _,i in ipairs({3,5,6,7,8,9,10}) do redis.call('ZREM',KEYS[i],d.id) end
if same_binding then redis.call('DEL',KEYS[4]) end
redis.call('DEL',KEYS[1],KEYS[2])
return {1}
`

type idemBinding struct {
	ID          string `json:"id"`
	Fingerprint string `json:"fingerprint"`
}

func (s *RedisStore) Create(j *VideoJob) error {
	if err := validateJob(j); err != nil {
		return err
	}
	_, err := s.create(context.Background(), j, false, "")
	return err
}
func (s *RedisStore) create(ctx context.Context, j *VideoJob, useIdem bool, reservationToken string) (*idemBinding, error) {
	fields, err := encodeJob(j)
	if err != nil {
		return nil, err
	}
	data, err := json.Marshal(fields)
	if err != nil {
		return nil, err
	}
	binding, _ := json.Marshal(idemBinding{j.ID, j.Fingerprint})
	flag := 0
	if useIdem {
		flag = 1
	}
	result, err := s.eval(ctx, redisCreateScript, s.keys(j), string(data), flag, NewID(), string(binding), reservationToken)
	if err != nil {
		return nil, err
	}
	switch result[0].(int64) {
	case 1:
		return nil, nil
	case 0:
		var b idemBinding
		if err := json.Unmarshal([]byte(result[1].(string)), &b); err != nil {
			return nil, fmt.Errorf("%w: invalid idempotency binding", ErrStoreUnavailable)
		}
		return &b, nil
	case -6:
		return nil, ErrActiveLimit
	case -2:
		return nil, ErrVideoJobExists
	default:
		return nil, ErrLeaseHeld
	}
}
func (s *RedisStore) Accept(ctx context.Context, req AcceptRequest) (AcceptResult, error) {
	j, err := prepareAccept(req, s.opts)
	if err != nil {
		return AcceptResult{}, err
	}
	// A replay need not reserve again; races after this read are handled by the
	// Lua SET NX result and explicit compensation below.
	var existing string
	err = s.do(func(rdb redis.UniversalClient) error {
		v, e := rdb.Get(ctx, s.idemKey(j)).Result()
		if errors.Is(e, redis.Nil) {
			return nil
		}
		existing = v
		return e
	})
	if err != nil {
		return AcceptResult{}, err
	}
	if existing != "" {
		var binding idemBinding
		if err := json.Unmarshal([]byte(existing), &binding); err != nil {
			return AcceptResult{}, ErrStoreUnavailable
		}
		return s.replay(ctx, binding, j.Fingerprint)
	}
	compensate, finish, token, err := s.reserveAccept(ctx, j)
	if err != nil {
		return AcceptResult{}, err
	}
	binding, err := s.create(ctx, j, true, token)
	if err != nil {
		// A lost reply is not proof that the Lua transaction failed. Read back on
		// an independent bounded context even if the request context was cancelled.
		recovery, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		var value string
		readErr := s.do(func(rdb redis.UniversalClient) error {
			v, e := rdb.Get(recovery, s.idemKey(j)).Result()
			if errors.Is(e, redis.Nil) {
				return nil
			}
			value = v
			return e
		})
		if readErr != nil {
			return AcceptResult{}, errors.Join(err, ErrReservationUncertain, readErr)
		}
		if value != "" {
			var found idemBinding
			if json.Unmarshal([]byte(value), &found) != nil {
				return AcceptResult{}, errors.Join(err, ErrReservationUncertain)
			}
			if found.ID == j.ID && found.Fingerprint == j.Fingerprint {
				_ = finish()
				return s.replay(recovery, found, j.Fingerprint)
			}
			if e := compensate(); e != nil {
				return AcceptResult{}, errors.Join(err, e)
			}
			return s.replay(recovery, found, j.Fingerprint)
		}
		return AcceptResult{}, errors.Join(err, compensate())
	}
	if binding != nil {
		if err := compensate(); err != nil {
			return AcceptResult{}, err
		}
		return s.replay(ctx, *binding, j.Fingerprint)
	}
	_ = finish() // A lost cleanup reply is harmless; GC sees the committed job.
	persisted, err := s.GetAccounting(ctx, j.ID)
	if err != nil {
		return AcceptResult{}, errors.Join(err, ErrReservationUncertain)
	}
	return AcceptResult{Job: persisted}, nil
}
func (s *RedisStore) replay(ctx context.Context, binding idemBinding, fp string) (AcceptResult, error) {
	j, err := s.GetAccounting(ctx, binding.ID)
	if err != nil {
		return AcceptResult{}, err
	}
	if binding.Fingerprint != j.Fingerprint {
		return AcceptResult{}, ErrStoreUnavailable
	}
	return replay(j, fp)
}
func (s *RedisStore) GetAccounting(ctx context.Context, id string) (*VideoJob, error) {
	var fields map[string]string
	err := s.do(func(rdb redis.UniversalClient) error {
		var err error
		fields, err = rdb.HGetAll(ctx, s.jobKey(id)).Result()
		return err
	})
	if err != nil {
		return nil, err
	}
	if fields["org_id"] == "" || fields["id"] != id {
		return nil, ErrVideoJobNotFound
	}
	j, err := decodeJob(fields)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrStoreUnavailable, err)
	}
	return j, nil
}
func (s *RedisStore) Get(id string) (*VideoJob, error) {
	j, err := s.GetAccounting(context.Background(), id)
	if err != nil {
		return nil, err
	}
	if j.DeletedAt != nil {
		return nil, ErrVideoJobNotFound
	}
	return j, nil
}
func (s *RedisStore) GetForOrg(id, org string) (*VideoJob, error) {
	j, err := s.Get(id)
	if err != nil {
		return nil, err
	}
	if org == "" || j.OrgID != org {
		return nil, ErrVideoJobNotFound
	}
	return j, nil
}
func (s *RedisStore) Lease(ctx context.Context, id, owner string, ttl time.Duration) (Lease, error) {
	return s.lease(ctx, id, owner, ttl, "")
}
func (s *RedisStore) lease(ctx context.Context, id, owner string, ttl time.Duration, queue string) (Lease, error) {
	if owner == "" || ttl < time.Millisecond {
		return Lease{}, fmt.Errorf("invalid lease")
	}
	flag := 0
	if queue != "" {
		flag = 1
	}
	result, err := s.eval(ctx, redisLeaseScript, []string{s.jobKey(id), s.leaseKey(id), s.opts.Prefix + queue}, owner, ttl.Milliseconds(), flag, id, s.opts.Now().Unix())
	if err != nil {
		return Lease{}, err
	}
	switch result[0].(int64) {
	case 0:
		return Lease{}, ErrVideoJobNotFound
	case -2:
		return Lease{}, ErrLeaseHeld
	}
	return Lease{Takeover: result[2].(int64) == 1, JobID: id, Owner: owner, Fence: result[1].(int64), ExpiresAt: s.opts.Now().Add(ttl)}, nil
}
func (s *RedisStore) Renew(ctx context.Context, l Lease, ttl time.Duration) error {
	if ttl < time.Millisecond {
		return fmt.Errorf("invalid lease TTL")
	}
	result, err := s.eval(ctx, redisRenewScript, []string{s.jobKey(l.JobID), s.leaseKey(l.JobID)}, l.Owner, l.Fence, ttl.Milliseconds())
	if err != nil {
		return err
	}
	if result[0].(int64) != 1 {
		return ErrLeaseLost
	}
	return nil
}
func (s *RedisStore) Release(ctx context.Context, l Lease) error {
	result, err := s.eval(ctx, redisReleaseScript, []string{s.jobKey(l.JobID), s.leaseKey(l.JobID)}, l.Owner, l.Fence)
	if err != nil {
		return err
	}
	if result[0].(int64) != 1 {
		return ErrLeaseLost
	}
	return nil
}
func (s *RedisStore) Save(ctx context.Context, j *VideoJob, l Lease) error {
	if j == nil || j.ID != l.JobID {
		return ErrLeaseLost
	}
	old, err := s.GetAccounting(ctx, j.ID)
	if err != nil {
		return err
	}
	copy, err := cloneJob(j)
	if err != nil {
		return err
	}
	if err := validateImmutable(old, copy); err != nil {
		return err
	}
	if err := ValidateTransition(old.Status, old.Phase, copy.Status, copy.Phase); err != nil && !authorizedResubmit(old, copy) {
		return err
	}
	if old.DeletedAt != nil {
		copy.DeletedAt = old.DeletedAt
	}
	stripDeleted(copy)
	if err := validateCompletion(old, copy); err != nil {
		return err
	}
	if err := validateJob(copy); err != nil {
		return err
	}
	copy.UpdatedAt = s.opts.Now()
	fields, err := encodeJob(copy)
	if err != nil {
		return err
	}
	data, err := json.Marshal(fields)
	if err != nil {
		return err
	}
	result, err := s.eval(ctx, redisSaveScript, s.keys(old), string(data), l.Owner, l.Fence)
	if err != nil {
		return err
	}
	switch result[0].(int64) {
	case 1:
		return nil
	case -1:
		return ErrLeaseLost
	case -4:
		return ErrIllegalTransition
	case -5:
		return fmt.Errorf("video pinned identity is immutable")
	}
	return ErrStoreUnavailable
}
func (s *RedisStore) Update(j *VideoJob) error {
	ctx := context.Background()
	l, err := s.Lease(ctx, j.ID, NewID(), time.Minute)
	if err != nil {
		return err
	}
	defer s.Release(ctx, l)
	return s.Save(ctx, j, l)
}
func (s *RedisStore) Tombstone(ctx context.Context, id, org string, l Lease) error {
	j, err := s.GetAccounting(ctx, id)
	if err != nil {
		return err
	}
	if org == "" || j.OrgID != org {
		return ErrVideoJobNotFound
	}
	if j.DeletedAt == nil {
		now := s.opts.Now()
		j.DeletedAt = &now
	}
	stripDeleted(j)
	return s.Save(ctx, j, l)
}
func (s *RedisStore) Delete(id string) error {
	ctx := context.Background()
	j, err := s.GetAccounting(ctx, id)
	if err != nil {
		return err
	}
	l, err := s.Lease(ctx, id, NewID(), time.Minute)
	if err != nil {
		return err
	}
	defer s.Release(ctx, l)
	return s.Tombstone(ctx, id, j.OrgID, l)
}
func (s *RedisStore) readJobs(ctx context.Context, ids []string) ([]*VideoJob, error) {
	var commands []*redis.MapStringStringCmd
	err := s.do(func(rdb redis.UniversalClient) error {
		pipe := rdb.Pipeline()
		for _, id := range ids {
			commands = append(commands, pipe.HGetAll(ctx, s.jobKey(id)))
		}
		_, err := pipe.Exec(ctx)
		return err
	})
	if err != nil {
		return nil, err
	}
	out := make([]*VideoJob, 0, len(commands))
	for i, command := range commands {
		fields := command.Val()
		if fields["org_id"] == "" || fields["id"] != ids[i] {
			continue
		}
		j, err := decodeJob(fields)
		if err != nil {
			return nil, fmt.Errorf("%w: %v", ErrStoreUnavailable, err)
		}
		out = append(out, j)
	}
	return out, nil
}

// Filtering precedes offset/limit and total calculation. As documented, offset
// pagination may shift under inserts; ties use created_at then ID ordering.
func (s *RedisStore) ListByOrg(org string, f VideoListFilters) ([]*VideoJob, int, error) {
	if err := validateFilters(f); err != nil {
		return nil, 0, err
	}
	if org == "" {
		return []*VideoJob{}, 0, nil
	}
	ctx := context.Background()
	var ids []string
	err := s.do(func(rdb redis.UniversalClient) error {
		var err error
		ids, err = rdb.ZRevRange(ctx, s.orgKey(org), 0, -1).Result()
		return err
	})
	if err != nil {
		return nil, 0, err
	}
	jobs, err := s.readJobs(ctx, ids)
	if err != nil {
		return nil, 0, err
	}
	return filterJobs(jobs, org, f)
}
func (s *RedisStore) GetActiveJobs() ([]*VideoJob, error) {
	ctx := context.Background()
	var ids []string
	err := s.do(func(rdb redis.UniversalClient) error {
		var err error
		ids, err = rdb.ZRange(ctx, s.opts.Prefix+"expire", 0, -1).Result()
		return err
	})
	if err != nil {
		return nil, err
	}
	jobs, err := s.readJobs(ctx, ids)
	if err != nil {
		return nil, err
	}
	out := []*VideoJob{}
	for _, j := range jobs {
		if !j.IsTerminal() && j.DeletedAt == nil {
			out = append(out, j)
		}
	}
	return out, nil
}
func (s *RedisStore) claim(ctx context.Context, queue, owner string, ttl time.Duration, limit int) ([]Claim, error) {
	if limit < 1 || owner == "" || ttl < time.Millisecond {
		return nil, fmt.Errorf("invalid claim")
	}
	var ids []string
	err := s.do(func(rdb redis.UniversalClient) error {
		var err error
		ids, err = rdb.ZRangeByScore(ctx, s.opts.Prefix+queue, &redis.ZRangeBy{Min: "-inf", Max: strconv.FormatInt(s.opts.Now().Unix(), 10)}).Result()
		return err
	})
	if err != nil {
		return nil, err
	}
	out := []Claim{}
	for _, id := range ids {
		l, err := s.lease(ctx, id, owner, ttl, queue)
		if errors.Is(err, ErrLeaseHeld) || errors.Is(err, ErrVideoJobNotFound) {
			continue
		}
		if err != nil {
			return nil, err
		}
		j, err := s.GetAccounting(ctx, id)
		if err != nil {
			_ = s.Release(ctx, l)
			if errors.Is(err, ErrVideoJobNotFound) {
				continue
			}
			return nil, err
		}
		if !inQueue(j, queue) {
			_ = s.Release(ctx, l)
			continue
		}
		out = append(out, Claim{Job: j, Lease: l})
		if len(out) == limit {
			break
		}
	}
	return out, nil
}
func (s *RedisStore) ClaimSubmit(ctx context.Context, o string, ttl time.Duration, n int) ([]Claim, error) {
	return s.claim(ctx, "submit", o, ttl, n)
}
func (s *RedisStore) ClaimDue(ctx context.Context, o string, ttl time.Duration, n int) ([]Claim, error) {
	return s.claim(ctx, "due", o, ttl, n)
}
func (s *RedisStore) ClaimReconcile(ctx context.Context, o string, ttl time.Duration, n int) ([]Claim, error) {
	return s.claim(ctx, "reconcile", o, ttl, n)
}
func (s *RedisStore) GarbageCollect() (int, error) {
	ctx := context.Background()
	if err := s.recoverReservations(ctx); err != nil {
		return 0, err
	}
	var ids []string
	err := s.do(func(rdb redis.UniversalClient) error {
		var err error
		ids, err = rdb.ZRangeByScore(ctx, s.opts.Prefix+"expire", &redis.ZRangeBy{Min: "-inf", Max: strconv.FormatInt(s.opts.Now().Unix(), 10)}).Result()
		return err
	})
	if err != nil {
		return 0, err
	}
	count := 0
	for _, id := range ids {
		l, err := s.Lease(ctx, id, NewID(), time.Minute)
		if errors.Is(err, ErrLeaseHeld) || errors.Is(err, ErrVideoJobNotFound) {
			continue
		}
		if err != nil {
			return count, err
		}
		j, err := s.GetAccounting(ctx, id)
		if err != nil {
			_ = s.Release(ctx, l)
			return count, err
		}
		result, err := s.eval(ctx, redisGCScript, s.keys(j), l.Owner, l.Fence, s.opts.Now().Unix())
		if err != nil {
			_ = s.Release(ctx, l)
			return count, err
		}
		if result[0].(int64) == 1 {
			count++
		} else {
			_ = s.Release(ctx, l)
		}
	}
	return count, nil
}

var _ Store = (*RedisStore)(nil)
var _ Store = (*MemoryStore)(nil)

func (s *RedisStore) ListAccounting(ctx context.Context) ([]*VideoJob, error) {
	var ids []string
	err := s.do(func(c redis.UniversalClient) error {
		var e error
		ids, e = c.ZRange(ctx, s.opts.Prefix+"expire", 0, -1).Result()
		return e
	})
	if err != nil {
		return nil, err
	}
	return s.readJobs(ctx, ids)
}
