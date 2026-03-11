from fastapi import Request, HTTPException

from github.GithubException import GithubException

import github_client_helper
import utils


async def _read_and_verify(req: Request) -> dict:
    body = await req.body()

    sig = req.headers.get("X-Hub-Signature-256")
    if not sig:
        raise HTTPException(status_code=400, detail="No signature")
    utils.verify_signature(body, sig)

    return await req.json()


def _get_installation_id(payload: dict) -> int:
    installation_id = (payload.get("installation") or {}).get("id")
    print(f"[A] installation_id={installation_id} from payload")
    if not installation_id:
        raise HTTPException(status_code=400, detail="Missing installation id")
    return int(installation_id)


def _get_installation_token(installation_id: int) -> str:
    # Delegate to utils (internally uses PyGithub integration).
    return utils.get_installation_token("", int(installation_id))


def _handle_check_run_rerequested(payload: dict) -> dict:
    check_run = payload.get("check_run") or {}
    details_url = check_run.get("details_url") or check_run.get("html_url")
    parsed = utils.parse_actions_run_from_url(details_url or "")
    print(f"[A] check_run rerequested details_url={details_url} parsed={parsed}")
    if not parsed:
        raise HTTPException(
            status_code=400, detail=f"Unsupported details_url: {details_url}"
        )

    owner, repo_name, run_id = parsed

    installation_id = _get_installation_id(payload)
    installation_token = _get_installation_token(installation_id)

    # We already have owner/repo from details_url, so no need to resolve by allowlist.
    picked = f"{owner}/{repo_name}"
    try:
        github_client_helper.rerun_workflow_run(
            token=installation_token,
            repo_full_name=picked,
            run_id=run_id,
            timeout=20,
        )
        print(f"[A] Rerunning workflow run_id={run_id} for {picked}")
    except GithubException as e:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Failed to rerun workflow run",
                "repo": picked,
                "run_id": run_id,
                "status": getattr(e, "status", None),
                "data": getattr(e, "data", None),
            },
        )
    return {"ok": True}


def _handle_pull_request(payload: dict, action: str | None) -> dict:
    repo = payload["repository"]["full_name"]
    sha = payload["pull_request"]["head"]["sha"]
    installation_id = payload["installation"]["id"]

    # Only handle events from the configured upstream repo.
    expected_upstream = (getattr(utils, "UPSTREAM_REPO", "") or "").strip()
    if expected_upstream:
        if repo.strip().lower() != expected_upstream.lower():
            return {"ignored": True}

    if action not in ("opened", "reopened", "synchronize"):
        return {"ignored": True}

    installation_token = _get_installation_token(int(installation_id))

    labels = [
        l.get("name")
        for l in ((payload.get("pull_request") or {}).get("labels") or [])
        if isinstance(l, dict)
    ]
    print(f"[A] pull_request action={action} repo={repo} sha={sha} labels={labels}")

    # Resolve and dispatch to all allowlisted downstream repos.
    repos = utils.list_installation_repositories(installation_token)
    if not utils.allowlist_map:
        raise HTTPException(status_code=400, detail="allowlist_map is empty")

    dispatched: list[dict] = []
    failed: list[dict] = []
    for downstream_device, allow_url in sorted(utils.allowlist_map.items()):
        picked = utils.pick_repo_full_name_by_allowlist(repos, allow_url)
        if not picked:
            failed.append(
                {
                    "downstream_device": downstream_device,
                    "allow_url": allow_url,
                    "error": "This installation cannot access repo; ensure the app installation includes it",
                }
            )
            continue
        if isinstance(picked, dict) and picked.get("ambiguous"):
            failed.append(
                {
                    "downstream_device": downstream_device,
                    "allow_url": allow_url,
                    "error": "Multiple repos matched allowlist; refine allowlist_map",
                    "candidates": picked["ambiguous"],
                }
            )
            continue

        print(
            f"[A] PR trigger downstream_device={downstream_device} repo={picked} sha={sha} installation_id={installation_id} action={action}"
        )
        try:
            github_client_helper.create_repository_dispatch(
                token=installation_token,
                repo_full_name=picked,
                event_type="pytorch-pr-trigger",
                client_payload={"upstream_repo": repo, "commit_sha": sha},
                timeout=20,
            )
            dispatched.append({"downstream_device": downstream_device, "repo": picked})
        except GithubException as e:
            failed.append(
                {
                    "downstream_device": downstream_device,
                    "repo": picked,
                    "error": f"GitHub dispatch failed: status={getattr(e, 'status', None)} data={getattr(e, 'data', None)}",
                }
            )
        except Exception as e:
            failed.append(
                {
                    "downstream_device": downstream_device,
                    "repo": picked,
                    "error": f"GitHub dispatch failed: {e}",
                }
            )

    if not dispatched:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "No downstream dispatch succeeded",
                "failed": failed,
            },
        )

    return {"ok": True, "dispatched": dispatched, "failed": failed}


async def handle_github_webhook(req: Request):
    try:
        payload = await _read_and_verify(req)
        event = req.headers.get("X-GitHub-Event")
        action = payload.get("action")

        if event == "check_run":
            if action != "rerequested":
                return {"ignored": True}
            return _handle_check_run_rerequested(payload)

        elif event == "pull_request":
            return _handle_pull_request(payload, action)

        return {"ignored": True}
    except HTTPException as e:
        print(f"[A] webhook error: status={e.status_code} detail={e.detail}")
        raise
