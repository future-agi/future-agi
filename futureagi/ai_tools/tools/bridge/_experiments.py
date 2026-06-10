"""DRF bridge registrations for experiments (Phase 2A Packet B).

Pre-provisioned by Packet A so Packet B can add `expose_to_mcp(...)(View)`
declarations without touching shared files. Bridge modules register
imperatively, so this module can ADD actions to ViewSets already registered
elsewhere (the registry only collides on same-name/different-class).
"""
