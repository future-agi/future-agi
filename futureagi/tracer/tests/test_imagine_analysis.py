from unittest.mock import patch

import pytest

from tracer.models.imagine_analysis import ImagineAnalysis
from tracer.models.saved_view import SavedView


@pytest.fixture
def imagine_saved_view(db, project, workspace, user):
    return SavedView.objects.create(
        project=project,
        workspace=workspace,
        created_by=user,
        name="Imagine",
        tab_type="imagine",
        config={"widgets": []},
    )


@pytest.mark.django_db
class TestImagineAnalysisView:
    def test_trigger_infers_project_from_saved_view(
        self, auth_client, trace, imagine_saved_view
    ):
        with patch(
            "tfc.temporal.imagine.client.start_imagine_analysis",
            return_value="workflow-1",
        ) as start_workflow:
            response = auth_client.post(
                "/tracer/imagine-analysis/",
                {
                    "saved_view_id": str(imagine_saved_view.id),
                    "trace_id": str(trace.id),
                    "widgets": [
                        {
                            "widget_id": "analysis-widget",
                            "prompt": "Explain this trace.",
                        }
                    ],
                },
                format="json",
            )

        assert response.status_code == 200
        assert response.data["status"] is True
        assert response.data["result"]["analyses"][0]["status"] == "running"
        start_workflow.assert_called_once()

        analysis = ImagineAnalysis.objects.get(widget_id="analysis-widget")
        assert analysis.project_id == imagine_saved_view.project_id
        assert analysis.saved_view == imagine_saved_view

    def test_trigger_returns_failed_analysis_when_workflow_cannot_start(
        self, auth_client, trace, imagine_saved_view
    ):
        with patch(
            "tfc.temporal.imagine.client.start_imagine_analysis",
            side_effect=RuntimeError("temporal unavailable"),
        ):
            response = auth_client.post(
                "/tracer/imagine-analysis/",
                {
                    "saved_view_id": str(imagine_saved_view.id),
                    "trace_id": str(trace.id),
                    "project_id": str(imagine_saved_view.project_id),
                    "widgets": [
                        {
                            "widget_id": "analysis-widget",
                            "prompt": "Explain this trace.",
                        }
                    ],
                },
                format="json",
            )

        assert response.status_code == 200
        result = response.data["result"]["analyses"][0]
        assert result["status"] == "failed"
        assert "temporal unavailable" in result["error"]

        poll_response = auth_client.get(
            "/tracer/imagine-analysis/",
            {
                "saved_view_id": str(imagine_saved_view.id),
                "trace_id": str(trace.id),
            },
        )

        assert poll_response.status_code == 200
        analysis = poll_response.data["result"]["analyses"][0]
        assert analysis["status"] == "failed"
        assert "temporal unavailable" in analysis["error"]
