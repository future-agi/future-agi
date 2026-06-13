"""
Class:              Curl
Description:        For server-side API call
"""

import requests

from tfc.utils.http_timeouts import DEFAULT_HTTP_TIMEOUT


class Curl:
    """
    Function:       __init__
    Input:          headers: Dictionary (Optional)
    """

    headers = {}

    def __init__(self, headers=None):
        if headers:
            self.headers.update(headers)

    """
        Function:       get
        Input:          api: String (required), params: Dict (Optional), headers: Dictionary (Optional)
        Output:         response: JSON, status_code, headers
    """

    def get(
        self,
        api,
        params=None,
        headers=None,
        cookies=None,
        auth=None,
        timeout=DEFAULT_HTTP_TIMEOUT,
    ):
        if not api:
            return False
        if headers:
            self.headers.update(headers)
        response = requests.get(
            api,
            json=params,
            headers=self.headers,
            cookies=cookies,
            auth=auth,
            timeout=timeout,
        )
        return response.json(), response.status_code, response.request.headers

    """
        Function:       post
        Input:          api: String (required), params: Dict (Optional), headers: Dictionary (Optional)
        Output:         response: JSON, status_code, headers
    """

    def post(
        self,
        api,
        params=None,
        headers=None,
        cookies=None,
        auth=None,
        timeout=DEFAULT_HTTP_TIMEOUT,
        verify=True,
    ):
        if not api:
            return False
        if headers:
            self.headers.update(headers)
        response = requests.post(
            api,
            json=params,
            headers=self.headers,
            cookies=cookies,
            auth=auth,
            timeout=timeout,
            verify=verify,
        )
        response.raise_for_status()
        return response.json(), response.status_code, response.request.headers

    """
        Function:       put
        Input:          api: String (required), params: Dict (Optional), headers: Dictionary (Optional)
        Output:         response: JSON, status_code, headers
    """

    def put(
        self,
        api,
        params=None,
        headers=None,
        cookies=None,
        auth=None,
        timeout=DEFAULT_HTTP_TIMEOUT,
    ):
        if not api:
            return False
        if headers:
            self.headers.update(headers)
        response = requests.put(
            api,
            json=params,
            headers=self.headers,
            cookies=cookies,
            auth=auth,
            timeout=timeout,
        )
        return response.json(), response.status_code, response.request.headers

    """
        Function:       delete
        Input:          api: String (required), params: Dict (Optional), headers: Dictionary (Optional)
        Output:         response: JSON, status_code, headers
    """

    def delete(
        self,
        api,
        params=None,
        headers=None,
        cookies=None,
        auth=None,
        timeout=DEFAULT_HTTP_TIMEOUT,
    ):
        if not api:
            return False
        if headers:
            self.headers.update(headers)
        response = requests.delete(
            api,
            json=params,
            headers=self.headers,
            cookies=cookies,
            auth=auth,
            timeout=timeout,
        )
        return response.json(), response.status_code, response.request.headers

    """
       Function:       update_headers
       Input:          headers: Dict (Optional)
       Output:         response: headers
   """

    def update_headers(self, headers=None):
        if headers:
            self.headers.update(headers)
            return self.headers
        else:
            return self.headers
