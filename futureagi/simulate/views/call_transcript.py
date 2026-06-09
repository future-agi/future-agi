import traceback

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.utils import get_request_organization
from simulate.models import CallExecution, CallTranscript
from simulate.serializers.test_execution import CallTranscriptSerializer
from simulate.services.branch_deviation_analyzer import BranchDeviationAnalyzer
from simulate.utils.stored_transcript_roles import get_displayable_transcript_roles


class CallTranscriptView(APIView):
    """
    API View to get call transcripts
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, call_execution_id, *args, **kwargs):
        """Get transcripts for a specific call execution"""
        try:
            # Get the organization of the logged-in user
            user_organization = get_request_organization(request)

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the call execution
            call_execution = get_object_or_404(
                CallExecution,
                id=call_execution_id,
                test_execution__organization=user_organization,
            )

            # Get all transcripts for this call
            transcripts = CallTranscript.objects.filter(
                call_execution=call_execution,
                speaker_role__in=get_displayable_transcript_roles(),
            ).order_by("start_time_ms")

            # Serialize the transcripts
            serializer = CallTranscriptSerializer(transcripts, many=True)

            return Response(
                {
                    "call_execution_id": str(call_execution.id),
                    "phone_number": call_execution.phone_number,
                    "status": call_execution.status,
                    "transcripts": serializer.data,
                    "total_transcripts": len(transcripts),
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            return Response(
                {"error": f"Failed to get call transcripts: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TestExecutionTranscriptsView(APIView):
    """
    API View to get all transcripts for a test execution
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, test_execution_id, *args, **kwargs):
        """Get all transcripts for a test execution"""
        try:
            # Get the organization of the logged-in user
            user_organization = get_request_organization(request)

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get all call executions for this test execution with prefetched transcripts
            call_executions = CallExecution.objects.filter(
                test_execution_id=test_execution_id,
                test_execution__organization=user_organization,
            ).prefetch_related("transcripts", "scenario")

            all_transcripts = []
            for call_execution in call_executions:
                # Filter transcripts by speaker role and order by start_time_ms
                # Since we prefetched, this filtering happens in Python
                displayable = get_displayable_transcript_roles()
                transcripts = [
                    t
                    for t in call_execution.transcripts.all()
                    if t.speaker_role in displayable
                ]
                transcripts.sort(key=lambda x: x.start_time_ms)

                call_data = {
                    "call_execution_id": str(call_execution.id),
                    "phone_number": call_execution.phone_number,
                    "status": call_execution.status,
                    "scenario_name": (
                        call_execution.scenario.name
                        if call_execution.scenario
                        else "Unknown"
                    ),
                    "transcripts": CallTranscriptSerializer(
                        transcripts, many=True
                    ).data,
                    "total_transcripts": len(transcripts),
                }
                all_transcripts.append(call_data)

            return Response(
                {
                    "test_execution_id": test_execution_id,
                    "calls": all_transcripts,
                    "total_calls": len(call_executions),
                    "total_transcripts": sum(
                        len(call["transcripts"]) for call in all_transcripts
                    ),
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            return Response(
                {"error": f"Failed to get test execution transcripts: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CallBranchAnalysisView(APIView):
    """
    API View to analyze call execution against graph branches
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, call_execution_id, *args, **kwargs):
        """Analyze a call execution against graph branches and identify deviations"""
        try:
            # Get the organization of the logged-in user
            user_organization = get_request_organization(request)

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            print(f"Getting call execution: {call_execution_id}")

            # Get the call execution
            call_execution = get_object_or_404(
                CallExecution,
                id=call_execution_id,
                # test_execution__organization=user_organization
            )

            # transcripts = CallTranscript.objects.filter(
            #     call_execution=call_execution,
            #     speaker_role__in=[CallTranscript.SpeakerRole.USER, CallTranscript.SpeakerRole.ASSISTANT]
            # ).order_by('start_time_ms')
            # transcript_data = []
            # for transcript in transcripts:
            #     print(f"Transcript: {transcript.speaker_role} - {transcript.content}")
            #     transcript_data.append({
            #         'speaker_role': transcript.speaker_role,
            #         'content': transcript.content,
            #         # 'start_time_ms': transcript.start_time_ms,
            #         # 'end_time_ms': transcript.end_time_ms
            #     })

            if isinstance(
                call_execution.analysis_data, dict
            ) and call_execution.analysis_data.get("branch_analysis", {}).get(
                "expected_path"
            ):
                analysis_data = call_execution.analysis_data.get("branch_analysis", {})
            else:

                # Perform branch analysis
                analyzer = BranchDeviationAnalyzer()
                analysis = analyzer.analyze_call_execution_branch(call_execution)
                analysis_data = {
                    "new_nodes": analysis.new_nodes,
                    "new_edges": analysis.new_edges,
                    "current_path": analysis.current_path,
                    "expected_path": analysis.expected_path,
                    "analysis_summary": analysis.analysis_summary,
                }
                if not call_execution.analysis_data:
                    call_execution.analysis_data = {}

                call_execution.analysis_data["branch_analysis"] = analysis_data
                call_execution.save(update_fields=["analysis_data"])

            # Format response
            response_data = {
                "call_execution_id": str(call_execution.id),
                "scenario_id": (
                    str(call_execution.scenario.id) if call_execution.scenario else None
                ),
                "scenario_name": (
                    call_execution.scenario.name
                    if call_execution.scenario
                    else "Unknown"
                ),
                "analysis": analysis_data,
                "analyzed_at": timezone.now().isoformat(),
                # 'transcripts': transcript_data
            }

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            traceback.print_exc()
            return Response(
                {"error": f"Failed to analyze call execution: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def post(self, request, call_execution_id, *args, **kwargs):
        """Create deviation nodes and edges for a call execution"""
        try:
            # Get the organization of the logged-in user
            user_organization = get_request_organization(request)

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the call execution
            call_execution = get_object_or_404(
                CallExecution,
                id=call_execution_id,
                test_execution__organization=user_organization,
            )

            # Perform branch analysis
            analyzer = BranchDeviationAnalyzer()
            analysis = analyzer.analyze_call_execution_branch(call_execution)

            # Get scenario graph
            scenario_graph = analyzer._get_scenario_graph(call_execution)
            if not scenario_graph:
                return Response(
                    {"error": "No scenario graph found for this call execution"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Create deviation nodes and edges
            deviation_data = analyzer.create_deviation_nodes_and_edges(
                analysis, scenario_graph
            )

            response_data = {
                "call_execution_id": str(call_execution.id),
                "scenario_graph_id": str(scenario_graph.id),
                "deviation_data": deviation_data,
                "message": f"Created {deviation_data.get('deviation_count', 0)} deviation nodes and edges",
            }

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"error": f"Failed to create deviation nodes: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
