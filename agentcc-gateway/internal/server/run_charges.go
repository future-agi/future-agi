package server

import (
	"context"
	"encoding/json"
	"log/slog"
	"strings"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// pendingChargeTTL is how long a run may take to finish and still be
// charged. Runs expire upstream well within it.
const pendingChargeTTL = 24 * time.Hour

// pendingCharge is what charging a finished run needs from the call that
// started it: who to bill, and for which model.
type pendingCharge struct {
	Metadata map[string]string `json:"metadata"`
	Model    string            `json:"model"`
	Provider string            `json:"provider"`
	UserID   string            `json:"user_id,omitempty"`
}

// runChargeStore remembers each run started through the gateway until its
// usage is charged.
//
// A run's usage is known only when it finishes, which a client may learn on
// a stream, or by polling any pod. Put records the run when it starts; Take
// hands it back exactly once, so the run is charged once, to the key that
// started it, whichever pod sees it finish.
type runChargeStore interface {
	Put(ctx context.Context, runID string, charge pendingCharge)
	Take(ctx context.Context, runID string) (pendingCharge, bool)
}

// newPendingCharge copies what a charge needs from the call that started the
// run. The caller's credential stays behind: the store may be shared Redis.
func newPendingCharge(rc *models.RequestContext, model string) pendingCharge {
	metadata := make(map[string]string, len(rc.Metadata))
	for key, value := range rc.Metadata {
		if key == "authorization" || (isCredentialShapedKey(key) && !strings.HasPrefix(key, "auth_")) {
			continue
		}
		metadata[key] = value
	}
	return pendingCharge{Metadata: metadata, Model: model, Provider: rc.Provider, UserID: rc.UserID}
}

// memoryRunCharges keeps pending charges in this process. Without Redis a
// run is charged only if the pod that started it sees it finish.
type memoryRunCharges struct {
	mu      sync.Mutex
	charges map[string]memoryCharge
}

type memoryCharge struct {
	charge  pendingCharge
	expires time.Time
}

func newMemoryRunCharges() *memoryRunCharges {
	return &memoryRunCharges{charges: make(map[string]memoryCharge)}
}

func (s *memoryRunCharges) Put(_ context.Context, runID string, charge pendingCharge) {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now()
	for id, c := range s.charges {
		if now.After(c.expires) {
			delete(s.charges, id)
		}
	}
	s.charges[runID] = memoryCharge{charge: charge, expires: now.Add(pendingChargeTTL)}
}

func (s *memoryRunCharges) Take(_ context.Context, runID string) (pendingCharge, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	c, ok := s.charges[runID]
	delete(s.charges, runID)
	if !ok || time.Now().After(c.expires) {
		return pendingCharge{}, false
	}
	return c.charge, true
}

// redisRunCharges shares pending charges between pods. Take reads and
// deletes in one script, so two pods that see the same run finish cannot both
// charge it.
type redisRunCharges struct {
	client *redisstate.Client
}

func runChargeKey(runID string) string { return "agentcc:run_charge:" + runID }

// takeChargeScript is GETDEL for any Redis 6: GETDEL itself needs 6.2.
//
// KEYS[1] = run charge key
var takeChargeScript = redis.NewScript(`
local charge = redis.call('GET', KEYS[1])
if charge then
    redis.call('DEL', KEYS[1])
end
return charge
`)

func (s *redisRunCharges) Put(ctx context.Context, runID string, charge pendingCharge) {
	data, err := json.Marshal(charge)
	if err != nil {
		return
	}
	if err := s.client.Do(func(rdb redis.UniversalClient) error {
		return rdb.Set(ctx, runChargeKey(runID), data, pendingChargeTTL).Err()
	}); err != nil {
		// Fail open: the run goes ahead, and is not charged when it finishes.
		slog.Warn("run charge not recorded; the run will not be charged", "run_id", runID, "error", err)
	}
}

func (s *redisRunCharges) Take(ctx context.Context, runID string) (pendingCharge, bool) {
	var data []byte
	err := s.client.Do(func(rdb redis.UniversalClient) error {
		charge, getErr := takeChargeScript.Run(ctx, rdb, []string{runChargeKey(runID)}).Text()
		if getErr == redis.Nil {
			return nil
		}
		data = []byte(charge)
		return getErr
	})
	if err != nil {
		slog.Warn("run charge not read; the run is not charged", "run_id", runID, "error", err)
		return pendingCharge{}, false
	}
	if len(data) == 0 {
		return pendingCharge{}, false
	}
	var charge pendingCharge
	if json.Unmarshal(data, &charge) != nil {
		return pendingCharge{}, false
	}
	return charge, true
}
