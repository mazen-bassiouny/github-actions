import json
import uuid
from multiprocessing.pool import ThreadPool
from threading import Lock
from datetime import datetime, timedelta

from azure.eventhub import EventData

from te_commons.logger import log


class MessageBuffer:
    """
    Wrapper for a vanilla list that is used to make sure that no
    messages get lost. Its main feature is the `get_list` method that
    returns the current list and replaces it with a fresh one in an
    atomic (and hence thread-safe) operation.
    """
    def __init__(self):
        # build an empty list of lists
        self.__buffer = [[]]

    def append(self, msg):
        self.__buffer[0].append(msg)

    def get_list(self) -> list:
        self.__buffer.append([])
        return self.__buffer.pop(0)

    def clear(self):
        self.__buffer = [[]]

    def __len__(self):
        return len(self.__buffer[0])


class TracktorProducer:
    """
    Producer to write into a EventHub stream.

    :param client: A EventHub producer client from azure.eventhub.
    :param encoding: The encoding used to encode messages. Hope you have
        good reasons to use something different from UTF-8...
    :param thread_pool_size: Number of threads to be kept in a pool
        to send messages asynchronously.
    :param buffer_size: Number of messages to be collected before
        putting them to EventHub.
    :param wait_after_error: Number of seconds to wait when an error
        occurs. During that time, no further messages will be sent.
    """

    thread_lock = Lock()
    __thread_pool = None

    def __init__(self,
                 client,
                 name,
                 buffer_size=3,
                 encoding="utf-8",
                 thread_pool_size=25,
                 wait_after_error=10.0):
        self.client = client
        self.name = name
        self.encoding = encoding
        self.buffer_size = buffer_size
        self.thread_pool_size = thread_pool_size
        self.wait_after_error = wait_after_error

        self.__message_buffer = MessageBuffer()
        self.__blocked_until = datetime.now()

    def write_dict(self, msg: dict):
        """
        Writes the content of `msg` to the message buffer. If the buffer
        size is reached, the messages in the buffer will be written to
        EventHub.
        """
        # Enrich some information
        message_id = str(uuid.uuid4())

        msg["message_id"] = message_id
        msg["message_time"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")

        # Write message to the buffer and flush it to EventHub if buffer size is reached
        self.__message_buffer.append(
            EventData(json.dumps(msg).encode(self.encoding))
        )

        if len(self.__message_buffer) >= self.buffer_size:
            self.flush_messages()

    @property
    def thread_pool(self) -> ThreadPool:
        """
        Get thread pool instance.

        This function is needed so that the thread pool is initialised
        the first time it is needed. Pre initialising is not easily
        possible because of the uWSGI pre-forking behaviour.
        """
        if self.__class__.__thread_pool is None:
            self.__class__.__thread_pool = ThreadPool(
                processes=self.thread_pool_size
            )
        return self.__class__.__thread_pool

    @property
    def is_blocked(self):
        return datetime.now() < self.__blocked_until

    def flush_messages(self):
        """
        Write buffered messages to EventHub
        """
        if not self.is_blocked:
            self.thread_pool.apply_async(
                func=self.__put_eventhub_event,
                args=(self.__message_buffer.get_list(),),
                error_callback=self.__put_error_callback
            )
        else:
            log.debug(
                f"Removing {len(self.__message_buffer)} messages because the "
                f"producer is blocked until {self.__blocked_until}"
            )
            self.__message_buffer.clear()

    @classmethod
    def close_pool(cls):
        if cls.__thread_pool is not None:
            cls.__thread_pool.close()
            cls.__thread_pool.join()
            cls.__thread_pool = None

    def __put_eventhub_event(self, msg_list: list):
        """
        Expects a list of messages and tries to put it to EventHub.
        """
        if len(msg_list) > 0:
            self.client.send_batch(event_data_batch=msg_list)

        log.debug("Wrote batch with {} messages".format(len(msg_list)))

    def __put_error_callback(self, e: BaseException):
        """
        Callback function that is invoked when `put_eventhub_event` fails.
        """
        self.__blocked_until = datetime.now() + timedelta(seconds=self.wait_after_error)
        log.error(
            f"Exception when calling put_eventhub_event. No further messages "
            f"for {self.wait_after_error} seconds. Exception: {str(e)}"
        )
