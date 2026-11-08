// resource.h - IDs of the launcher resources (dialogs, controls, icon). ASCII only (llvm-rc on Linux reads it as ASCII).
#pragma once

#define IDI_LAUNCHER            1

#define IDD_MAIN                100
#define IDD_PLAY                101
#define IDD_INSTALL             102
#define IDD_MODS                103
#define IDD_CONTROLS            104
#define IDD_LICENSE             105
#define IDD_RESTORE             106
#define IDD_FETCH               107

// main window
#define IDC_TAB                 1000
#define IDC_FOOTER              1001

// Play / save data
#define IDC_PLAY_GROUP          1100
#define IDC_PLAY_EDITION_LABEL  1101
#define IDC_PLAY_EDITION        1102
#define IDC_PLAY_EXE            1103
#define IDC_PLAY_DUMP           1104
#define IDC_PLAY_STATE          1105
#define IDC_PLAY_CONSOLE        1106
#define IDC_PLAY_CLOSE          1107
#define IDC_PLAY_LOG            1108
#define IDC_PLAY_DATA           1109
#define IDC_PLAY_GOINSTALL      1110
#define IDC_SAVE_GROUP          1120
#define IDC_SAVE_PATH           1121
#define IDC_SAVE_INFO           1122
#define IDC_SAVE_BACKUPS        1123
#define IDC_SAVE_NOTE           1124
#define IDC_SAVE_OPEN           1125
#define IDC_SAVE_BACKUP         1126
#define IDC_SAVE_RESTORE        1127
#define IDC_SAVE_OPENBACKUPS    1128
#define IDC_SAVE_IMPORT         1129
#define IDC_PLAY_RUNNING        1130

// Install and recompile
#define IDC_INS_FILES_GROUP     1200
#define IDC_INS_DUMPS           1201
#define IDC_INS_KEYS            1202
#define IDC_INS_TOOLS           1203
#define IDC_INS_RUN_GROUP       1210
#define IDC_INS_JP              1211
#define IDC_INS_JP_STATE        1212
#define IDC_INS_GL              1213
#define IDC_INS_GL_STATE        1214
#define IDC_INS_REQ             1215
#define IDC_INS_START           1216
#define IDC_INS_CONSOLE         1217
#define IDC_INS_CANCEL          1218
#define IDC_INS_PROG_GROUP      1220
#define IDC_INS_STEP            1221
#define IDC_INS_TIME            1222
#define IDC_INS_PROGRESS        1223
#define IDC_INS_LOG             1224
#define IDC_INS_RESULT          1225
#define IDC_INS_COPY            1226
#define IDC_INS_OPENLOGS        1227
#define IDC_INS_YES             1228

// Mods and updates (part 2: for now only the folders)
#define IDC_MODS_GROUP          1300
#define IDC_MODS_TEXT           1301
#define IDC_MODS_OPEN           1302
#define IDC_MODS_UPDATES        1303

// Controls and title screen (part 3: for now only the folders)
#define IDC_CTL_GROUP           1400
#define IDC_CTL_TEXT            1401
#define IDC_CTL_OPENDATA        1402
#define IDC_CTL_EXAMPLES        1403

// Download of the port (first run without it)
#define IDC_FETCH_TEXT          1601
#define IDC_FETCH_LINK          1602
#define IDC_FETCH_STATUS        1603
#define IDC_FETCH_PROGRESS      1604
#define IDC_FETCH_GO            1605

// License
#define IDC_LIC_MS_GROUP        1500
#define IDC_LIC_MS_TEXT         1501
#define IDC_LIC_LINK            1502
#define IDC_LIC_ACCEPT          1503
#define IDC_LIC_PORT_GROUP      1504
#define IDC_LIC_PORT_TEXT       1505

// Restore a backup
#define IDC_RESTORE_TEXT        1600
#define IDC_RESTORE_LIST        1601
