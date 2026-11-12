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
	person := newEligibilityFixture()
	seedEligibility(t, pool, person)
	peppers := mustPeppers(t, goldenPepper)

	// Extend only this test's private schema with the sealed-secret and
	// coding-agent fields from accounts migrations 0026 and 0031.
	f.exec(`ALTER TABLE accounts_orgapikey
		ADD COLUMN secret_key_hash varchar(128) NOT NULL DEFAULT '',
		ADD COLUMN secret_key_hint varchar(32) NOT NULL DEFAULT '',
		ADD COLUMN secret_key_ciphertext text NOT NULL DEFAULT '',
		ADD COLUMN simulation_environment_id uuid NULL,
		ADD COLUMN minted_by_id uuid NULL REFERENCES accounts_user (id),
		ADD CONSTRAINT coding_agent_api_key_names_an_environment CHECK (
			type <> 'coding_agent' OR (
				simulation_environment_id IS NOT NULL AND user_id IS NULL AND minted_by_id IS NOT NULL
			)
		)`)
	// Both rows have valid sealed secrets, so refusal is about the principal,
	// not a missing pepper, invalid secret or ineligible personal control.
	f.exec(`UPDATE accounts_orgapikey SET secret_key = '', secret_key_hash = $2 WHERE api_key = $1`,
		person.api, peppers.HashSecret(person.secret))
	api, secret := uuid.NewString(), uuid.NewString()
	f.exec(`INSERT INTO accounts_orgapikey (
		id, name, api_key, secret_key, secret_key_hash, organization_id, type,
		enabled, user_id, workspace_id, created_at, updated_at, deleted,
		deleted_at, simulation_agent_id, simulation_environment_id, minted_by_id
	) VALUES (
		$1, 'coding-agent', $2, '', $3, $4, 'coding_agent', true, NULL, $5,
		now(), now(), false, NULL, NULL, $6, $7
	)`, uuid.NewString(), api, peppers.HashSecret(secret), person.org, person.ws, uuid.NewString(), person.user)

	for _, state := range []columnState{columnsPresent, columnsUnknown, columnsMissing} {
		r := routingResolver(pool, state)
		r.peppers = peppers
		if res, err := r.ValidateKey(ctx, api, secret); err != nil || res != nil {
			t.Fatalf("state %v: coding agent key = %+v, %v", state, res, err)
		}
		// The same resolver still accepts the minter's personal key.
		res, err := r.ValidateKey(ctx, person.api, person.secret)
		if err != nil || res == nil {
			t.Fatalf("state %v: user key = %v, %v", state, res, err)
		}
		if res.Principal != PrincipalPersonal || res.KeyType != "user" ||
			res.UserID != person.user || res.OrgID != person.org || res.WorkspaceID != person.ws {
			t.Fatalf("state %v: unexpected personal principal = %+v", state, res)
		}
	}
}
