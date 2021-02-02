import json
import sys

# 3rd party modules
import falcon

# own installed module
from te_commons.api_helpers import HealthCheck, CorsMiddleware
from te_commons.logger import log, init_logger
from te_commons.configs import Config

# own modules
from api_calls import MessageApiCall, PixelCall, TracktorCall, GetUuidCall
from ascii import headline
from multi_producer import MultiProducer


def main(args):
    # Init configs
    config = Config(
        description="The infamous tracker of the Metatron, called Tracktor."
    )
    config.add_standard_arguments(__file__)
    arguments = config.parse_arguments(args)
    configs = config.parse_config(arguments["config"])

    # Init logging
    init_logger(
        name="tracktor",
        level=arguments["log_level"]
    )
    log.info("Arguments: {}".format(json.dumps(arguments)))
    log.debug("Config: {}".format(json.dumps(configs)))

    api_health_check = HealthCheck()
    middleware = [CorsMiddleware()]

    # Create falcon app
    _api = falcon.API(middleware=middleware)
    _api.add_route("/health", api_health_check)

    # create MultiProducer
    producer = MultiProducer(
        config=configs,
        uri_to_producer_section_name="uri_to_producer",
        producer_config_startswith="producer_",
        uri_parameter_switch="eh",
        buffer_size=int(configs["shared_producer_settings"]["buffer_size"]),
        wait_after_error=float(configs["shared_producer_settings"]["wait_after_error"]),
        thread_pool_size=int(configs["shared_producer_settings"]["thread_pool_size"])
    )

    # initialize API
    MessageApiCall.init(
        cookie_name=configs["cookies"]["cookie_name"],
        retargeting_cookie_name=configs["retargeting"]["cookie_name"],
        retargeting_query_param=configs["retargeting"]["query_parameter"],
        retargeting_segment_limit=int(configs["retargeting"]["segment_limit"]),
        retargeting_prefix_truncate_list=json.loads(configs["retargeting"]["prefix_truncate_list"]),
        ip_token=configs["secrets"]["ip_token"],
        cookie_domain=configs["cookies"]["cookie_domain"],
        producer=producer
    )

    # Create objects for the main endpoints
    call_pixel = PixelCall()
    call_tracktor = TracktorCall()
    call_get_uuid = GetUuidCall()

    _api.add_route("/pixel.gif", call_pixel)
    _api.add_route("/tracktor", call_tracktor)
    _api.add_route("/get", call_get_uuid)

    return _api


if "uwsgi_file" in __name__:
    print(headline)
    api = application = main(sys.argv[1:])

    # Register function to persist buffered messages when exiting
    import atexit
    atexit.register(MessageApiCall.flush_messages)
