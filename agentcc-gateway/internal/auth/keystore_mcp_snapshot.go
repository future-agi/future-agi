package auth

// MCPReadIdentity snapshots an active key's org and MCP ACL under the store
// lock. An administrative preview looks up an ID, never a secret, and does
// not authenticate or count the key as used. Returned slices are independent.
func (ks *KeyStore) MCPReadIdentity(id string) (Identity, []string, []string, bool) {
	ks.mu.RLock()
	defer ks.mu.RUnlock()
	key := ks.byID[id]
	if key == nil || !key.IsActive() {
		return Identity{}, nil, nil, false
	}
	allowed, denied := key.MCPToolLists()
	return Identity{KeyID: key.ID, OrgID: key.Metadata["org_id"]}, append([]string(nil), allowed...), append([]string(nil), denied...), true
}
