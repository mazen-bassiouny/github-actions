"""
Classes that provide the actual endpoints of the API are defined here.
"""
import json
import os
import typing as t

import falcon
from falcon import Request, Response

import te_commons.cookies as cookies
from te_commons.consent import ConsentMode

from multi_producer import MultiProducer
from producer import TracktorProducer
from ipmagic import obfuscated_ip_from_request
from consent import TracktorConsent


class MessageApiCall(object):
    """
    Base class for API calls to be defined for falcon's endpoint. Note
    that in order to instantiate any MessageApiCall (or from a derived
    class), the static method `init` has to be called first.
    """
    __producer: MultiProducer = None
    __uri_stream_parameter = None
    __cookie_name = None
    __cookie_domain = None

    # Retargeting config
    __rt_cookie_name = None
    __rt_query_parameter = None
    __rt_segment_limit = None
    __rt_prefix_truncate_list = None

    # Secrets
    __ip_token: bytes = None

    __is_initialized = False

    # Constants
    GDPR_CONSENT = "gdpr_consent"
    GDPR_MODE = "gdpr_mode_"
    HAS_CONSENT = "has_consent"
    OPT_OUT_VAL = "OPT-OUT"

    def __init__(self):
        if not MessageApiCall.__is_initialized:
            raise RuntimeError(
                f"You need to initialize shared settings via the "
                f"MessageApiCall.init method before you can create an "
                f"instance of {self.__class__.__name__}."
            )

    @classmethod
    def init(cls,
             cookie_name: str,
             retargeting_cookie_name: str,
             retargeting_query_param: str,
             retargeting_segment_limit: int,
             retargeting_prefix_truncate_list: str,
             ip_token: str,
             cookie_domain: str,
             producer: MultiProducer):
        """
        Initialize shared configurations, i.e., things that are also
        important in derived instances.

        :param cookie_name: Name of the cookie where the UUID is stored.
        :param retargeting_cookie_name: Name of the cookie where
            re-targeting segments are stored.
        :param retargeting_query_param: Name of the query parameter that
            contains the retargeting segment.
        :param retargeting_segment_limit: Maximal number of segments to be
            stored for retargeting.
        :param retargeting_prefix_truncate_list: List of segment prefixes ought
            to be removed.
        :param ip_token: Token/Secret prepended to IP address befor hashing
            it.
        :param cookie_domain: Domain attribute for setting cookies.
        :param producer: Producer used to write messages to a stream.
        """
        # Initialize the producer
        cls.__producer = producer

        # Initialize the cookie stuff
        cls.__cookie_name = cookie_name
        cls.__cookie_domain = cookie_domain

        # Initialize retargeting stuff
        cls.__rt_query_parameter = retargeting_query_param
        cls.__rt_cookie_name = retargeting_cookie_name
        cls.__rt_segment_limit = retargeting_segment_limit
        cls.__rt_prefix_truncate_list = retargeting_prefix_truncate_list

        # Initialize secrets
        cls.__ip_token = ip_token.encode("utf-8")

        # Make it ready
        MessageApiCall.__is_initialized = True

    @property
    def producer(self) -> t.Union[TracktorProducer, MultiProducer]:
        """
        Get an instance of a `TracktorProducer`.
        """
        return self.__class__.__producer

    @classmethod
    def flush_messages(cls):
        """
        Write all remaining messages (synchronously) to EventHub.
        Note that this function is blocking.
        """
        cls.__producer.flush_messages(close_pool=True)

    @property
    def cookie_name(self) -> str:
        """
        Get the UUID cookie name.
        """
        return MessageApiCall.__cookie_name

    @property
    def retargeting_cookie_name(self) -> str:
        """Get the retargeting cookie name."""
        return MessageApiCall.__rt_cookie_name

    @property
    def retargeting_query_parameter(self) -> str:
        """Get the retargeting cookie name."""
        return MessageApiCall.__rt_query_parameter

    @property
    def retargeting_segment_limit(self) -> int:
        """Get the retargeting cookie name."""
        return MessageApiCall.__rt_segment_limit

    @property
    def cookie_domain(self) -> str:
        """
        Get the domain to which the cookie is associated.
        from the one
        """
        return MessageApiCall.__cookie_domain

    @classmethod
    def consent(cls, req: Request) -> TracktorConsent:
        """
        Creates a `TracktorConsent` object from the `Request`.
        """
        is_opt_out = req.params.get("opt") == "out"
        consent_mode_param = req.params.get(cls.GDPR_MODE)
        has_consent_param = req.params.get(cls.HAS_CONSENT)
        tcf_string = req.params.get(cls.GDPR_CONSENT)

        if consent_mode_param == "0" or has_consent_param == "1":
            consent_mode = ConsentMode.DEACTIVATED
        elif consent_mode_param == "1":
            consent_mode = ConsentMode.LOOSE
        elif consent_mode_param == "2":
            consent_mode = ConsentMode.STRICT
        else:
            consent_mode = ConsentMode.LOOSE

        return TracktorConsent(
            consent_mode=consent_mode,
            opt_out=is_opt_out,
            forbid_cookies=is_opt_out,
            tcf_string=tcf_string,
            has_consent=has_consent_param
        )

    def assign_uuid(self,
                    req: Request,
                    resp: Response,
                    consent: TracktorConsent = None) -> str:
        """
        Get the UUID from the current `Request`. If no UUID cookie is
        present, the cookie will be created in the `Response`
        (unless in opt-out cases).
        """
        if consent is None:
            consent = self.consent(req)

        if not consent.user_identifier:
            user_uuid = self.OPT_OUT_VAL
        elif self.cookie_name in req.params:
            user_uuid = req.params[self.cookie_name]
        elif consent.cookies:
            user_uuid = cookies.set_uuid_cookie(
                cookie_name=self.cookie_name,
                req=req,
                resp=resp,
                domain=self.cookie_domain
            )
        else:
            user_uuid = self.OPT_OUT_VAL

        return user_uuid

    def set_retargeting_segment(self,
                                req: Request,
                                resp: Response,
                                consent: TracktorConsent = None):
        """
        Stores segment name in a list in a cookie, given that the
        corresponding `retargeting_query_parameter` is provided and no
        opt-out is going on.

        When the list's size exceeds the `retargeting_segment_limit`,
        the first element is removed. This does only happen, when a new
        segment occurs and hence successive calls with the same segment
        don't do harm.
        """
        if consent is None:
            consent = self.consent(req)

        # Abort when retargeting is not mentioned or opt-out
        if (
            self.retargeting_query_parameter not in req.params
            or not consent.retargeting
        ):
            return

        # Fetch the segment id
        segment = self._clean_segment_name(
            req.params[self.retargeting_query_parameter]
        )

        # Fetch the list of segments
        if self.retargeting_cookie_name in req.cookies:
            seg_list = cookies.decode_dict(
                req.cookies.get(self.retargeting_cookie_name)
            )
            # Check if something went wrong, repair if necessary
            if not isinstance(seg_list, list):
                seg_list = list()
        else:
            seg_list = list()

        if segment not in seg_list:
            seg_list.append(segment)

            while len(seg_list) > self.retargeting_segment_limit:
                seg_list.pop(0)

            cookies.set_cookie(
                resp=resp,
                name=self.retargeting_cookie_name,
                value=cookies.encode_dict(seg_list),
                domain=self.cookie_domain
            )

    @classmethod
    def _clean_segment_name(cls, segment: str):
        """
        Cleans the `segment`'s name.
        """
        # Turn to lowercase
        segment = segment.lower()

        # Remove prefixes from blacklist
        for prefix in cls.__rt_prefix_truncate_list:
            segment = segment[len(prefix) if segment.startswith(prefix) else 0:]

        return segment

    def req_to_message(self,
                       req: Request,
                       resp: Response,
                       consent: TracktorConsent = None) -> str:
        """
        Writes a message to the `TracktorProducer` that is derived from
        the Request object. The `assign_uuid` method will be invoked in
        the process.

        :param req: Falcon's Request object.
        :param resp: Falcon's Response object.
        :param consent: A `TracktorConsent`. If none is given, the object will
            be created from the request.
        :return: The UUID associated with the request
            (either read from cookie or freshly generated).
        """
        if consent is None:
            consent = self.consent(req)

        # Directly cancel when not even simple logging is allowed
        if not consent.track_anonymous:
            return ""

        # Prepare cookie information
        user_uuid = self.assign_uuid(req, resp, consent=consent)
        if consent.user_identifier and consent.cookies:
            legacy_cookie = req.cookies.get("technical-service")
        else:
            legacy_cookie = self.OPT_OUT_VAL

        if consent.cookies:
            cookies_list = list(req.cookies.keys())
        else:
            cookies_list = []

        # Check if there is a non-empty body
        if req.content_length:
            json_body = req.bounded_stream.read()
            try:
                body = json.loads(json_body)
            except json.JSONDecodeError:
                body = json_body.decode()
            except UnicodeDecodeError as e:
                body = {
                    "py_exception": type(e).__name__,
                    "py_exception_msg": str(e)
                }
        else:
            body = None

        # Fetch IP addresses
        if consent.track_ip_hashed:
            collision_hashed_ip, token_hashed_ip, geoip = obfuscated_ip_from_request(
                req=req, token=self.__ip_token
            )
            if not consent.track_ip_clear:
                token_hashed_ip = self.OPT_OUT_VAL
                geoip = self.OPT_OUT_VAL
        else:
            collision_hashed_ip, token_hashed_ip, geoip = (self.OPT_OUT_VAL,) * 3

        # fetch tracktor url
        tracktor_url = str(req.url).split("?")[0]

        # Define the message as a dictionary
        msg = {
            "user_uuid": user_uuid,
            "cookies": cookies_list,
            "legacy_cookie": legacy_cookie,
            "query_parameter": req.params,
            "user_agent": req.user_agent,
            "tracktor_url": tracktor_url,
            "referrer": req.referer,
            "ip_hashed": collision_hashed_ip,
            "ip_unique_hash": token_hashed_ip,  # May only be used for Bertelsmann-ID or AdAlliance-ID project
            "ip_geo": geoip,
            "custom_data": body,
            "tcf": consent.summary
        }

        # Push the message to the producer
        self.producer.write_dict(msg, req)
        return user_uuid


class PixelCall(MessageApiCall):
    """
    Used to provide an GET-endpoint that tracks client data, sets the
    UUID and returns a GIF pixel (to be precise, it will be 2x2 pixels).
    """
    def __init__(self):
        super().__init__()
        pixel_path = os.path.join(os.path.dirname(__file__), "img/pixel.gif")
        self.pixel_size = os.path.getsize(pixel_path)
        with open(pixel_path, "rb") as f:
            self.pixel_bytes = f.read()

    def on_get(self, req: Request, resp: Response):
        consent = MessageApiCall.consent(req)

        self.req_to_message(req, resp, consent=consent)

        self.set_retargeting_segment(req, resp, consent=consent)

        resp.content_type = falcon.MEDIA_GIF
        resp.content_length = self.pixel_size
        resp.data = self.pixel_bytes
        resp.status = falcon.HTTP_200


class TracktorCall(MessageApiCall):
    """
    Used to provide an POST-endpoint. In the POST-body, arbitrary data
    can be provided in JSON format. The response body will contain the
    UUID.
    """
    def __init__(self):
        super().__init__()

    def on_post(self, req: Request, resp: Response):
        user_uuid = self.req_to_message(req, resp)
        resp.body = user_uuid
        resp.status = falcon.HTTP_200


class GetUuidCall(MessageApiCall):
    """
    Endpoint that will display the UUID in the JavaScript format::

        var varname = {"keyname": "c2762114-66f2-4513-b377-82a7f646057f"};

    The name of the variable (`varname` in the example) and key
    (`keyname` in the example) can be controlled via the `vn` and `kn`
    query parameter, respectively.

    Note that no tracking will take place unless the `tr` query
    parameter is set to something truthy (like "1").
    """
    def __init__(self):
        super().__init__()

    def on_get(self, req: Request, resp: Response):
        if req.params.get("tr"):
            user_uuid = self.req_to_message(req, resp)
        else:
            user_uuid = self.assign_uuid(req, resp)

        var_name = req.params.get("vn", "thcObj")
        key_name = req.params.get("kn", "technic")
        resp.body = f"""var {var_name} = {{"{key_name}": "{user_uuid}"}};"""
        resp.content_type = falcon.MEDIA_JS
        resp.status = falcon.HTTP_200
