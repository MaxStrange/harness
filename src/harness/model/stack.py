"""What is resident on the model machine, and swapping it around generation skills.

The machine has one pool of memory (the EVO X3's 120 GiB GTT) shared by the
llama.cpp router's models and the generation service. A generation skill asks
for what it needs (``acquire(["image"])``); the stack unloads just enough of
what is resident to fit it, lowest priority first, and loads it. Everything it
unloaded is remembered, and ``restore()`` brings it back, unloading generation
components again only as far as needed. The agent calls ``restore()`` before
the main model's next call, so the main model picks up the skill's result as if
it had never left.

Planning is a pure function (:func:`plan`) over declared sizes, so it is
predictable and testable; the declared sizes come from measurements (see the
``models.stack`` config).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import httpx

from harness.config import ConfigError, Endpoint, StackComponent, StackConfig
from harness.model.types import CancelToken, ModelCancelled

log = logging.getLogger(__name__)

Progress = Callable[[str], None]
POLL_S = 1.0


class StackError(Exception):
    """The stack cannot provide what was asked (too big, a load failed, a server is down)."""


@dataclass
class Plan:
    unload: list[str]
    load: list[str]

    @property
    def empty(self) -> bool:
        return not self.unload and not self.load


def plan(
    need: Iterable[str],
    resident: Iterable[str],
    components: dict[str, StackComponent],
    budget_gib: float,
    pinned: Iterable[str] = (),
) -> Plan:
    """What to unload (in order) and load (in order) so that ``need`` is resident within budget.

    Components not in ``need`` are unloaded lowest priority first (the biggest first among
    equals) until everything fits; an exclusive component in ``need`` unloads everything else.
    ``pinned`` components are never unloaded, even if the result is then over budget.
    """
    need = list(dict.fromkeys(need))
    unknown = [n for n in need if n not in components]
    if unknown:
        raise StackError(f"unknown stack component(s): {', '.join(unknown)}")
    needed_gib = sum(components[n].gib for n in need)
    if needed_gib > budget_gib:
        raise StackError(
            f"{' + '.join(need)} need {needed_gib:g} GiB together; the budget is {budget_gib:g} GiB"
        )
    resident = [r for r in resident if r in components]
    exclusive = any(components[n].exclusive for n in need) or any(
        components[r].exclusive for r in resident if r not in need
    )
    pinned = set(pinned)
    others = sorted(
        (r for r in resident if r not in need and r not in pinned),
        key=lambda r: (components[r].priority, -components[r].gib),
    )
    kept = set(resident) | set(need)
    unload: list[str] = []
    for name in others:
        if not exclusive and sum(components[k].gib for k in kept) <= budget_gib:
            break
        unload.append(name)
        kept.discard(name)
    load = sorted((n for n in need if n not in resident), key=lambda n: -components[n].priority)
    return Plan(unload, load)


class ModelStack:
    def __init__(self, config: StackConfig, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self.components = config.components
        self._clients: dict[str, httpx.Client] = {}
        self._transport = transport
        self._lock = threading.RLock()
        self._evicted: list[str] = []  # what acquire() unloaded and restore() brings back
        self._loaded: list[str] = []  # what acquire() loaded

    # -- state ----------------------------------------------------------------------

    @property
    def dirty(self) -> bool:
        """Something was swapped for a skill and has not been restored yet."""
        return bool(self._evicted or self._loaded)

    def resident(self) -> set[str]:
        """Components loaded right now, asked of the servers (one request per server)."""
        loaded: set[str] = set()
        routers: dict[str, dict[str, str]] = {}
        services: dict[str, dict[str, bool]] = {}
        for key, comp in self.components.items():
            if comp.kind == "router":
                if comp.url not in routers:
                    routers[comp.url] = self._router_states(comp)
                if routers[comp.url].get(comp.name) == "loaded":
                    loaded.add(key)
            else:
                if comp.url not in services:
                    services[comp.url] = self._service_states(comp)
                if services[comp.url].get(comp.name):
                    loaded.add(key)
        return loaded

    def describe(self) -> str:
        """One line for status displays: what is loaded and how much of the budget it uses."""
        try:
            resident = self.resident()
        except StackError as exc:
            return f"stack unknown: {exc}"
        used = sum(self.components[r].gib for r in resident)
        names = ", ".join(sorted(resident)) or "nothing"
        return f"{names} loaded ({used:g} of {self.config.budget_gib:g} GiB)"

    # -- swapping ---------------------------------------------------------------------

    def acquire(
        self,
        need: Iterable[str],
        *,
        progress: Progress | None = None,
        cancel: CancelToken | None = None,
    ) -> None:
        """Make ``need`` resident, unloading what it takes; remember it all for :meth:`restore`."""
        need = list(need)
        with self._lock:
            resident = self.resident()
            steps = plan(need, resident, self.components, self.config.budget_gib)
            for name in steps.unload:
                self._unload(name, progress)
                if name not in self._loaded and name not in self._evicted:
                    self._evicted.append(name)
                if name in self._loaded:
                    self._loaded.remove(name)
            for name in steps.load:
                self._load(name, progress, cancel)
                if name in self._evicted:
                    self._evicted.remove(name)
                elif name not in self._loaded:
                    self._loaded.append(name)

    def restore(
        self, *, progress: Progress | None = None, cancel: CancelToken | None = None
    ) -> None:
        """Bring back what :meth:`acquire` unloaded, making room by unloading what it loaded."""
        with self._lock:
            if not self.dirty:
                return
            evicted, self._evicted = self._evicted, []
            loaded, self._loaded = self._loaded, []
            try:
                resident = self.resident()
                # Only what was loaded for the skill may make room: everything else was there
                # before, alongside what is coming back, so that state is known to fit.
                pinned = set(resident) - set(loaded)
                steps = plan(evicted, resident, self.components, self.config.budget_gib, pinned)
                for name in steps.unload:
                    self._unload(name, progress)
                for name in steps.load:
                    self._load(name, progress, cancel)
            except BaseException:
                # Keep the bookkeeping so the next restore (the end of the turn) tries again.
                self._evicted, self._loaded = evicted, loaded
                raise

    # -- servers ----------------------------------------------------------------------

    def _client(self, comp: StackComponent) -> httpx.Client:
        client = self._clients.get(comp.url)
        if client is None:
            headers = {"Content-Type": "application/json"}
            if comp.api_key_file:
                try:
                    key = Endpoint(
                        base_url=comp.url, api_key_file=comp.api_key_file
                    ).resolve_api_key()
                except ConfigError as exc:
                    raise StackError(str(exc)) from exc
                headers["Authorization"] = f"Bearer {key}"
            client = httpx.Client(
                base_url=comp.url, headers=headers, timeout=30.0, transport=self._transport
            )
            self._clients[comp.url] = client
        return client

    def _request(self, comp: StackComponent, method: str, path: str, **kwargs) -> dict:
        try:
            response = self._client(comp).request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise StackError(f"{comp.url}{path}: {exc.__class__.__name__}: {exc}") from exc
        if response.status_code >= 400:
            detail = response.text[:300]
            raise StackError(f"{comp.url}{path}: HTTP {response.status_code}: {detail}")
        try:
            return response.json()
        except ValueError as exc:
            raise StackError(f"{comp.url}{path}: not JSON: {response.text[:200]}") from exc

    def _router_states(self, comp: StackComponent) -> dict[str, str]:
        data = self._request(comp, "GET", "/models")
        states = {}
        for model in data.get("data", []):
            status = model.get("status")
            value = status.get("value") if isinstance(status, dict) else status
            states[model.get("id")] = value or "loaded"  # a plain server reports no status
        return states

    def _service_states(self, comp: StackComponent) -> dict[str, bool]:
        data = self._request(comp, "GET", "/health")
        return {
            name: bool(info.get("loaded"))
            for name, info in (data.get("components") or {}).items()
            if isinstance(info, dict)
        }

    def _unload(self, key: str, progress: Progress | None) -> None:
        comp = self.components[key]
        _say(progress, f"Unloading {comp.name} to make room...")
        log.info("stack: unloading %s (%s)", key, comp.name)
        if comp.kind == "router":
            self._request(comp, "POST", "/models/unload", json={"model": comp.name})
            self._wait(key, lambda: self._router_states(comp).get(comp.name) != "loaded", 120)
        else:
            self._request(comp, "POST", "/unload", json={"component": comp.name}, timeout=300)

    def _load(self, key: str, progress: Progress | None, cancel: CancelToken | None) -> None:
        comp = self.components[key]
        _say(progress, f"Loading {comp.name}...")
        log.info("stack: loading %s (%s)", key, comp.name)
        started = time.monotonic()
        if comp.kind == "router":
            self._request(comp, "POST", "/models/load", json={"model": comp.name})

            def ready() -> bool:
                state = self._router_states(comp).get(comp.name)
                if state in ("failed", "error"):
                    raise StackError(f"the router failed to load {comp.name}")
                return state == "loaded"

            self._wait(key, ready, comp.load_timeout_s, cancel)
        else:
            self._request(
                comp, "POST", "/load", json={"component": comp.name}, timeout=comp.load_timeout_s
            )
        log.info("stack: %s loaded in %.0fs", key, time.monotonic() - started)

    def _wait(
        self,
        key: str,
        done: Callable[[], bool],
        timeout_s: float,
        cancel: CancelToken | None = None,
    ) -> None:
        deadline = time.monotonic() + timeout_s
        while not done():
            if cancel is not None and cancel.cancelled:
                raise ModelCancelled()
            if time.monotonic() > deadline:
                raise StackError(f"{key} did not finish loading or unloading in {timeout_s:g}s")
            time.sleep(POLL_S)

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()


def _say(progress: Progress | None, text: str) -> None:
    if progress is not None:
        try:
            progress(text)
        except Exception:  # noqa: BLE001 - progress is cosmetic
            log.debug("stack progress callback failed", exc_info=True)
