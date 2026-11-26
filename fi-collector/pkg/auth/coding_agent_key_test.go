package auth

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestValidateKeyRefusesACodingAgentKey(t *testing.T) {
	pool := routingDatabase(t)
	f := routingFixture{t, pool}
	ctx := context.Background()
	org := uuid.NewString()
	ws := f.workspace(org, true, true, false)

	api, secret := f.key("coding_agent", org, &ws, nil)
	for _, state := range []columnState{columnsPresent, columnsUnknown, columnsMissing} {
		if res, err := routingResolver(pool, state).ValidateKey(ctx, api, secret); err != nil || res != nil {
			t.Fatalf("state %v: coding agent key = %+v, %v", state, res, err)
		}
	}
	// A person's key of the same organization still validates.
	api, secret = f.key("user", org, &ws, nil)
	if res, err := routingResolver(pool, columnsPresent).ValidateKey(ctx, api, secret); err != nil || res == nil {
		t.Fatalf("user key = %v, %v", res, err)
	}
}
