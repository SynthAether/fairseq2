# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from time import perf_counter
from typing import Any, final

import torch
from torch.profiler import (
    ProfilerActivity,
    profile,
    schedule,
    tensorboard_trace_handler,
)
from typing_extensions import Self, override

from fairseq2.error import InvalidOperationError
from fairseq2.gang import Gang
from fairseq2.typing import Device


class Profiler(ABC):
    @abstractmethod
    def start(self) -> None:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...

    @abstractmethod
    def step(self) -> None:
        """Move to the next profiling step."""

    @abstractmethod
    def __enter__(self) -> Self:
        ...

    @abstractmethod
    def __exit__(self, *exc: Any) -> None:
        ...


class AbstractProfiler(Profiler):
    @override
    def __enter__(self) -> Self:
        self.start()

        return self

    @override
    def __exit__(self, *exc: Any) -> None:
        self.stop()


@final
class TorchProfiler(AbstractProfiler):
    """Represents a convenience wrapper for :class:`profile`."""

    _profile: profile

    def __init__(
        self,
        skip_first: int,
        active: int,
        log_dir: Path,
        gang: Gang,
    ) -> None:
        """
        :param skip_first: The number of steps to skip at the beginning of the
            job. The last skipped step will be treated as the warm-up step.
        :param active: The number of steps with active recording.
        :param log_dir: The TensorBoard log directory under which to store the
            trace files.
        :param gang: The associated gang.
        """
        if skip_first <= 0:
            raise ValueError("`skip_first` must be greater than zero.")

        activities = [ProfilerActivity.CPU, ProfilerActivity.CUDA]

        schedule_ = schedule(
            skip_first=skip_first - 1, wait=0, warmup=1, active=active, repeat=1
        )

        trace_handler = tensorboard_trace_handler(
            str(log_dir), worker_name=f"rank_{gang.rank}", use_gzip=True
        )

        self._profile = profile(
            activities=activities,
            schedule=schedule_,
            on_trace_ready=trace_handler,
            record_shapes=True,
            with_stack=True,
        )

    @override
    def start(self) -> None:
        if self._profile is not None:
            self._profile.start()

    @override
    def stop(self) -> None:
        if self._profile is not None:
            self._profile.stop()

    @override
    def step(self) -> None:
        if self._profile is not None:
            self._profile.step()

    @property
    def wrapped_profile(self) -> profile:
        return self._profile


@final
class NoopProfiler(AbstractProfiler):
    @override
    def start(self) -> None:
        pass

    @override
    def stop(self) -> None:
        pass

    @override
    def step(self) -> None:
        pass


@final
class Stopwatch:
    """Measures elapsed execution time."""

    _start_time: float | None
    _device: Device | None

    def __init__(self, *, start: bool = False, device: Device | None = None) -> None:
        """
        :param start: If ``True``, starts the stopwatch immediately.
        :param device: If not ``None``, waits for all operations on ``device``
            to complete before measuring the elapsed time. Note that this can
            have a negative impact on the runtime performance if not used
            carefully.
        """
        self._start_time = None

        if device is not None:
            if device.type != "cpu" and device.type != "cuda":
                raise ValueError(
                    f"The type of `device` must be `cpu` or `cuda`, but is `{device.type}` instead."
                )

        self._device = device

        if start:
            self.start()

    def start(self) -> None:
        """Start the stopwatch."""
        if self._start_time is not None:
            raise InvalidOperationError("The stopwatch is already running.")

        self._sync_device()

        self._start_time = perf_counter()

    def stop(self) -> None:
        """Stop the stopwatch."""
        self._start_time = None

    def reset(self) -> None:
        """Reset the stopwatch."""
        if self._start_time is None:
            raise InvalidOperationError("The stopwatch is not running.")

        self._sync_device()

        self._start_time = perf_counter()

    def get_elapsed_time(self) -> float:
        """Return the elapsed time since the last :meth:`start` or :meth:`reset`."""
        if self._start_time is None:
            return 0.0

        self._sync_device()

        return perf_counter() - self._start_time

    def _sync_device(self) -> None:
        if self._device is not None and self._device.type == "cuda":
            torch.cuda.synchronize(self._device)

    def __enter__(self) -> Self:
        if self._start_time is None:
            self.start()

        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    @property
    def is_running(self) -> bool:
        """Return ``True`` if the stopwatch is running."""
        return self._start_time is not None
