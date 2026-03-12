import logging
import os
import json

from fastapi import FastAPI, APIRouter, Request

import result_handler
import webhook_handler
from config import RelayConfig


logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


webhook_router = APIRouter()
result_router = APIRouter()


@webhook_router.post("/github/webhook")
async def github_webhook(req: Request):
    body = await req.body()
    data = json.loads(body)
    relay_config = RelayConfig.from_env()
    sig = req.headers.get("X-Hub-Signature-256")
    event = req.headers.get("X-GitHub-Event")

    logger.info("webhook received event=%s", event)

    return webhook_handler.handle_github_webhook(relay_config, body, data, sig, event)


@result_router.post("/ci/result")
async def ci_result(req: Request):
    data = await req.json()
    logger.info(
        "ci/result received upstream_repo=%s commit_sha=%s",
        data.get("upstream_repo"),
        data.get("commit_sha"),
    )

    relay_config = RelayConfig.from_env()
    return result_handler.handle_ci_result(relay_config, data)


webhook_app = FastAPI()
webhook_app.include_router(webhook_router)

result_app = FastAPI()
result_app.include_router(result_router)

app = FastAPI()
app.include_router(webhook_router)
app.include_router(result_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
