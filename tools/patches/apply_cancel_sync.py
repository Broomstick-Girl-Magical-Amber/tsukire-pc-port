#!/usr/bin/env python3
"""apply_cancel_sync.py - svcCancelSynchronization with the Horizon semantics (a notice is not lost).

In Horizon, if CancelSynchronization arrives when the thread has NOT entered WaitSynchronization yet, the notice is
kept and the NEXT wait returns right away with "cancelled" (0xEC01). The sdk depends on that: nn::os::WaitAny
(MultiWait) checks its user events, and if none is signaled it enters svcWaitSynchronization; a
SignalEvent from another thread wakes it with CancelSynchronization. If the SignalEvent falls right between the check
and the entry into the wait, only the kept notice saves it.

The framework cleared that notice when entering the wait (self->cancel = false), so in that race the thread stayed
asleep forever, with the event already signaled (and a SignalEvent on an already signaled event does not notify again).
In Tsukihime it looked like the prologue video/audio cutting off after a few seconds: the event thread of the
player stayed in WaitAny with ready outputs nobody took.

Now, as in Horizon: if there is a pending cancel when entering, it is consumed and 0xEC01 is returned (the sdk looks again).
Keeps CRLF and is idempotent.
Usage (from <root>\\framework):  private\\venv\\Scripts\\python ..\\tools\\patches\\apply_cancel_sync.py
"""
import sys
from pathlib import Path

KERNEL = Path("runtime/src/kernel.cpp")
MARK = "CancelSynchronization that arrived before"

OLD = """    KThread* self = t_self;
    self->cancel = false;
    g_sync_waiters.push_back(self);"""

NEW = """    KThread* self = t_self;
    // Horizon: a CancelSynchronization that arrived before this wait is kept and ends it at once (ResultCancelled).
    // nn::os::WaitAny checks its user-mode objects, then waits here; a SignalEvent in between cancels this thread.
    // Clearing the flag here lost that wakeup: the thread slept for good on an event that was already signaled.
    if (self->cancel) {
        self->cancel = false;
        return 0xEC01;
    }
    g_sync_waiters.push_back(self);"""


def load(path):
    raw = path.read_bytes().decode("utf-8")
    return raw.replace("\r\n", "\n"), "\r\n" in raw


def save(path, text, crlf):
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def main():
    if not KERNEL.exists():
        sys.exit(f"cannot find {KERNEL}: run this from <root>\\framework")
    text, crlf = load(KERNEL)
    if MARK in text:
        print(f"{KERNEL}: already applied, nothing to do")
        return
    if text.count(OLD) != 1:
        sys.exit("did not find (exactly once) the start of the wait in svc_wait_synchronization; the file changed")
    save(KERNEL, text.replace(OLD, NEW), crlf)
    print(f"{KERNEL}: CancelSynchronization is no longer lost ({'CRLF' if crlf else 'LF'})")


if __name__ == "__main__":
    main()