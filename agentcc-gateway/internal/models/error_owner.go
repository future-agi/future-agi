package models

// BindPublicationCredentialOwner records source-owned provenance on a call's
// error before it is shared. Only call sites that retained the dispatched
// provider instance or candidate credential may call this. Billing labels and
// provider response metadata are not ownership evidence. The first binding wins.
func (e *APIError) BindPublicationCredentialOwner(owner CredentialType) {
	if e == nil || e.publicationOwner != "" {
		return
	}
	if owner != CredentialBYOK {
		owner = CredentialManaged
	}
	e.publicationOwner = owner
}

// PublicationCredentialOwner is never part of an HTTP/native/SSE error body.
func (e *APIError) PublicationCredentialOwner() (CredentialType, bool) {
	if e == nil {
		return "", false
	}
	return e.publicationOwner, e.publicationOwner != ""
}
