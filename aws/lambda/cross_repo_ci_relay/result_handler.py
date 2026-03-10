import json
import os

import requests
import yaml
from fastapi import HTTPException, Request

import utils

# ================= ClickHouse 配置 =================

_CH_URL      = os.getenv("CLICKHOUSE_URL",      "http://localhost:8123")
_CH_USER     = os.getenv("CLICKHOUSE_USER",     "admin")
_CH_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "admin123")

# ================= Allowlist =================

_WHITELIST_PATH = os.path.join(os.path.dirname(__file__), "whitelist.yaml")


def _load_allowlist() -> dict[str, dict]:
	"""Parse whitelist.yaml and return device → {level, repo, url, oncall}."""
	with open(_WHITELIST_PATH, "r") as f:
		raw = yaml.safe_load(f) or {}
	mapping: dict[str, dict] = {}
	for level in ("L1", "L2", "L3", "L4"):
		entries = raw.get(level) or []
		for entry in entries:
			if not isinstance(entry, dict):
				continue
			device = entry.get("device")
			if not device:
				continue
			mapping[device] = {
				"level": level,
				"repo": entry.get("repo", ""),
				"url": (entry.get("url") or "").rstrip("/"),
				"oncall": entry.get("oncall") or [],
			}
	return mapping


def _ensure_device_from_allowlist(run_url: str, allowlist: dict) -> str:
	"""Validate run_url against allowlist and return the matching device name."""
	if not run_url:
		raise HTTPException(status_code=400, detail="Missing url")

	repo_html_url = utils._repo_html_url_from_actions_run_url(run_url)
	if not repo_html_url:
		raise HTTPException(status_code=400, detail=f"Unsupported url: {run_url}")

	norm = repo_html_url.rstrip("/")
	for device, info in allowlist.items():
		if info["url"] == norm:
			return device

	raise HTTPException(
		status_code=403,
		detail={
			"message": "ci/result rejected: run url repo is not allowlisted",
			"repo_html_url": repo_html_url,
			"allowed": sorted(info["url"] for info in allowlist.values()),
		},
	)

# ================= ClickHouse 工具 =================

_ch_table_ensured = False


def _ch_ensure_table():
	"""Create oot_ci_results table if it does not exist (idempotent)."""
	global _ch_table_ensured
	if _ch_table_ensured:
		return
	sql = """
CREATE TABLE IF NOT EXISTS oot_ci_results (
    recorded_at   DateTime DEFAULT now(),
    device        String,
    upstream_repo String,
    commit_sha    String,
    workflow_name String,
    conclusion    String,
    run_url       String
) ENGINE = MergeTree()
ORDER BY (upstream_repo, commit_sha, device)
""".strip()
	r = requests.post(
		_CH_URL,
		params={"user": _CH_USER, "password": _CH_PASSWORD},
		data=sql,
		timeout=10,
	)
	r.raise_for_status()
	_ch_table_ensured = True


def _ch_write(
	*,
	device: str,
	upstream_repo: str,
	commit_sha: str,
	workflow_name: str,
	conclusion: str,
	run_url: str,
):
	"""Insert one result row into oot_ci_results using JSONEachRow format."""
	row = {
		"device":        device,
		"upstream_repo": upstream_repo,
		"commit_sha":    commit_sha,
		"workflow_name": workflow_name,
		"conclusion":    conclusion,
		"run_url":       run_url,
	}
	r = requests.post(
		_CH_URL,
		params={
			"user":     _CH_USER,
			"password": _CH_PASSWORD,
			"query":    "INSERT INTO oot_ci_results FORMAT JSONEachRow",
		},
		data=json.dumps(row),
		timeout=10,
	)
	r.raise_for_status()

# ================= GitHub 工具 =================


def _get_pr_labels(token: str, upstream_repo: str, commit_sha: str) -> list[str]:
	"""Return label names from the first open PR associated with commit_sha."""
	r = requests.get(
		f"{utils.GITHUB_API}/repos/{upstream_repo}/commits/{commit_sha}/pulls",
		headers={
			"Authorization": f"Bearer {token}",
			"Accept": "application/vnd.github+json",
			"X-GitHub-Api-Version": "2022-11-28",
		},
		timeout=20,
	)
	r.raise_for_status()
	prs = r.json()
	if not prs:
		return []
	return [label["name"] for label in prs[0].get("labels", [])]


def _create_check_run_with_fallback(
	*,
	workflow_name: str,
	upstream_repo: str,
	device: str,
	commit_sha: str,
	conclusion: str,
	run_url: str,
):
	"""Create a completed check-run on upstream_repo.

	Tries direct installation lookup first; falls back to scanning all installations.
	"""
	jwt_token = utils.create_app_jwt()

	# Prefer direct lookup
	try:
		upstream_installation_id = utils.get_repo_installation_id(jwt_token, upstream_repo)
		token = utils.get_installation_token(jwt_token, upstream_installation_id)
		utils.create_completed_check_run(
			workflow_name, token, upstream_repo, device, commit_sha, conclusion, run_url
		)
		print(f"[upstream] check-run created for {device}")
		return
	except Exception as e:
		print(f"[upstream] direct installation lookup failed: {e}")

	# Fallback: scan all installations
	r = requests.get(
		f"{utils.GITHUB_API}/app/installations",
		headers={
			"Authorization": f"Bearer {jwt_token}",
			"Accept": "application/vnd.github+json",
		},
		timeout=20,
	)
	r.raise_for_status()

	last_error = None
	for inst in r.json():
		token = utils.get_installation_token(jwt_token, inst["id"])
		try:
			utils.create_completed_check_run(
				workflow_name, token, upstream_repo, device, commit_sha, conclusion, run_url
			)
			print(f"[upstream] check-run created for {device} (fallback)")
			return
		except Exception as e:
			last_error = e
			continue

	raise HTTPException(status_code=404, detail=f"Installation not found: {last_error}")

# ================= 入口 =================


async def handle_ci_result(req: Request):
	data = await req.json()

	run_url       = data.get("url", "")
	allowlist     = _load_allowlist()
	device        = _ensure_device_from_allowlist(run_url, allowlist)
	info          = allowlist[device]
	level         = info["level"]

	workflow_name = data["workflow_name"]
	upstream_repo = data["upstream_repo"]
	commit_sha    = data["commit_sha"]
	conclusion    = data["conclusion"]  # success / failure / cancelled

	print(f"[{device}] CI finished: {conclusion} (level={level})")

	# ── L1: forward only, no feedback to upstream ──────────────────────────
	if level == "L1":
		return {"ok": True, "action": "ignored"}

	# ── L2+: write result to ClickHouse (OOT HUD) ──────────────────────────
	_ch_ensure_table()
	_ch_write(
		device=device,
		upstream_repo=upstream_repo,
		commit_sha=commit_sha,
		workflow_name=workflow_name,
		conclusion=conclusion,
		run_url=run_url,
	)

	if level == "L2":
		return {"ok": True, "action": "hud_only"}

	# ── L3: create non-blocking check-run only when ciflow/oot/ label present
	if level == "L3":
		jwt_token = utils.create_app_jwt()
		try:
			upstream_installation_id = utils.get_repo_installation_id(jwt_token, upstream_repo)
			token = utils.get_installation_token(jwt_token, upstream_installation_id)
		except Exception as e:
			raise HTTPException(status_code=502, detail=f"Failed to get upstream token: {e}")

		labels = _get_pr_labels(token, upstream_repo, commit_sha)
		if not any(label.startswith("ciflow/oot/") for label in labels):
			print(f"[{device}] L3: no ciflow/oot/ label, skipping check-run")
			return {"ok": True, "action": "no_label"}

	# ── L3 (label present) / L4: create check-run on upstream PR ───────────
	_create_check_run_with_fallback(
		workflow_name=workflow_name,
		upstream_repo=upstream_repo,
		device=device,
		commit_sha=commit_sha,
		conclusion=conclusion,
		run_url=run_url,
	)
	return {"ok": True, "action": "check_run_created"}
