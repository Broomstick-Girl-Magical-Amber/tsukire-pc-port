# games/tsukihime_global/game.cmake: dependencias propias del juego (lo incluye el CMakeLists.txt raiz).
# Codigo comun de las dos ediciones (games/tsukihime_common: HLE de movie/codec/hid/pl, input, .rc e icono) con
# FFmpeg y libopus; define NX_TSUKI_COMMON.
include("${CMAKE_CURRENT_LIST_DIR}/../tsukihime_common/tsukihime_common.cmake")

# Origen de ventana NVN abajo-izquierda por defecto (apply_origin_default.py); NX_ORIGIN_LOWER_LEFT=0 lo apaga
target_compile_definitions(${NX_GAME} PRIVATE NX_DEFAULT_ORIGIN_LOWER_LEFT)

# Imagen con su aspecto (franjas negras) y ventana que solo escala en 16:9 (apply_aspect.py); Ctrl+1/2/3 = 50/75/100 %
target_compile_definitions(${NX_GAME} PRIVATE NX_FIXED_ASPECT)

# AP_LANG_DEFAULT: idioma si no hay NX_LANGUAGE ni <data root>\language.txt (apply_game_language.py); edicion global (tambien zh-Hans, zh-Hant, ja)
target_compile_definitions(${NX_GAME} PRIVATE NX_DEFAULT_LANGUAGE="en-US")

# AP_DATA_ROOT_EXE: el pc_data_root relativo de game.cpp se toma desde la carpeta del exe (apply_data_root_exe.py)
target_compile_definitions(${NX_GAME} PRIVATE NX_DATA_ROOT_EXE)
# AP_VK_REQ (apply_vk_requirements.py): Vulkan 1.3 o un aviso claro al iniciar
target_compile_definitions(${NX_GAME} PRIVATE NX_VK_REQUIRE_13)

# Titulo e icono de la ventana (apply_game_window.py): kWindowTitle en game.cpp, icono 1 en tsukihime.rc
enable_language(RC)
target_compile_definitions(${NX_GAME} PRIVATE NX_CUSTOM_WINDOW_TITLE NX_WINDOW_ICON)
target_sources(${NX_GAME} PRIVATE "${NX_TSUKI_COMMON}/tsukihime.rc")

# AP_DT_INIT: DT_INIT (_init) de main y sdk en +0x10 en el SDK 17.5.4 (el runtime asume +0x30; apply_dt_init.py)
target_compile_definitions(${NX_GAME} PRIVATE NX_DT_INIT=0x10)
