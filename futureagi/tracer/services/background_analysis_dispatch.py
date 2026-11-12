"""Persist Falcon generations at its task boundary, before queue dispatch.

Keeps the existing activity identity/registry and public call signature. Both
Temporal dispatch helpers are wrapped; no caller or transport module changes.
"""

from tracer.queries import background_analysis_state


def durable_background_dispatch(task):
    dispatch = task.apply_async

    def apply_async(args=None, kwargs=None, **options):
        args = tuple(args or ())
        kwargs = dict(kwargs or {})
        project_id = args[0] if args else kwargs["project_id"]
        trace_ids = args[1] if len(args) > 1 else kwargs["trace_ids"]
        generations = {}
        try:
            for trace_id in trace_ids:
                # A duplicate ID in one batch must not discard its first claim.
                if str(trace_id) in generations:
                    continue
                generation = background_analysis_state.begin(project_id, trace_id)
                if generation is not None:
                    generations[str(trace_id)] = generation
            kwargs["generations"] = generations
            return dispatch(args=args, kwargs=kwargs, **options)
        except Exception:
            for trace_id, generation in generations.items():
                background_analysis_state.abort(
                    project_id,
                    trace_id,
                    generation,
                    "analysis_dispatch_failed",
                    expected_status="retry_pending",
                )
            raise

    def delay(*args, **kwargs):
        return apply_async(args=args, kwargs=kwargs)

    task.apply_async = apply_async
    task.delay = delay
    return task
