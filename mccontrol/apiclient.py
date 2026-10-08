"""The client of the mccontrol API: one method per route, parsed JSON back.

This is the only module an interface imports from the package. It knows HTTP and the
routes in `api/openapi.json` (each method is named by its operationId), nothing of the
backend. A failure is an `ApiError(code, message, status)`; status 0 means the server could
not be reached.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

FINISHED = ("done", "failed", "cancelled")


class ApiError(Exception):
    def __init__(self, code, message, status):
        super().__init__(f"{message} ({code})")
        self.code, self.message, self.status = code, message, status


def _q(ref):
    return urllib.parse.quote(str(ref), safe="")


class Client:
    def __init__(self, base_url, timeout=30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def call(self, method, path, query=None, body=None, raw=False):
        """One request; the parsed JSON (or the text, with `raw`)."""
        query = {k: v for k, v in (query or {}).items() if v is not None}
        url = self.base_url + path + ("?" + urllib.parse.urlencode(query) if query else "")
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            try:
                err = json.loads(exc.read().decode("utf-8"))["error"]
                raise ApiError(err["code"], err["message"], exc.code) from None
            except (ValueError, KeyError, TypeError):
                raise ApiError("http", f"{method} {path} answered {exc.code}", exc.code) from None
        except (urllib.error.URLError, OSError) as exc:
            raise ApiError("unreachable", f"the mccontrol API at {self.base_url} cannot be reached: {exc}", 0) from None
        return text if raw else json.loads(text)

    # -- meta ----------------------------------------------------------------------------

    def openapi(self):
        return self.call("GET", "/openapi.json")

    def docs_page(self):
        return self.call("GET", "/docs", raw=True)

    # -- configuration and keys ----------------------------------------------------------

    def config(self):
        return self.call("GET", "/config")

    def set_config(self, **changes):
        return self.call("PATCH", "/config", body=changes)

    def key(self):
        return self.call("GET", "/key")

    def create_key(self, overwrite=False, seed=None):
        body = {"overwrite": overwrite}
        if seed is not None:
            body["seed"] = seed
        return self.call("POST", "/key", body=body)

    # -- devices -------------------------------------------------------------------------

    def devices(self, sort=None, width=None):
        return self.call("GET", "/devices", {"sort": sort, "width": width})

    def add_device(self, node, region, name="", notes="", key=None):
        return self.call("POST", "/devices", body={"node": node, "region": region, "name": name, "notes": notes, "key": key})

    def adopt(self, node, region, key=None, name=""):
        return self.call("POST", "/devices/adopt", body={"node": node, "region": region, "key": key, "name": name})

    def device(self, ref):
        return self.call("GET", f"/devices/{_q(ref)}")

    def edit_device(self, ref, **changes):
        return self.call("PATCH", f"/devices/{_q(ref)}", body=changes)

    def forget(self, ref):
        return self.call("DELETE", f"/devices/{_q(ref)}")

    def history(self, ref, limit=None):
        return self.call("GET", f"/devices/{_q(ref)}/history", {"limit": limit})

    def broadcast_history(self, limit=None):
        return self.call("GET", "/history", {"limit": limit})

    def reads(self, ref):
        return self.call("GET", f"/devices/{_q(ref)}/reads")

    def device_commands(self, ref):
        return self.call("GET", f"/devices/{_q(ref)}/commands")

    def commands(self):
        return self.call("GET", "/commands")

    def check_command(self, command, device=None):
        return self.call("GET", "/commands/check", {"command": command, "device": device})

    # -- tags and regions ----------------------------------------------------------------

    def tags(self):
        return self.call("GET", "/tags")

    def set_tags(self, nodes):
        return self.call("PUT", "/tags", body={"nodes": list(nodes)})

    def tag(self, devices=None, region=None):
        return self.call("POST", "/tags", body={"devices": list(devices or []), "region": region or ""})

    def untag(self, devices=None, region=None):
        return self.call("DELETE", "/tags", {"devices": ",".join(devices) if devices else None, "region": region})

    def invert_tags(self):
        return self.call("POST", "/tags/invert", body={})

    def regions(self):
        return self.call("GET", "/regions")

    # -- sets and shortcuts --------------------------------------------------------------

    def sets(self):
        return self.call("GET", "/sets")

    def save_set(self, name, steps):
        return self.call("PUT", f"/sets/{_q(name)}", body={"steps": list(steps)})

    def remove_set(self, name):
        return self.call("DELETE", f"/sets/{_q(name)}")

    def shortcuts(self):
        return self.call("GET", "/shortcuts")

    def set_shortcuts(self, shortcuts):
        return self.call("PUT", "/shortcuts", body={"shortcuts": list(shortcuts)})

    # -- help ----------------------------------------------------------------------------

    def help_index(self):
        return self.call("GET", "/help")

    def help_topic(self, name):
        return self.call("GET", f"/help/{_q(name)}")

    # -- jobs ----------------------------------------------------------------------------

    def jobs(self):
        return self.call("GET", "/jobs")

    def job(self, job_id):
        return self.call("GET", f"/jobs/{_q(job_id)}")

    def cancel_job(self, job_id):
        return self.call("DELETE", f"/jobs/{_q(job_id)}")

    def _start(self, verb, **body):
        return self.call("POST", f"/jobs/{verb}", body={k: v for k, v in body.items() if v is not None})

    def start_send(self, command, devices=None, tagged=None, region=None, dry_run=None, confirm=None, wait_secs=None):
        return self._start("send", command=command, devices=devices, tagged=tagged, region=region,
                           dry_run=dry_run, confirm=confirm, wait_secs=wait_secs)

    def start_run_set(self, name, arg=None, devices=None, tagged=None, region=None, dry_run=None, confirm=None, wait_secs=None):
        return self._start("run-set", set=name, arg=arg, devices=devices, tagged=tagged, region=region,
                           dry_run=dry_run, confirm=confirm, wait_secs=wait_secs)

    def start_discover(self, region, wait_secs=None, add=None):
        return self._start("discover", region=region, wait_secs=wait_secs, add=add)

    def start_refresh_names(self, devices=None, tagged=None, wait_secs=None):
        return self._start("refresh-names", devices=devices, tagged=tagged, wait_secs=wait_secs)

    def start_refresh_fleet(self, devices=None, tagged=None):
        return self._start("refresh-fleet", devices=devices, tagged=tagged)

    def start_statistics(self, device, range=None, interval=None, live=None):
        return self._start("statistics", device=device, range=range, interval=interval, live=live)

    def start_test(self):
        return self._start("test")

    def start_register(self):
        return self._start("register")

    def wait_job(self, job_id, on_progress=None, poll=0.2, timeout=None):
        """Poll a job until it is no longer running and return its record.

        `on_progress(job)` is called after every poll; returning True cancels the job (the
        way a progress callback's True stops a wait), and the wait goes on until the server
        says it has stopped. `timeout` seconds without finishing cancels the job and raises
        an ApiError, so nothing is left running behind the caller."""
        start = time.monotonic()
        cancelled = False
        while True:
            job = self.job(job_id)
            if job["status"] in FINISHED:
                return job
            if on_progress and on_progress(job) and not cancelled:
                cancelled = self._cancel_quietly(job_id)
            if timeout is not None and time.monotonic() - start > timeout:
                self._cancel_quietly(job_id)
                raise ApiError("timeout", f"job {job_id} still running after {timeout} s; cancelled", 0)
            time.sleep(poll)

    def _cancel_quietly(self, job_id):
        """Cancel; a job that finished in the meantime is not an error here."""
        try:
            self.cancel_job(job_id)
        except ApiError as exc:
            if exc.code != "finished":
                raise
        return True
