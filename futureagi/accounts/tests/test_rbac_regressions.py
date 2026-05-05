from tfc.constants.levels import Level


def test_owner_level_maps_to_workspace_admin_label():
    assert Level.to_ws_string(Level.OWNER) == "Workspace Admin"
    assert Level.to_ws_role(Level.OWNER) == "workspace_admin"
