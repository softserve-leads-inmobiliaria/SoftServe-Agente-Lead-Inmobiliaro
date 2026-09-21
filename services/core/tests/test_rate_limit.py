"""Tests de A-2: el límite de 10 solicitudes por minuto y por modelo (DT-6), con reloj simulado."""
import asyncio

import pytest

from app.rate_limit import RateLimited, RateLimiter, RateLimits


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def limiter(clock, max_calls=10, per_seconds=60.0):
    return RateLimiter(max_calls, per_seconds, clock=clock, sleep=clock.sleep)


def run(coro):
    return asyncio.run(coro)


def test_calls_under_the_limit_go_through_without_waiting():
    clock = Clock()

    async def go():
        lim = limiter(clock)
        return [await lim.acquire(max_wait=5) for _ in range(10)]

    assert run(go()) == [0.0] * 10 and clock.slept == []


def test_the_call_over_the_limit_waits_until_the_oldest_one_leaves_the_window():
    clock = Clock()

    async def go():
        lim = limiter(clock)
        for _ in range(10):
            await lim.acquire(max_wait=100)
        clock.now += 20  # 40 s para que salga la primera
        return await lim.acquire(max_wait=100)

    assert run(go()) == pytest.approx(40.0) and clock.slept == [pytest.approx(40.0)]


def test_it_gives_up_when_the_wait_would_exceed_the_maximum():
    clock = Clock()

    async def go():
        lim = limiter(clock)
        for _ in range(10):
            await lim.acquire(max_wait=100)
        with pytest.raises(RateLimited):
            await lim.acquire(max_wait=15)

    run(go())
    assert clock.slept == []  # no espera para luego rendirse: decide antes


def test_a_rejected_call_does_not_consume_quota():
    clock = Clock()

    async def go():
        lim = limiter(clock, max_calls=1, per_seconds=60)
        await lim.acquire(max_wait=0)
        with pytest.raises(RateLimited):
            await lim.acquire(max_wait=1)
        clock.now += 60
        return await lim.acquire(max_wait=0)  # sigue habiendo cupo justo al salir la primera

    assert run(go()) == 0.0


def test_the_window_slides():
    clock = Clock()

    async def go():
        lim = limiter(clock, max_calls=2, per_seconds=10)
        await lim.acquire(0)
        clock.now += 6
        await lim.acquire(0)
        clock.now += 5  # la primera ya salió, la segunda no
        await lim.acquire(0)
        with pytest.raises(RateLimited):
            await lim.acquire(0)

    run(go())


def test_waiters_are_served_in_arrival_order():
    clock = Clock()
    order = []

    async def call(lim, name):
        await lim.acquire(max_wait=1000)
        order.append(name)

    async def go():
        lim = limiter(clock, max_calls=1, per_seconds=10)
        await lim.acquire(0)
        await asyncio.gather(*(call(lim, n) for n in ("a", "b", "c")))

    run(go())
    assert order == ["a", "b", "c"]
    assert clock.now == pytest.approx(1030.0)  # cada uno esperó su ventana


def test_each_model_has_its_own_limit():
    clock = Clock()

    async def go():
        limits = RateLimits(1, 60, clock=clock, sleep=clock.sleep)
        await limits.for_model("chat").acquire(0)
        await limits.for_model("clasificador").acquire(0)  # otro modelo: cupo propio
        with pytest.raises(RateLimited):
            await limits.for_model("chat").acquire(0)
        assert limits.for_model("chat") is limits.for_model("chat")

    run(go())


@pytest.mark.parametrize("max_calls,per_seconds", [(0, 60), (-1, 60), (10, 0), (10, -5)])
def test_an_impossible_limit_is_rejected(max_calls, per_seconds):
    with pytest.raises(ValueError):
        RateLimiter(max_calls, per_seconds)
