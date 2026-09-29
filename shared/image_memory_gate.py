#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Coordinate local chat requests with a single Mac image worker."""
from __future__ import annotations

import threading
import time


class ImageMemoryGate:
    def __init__(self, *, clock=time.monotonic, lease_s: float = 1200):
        self.clock = clock
        self.lease_s = lease_s
        self._condition = threading.Condition()
        self._owner = ""
        self._expires = 0.0
        self._chats = 0

    def _expire_locked(self):
        if self._owner and self.clock() >= self._expires:
            self._owner = ""
            self._expires = 0.0
            self._condition.notify_all()

    def enter_chat(self, timeout: float = 120) -> bool:
        deadline = self.clock() + timeout
        with self._condition:
            while True:
                self._expire_locked()
                if not self._owner:
                    self._chats += 1
                    return True
                remaining = deadline - self.clock()
                if remaining <= 0:
                    return False
                self._condition.wait(min(remaining, max(0.1, self._expires - self.clock())))

    def leave_chat(self):
        with self._condition:
            self._chats = max(0, self._chats - 1)
            self._condition.notify_all()

    def reserve_image(self, job_id: str, timeout: float = 120) -> bool:
        deadline = self.clock() + timeout
        with self._condition:
            self._expire_locked()
            if self._owner:
                return False
            self._owner = job_id
            self._expires = self.clock() + self.lease_s
            while self._chats:
                remaining = deadline - self.clock()
                if remaining <= 0:
                    self._owner = ""
                    self._expires = 0.0
                    self._condition.notify_all()
                    return False
                self._condition.wait(remaining)
            return True

    def release_image(self, job_id: str):
        with self._condition:
            if self._owner == job_id:
                self._owner = ""
                self._expires = 0.0
                self._condition.notify_all()

    def status(self):
        with self._condition:
            self._expire_locked()
            return {"image_job": self._owner, "active_chats": self._chats,
                    "lease_remaining_s": max(0, int(self._expires - self.clock()))}
