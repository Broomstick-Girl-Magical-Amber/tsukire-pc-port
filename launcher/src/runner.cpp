// runner.cpp - runs one script at a time without a visible console and streams its output to the window.
//
// - stdout and stderr go to one pipe; stdin is NUL (a question that slipped through reads an empty answer = "no").
// - The process and everything it starts (recomp, cmake, ninja, clang-cl, python...) go into a job object: Cancel and
//   closing the launcher end the whole tree. Processes elevated with UAC (only the VS Build Tools flow) are not in it.
// - Each line is decoded as UTF-8 (install_requirements.ps1 switches the console to UTF-8) and, if it is not valid
//   UTF-8, with the console code page (OEM; recomp_tsukihime.ps1 started alone does not switch it).
// - A carriage return without a line feed (a progress line that rewrites itself) keeps only the last version.
// - Everything is also appended, as UTF-8, to the log file of the run.
#include "common.h"

#include <atomic>

namespace {

struct Run {
    HWND owner = nullptr;
    HANDLE process = nullptr;
    HANDLE job = nullptr;
    HANDLE readPipe = nullptr;
    HANDLE log = INVALID_HANDLE_VALUE;
    HANDLE reader = nullptr;
    HANDLE waiter = nullptr;
    std::atomic<bool> cancelled{false};
};

Run* g_run = nullptr;   // guarded by g_lock (Cancel from the UI thread, cleanup in the waiter thread)
SRWLOCK g_lock = SRWLOCK_INIT;
std::atomic<bool> g_busy{false};

void WriteLog(Run* r, const std::string& bytes) {
    if (r->log == INVALID_HANDLE_VALUE) return;
    DWORD w = 0;
    WriteFile(r->log, bytes.data(), DWORD(bytes.size()), &w, nullptr);
}

std::wstring Decode(const std::string& line) {
    if (line.empty()) return L"";
    int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, line.data(), int(line.size()), nullptr, 0);
    UINT cp = CP_UTF8;
    if (n <= 0) {
        cp = CP_OEMCP;
        n = MultiByteToWideChar(cp, 0, line.data(), int(line.size()), nullptr, 0);
    }
    std::wstring w(size_t(n), L'\0');
    MultiByteToWideChar(cp, 0, line.data(), int(line.size()), &w[0], n);
    return w;
}

void Emit(Run* r, std::string line) {
    size_t cr = line.find_last_of('\r');   // "progress\rprogress\r...": the last version only
    if (cr != std::string::npos) {
        std::string tail = line.substr(cr + 1);
        if (tail.empty()) {   // ends in \r: take the last non-empty piece
            line.erase(cr);
            size_t prev = line.find_last_of('\r');
            if (prev != std::string::npos) line = line.substr(prev + 1);
        } else {
            line = tail;
        }
    }
    std::wstring w = Decode(line);
    WriteLog(r, WideToUtf8(w) + "\r\n");
    PostMessageW(r->owner, WM_APP_LINE, 0, LPARAM(new std::wstring(std::move(w))));
}

DWORD WINAPI ReaderThread(void* p) {
    Run* r = static_cast<Run*>(p);
    std::string pending;
    char buf[8192];
    DWORD got = 0;
    while (ReadFile(r->readPipe, buf, sizeof buf, &got, nullptr) && got) {
        pending.append(buf, got);
        size_t start = 0, nl;
        while ((nl = pending.find('\n', start)) != std::string::npos) {
            std::string line = pending.substr(start, nl - start);
            if (!line.empty() && line.back() == '\r') line.pop_back();
            Emit(r, line);
            start = nl + 1;
        }
        pending.erase(0, start);
    }
    if (!pending.empty()) Emit(r, pending);
    return 0;
}

DWORD WINAPI WaiterThread(void* p) {
    Run* r = static_cast<Run*>(p);
    WaitForSingleObject(r->process, INFINITE);
    // A child that outlived the script could keep the pipe open: give the reader a moment, then stop it.
    if (WaitForSingleObject(r->reader, 3000) == WAIT_TIMEOUT) {
        CancelSynchronousIo(r->reader);
        WaitForSingleObject(r->reader, 2000);
    }
    DWORD code = 1;
    GetExitCodeProcess(r->process, &code);
    TerminateJobObject(r->job, 1);   // nothing of the run is left behind
    bool cancelled = r->cancelled.load();
    if (r->log != INVALID_HANDLE_VALUE) {
        WriteLog(r, cancelled ? "\r\n[launcher] cancelled\r\n" : "\r\n[launcher] exit code " + std::to_string(code) + "\r\n");
        CloseHandle(r->log);
    }
    CloseHandle(r->readPipe);
    CloseHandle(r->reader);
    CloseHandle(r->process);
    HWND owner = r->owner;
    AcquireSRWLockExclusive(&g_lock);
    CloseHandle(r->job);
    g_run = nullptr;
    ReleaseSRWLockExclusive(&g_lock);
    delete r;
    g_busy = false;
    PostMessageW(owner, WM_APP_DONE, WPARAM(code), cancelled ? 1 : 0);
    return 0;
}

}  // namespace

bool RunnerBusy() { return g_busy.load(); }

bool RunnerStart(HWND owner, const std::wstring& cmdline, const std::wstring& cwd, const std::wstring& logFile) {
    if (g_busy.exchange(true)) return false;
    Run* r = new Run;
    r->owner = owner;

    SECURITY_ATTRIBUTES sa{sizeof sa, nullptr, TRUE};
    HANDLE writePipe = nullptr;
    HANDLE nul = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, &sa, OPEN_EXISTING, 0, nullptr);
    if (!CreatePipe(&r->readPipe, &writePipe, &sa, 0) || nul == INVALID_HANDLE_VALUE) {
        if (nul != INVALID_HANDLE_VALUE) CloseHandle(nul);
        delete r;
        g_busy = false;
        return false;
    }
    SetHandleInformation(r->readPipe, HANDLE_FLAG_INHERIT, 0);

    if (!logFile.empty()) {
        r->log = CreateFileW(logFile.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_ALWAYS, 0, nullptr);
        WriteLog(r, "[launcher] " + WideToUtf8(cmdline) + "\r\n\r\n");
    }

    r->job = CreateJobObjectW(nullptr, nullptr);
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION li{};
    li.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    SetInformationJobObject(r->job, JobObjectExtendedLimitInformation, &li, sizeof li);

    STARTUPINFOW si{};
    si.cb = sizeof si;
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = nul;
    si.hStdOutput = writePipe;
    si.hStdError = writePipe;
    PROCESS_INFORMATION pi{};
    std::wstring cmd = cmdline;   // CreateProcessW may write to it
    BOOL ok = CreateProcessW(nullptr, &cmd[0], nullptr, nullptr, TRUE, CREATE_NO_WINDOW | CREATE_SUSPENDED, nullptr,
                             cwd.c_str(), &si, &pi);
    CloseHandle(writePipe);
    CloseHandle(nul);
    if (!ok) {
        DWORD e = GetLastError();
        if (r->log != INVALID_HANDLE_VALUE) CloseHandle(r->log);
        CloseHandle(r->readPipe);
        CloseHandle(r->job);
        delete r;
        g_busy = false;
        SetLastError(e);
        return false;
    }
    AssignProcessToJobObject(r->job, pi.hProcess);   // before it runs: its children are born inside the job
    ResumeThread(pi.hThread);
    CloseHandle(pi.hThread);
    r->process = pi.hProcess;
    AcquireSRWLockExclusive(&g_lock);
    g_run = r;
    ReleaseSRWLockExclusive(&g_lock);
    r->reader = CreateThread(nullptr, 0, ReaderThread, r, 0, nullptr);
    r->waiter = CreateThread(nullptr, 0, WaiterThread, r, 0, nullptr);
    CloseHandle(r->waiter);
    return true;
}

void RunnerCancel() {
    AcquireSRWLockExclusive(&g_lock);
    if (Run* r = g_run) {
        r->cancelled = true;
        TerminateJobObject(r->job, 1);
    }
    ReleaseSRWLockExclusive(&g_lock);
}
