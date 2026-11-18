package auth

import (
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
)

// NewSimulationCostFixture shares the routing DB with the external test that
// joins auth, pricing (which itself imports auth), and the row converter.
func NewSimulationCostFixture(t *testing.T) (*pgxpool.Pool, string, string) {
	t.Helper()
	pool := routingDatabase(t)
	f := routingFixture{t, pool}
	org := uuid.NewString()
	ws := f.workspace(org, true, true, false)
	agent := f.agent(org, &ws, false)
	key, secret := f.key(keyTypeIngest, org, &ws, &agent)
	f.exec(`CREATE TABLE model_hub_customaimodel (organization_id uuid, user_model_id text,
		input_token_cost float8, output_token_cost float8, deleted boolean)`)
	f.exec(`INSERT INTO model_hub_customaimodel VALUES ($1, 'custom-chat', 2.50, 0, false)`, org)
	return pool, key, secret
}
