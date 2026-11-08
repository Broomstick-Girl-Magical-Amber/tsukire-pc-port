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

    {IDD_MODS, IDC_MODS_GROUP, L"Mods y actualizaciones"},
    {IDD_MODS, IDC_MODS_OPEN, L"Carpeta de mods"},
    {IDD_MODS, IDC_MODS_UPDATES, L"Carpeta updates"},

    {IDD_CONTROLS, IDC_CTL_GROUP, L"Teclado, mando y pantalla de t\u00edtulo"},
    {IDD_CONTROLS, IDC_CTL_OPENDATA, L"Carpeta de datos"},
    {IDD_CONTROLS, IDC_CTL_EXAMPLES, L"Ver los ejemplos"},

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
