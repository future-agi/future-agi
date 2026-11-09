package auth

import (
	"context"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

// Look up the public id only; verify its secret in Go. JSON column access
// keeps this query compatible with rolling out before the retirement column.
const validateKeyQuery = `
 SELECT k.organization_id, k.workspace_id, k.user_id, k.type,
        k.secret_key, COALESCE(to_jsonb(k)->>'secret_key_hash', '')
 FROM accounts_orgapikey k
 WHERE k.api_key = $1 AND k.enabled = true AND k.deleted = false
   AND k.type = 'user' AND k.user_id IS NOT NULL
   AND COALESCE(to_jsonb(k)->>'retired_at', '') = ''
   AND COALESCE(to_jsonb(k)->>'retired_reason', '') = ''
   AND EXISTS (SELECT 1 FROM accounts_user u WHERE u.id = k.user_id AND u.is_active)
   AND EXISTS (SELECT 1 FROM accounts_organization_membership m
       WHERE m.user_id = k.user_id AND m.organization_id = k.organization_id AND m.is_active AND NOT m.deleted)
   AND (k.workspace_id IS NULL OR EXISTS (
       SELECT 1 FROM accounts_workspacemembership m JOIN accounts_workspace w ON w.id = m.workspace_id
       WHERE m.user_id = k.user_id AND m.workspace_id = k.workspace_id AND m.is_active AND NOT m.deleted
         AND w.organization_id = k.organization_id AND w.is_active AND NOT w.deleted))
 LIMIT 1`

// ValidateKey returns nil, nil for a key that is absent or ineligible.
func (r *PGResolver) ValidateKey(ctx context.Context, apiKey, secretKey string) (*ResolveResult, error) {
	if r.peppers == nil {
		return nil, ErrPepperMissing
	}
	var orgID, keyType, legacySecret, storedHash string
	var workspaceID, userID *string
	// Eligibility is authoritative only on the primary. FI_PG_READ may lag
	// a committed revocation by arbitrarily longer than our cache lifetime.
	err := r.write.QueryRow(ctx, validateKeyQuery, apiKey).Scan(
		&orgID, &workspaceID, &userID, &keyType, &legacySecret, &storedHash)
	if err != nil {
		if err == pgx.ErrNoRows {
			r.peppers.VerifySecret("", "", secretKey)
			return nil, nil
		}
		return nil, fmt.Errorf("validate key: %w", err)
	}
	if !r.peppers.VerifySecret(storedHash, legacySecret, secretKey) {
		return nil, nil
	}
	effectiveWorkspace := ""
	if workspaceID != nil {
		effectiveWorkspace = *workspaceID
	}
	if effectiveWorkspace == "" {
		effectiveWorkspace, err = r.resolveDefaultWorkspace(ctx, orgID)
		if err != nil {
			return nil, fmt.Errorf("validate key workspace: %w", err)
		}
	}
	if userID == nil {
		return nil, nil
	}
	allowed, err := r.personalWriteAllowed(ctx, *userID, orgID, effectiveWorkspace)
	if err != nil {
		return nil, err
	}
	if !allowed {
		return nil, nil
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	res := &ResolveResult{
		OrgID: orgID, WorkspaceID: effectiveWorkspace,
		KeyType: keyType, Principal: PrincipalPersonal, Projects: make(map[string]string),
		APIKeyDigest: APIKeyDigest(apiKey),
	}
	if userID != nil {
		res.UserID = *userID
	}
	return res, nil
}

// retiredKeyQuery reads a key's retirement whatever its other state, as Django's
// authenticate_api_key does (futureagi/accounts/api_key_policy.py:104-110). JSON
// access keeps it valid before the retirement and hash columns exist.
const retiredKeyQuery = `
 SELECT k.type, COALESCE(to_jsonb(k)->>'retired_at', ''),
        COALESCE(to_jsonb(k)->>'retired_reason', ''),
        COALESCE(to_jsonb(k)->>'secret_key_hash', '')
 FROM accounts_orgapikey k
 WHERE k.api_key = $1
 LIMIT 1`

// LookupRetirement only words a refusal (retiredPair); it never admits a key.
// It reads the primary, as Django's @primary_required check does.
func (r *PGResolver) LookupRetirement(ctx context.Context, apiKey string) (keyType, retiredAt, retiredReason, secretKeyHash string, err error) {
	if r == nil || r.write == nil {
		return "", "", "", "", errors.New("retirement lookup requires the primary pool")
	}
	err = r.write.QueryRow(ctx, retiredKeyQuery, apiKey).Scan(&keyType, &retiredAt, &retiredReason, &secretKeyHash)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", "", "", "", nil
	}
	if err != nil {
		return "", "", "", "", fmt.Errorf("retired key lookup: %w", err)
	}
	return keyType, retiredAt, retiredReason, secretKeyHash, nil
}

func isUndefinedColumn(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == "42703"
}
