// text.cpp - Spanish text of the dialogs (the English one is in launcher.rc).
#include "text.h"

#include "../res/resource.h"

bool g_spanish = false;

void DetectLanguage() {
    std::wstring want = IniGet(L"language", L"auto");
    if (want == L"es") g_spanish = true;
    else if (want == L"en") g_spanish = false;
    else g_spanish = PRIMARYLANGID(GetUserDefaultUILanguage()) == LANG_SPANISH;
}

namespace {
struct Item {
    int dialog;
    int control;   // 0 = the dialog's caption
    const wchar_t* es;
};

const Item kSpanish[] = {
    {IDD_FETCH, 0, L"Tsukihime launcher - descargar el port"},
    {IDD_FETCH, IDC_FETCH_GO, L"Descargar"},
    {IDD_FETCH, IDCANCEL, L"Cerrar"},

    {IDD_MAIN, 0, L"Tsukihime PC port"},
    {IDD_MAIN, IDOK, L"Jugar"},
    {IDD_MAIN, IDCANCEL, L"Cerrar"},

    {IDD_PLAY, IDC_PLAY_GROUP, L"Juego"},
    {IDD_PLAY, IDC_PLAY_EDITION_LABEL, L"Edici\u00f3n"},
    {IDD_PLAY, IDC_PLAY_CONSOLE, L"Mostrar la consola con el log del juego"},
    {IDD_PLAY, IDC_PLAY_CLOSE, L"Cerrar esta ventana al iniciar el juego"},
    {IDD_PLAY, IDC_PLAY_LOG, L"Abrir nx.log"},
    {IDD_PLAY, IDC_PLAY_DATA, L"Carpeta de datos"},
    {IDD_PLAY, IDC_PLAY_GOINSTALL, L"Instalar / recompilar..."},
    {IDD_PLAY, IDC_SAVE_GROUP, L"Partidas guardadas"},
    {IDD_PLAY, IDC_SAVE_OPEN, L"Abrir carpeta"},
    {IDD_PLAY, IDC_SAVE_BACKUP, L"Hacer una copia"},
    {IDD_PLAY, IDC_SAVE_RESTORE, L"Restaurar..."},
    {IDD_PLAY, IDC_SAVE_IMPORT, L"Importar partida..."},
    {IDD_PLAY, IDC_SAVE_OPENBACKUPS, L"Carpeta de copias"},

    {IDD_INSTALL, IDC_INS_FILES_GROUP, L"Tus archivos"},
    {IDD_INSTALL, IDC_INS_RUN_GROUP, L"Compilar"},
    {IDD_INSTALL, IDC_INS_JP, L"Edici\u00f3n japonesa"},
    {IDD_INSTALL, IDC_INS_GL, L"Edici\u00f3n global"},
    {IDD_INSTALL, IDC_INS_REQ, L"Instalar o comprobar antes los requisitos (necesario la primera vez)"},
    {IDD_INSTALL, IDC_INS_YES, L"Responder s\u00ed a las preguntas de la instalaci\u00f3n."},
    {IDD_INSTALL, IDC_INS_CONSOLE, L"Recompilar en una consola"},
    {IDD_INSTALL, IDC_INS_CANCEL, L"Cancelar"},
    {IDD_INSTALL, IDC_INS_PROG_GROUP, L"Progreso"},
    {IDD_INSTALL, IDC_INS_COPY, L"Copiar el log"},
    {IDD_INSTALL, IDC_INS_OPENLOGS, L"Carpeta de logs"},

    {IDD_MODS, IDC_MODS_EDITION_LABEL, L"Edici\u00f3n"},
    {IDD_MODS, IDC_MODS_RECOMP, L"Recompilar..."},
    {IDD_MODS, IDC_MODS_ADD, L"Agregar mod..."},
    {IDD_MODS, IDC_MODS_REMOVE, L"Quitar..."},
    {IDD_MODS, IDC_MODS_OPEN, L"Abrir carpeta"},
    {IDD_MODS, IDC_UPD_GROUP, L"Actualizaciones y parches (.nsp en updates)"},
    {IDD_MODS, IDC_UPD_ADD, L"Agregar .nsp..."},
    {IDD_MODS, IDC_UPD_REMOVE, L"Quitar..."},
    {IDD_MODS, IDC_MODS_UPDATES, L"Abrir carpeta"},

    {IDD_MODNAME, 0, L"Agregar mod"},
    {IDD_MODNAME, IDOK, L"Agregar"},
    {IDD_MODNAME, IDCANCEL, L"Cancelar"},

    {IDD_COPY, 0, L"Copiando"},
    {IDD_COPY, IDCANCEL, L"Cancelar"},

    {IDD_CONTROLS, IDC_CTL_EDITION_LABEL, L"Edici\u00f3n"},
    {IDD_CTL_INPUT, IDC_IN_KB, L"Teclado"},
    {IDD_CTL_INPUT, IDC_IN_PAD, L"Mando"},
    {IDD_CTL_INPUT, IDC_IN_MOUSE, L"Mouse"},
    {IDD_CTL_INPUT, IDC_IN_EDIT, L"Cambiar..."},
    {IDD_CTL_INPUT, IDC_IN_CLEAR, L"Quitar todo"},
    {IDD_CTL_INPUT, IDC_IN_RESET, L"Por defecto"},
    {IDD_CTL_INPUT, IDC_IN_COPY, L"Copiar a la otra edici\u00f3n"},
    {IDD_CTL_INPUT, IDC_IN_OPEN, L"Abrir input.ini"},
    {IDD_CTL_TITLE, IDC_TI_GROUP, L"Video y m\u00fasica de la pantalla de t\u00edtulo"},
    {IDD_CTL_TITLE, IDC_TI_VIDEO_L, L"Video"},
    {IDD_CTL_TITLE, IDC_TI_POOL_L, L"Videos que recorre"},
    {IDD_CTL_TITLE, IDC_TI_POOL1, L"Predeterminado"},
    {IDD_CTL_TITLE, IDC_TI_POOL2, L"Ruta de Arcueid completada"},
    {IDD_CTL_TITLE, IDC_TI_POOL3, L"Inicio de ruta de Ciel"},
    {IDD_CTL_TITLE, IDC_TI_POOL4, L"Juego completado"},
    {IDD_CTL_TITLE, IDC_TI_MUSIC_L, L"M\u00fasica"},
    {IDD_CTL_TITLE, IDC_TI_HOTKEYS, L"F5 / F6 cambian el video en la pantalla de t\u00edtulo"},
    {IDD_CTL_TITLE, IDC_TI_CLICK, L"Un clic en el logo del men\u00fa del t\u00edtulo cambia el video"},
    {IDD_CTL_TITLE, IDC_TI_UNLOCK, L"Solo los videos que tu partida ya desbloque\u00f3"},
    {IDD_CTL_TITLE, IDC_TI_FADE_L, L"Fundido"},
    {IDD_CTL_TITLE, IDC_TI_FADE_U, L"ms de cada mitad del fundido (0 = sin fundido)"},
    {IDD_CTL_TITLE, IDC_TI_DELAY_L, L"Espera"},
    {IDD_CTL_TITLE, IDC_TI_DELAY_U, L"ms tras cambiar el video (s\u00fabelo si a\u00fan ves el anterior)"},
    {IDD_CTL_TITLE, IDC_TI_RESET, L"Por defecto"},
    {IDD_CTL_TITLE, IDC_TI_OPEN, L"Abrir title.ini"},
    {IDD_CTL_OPTIONS, IDC_OP_GRP_SCREEN, L"Pantalla"},
    {IDD_CTL_OPTIONS, IDC_OP_FULLSCREEN, L"Iniciar en pantalla completa"},
    {IDD_CTL_OPTIONS, IDC_OP_RES_L, L"Resoluci\u00f3n interna"},
    {IDD_CTL_OPTIONS, IDC_OP_FPS_L, L"L\u00edmite de FPS"},
    {IDD_CTL_OPTIONS, IDC_OP_FIFO, L"Sincronizaci\u00f3n vertical estricta"},
    {IDD_CTL_OPTIONS, IDC_OP_GPU_L, L"GPU"},
    {IDD_CTL_OPTIONS, IDC_OP_GRP_INPUT, L"Controles"},
    {IDD_CTL_OPTIONS, IDC_OP_HUD_L, L"\u00cdconos de botones"},
    {IDD_CTL_OPTIONS, IDC_OP_SDL, L"Soporte extra para mandos de PlayStation y Switch"},
    {IDD_CTL_OPTIONS, IDC_OP_GRP_OTHER, L"Otros"},
    {IDD_CTL_OPTIONS, IDC_OP_DISCORD, L"Discord Rich Presence"},
    {IDD_CTL_OPTIONS, IDC_OP_MUTE, L"Silenciar el sonido"},
    {IDD_CTL_OPTIONS, IDC_OP_RESET, L"Por defecto"},
    {IDD_CTL_OPTIONS, IDC_OP_OPEN, L"Abrir config.txt"},
    {IDD_BIND, IDC_BI_REMOVE, L"Quitar"},
    {IDD_BIND, IDC_BI_ADD, L"Agregar"},
    {IDD_BIND, IDOK, L"Aceptar"},
    {IDD_BIND, IDCANCEL, L"Cancelar"},

    {IDD_LICENSE, 0, L"Licencias"},
    {IDD_LICENSE, IDC_LIC_MS_GROUP, L"Licencia de Microsoft (CRT y SDK de Windows)"},
    {IDD_LICENSE, IDC_LIC_ACCEPT, L"Le\u00ed y acepto los t\u00e9rminos de la licencia de Microsoft"},
    {IDD_LICENSE, IDC_LIC_PORT_GROUP, L"Este port (LICENSE y NOTICE.md)"},
    {IDD_LICENSE, IDOK, L"Aceptar y seguir"},
    {IDD_LICENSE, IDCANCEL, L"Cancelar"},

    {IDD_RESTORE, 0, L"Restaurar partidas"},
    {IDD_RESTORE, IDOK, L"Restaurar"},
    {IDD_RESTORE, IDCANCEL, L"Cancelar"},
};
}  // namespace

void ApplyDialogText(HWND dlg, int dialogId) {
    if (!g_spanish) return;
    for (const Item& it : kSpanish) {
        if (it.dialog != dialogId) continue;
        if (it.control == 0) SetWindowTextW(dlg, it.es);
        else SetDlgItemTextW(dlg, it.control, it.es);
    }
}
