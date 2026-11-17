"""Private botocore wire admission; never patch an SDK class or shared pool."""


def guard_bedrock(client):
    from botocore.awsrequest import AWSHTTPConnection, AWSHTTPSConnection
    from botocore.httpsession import URLLib3Session

    from tracer.services.analysis_result_completion import IncompleteAnalysis
    from tracer.services.background_transport import _guarded_pool_classes, before_send

    session = client._endpoint.http_session
    # Only the just-created botocore client may receive these private pool types.
    # An opaque/custom transport cannot establish the actual-wire guarantee.
    if not isinstance(session, URLLib3Session):
        raise IncompleteAnalysis()
    pools = _guarded_pool_classes()

    class HTTP(pools["http"].ConnectionCls, AWSHTTPConnection):
        pass

    class HTTPS(pools["https"].ConnectionCls, AWSHTTPSConnection):
        pass

    class HTTPPool(pools["http"]):
        ConnectionCls = HTTP

    class HTTPSPool(pools["https"]):
        ConnectionCls = HTTPS

    private = {"http": HTTPPool, "https": HTTPSPool}
    session._pool_classes_by_scheme = private
    session._manager.pool_classes_by_scheme = private
    # No request has run yet, but bind any privately configured proxy managers too.
    for manager in session._proxy_managers.values():
        manager.pool_classes_by_scheme = private

    def admit(**kwargs):
        # Botocore emits this after request signing and credential refresh.
        before_send()

    client.meta.events.register("before-send.bedrock-runtime.InvokeModel", admit)
