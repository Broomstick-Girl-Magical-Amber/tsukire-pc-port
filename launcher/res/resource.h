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
#define IDD_MODNAME             108
#define IDD_COPY                109
#define IDD_CTL_INPUT           110
#define IDD_CTL_TITLE           111
#define IDD_CTL_OPTIONS         112
#define IDD_BIND                113
#define IDD_PLAYTAB             114

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
#define IDC_PLAY_TABS           1130

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

// Mods and updates
#define IDC_MODS_GROUP          1300
#define IDC_MODS_BANNER         1301
#define IDC_MODS_OPEN           1302
#define IDC_MODS_UPDATES        1303
#define IDC_MODS_EDITION_LABEL  1304
#define IDC_MODS_EDITION        1305
#define IDC_MODS_RECOMP         1306
#define IDC_MODS_LIST           1307
#define IDC_MODS_DETAIL         1308
#define IDC_MODS_ADD            1309
#define IDC_MODS_REMOVE         1310
#define IDC_UPD_GROUP           1311
#define IDC_UPD_LIST            1312
#define IDC_UPD_DETAIL          1313
#define IDC_UPD_ADD             1314
#define IDC_UPD_REMOVE          1315

// Controls and title screen (input.ini, title.ini, language.txt, config.txt)
#define IDC_CTL_EDITION_LABEL   1404
#define IDC_CTL_EDITION         1405
#define IDC_CTL_NOTE            1406
#define IDC_CTL_TABS            1407
// - controls (input.ini)
#define IDC_IN_KB               1410
#define IDC_IN_MOUSE            1412
#define IDC_IN_LIST             1413
#define IDC_IN_DETAIL           1414
#define IDC_IN_EDIT             1415
#define IDC_IN_CLEAR            1416
#define IDC_IN_RESET            1417
#define IDC_IN_OPEN             1418
#define IDC_IN_FILE             1419
#define IDC_IN_COPY             1420
// - title screen (title.ini)
#define IDC_TI_GROUP            1430
#define IDC_TI_VIDEO_L          1431
#define IDC_TI_VIDEO            1432
#define IDC_TI_POOL_L           1433
#define IDC_TI_POOL1            1434
#define IDC_TI_POOL2            1435
#define IDC_TI_POOL3            1436
#define IDC_TI_POOL4            1437
#define IDC_TI_MUSIC_L          1438
#define IDC_TI_MUSIC            1439
#define IDC_TI_HOTKEYS          1440
#define IDC_TI_CLICK            1441
#define IDC_TI_UNLOCK           1442
#define IDC_TI_FADE_L           1443
#define IDC_TI_FADE             1444
#define IDC_TI_FADE_U           1445
#define IDC_TI_DELAY_L          1446
#define IDC_TI_DELAY            1447
#define IDC_TI_DELAY_U          1448
#define IDC_TI_NOTE             1449
#define IDC_TI_RESET            1450
#define IDC_TI_OPEN             1451
#define IDC_TI_FILE             1452
// - options (config.txt, language.txt)
#define IDC_OP_GRP_SCREEN       1460
#define IDC_OP_FULLSCREEN       1461
#define IDC_OP_RES_L            1462
#define IDC_OP_RES              1463
#define IDC_OP_RES_CUSTOM       1464
#define IDC_OP_FPS_L            1468
#define IDC_OP_FPS              1469
#define IDC_OP_FIFO             1470
#define IDC_OP_GPU_L            1471
#define IDC_OP_GPU              1472
#define IDC_OP_GRP_INPUT        1473
#define IDC_OP_HUD_L            1474
#define IDC_OP_HUD              1475
#define IDC_OP_SDL              1476
#define IDC_OP_GRP_OTHER        1477
#define IDC_OP_DISCORD          1480
#define IDC_OP_MUTE             1481
#define IDC_OP_RESET            1482
#define IDC_OP_OPEN             1483
#define IDC_OP_FILE             1484
#define IDC_OP_WARN             1485
// - one action's buttons
#define IDC_BI_TEXT             1490
#define IDC_BI_LIST             1491
#define IDC_BI_REMOVE           1492
#define IDC_BI_CAPTURE          1493
#define IDC_BI_COMBO            1494
#define IDC_BI_ADD              1495
#define IDC_BI_STATUS           1496

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
#define IDC_RESTORE_LIST        1601// Name of a mod being added
#define IDC_MODNAME_TEXT        1700
#define IDC_MODNAME_EDIT        1701
#define IDC_MODNAME_ERR         1702

// Copy with progress
#define IDC_COPY_TEXT           1710
#define IDC_COPY_PROGRESS       1711
#define IDC_COPY_SIZE           1712
