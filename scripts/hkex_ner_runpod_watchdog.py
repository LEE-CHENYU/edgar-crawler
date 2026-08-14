#!/usr/bin/env python3
"""Watch HKEX NER RunPod Jupyter workers and clean up after verification."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import ssl
import subprocess
import sys
import tarfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import websocket


USER_AGENT = "Mozilla/5.0"


@dataclass(frozen=True)
class PodShard:
    pod_id: str
    shard: str

    @property
    def base_url(self) -> str:
        return f"https://{self.pod_id}-8888.proxy.runpod.net"

    @property
    def ws_url(self) -> str:
        return f"wss://{self.pod_id}-8888.proxy.runpod.net"


def log(message: str) -> None:
    print(f"{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} {message}", flush=True)


def parse_pods(value: str) -> list[PodShard]:
    out: list[PodShard] = []
    for item in value.split(","):
        pod_id, shard = item.strip().split(":", 1)
        out.append(PodShard(pod_id=pod_id.strip(), shard=shard.strip()))
    return out


def wait_for_proxy(pod: PodShard, token: str, timeout_seconds: int = 180) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            response = requests.get(
                f"{pod.base_url}/api/status",
                params={"token": token},
                headers={"User-Agent": USER_AGENT},
                timeout=20,
            )
            if response.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(10)
    raise TimeoutError(f"Jupyter proxy not ready for {pod.pod_id}")


def execute_code(pod: PodShard, token: str, code: str, timeout_seconds: int = 900) -> str:
    wait_for_proxy(pod, token)
    headers = {"User-Agent": USER_AGENT}
    kernel_id = ""
    ws = None
    try:
        response = requests.post(f"{pod.base_url}/api/kernels", params={"token": token}, headers=headers, timeout=30)
        response.raise_for_status()
        kernel_id = response.json()["id"]
        ws = websocket.create_connection(
            f"{pod.ws_url}/api/kernels/{kernel_id}/channels?token={token}",
            header=[f"User-Agent: {USER_AGENT}"],
            timeout=120,
            sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False},
        )
        msg_id = uuid.uuid4().hex
        session = uuid.uuid4().hex
        ws.send(
            json.dumps(
                {
                    "header": {
                        "msg_id": msg_id,
                        "username": "codex",
                        "session": session,
                        "date": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "msg_type": "execute_request",
                        "version": "5.3",
                    },
                    "parent_header": {},
                    "metadata": {},
                    "content": {
                        "code": code,
                        "silent": False,
                        "store_history": True,
                        "user_expressions": {},
                        "allow_stdin": False,
                    },
                    "channel": "shell",
                    "buffers": [],
                }
            )
        )
        output: list[str] = []
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            data = json.loads(ws.recv())
            if data.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            msg_type = data.get("msg_type") or data.get("header", {}).get("msg_type")
            content = data.get("content", {})
            if msg_type == "stream":
                output.append(content.get("text", ""))
            elif msg_type == "execute_result":
                output.append(content.get("data", {}).get("text/plain", ""))
            elif msg_type == "error":
                raise RuntimeError("\n".join(content.get("traceback", [])))
            elif msg_type == "status" and content.get("execution_state") == "idle":
                return "".join(output)
        raise TimeoutError(f"execution timed out on {pod.pod_id}")
    finally:
        if ws is not None:
            ws.close()
        if kernel_id:
            try:
                requests.delete(f"{pod.base_url}/api/kernels/{kernel_id}", params={"token": token}, headers=headers, timeout=30)
            except Exception as exc:
                log(f"{pod.pod_id} kernel cleanup failed: {type(exc).__name__}: {exc}")


def remote_status(pod: PodShard, token: str) -> dict[str, Any]:
    code = f"""
import json
import subprocess
from pathlib import Path

shard = {pod.shard!r}
event_path = Path(f"hkex_ner/logs/hkex_ner_shard_{{shard}}.jsonl")
stdout_path = Path(f"hkex_ner/logs/stdout_shard_{{shard}}.log")
pgrep = subprocess.run(["bash", "-lc", "pgrep -af hkex_ner_pipeline.py || true"], capture_output=True, text=True)
processes = [line for line in pgrep.stdout.splitlines() if "pgrep -af" not in line and "bash -lc" not in line]
events = 0
last = {{}}
statuses = {{}}
if event_path.exists():
    with event_path.open("r", encoding="utf-8") as fin:
        for raw in fin:
            raw = raw.strip()
            if not raw:
                continue
            events += 1
            item = json.loads(raw)
            last = item
            status = item.get("status", "")
            statuses[status] = statuses.get(status, 0) + 1
entities = subprocess.run(["bash", "-lc", "find hkex_ner/entities -name '*.jsonl.gz' 2>/dev/null | wc -l"], capture_output=True, text=True).stdout.strip()
summaries = subprocess.run(["bash", "-lc", "find hkex_ner/summaries -name '*.json' 2>/dev/null | wc -l"], capture_output=True, text=True).stdout.strip()
du = subprocess.run(["bash", "-lc", "du -sh hkex_ner 2>/dev/null | awk '{{print $1}}' || true"], capture_output=True, text=True).stdout.strip()
df = subprocess.run(["bash", "-lc", "df -h . | tail -n 1"], capture_output=True, text=True).stdout.strip()
stdout_tail = ""
if stdout_path.exists():
    stdout_tail = "\\n".join(stdout_path.read_text(encoding="utf-8", errors="replace").splitlines()[-5:])
row_total = int(last.get("row_total") or 0)
row_offset = int(last.get("row_offset") or 0)
complete = bool(row_total and events == row_total and row_offset == row_total and not processes)
print(json.dumps({{
    "pod_id": {pod.pod_id!r},
    "shard": shard,
    "running": bool(processes),
    "events": events,
    "row_total": row_total,
    "row_offset": row_offset,
    "complete": complete,
    "statuses": statuses,
    "entities": int(entities or 0),
    "summaries": int(summaries or 0),
    "du": du,
    "df": df,
    "stdout_tail": stdout_tail,
    "last_event": last,
}}))
"""
    output = execute_code(pod, token, code, timeout_seconds=180)
    return json.loads(output.strip().splitlines()[-1])


def package_remote_output(pod: PodShard, token: str) -> dict[str, Any]:
    tarball = f"hkex_ner_shard_{pod.shard}.tgz"
    code = f"""
import hashlib
import json
import subprocess
from pathlib import Path
tarball = Path({tarball!r})
subprocess.run(["tar", "-czf", str(tarball), "hkex_ner"], check=True)
digest = hashlib.sha256()
with tarball.open("rb") as fin:
    for chunk in iter(lambda: fin.read(1024 * 1024), b""):
        digest.update(chunk)
print(json.dumps({{"tarball": str(tarball), "size": tarball.stat().st_size, "sha256": digest.hexdigest()}}))
"""
    output = execute_code(pod, token, code, timeout_seconds=3600)
    return json.loads(output.strip().splitlines()[-1])


def download_file(pod: PodShard, token: str, remote_path: str, local_path: Path) -> str:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with requests.get(
        f"{pod.base_url}/files/{remote_path}",
        params={"token": token, "download": "1"},
        headers={"User-Agent": USER_AGENT},
        stream=True,
        timeout=(30, 300),
    ) as response:
        response.raise_for_status()
        with local_path.open("wb") as fout:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    digest.update(chunk)
                    fout.write(chunk)
    return digest.hexdigest()


def safe_extract_tar(tar_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    resolved_dest = dest.resolve()
    with tarfile.open(tar_path, "r:gz") as archive:
        for member in archive.getmembers():
            member_path = (dest / member.name).resolve()
            if not str(member_path).startswith(str(resolved_dest) + os.sep):
                raise RuntimeError(f"unsafe tar member: {member.name}")
        archive.extractall(dest)


def count_local_events(dest: Path, shard: str) -> int:
    path = dest / "hkex_ner" / "logs" / f"hkex_ner_shard_{shard}.jsonl"
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as fin:
        return sum(1 for line in fin if line.strip())


def delete_pod(pod_id: str) -> None:
    for attempt in range(1, 4):
        result = subprocess.run(["runpodctl", "pod", "delete", pod_id], capture_output=True, text=True)
        if result.returncode == 0:
            log(f"deleted pod {pod_id}")
            return
        log(f"delete attempt {attempt} failed for {pod_id}: {result.stderr.strip() or result.stdout.strip()}")
        time.sleep(10)
    raise RuntimeError(f"failed to delete pod {pod_id}")


def run(args: argparse.Namespace) -> int:
    token = os.environ.get(args.token_env)
    if not token:
        raise SystemExit(f"Missing Jupyter token in ${args.token_env}")
    pods = parse_pods(args.pods)
    dest = Path(args.dest).expanduser()
    download_dir = Path(args.download_dir).expanduser()
    manifest: dict[str, Any] = {
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pods": [],
        "dest": str(dest),
    }

    statuses: list[dict[str, Any]] = []
    while True:
        statuses = []
        all_complete = True
        for pod in pods:
            try:
                status = remote_status(pod, token)
                statuses.append(status)
                all_complete = all_complete and bool(status["complete"])
                progress = f"{status['row_offset']}/{status['row_total']}" if status["row_total"] else f"{status['events']}/?"
                log(
                    f"{pod.pod_id} shard {pod.shard}: progress={progress} running={status['running']} "
                    f"entities={status['entities']} statuses={status['statuses']}"
                )
            except Exception as exc:
                all_complete = False
                log(f"{pod.pod_id} shard {pod.shard}: status failed: {type(exc).__name__}: {exc}")
        if all_complete:
            log("all NER shards complete; packaging and downloading")
            break
        time.sleep(args.poll_seconds)

    for pod, status in zip(pods, statuses):
        if not status["complete"]:
            raise RuntimeError(f"refusing to download/delete incomplete shard {pod.shard}: {status}")
        package = package_remote_output(pod, token)
        local_tar = download_dir / package["tarball"]
        digest = download_file(pod, token, package["tarball"], local_tar)
        if digest != package["sha256"]:
            raise RuntimeError(f"sha256 mismatch for {local_tar}: {digest} != {package['sha256']}")
        safe_extract_tar(local_tar, dest)
        local_events = count_local_events(dest, pod.shard)
        if local_events != status["row_total"]:
            raise RuntimeError(f"event count mismatch for shard {pod.shard}: {local_events} != {status['row_total']}")
        manifest["pods"].append(
            {
                "pod_id": pod.pod_id,
                "shard": pod.shard,
                "status": status,
                "archive": str(local_tar),
                "archive_sha256": digest,
                "archive_size": local_tar.stat().st_size,
                "local_events": local_events,
            }
        )
        log(f"verified shard {pod.shard}: archive={local_tar} events={local_events}")

    manifest["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    manifest_path = dest / "hkex_ner_runpod_download_manifest.json"
    dest.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"wrote manifest {manifest_path}")

    if args.delete_pods:
        for pod in pods:
            delete_pod(pod.pod_id)
    else:
        log("delete-pods disabled; leaving pods running")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pods", required=True, help="Comma-separated pod_id:shard entries")
    parser.add_argument(
        "--dest",
        default="/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived/HKEX_NER",
    )
    parser.add_argument("--download-dir", default="/tmp/hkex_ner_runpod_downloads")
    parser.add_argument("--poll-seconds", type=int, default=300)
    parser.add_argument("--token-env", default="HKEX_JUPYTER_TOKEN")
    parser.add_argument("--delete-pods", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())

