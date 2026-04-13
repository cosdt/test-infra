from typing import NotRequired, TypedDict


class HTTPException(Exception):
    def __init__(self, status_code: int, detail):
        self.status_code = status_code
        self.detail = detail


class EventDispatchPayload(TypedDict):
    event_type: str
    delivery_id: str
    payload: dict
    callback_token: NotRequired[str]


class ResultCallbackPayload(TypedDict):
    head_sha: str
    status: str
    conclusion: str | None
    workflow_name: str
    workflow_url: str
    downstream_repo: str
    upstream_repo: str
    pr_number: int
    callback_token: NotRequired[str]
    run_id: NotRequired[int]
    job_id: NotRequired[int]


class WorkflowTimingRecord(TypedDict):
    queue_time: float | None
    execution_time: float | None
