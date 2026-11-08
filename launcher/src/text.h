// text.h - interface language: English, or Spanish when Windows is in Spanish (launcher.ini: language=en|es|auto).
// The English text of the dialogs is in launcher.rc; the Spanish one replaces it by control ID (text.cpp). Text built
// at run time picks one of the two with L2().
#pragma once
#include "common.h"

extern bool g_spanish;
inline const wchar_t* L2(const wchar_t* en, const wchar_t* es) { return g_spanish ? es : en; }
void DetectLanguage();
void ApplyDialogText(HWND dlg, int dialogId);   // Spanish texts of a dialog's controls (nothing in English)
