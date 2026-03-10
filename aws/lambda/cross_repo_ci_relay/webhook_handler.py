import requests
from fastapi import Request, HTTPException

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
	if not installation_id:
		raise HTTPException(status_code=400, detail="Missing installation id")
	return int(installation_id)


def _get_installation_token(installation_id: int) -> str:
	jwt_token = utils.create_app_jwt()
	return utils.get_installation_token(jwt_token, installation_id)


def _resolve_repo_full_name_for_check_run(
	*,
	repos: list[dict],
	owner: str,
	repo_name: str,
	allow_device: str | None,
) -> tuple[str | dict | None, str | None]:
	if allow_device:
		allow_url = utils.allowlist_map[allow_device]
		picked = utils.pick_repo_full_name_by_allowlist(repos, allow_url)
		return picked, allow_url

	allow_url = None
	preferred_full_name = f"{owner}/{repo_name}" if owner and repo_name else None
	picked = utils.pick_repo_full_name(repos, name=repo_name, preferred_full_name=preferred_full_name)
	return picked, allow_url


def _handle_check_run_rerequested(payload: dict) -> dict:
	check_run = payload.get("check_run") or {}
	details_url = check_run.get("details_url") or check_run.get("html_url")
	parsed = utils.parse_actions_run_from_url(details_url or "")
	if not parsed:
		raise HTTPException(status_code=400, detail=f"Unsupported details_url: {details_url}")

	owner, repo_name, run_id = parsed

	repo_html_url = f"https://github.com/{owner}/{repo_name}"
	allow_device = utils.allowlist_find_device_by_repo_html_url(repo_html_url)

	installation_id = _get_installation_id(payload)
	installation_token = _get_installation_token(installation_id)

	repos = utils.list_installation_repositories(installation_token)
	picked, allow_url = _resolve_repo_full_name_for_check_run(
		repos=repos,
		owner=owner,
		repo_name=repo_name,
		allow_device=allow_device,
	)

	if not picked:
		raise HTTPException(
			status_code=403,
			detail={
				"message": "This installation cannot access target repo; ensure the app is installed on that repo",
				"target_repo_name": repo_name,
				"allow_url": allow_url,
			},
		)
	if isinstance(picked, dict) and picked.get("ambiguous"):
		raise HTTPException(
			status_code=400,
			detail={
				"message": "Multiple repos matched allowlist; refine allowlist_map",
				"target_repo_name": repo_name,
				"candidates": picked["ambiguous"],
			},
		)

	api_url = f"{utils.GITHUB_API}/repos/{picked}/actions/runs/{run_id}/rerun"
	r = requests.post(
		api_url,
		headers={
			"Authorization": f"Bearer {installation_token}",
			"Accept": "application/vnd.github+json",
			"X-GitHub-Api-Version": "2022-11-28",
		},
		timeout=20,
	)
	print(f"[A] Rerunning workflow run_id={run_id} for {picked} (API: {api_url})")
	r.raise_for_status()
	return {"ok": True}


def _handle_pull_request(payload: dict, action: str | None) -> dict:
	repo = payload["repository"]["full_name"]
	sha = payload["pull_request"]["head"]["sha"]
	installation_id = payload["installation"]["id"]

	if "UpStream" not in repo:
		return {"ignored": True}

	if action not in ("opened", "reopened", "synchronize"):
		return {"ignored": True}

	installation_token = _get_installation_token(int(installation_id))

	# Resolve target repo B within repos accessible to this installation.
	repos = utils.list_installation_repositories(installation_token)
	downstream_device = utils.allowlist_pick_single_device()
	allow_url = utils.allowlist_map[downstream_device]
	picked = utils.pick_repo_full_name_by_allowlist(repos, allow_url)
	if not picked:
		raise HTTPException(
			status_code=403,
			detail={
				"message": "This installation cannot access repo B; ensure the app installation includes repo B",
				"downstream_device": downstream_device,
				"allow_url": allow_url,
			},
		)
	if isinstance(picked, dict) and picked.get("ambiguous"):
		raise HTTPException(
			status_code=400,
			detail={
				"message": "Multiple repos matched allowlist; refine allowlist_map",
				"downstream_device": downstream_device,
				"allow_url": allow_url,
				"candidates": picked["ambiguous"],
			},
		)

	print(f"[A] PR trigger repo={picked} sha={sha} installation_id={installation_id} action={action}")

	# 只做一件事：dispatch B
	r = requests.post(
		f"{utils.GITHUB_API}/repos/{picked}/dispatches",
		headers={
			"Authorization": f"Bearer {installation_token}",
			"Accept": "application/vnd.github+json",
			"X-GitHub-Api-Version": "2022-11-28",
		},
		json={
			"event_type": "pytorch-pr-trigger",
			"client_payload": {
				"upstream_repo": repo,
				"commit_sha": sha,
			},
		},
		timeout=20,
	)
	r.raise_for_status()

	return {"ok": True}


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
