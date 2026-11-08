// pages.h - the tabs of the main window and what they share.
#pragma once
#include "common.h"

enum Page { kPagePlay = 0, kPageInstall = 1, kPageMods = 2, kPageControls = 3 };

INT_PTR CALLBACK PlayProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp);
INT_PTR CALLBACK InstallProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp);
INT_PTR CALLBACK ModsProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp);
INT_PTR CALLBACK ControlsProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp);

void ShowPage(int page);
void RefreshAll();               // re-reads the port folder (every tab)
void RefreshPlay();
void RefreshInstall();
void PreselectInstall(int ed);   // Install tab with that edition ticked
bool LicenseAccepted(HWND owner);   // shows the license window unless it was accepted before (launcher.ini)
void PlayGame();
void OnGameExit(DWORD code);
std::wstring EditionName(int ed);
std::wstring RelPath(const std::wstring& full);   // relative to the port folder, for display
int Message(HWND owner, UINT flags, const std::wstring& text);   // MessageBox with the launcher's title
// Static text in red (errors) on a themed tab page: WM_CTLCOLORSTATIC helper.
INT_PTR ColorStatic(HWND dlg, WPARAM wp, LPARAM lp, int id, COLORREF color);
