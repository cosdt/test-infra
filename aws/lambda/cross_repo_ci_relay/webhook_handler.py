import os

import requests
from fastapi import Request, HTTPException

from github import Github
from github import Auth
from github.GithubException import GithubException

import utils


# ================= ClickHouse (PR display flag) =================

_CH_URL = os.getenv("CLICKHOUSE_URL", "http://localhost:8123")
_CH_USER = os.getenv("CLICKHOUSE_USER", "admin")
_CH_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "admin123")


def _parse_oot_selector(label_name: str) -> str | None:
    if not isinstance(label_name, str):
        return None
    n = label_name.strip()
    if not n.startswith("ciflow/oot/"):
        return None
    selector = n[len("ciflow/oot/") :].split("/", 1)[0].strip().lower()
    return selector or None


def _l3_device_by_selector(selector: str | None) -> str | None:
    if not selector:
        return None
    info_map = utils.allowlist_info_map or {}
    for device, info in info_map.items():
        try:
            if (info or {}).get("level") != "L3":
                continue
            if str(device).strip().lower() == selector:
                return str(device)
        except Exception:
            continue
    return None


def _ch_try_enable_display_on_pr_column() -> None:
    # Idempotent for newer ClickHouse versions. If table doesn't exist yet, just log and continue.
    sql = "ALTER TABLE oot_ci_results ADD COLUMN IF NOT EXISTS display_on_pr UInt8 DEFAULT 0"
    try:
        r = requests.post(
            _CH_URL,
            params={"user": _CH_USER, "password": _CH_PASSWORD, "query": sql},
            timeout=10,
        )
        r.raise_for_status()
    except Exception as e:
        print(f"[A] ClickHouse: unable to ensure display_on_pr column: {e}")


def _ch_set_display_on_pr(
    *, upstream_repo: str, commit_sha: str, device: str, value: int = 1
) -> None:
    # Note: ClickHouse mutations are asynchronous; this only triggers the mutation.
    val = 1 if int(value) else 0
    sql = (
        "ALTER TABLE oot_ci_results "
        f"UPDATE display_on_pr={val} "
        "WHERE upstream_repo={upstream_repo:String} AND commit_sha={commit_sha:String} AND device={device:String}"
    )
    r = requests.post(
        _CH_URL,
        params={
            "user": _CH_USER,
            "password": _CH_PASSWORD,
            "query": sql,
            "upstream_repo": upstream_repo,
            "commit_sha": commit_sha,
            "device": device,
        },
        timeout=10,
    )
    r.raise_for_status()
    print(
        f"[A] ClickHouse: set display_on_pr={val} upstream_repo={upstream_repo} sha={commit_sha} device={device}",
    )


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
    gh = Github(auth=Auth.Token(installation_token), timeout=20)
    try:
        run = gh.get_repo(picked).get_workflow_run(run_id)
        run.rerun()
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

    if action not in ("opened", "reopened", "synchronize", "labeled"):
        return {"ignored": True}

    installation_token = _get_installation_token(int(installation_id))
    gh = Github(auth=Auth.Token(installation_token), timeout=20)

    labels = [
        l.get("name")
        for l in ((payload.get("pull_request") or {}).get("labels") or [])
        if isinstance(l, dict)
    ]
    print(f"[A] pull_request action={action} repo={repo} sha={sha} labels={labels}")

    # L3: when a ciflow/oot/<device> label is added, mark display_on_pr=1 in ClickHouse
    # for that (upstream_repo, sha, device) record, and ensure an in-progress check-run.
    if action == "labeled":
        added_label = ((payload.get("label") or {}).get("name") or "").strip()
        added_selector = _parse_oot_selector(added_label)
        l3_device = _l3_device_by_selector(added_selector)
        if l3_device:
            try:
                _ch_try_enable_display_on_pr_column()
                _ch_set_display_on_pr(
                    upstream_repo=repo, commit_sha=sha, device=l3_device, value=1
                )
            except requests.RequestException as e:
                resp = getattr(e, "response", None)
                print(
                    f"[A] ClickHouse: failed to set display_on_pr for {l3_device}: status={getattr(resp, 'status_code', None)} body={(getattr(resp, 'text', '') or '')[:500]}"
                )
            except Exception as e:
                print(
                    f"[A] ClickHouse: failed to set display_on_pr for {l3_device}: {e}"
                )

    # For L3: only create check-run when a matching label exists: ciflow/oot/<device>[/...]
    oot_selectors: set[str] = set()
    for n in labels:
        selector = _parse_oot_selector(n)
        if not selector:
            continue
        if selector:
            oot_selectors.add(selector)

    # If action is 'labeled', also consider the newly added label explicitly.
    if action == "labeled":
        added = ((payload.get("label") or {}).get("name") or "").strip()
        selector = _parse_oot_selector(added)
        if selector:
            oot_selectors.add(selector)
    print(f"[A] oot_selectors={sorted(oot_selectors)}")

    # Resolve and dispatch to all allowlisted downstream repos.
    repos = utils.list_installation_repositories(installation_token)
    if not utils.allowlist_map:
        raise HTTPException(status_code=400, detail="allowlist_map is empty")

    dispatched: list[dict] = []
    failed: list[dict] = []
    for downstream_device, allow_url in sorted(utils.allowlist_map.items()):
        info = (utils.allowlist_info_map or {}).get(downstream_device) or {}
        level = info.get("level")
        check_name = None
        if level == "L4":
            check_name = "gate"
        elif level == "L3":
            dd = str(downstream_device).lower()
            if dd in oot_selectors:
                check_name = "info"
            else:
                if action == "opened":
                    print(
                        f"[A] L3 skip in_progress (no matching label) device={downstream_device} selectors={sorted(oot_selectors)}",
                    )
        if check_name:
            try:
                details_url = allow_url.rstrip("/") + "/actions"
                cid = utils.ensure_in_progress_check_run(
                    check_name,
                    installation_token,
                    repo,
                    downstream_device,
                    sha,
                    details_url,
                )
                print(
                    f"[A] ensured in_progress check-run name=oot / {downstream_device} / {check_name} id={cid} repo={repo} sha={sha}",
                )
            except GithubException as e:
                failed.append(
                    {
                        "downstream_device": downstream_device,
                        "error": "Failed to create in-progress check-run",
                        "status": getattr(e, "status", None),
                        "body": str(getattr(e, "data", None))[:2000],
                    }
                )
                print(
                    f"[A] Failed to create in-progress check-run for {downstream_device}: status={getattr(e, 'status', None)} body={str(getattr(e, 'data', None))[:500]}"
                )
            except Exception as e:
                failed.append(
                    {
                        "downstream_device": downstream_device,
                        "error": f"Failed to create in-progress check-run: {e}",
                    }
                )

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
            repo_obj = gh.get_repo(picked)
            repo_obj.create_repository_dispatch(
                "pytorch-pr-trigger",
                {"upstream_repo": repo, "commit_sha": sha},
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
