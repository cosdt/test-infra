from enum import Enum
from typing import TypedDict


class HTTPException(Exception):
    def __init__(self, status_code: int, detail):
        self.status_code = status_code
        self.detail = detail


class EventDispatchPayload(TypedDict):
    event_type: str
    delivery_id: str
    payload: dict


class TimingPhase(str, Enum):
    """Phases recorded in the crcr:timing:* Redis keys.

    - ``DISPATCH``: webhook side, when a repository_dispatch is fired.
    - ``IN_PROGRESS``: result side, when the downstream workflow reports it
      has started running.
    """

    DISPATCH = "dispatch"
    IN_PROGRESS = "in_progress"
