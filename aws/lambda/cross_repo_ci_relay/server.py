from fastapi import FastAPI, APIRouter, Request

import result_handler
import webhook_handler


webhook_router = APIRouter()
result_router = APIRouter()


@webhook_router.post("/github/webhook")
async def github_webhook(req: Request):
    return await webhook_handler.handle_github_webhook(req)


@result_router.post("/ci/result")
async def ci_result(req: Request):
    return await result_handler.handle_ci_result(req)


# ================= FastAPI apps =================
# - webhook_app: only /github/webhook (for smee forward)
# - result_app: only /ci/result (for downstream callback)
# - app: combined (backward compatible)

webhook_app = FastAPI()
webhook_app.include_router(webhook_router)

result_app = FastAPI()
result_app.include_router(result_router)

app = FastAPI()
app.include_router(webhook_router)
app.include_router(result_router)
