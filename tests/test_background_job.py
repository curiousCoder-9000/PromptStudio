"""The scaffolding every background job was copying by hand (S6).

The duplication was mostly cosmetic, but two things in it were not, and both
are asserted here rather than left to each manager to get right again:

* **The lease must be released even when the runner raises.** `BatchPromptManager`
  shipped without a `try/finally`, so an exception escaping the loop left
  `running=True` forever — the job could never be restarted without bouncing the
  server — and once leases landed it would also have stranded Ollama.
* **The singleton must be per subclass.** A `_instance` attribute declared only
  on the base is shared by every subclass, so the second job type to call `get()`
  would be handed the first one's instance. Nothing about that fails loudly.
"""

import threading
import time

import pytest

from promptstudio.jobs import COMFY, LEASES, OLLAMA, BackgroundJob


@pytest.fixture(autouse=True)
def _clean_leases():
    LEASES.reset()
    yield
    LEASES.reset()


class Toy(BackgroundJob):
    """Minimal subclass: one resource, one extra status key."""

    resources = (OLLAMA,)
    owner = "toy"

    def _idle_status(self):
        return {**super()._idle_status(), "widgets": 0}

    def run(self, body):
        return self._start(body, total=1)


class OtherToy(BackgroundJob):
    resources = (COMFY,)
    owner = "other_toy"


def _wait_idle(job, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not job.is_running():
            return True
        time.sleep(0.01)
    return False


# ── singleton ────────────────────────────────────────────────────────


def test_get_returns_the_same_instance():
    assert Toy.get() is Toy.get()


def test_each_subclass_gets_its_own_singleton():
    """A `_instance` on the base alone would hand OtherToy the Toy."""
    assert Toy.get() is not OtherToy.get()
    assert isinstance(OtherToy.get(), OtherToy)


# ── status ───────────────────────────────────────────────────────────


def test_idle_job_is_not_running():
    assert Toy().is_running() is False


def test_get_status_returns_a_copy():
    job = Toy()
    status = job.get_status()
    status["running"] = "tampered"
    assert job.get_status()["running"] is False


def test_subclass_status_keys_are_present_when_idle():
    assert Toy().get_status()["widgets"] == 0


def test_cancel_on_an_idle_job_is_a_no_op():
    assert Toy().cancel() is False


# ── the lease ────────────────────────────────────────────────────────


def test_the_lease_is_held_for_the_duration_of_the_run():
    job = Toy()
    entered, release = threading.Event(), threading.Event()

    def body():
        entered.set()
        release.wait(5)

    assert job.run(body) is True
    assert entered.wait(5)
    assert LEASES.holder(OLLAMA) == "toy"
    release.set()
    assert _wait_idle(job)
    assert LEASES.holder(OLLAMA) is None


def test_start_is_refused_when_the_resource_is_held_and_names_the_holder():
    LEASES.acquire([OLLAMA], "some_other_job")
    job = Toy()
    assert job.run(lambda: None) is False
    assert "some_other_job" in job.last_refusal


def test_start_is_refused_while_already_running():
    job = Toy()
    release = threading.Event()
    assert job.run(lambda: release.wait(5)) is True
    try:
        assert job.run(lambda: None) is False
        assert job.last_refusal
    finally:
        release.set()
        assert _wait_idle(job)


def test_a_refused_start_does_not_strand_the_lease():
    """The self-collision path acquires before it notices it is already running."""
    job = Toy()
    release = threading.Event()
    assert job.run(lambda: release.wait(5)) is True
    job.run(lambda: None)
    release.set()
    assert _wait_idle(job)
    assert LEASES.holder(OLLAMA) is None


def test_a_refused_duplicate_start_leaves_the_running_job_holding_its_lease():
    """The bug every hand-rolled manager shipped.

    Acquire-then-check succeeds on the second call — re-acquiring your own
    lease is legal, and it has to be, or a job could not restart its inner
    loop. The `release()` that follows the "already running" check then drops
    the lease *out from under the job that is still running*, and the next
    contender for the same resource sails through. Two jobs on one Ollama, from
    a duplicate button press.
    """
    job = Toy()
    entered, release = threading.Event(), threading.Event()

    def body():
        entered.set()
        release.wait(5)

    assert job.run(body) is True
    assert entered.wait(5)
    try:
        assert job.run(lambda: None) is False
        assert LEASES.holder(OLLAMA) == "toy", "the running job still needs it"
        assert LEASES.acquire([OLLAMA], "an_unrelated_job") == OLLAMA
    finally:
        release.set()
        assert _wait_idle(job)


# ── the crash ────────────────────────────────────────────────────────


def test_a_crash_in_the_runner_releases_the_lease():
    job = Toy()

    def boom():
        raise RuntimeError("runner exploded")

    assert job.run(boom) is True
    assert _wait_idle(job)
    assert LEASES.holder(OLLAMA) is None


def test_a_crash_in_the_runner_clears_running():
    """Without this the job is unrestartable until the server is bounced."""
    job = Toy()
    assert job.run(lambda: (_ for _ in ()).throw(RuntimeError("boom"))) is True
    assert _wait_idle(job)
    assert job.get_status()["running"] is False


def test_a_crash_is_recorded_in_status_rather_than_swallowed():
    job = Toy()

    def boom():
        raise RuntimeError("runner exploded")

    assert job.run(boom) is True
    assert _wait_idle(job)
    assert "runner exploded" in (job.get_status()["error"] or "")


def test_a_job_can_be_restarted_after_a_crash():
    job = Toy()
    assert job.run(lambda: (_ for _ in ()).throw(RuntimeError("boom"))) is True
    assert _wait_idle(job)
    assert job.run(lambda: None) is True
    assert _wait_idle(job)


# ── cancel ───────────────────────────────────────────────────────────


def test_cancel_sets_the_event_the_runner_polls():
    job = Toy()
    entered, saw_cancel = threading.Event(), threading.Event()

    def body():
        entered.set()
        for _ in range(500):
            if job.cancel_requested():
                saw_cancel.set()
                return
            time.sleep(0.01)

    assert job.run(body) is True
    assert entered.wait(5)
    assert job.cancel() is True
    assert saw_cancel.wait(5)
    assert _wait_idle(job)


def test_cancel_is_visible_in_status_while_the_job_runs():
    job = Toy()
    entered, release = threading.Event(), threading.Event()

    def body():
        entered.set()
        release.wait(5)

    job.run(body)
    assert entered.wait(5)
    job.cancel()
    assert job.get_status()["cancel_requested"] is True
    release.set()
    assert _wait_idle(job)


def test_cancel_requested_clears_once_the_job_is_over():
    """Otherwise the next status poll reports a stale "stopping…" forever."""
    job = Toy()
    entered, release = threading.Event(), threading.Event()

    def body():
        entered.set()
        release.wait(5)

    job.run(body)
    assert entered.wait(5)
    job.cancel()
    release.set()
    assert _wait_idle(job)
    assert job.get_status()["cancel_requested"] is False


def test_finished_at_is_stamped_when_the_job_ends():
    job = Toy()
    assert job.run(lambda: None) is True
    assert _wait_idle(job)
    assert job.get_status()["finished_at"]
