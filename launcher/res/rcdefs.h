// rcdefs.h - the Windows constants launcher.rc uses, defined here so the .rc includes no SDK header: the same .rc then
// compiles with rc.exe, llvm-rc (Windows and Linux, where SDK header names differ in case) and windres. Values from
// winuser.h, commctrl.h and verrsrc.h; they never change. Only for launcher.rc (the C++ uses windows.h).
#pragma once
#ifndef WS_POPUP
#define WS_POPUP            0x80000000L
#define WS_CHILD            0x40000000L
#define WS_DISABLED         0x08000000L
#define WS_CAPTION          0x00C00000L
#define WS_BORDER           0x00800000L
#define WS_VSCROLL          0x00200000L
#define WS_HSCROLL          0x00100000L
#define WS_SYSMENU          0x00080000L
#define WS_MINIMIZEBOX      0x00020000L
#define WS_TABSTOP          0x00010000L
#define WS_EX_APPWINDOW     0x00040000L
#define DS_MODALFRAME       0x80L
#define DS_SETFONT          0x40L
#define DS_FIXEDSYS         0x08L
#define DS_SHELLFONT        (DS_SETFONT | DS_FIXEDSYS)
#define DS_CONTROL          0x400L
#define DS_CENTER           0x800L
#define CBS_DROPDOWNLIST    0x0003L
#define ES_MULTILINE        0x0004L
#define ES_AUTOVSCROLL      0x0040L
#define ES_AUTOHSCROLL      0x0080L
#define ES_READONLY         0x0800L
#define SS_ENDELLIPSIS      0x00004000L
#define PBS_MARQUEE         0x00000008L
#define SS_PATHELLIPSIS     0x00008000L
#define LBS_NOTIFY          0x0001L
#define LVS_REPORT          0x0001L
#define LVS_SINGLESEL       0x0004L
#define LVS_SHOWSELALWAYS   0x0008L
#define LVS_NOSORTHEADER    0x8000L
#define LBS_NOINTEGRALHEIGHT 0x0100L
#define IDOK                1
#define IDCANCEL            2
#define VS_VERSION_INFO     1
#define VOS_NT_WINDOWS32    0x00040004L
#define VFT_APP             0x00000001L
#endif
