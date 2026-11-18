#!/usr/bin/env python3
"""Read-only integration checks for Doc2PDF role isolation; requires a running API and seeded jobs.
Run: python data/seed.py && python data/verify_access.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("SEED_API_URL", "http://localhost:8000").rstrip("/")

def request(path, token=None, method="GET", payload=None):
    headers = {"Accept": "application/json"}
    if token: headers["Authorization"] = "Bearer " + token
    body = None if payload is None else json.dumps(payload).encode()
    if body is not None: headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        try: data = json.load(exc)
        except Exception: data = {}
        return exc.code, data

def main():
    try:
        tokens = {}
        for name in ("alice", "bob", "operator"):
            pwd = os.environ.get("SEED_PASSWORD_" + name.upper(), name + "123" if name != "operator" else "operator123")
            status, data = request("/auth/login", method="POST", payload={"username":name,"password":pwd})
            assert status == 200 and data.get("access_token"), f"login {name}: HTTP {status}"
            tokens[name] = data["access_token"]
        for name in ("alice", "bob"):
            status, data = request("/operator/jobs", tokens[name])
            assert status == 403, f"{name} can access operator endpoint: HTTP {status}"
            status, data = request("/jobs?limit=100", tokens[name])
            assert status == 200, f"list {name}: HTTP {status}"
            assert all(job["owner_id"] == name for job in data["items"]), f"{name} sees foreign jobs"
            print(f"PASS {name}: only own jobs; operator endpoint forbidden")
        status, data = request("/operator/jobs?limit=100", tokens["operator"])
        assert status == 200, f"operator list HTTP {status}"
        jobs = data["items"]
        for owner in ("alice", "bob"):
            foreign = next((j for j in jobs if j["owner_id"] == owner), None)
            if foreign is None:
                print(f"SKIP cross-user job check for {owner}: run seed first")
                continue
            other = "bob" if owner == "alice" else "alice"
            job_id = foreign["id"]
            for path in (f"/jobs/{job_id}", f"/jobs/{job_id}/attempts", f"/jobs/{job_id}/download"):
                status, _ = request(path, tokens[other])
                assert status in (403, 404), f"{other} can read {owner} job: {path} HTTP {status}"
            status, _ = request(f"/jobs/{job_id}/retry", tokens[other], method="POST")
            assert status in (403, 404), f"{other} can retry {owner} job: HTTP {status}"
            print(f"PASS {other}: cannot access or retry {owner} job")
        assert all(any(j["owner_id"] == u for j in jobs) for u in ("alice", "bob")), "Need seeded jobs for both users"
        print("PASS operator: can list both users; all access-control checks passed")
        return 0
    except (AssertionError, OSError, ValueError, KeyError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__": sys.exit(main())
