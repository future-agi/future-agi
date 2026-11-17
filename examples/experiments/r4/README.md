# Experiments examples

| Folder           | What                                                                                                                                       |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `endpoint-http/` | Endpoint · a stdlib HTTP server that answers the drawer's body template, echoes each try's `traceparent` and can verify request signatures |

The generated runner scripts that used to live here, and the stand-ins their dry runs used, were removed: they
called commands and routes that do not exist.

`bash tools/verify_dry_runs.sh` (from the repository root) runs every dry run these READMEs print, exactly as written,
and checks what each one claims (one reply per sample row, row ids in order, the body template).
