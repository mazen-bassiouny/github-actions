import typing as t

from azure.eventhub import EventHubProducerClient
from falcon import Request

from te_commons.logger import log

from producer import TracktorProducer


class MultiProducer:
    __named_producers: t.Dict[str, TracktorProducer] = None
    __producers: t.Dict[str, t.Set[TracktorProducer]] = None
    __uri_stream_parameter = None

    def __init__(self,
                 config: t.Dict[str, t.Dict[str, str]],
                 uri_parameter_switch: str,
                 uri_to_producer_section_name: str = "uri_to_producer",
                 producer_config_startswith: str = "producer_",
                 thread_pool_size: int = 25,
                 buffer_size: int = 3,
                 wait_after_error: float = 10.0):
        """
        Enables Tracktor to write different events to different streams.

        The differentiation which event is written to which stream is
        done based on the value of a URI parameter. The 'routing' is
        flexible so that it is possible to write the same event to
        several streams. However, it is not guaranteed that different
        streams write flush the same event at the same time because each
        stream/producer has its own message buffer.

        :param config: Configuration dictionary created via the
            te-commons package.
        :param uri_parameter_switch: Name of URI parameter which is used
            to determine the correct stream/producer.
        :param uri_to_producer_section_name: Configuration section name
            containing the mapping from URI value to producers.
        :param producer_config_startswith: Configuration section prefix
            storing the relevant producer config.
        :param thread_pool_size: Size of the shared producer thread pool
        :param buffer_size: Maximum size of message buffer before
            messages are flushed.
        :param wait_after_error: Time in seconds to wait before
            attempting the next message write after an error occurred.
        """
        self.__class__.__uri_stream_parameter = uri_parameter_switch
        self.__class__.__named_producers = {}
        self.__class__.__producers = {}
        self.__class__.create_producers(
            configs=config,
            uri_section=uri_to_producer_section_name,
            producer_prefix=producer_config_startswith,
            thread_pool_size=thread_pool_size,
            buffer_size=buffer_size,
            wait_after_error=wait_after_error
        )

    @classmethod
    def producers(cls, req: Request) -> t.Set[TracktorProducer]:
        """
        Get instance set of `TracktorProducer`s associated to this
        request.

        :param req: Falcon request needed to determine which producer is
            requested
        """
        return cls.__producers.get(
            req.get_param(cls.__uri_stream_parameter),
            cls.__producers["default"]
        )

    def write_dict(self, msg: dict, req: Request):
        """
        Sends the content of `msg` to all producers associated
        to this request. 

        :param msg: Dictionary to be converted to JSON message
        :param req: Falcon request needed to determine which producer to
            use to write the message
        """
        for producer in self.producers(req):
            producer.write_dict(msg)

    @classmethod
    def flush_messages(cls, close_pool=False):
        """
        Write buffered messages of all producers to stream

        :param close_pool: If set to True, the calling thread will wait
            until the messages are written. Furthermore, the shared
            thread pool will be closed.
        """
        for producer in cls.__named_producers.values():
            producer.flush_messages()

        if close_pool:
            TracktorProducer.close_pool()

    @classmethod
    def create_producers(
        cls,
        configs: t.Dict[str, t.Dict[str, str]],
        uri_section: str,
        producer_prefix: str,
        thread_pool_size: int,
        buffer_size: int,
        wait_after_error: float,
    ) -> None:
        """
        Initialises all producers specified in the config and assigns
        the mapping from URI parameter to set of producers.

        Steps taken are:
        1) Parsing the provided config for:
            * configuration for TracktorProducers
            * mapping URI value -> set of requested TracktorProducers
        2) Create and store all requested TrackorProducers
        3) Assign relevant TracktorProducers to their URI value

        Note:
            The parameters @thread_pool_size, @buffer_size, and
            @wait_after_error will be used for all TracktorProducers
            initialised, so even if these are not stored in a variable
            shared by all instances the value is the same everywhere.

        :param configs: Configuration dictionary created via the
            te-commons package
        :param uri_section: Configuration section name where to find the
            mapping from URI value to list of producers
        :param producer_prefix: Prefix identifying all sections which
            contain producer configurations
        :param thread_pool_size: Size of shared thread pool.
        :param buffer_size: Maximum size of message buffer before it is
            written to the stream.
        :param wait_after_error: Seconds to wait after an error accorded
            before a next attempt to write messages to the stream.
        """
        # get relevant configuration
        producer_config = cls.get_eventhub_config(
            configs=configs, config_key_startswith=producer_prefix
        )
        switch_to_producer = cls.get_uri_to_producer(
            configs=configs, section_name=uri_section
        )

        for name, single_config in producer_config.items():
            if name not in cls.__named_producers:
                client = EventHubProducerClient.from_connection_string(
                    single_config["connection_str"],
                    eventhub_name=single_config["eventhub_name"]
                )
                cls.__named_producers[name] = TracktorProducer(
                    client=client,
                    name=name,
                    buffer_size=buffer_size,
                    thread_pool_size=thread_pool_size,
                    wait_after_error=wait_after_error,
                    encoding=single_config.get("encoding", "utf-8"),
                )

        for switch, producer_list in switch_to_producer.items():
            cls.__producers[switch] = {
                cls.__named_producers[name] for name in producer_list
            }

    @staticmethod
    def get_eventhub_config(
        configs: t.Dict[str, t.Dict[str, str]],
        config_key_startswith="producer"
    ) -> t.Dict[str, dict]:
        """
        Extract the relevant EventHub config

        The expected structure of the dictionary is:
            {
                ...,
                '<config_key_startswith>producer1': {
                    'connection_str': 'abc',
                    'eventhub_name': 'efg'
                },
                '<config_key_startswith>producer2': {...},
                ...
            }

        :param configs: Configuration dictionary returned by the commons
            config package
        :param config_key_startswith: Indicator which sections are used
            for partner configuration.
        :returns: Mapping of internal EventHub producer name to its config
        """
        eventhub_config = {}
        for section, conf in configs.items():
            if section.startswith(config_key_startswith):
                name = section.replace(config_key_startswith, "")
                eventhub_config[name] = conf

        if len(eventhub_config) == 0:
            log.error(
                f"Could not find any section in config file starting with "
                f"{config_key_startswith}"
            )
            raise RuntimeError(f"No section name starting with "
                               f"'{config_key_startswith}'")

        return eventhub_config

    @staticmethod
    def get_uri_to_producer(
        configs: t.Dict[str, t.Dict[str, str]],
        section_name: str = "uri_to_producer"
    ) -> t.Dict[str, t.Set[str]]:
        """
        Extract mapping of URI parameter value to set of producers

        The expected structure of the dictionary is:
            {
                ...,
                '<config_key_startswith>producer3': {...},
                ...,
                '<section_name>': {
                    '12': 'producer1,producer2',
                    'fc': 'producer3'
                },
                ...
            }
        Note:
        * The key is the URI value which needs to be passed to use the
          producers listed in the dictionary value.
        * Multiple producers can be specified via a comma separated list
          (without spaces).
        * The producer names MUST match the section name of the producer
          config minus their identifying prefix
          (see @get_eventhub_config)

        :param configs: Configuration dictionary returned by the commons
            config package
        :param section_name: Section name containing the information
        :return: Mapping URI parameter value -> Set internal producer
            names
        """
        uri_to_producer = {}

        for switch, producer_set in configs[section_name].items():
            if producer_set == "":
                continue
            uri_to_producer[switch] = set(producer_set.split(","))

        if len(uri_to_producer) == 0:
            log.error(
                "No mapping from URI value to producer found. Possible "
                "reasons are: wrong section name, no section present, or no "
                "assignments of producer names"
            )
            raise RuntimeError(
                "No or only empty uri to producer matching found."
            )

        if "default" not in uri_to_producer:
            uri_to_producer["default"] = {}

        return uri_to_producer
