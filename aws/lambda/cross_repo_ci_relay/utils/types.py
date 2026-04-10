from typing import NotRequired, TypedDict


class HTTPException(Exception):
    def __init__(self, status_code: int, detail):
        self.status_code = status_code
        self.detail = detail


class EventDispatchPayload(TypedDict):
    event_type: str
    delivery_id: str
    payload: dict


class ResultCallbackPayload(TypedDict):
    head_sha: str
    status: str
    conclusion: str | None
    workflow_name: str
    workflow_url: str
    downstream_repo: str
    upstream_repo: str
    pr_number: int
    run_id: NotRequired[int]
    job_id: NotRequired[int]


class OOTStatusRecord(TypedDict):
    downstream_repo: str
    upstream_repo: str
    head_sha: str
    pr_number: int
    status: str
    conclusion: str | None
    workflow_name: str
    workflow_url: str
    run_id: NotRequired[int]
    job_id: NotRequired[int]
