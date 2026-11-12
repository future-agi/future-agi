package redisstate

import (
	"context"
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/redis/go-redis/v9"
)

// ReadOwnedJournal reads the ledger's immutable financial identities, not the
// compacted request log. Final settlement still validates against the current
// journal atomically; this read never authorizes releasing a hold by itself.
func (s *ManagedCreditStore) ReadOwnedJournal(ctx context.Context, org, request string) (billing.OwnedRequest, error) {
	if validOrg(org) != nil || request == "" {
		return billing.OwnedRequest{}, billing.ErrOwnership
	}
	var raw []byte
	err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		raw, err = rdb.Get(ctx, s.ownedKeys(org, request)[5]).Bytes()
		return err
	})
	if err != nil {
		return billing.OwnedRequest{}, err
	}
	var owned billing.OwnedRequest
	err = json.Unmarshal(raw, &owned)
	return owned, err
}
