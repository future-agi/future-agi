"""The Users setup snippet must not register a project the user never named.

The top-level Users page (/dashboard/users) has no project in its URL, so the
snippet used to say project_name="New Project" and copying it as-is sent traces
to an oddly named project. Without a project it now shows a neutral placeholder
with a note on the same line telling the user to replace it (OBS-SW-109).
"""

import pytest
from rest_framework import status

USER_CODE_EXAMPLE_PATH = "/tracer/users/get_code_example/"
REPLACE_NOTE = "# Replace with your project name"


@pytest.mark.django_db
def test_user_code_example_without_project_shows_a_placeholder_to_replace(
    auth_client,
):
    response = auth_client.get(USER_CODE_EXAMPLE_PATH)

    assert response.status_code == status.HTTP_200_OK
    code_example = response.json()["result"]
    assert "New Project" not in code_example
    assert f'    project_name="my-project",  {REPLACE_NOTE}\n' in code_example


@pytest.mark.django_db
def test_user_code_example_for_a_project_uses_its_name_without_the_note(
    auth_client, observe_project
):
    response = auth_client.get(
        USER_CODE_EXAMPLE_PATH,
        data={"project_id": str(observe_project.id)},
    )

    assert response.status_code == status.HTTP_200_OK
    code_example = response.json()["result"]
    assert f'    project_name="{observe_project.name}",\n' in code_example
    assert REPLACE_NOTE not in code_example
