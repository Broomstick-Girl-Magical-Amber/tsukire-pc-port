# launcher/CMakeLists.txt - tsukihime_launcher.exe: one Win32 executable with no dependencies besides Windows itself.
#
# Built with the same chain as the game (tools/dev/build_launcher.ps1): clang-cl + lld-link + llvm-rc against the
# CRT/SDK of xwin (tools/linux/clangcl-xwin.cmake), on Windows or Linux. The C++ runtime is linked statically (/MT):
# the exe does not need VCRUNTIME140.dll. It contains no code or data of the game.
cmake_minimum_required(VERSION 3.20)
project(tsukihime_launcher LANGUAGES CXX RC)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")   # /MT (static CRT)

add_executable(tsukihime_launcher WIN32
    src/main.cpp
    src/mods.cpp
    src/controls.cpp
    src/fetch.cpp
    src/install.cpp
    src/runner.cpp
    src/status.cpp
    src/util.cpp
    src/text.cpp
    src/android.cpp   # AP_LAUNCHER_ANDROID (apply_launcher_android.py): the Android sub-tab
    res/launcher.rc
)
# The .rc names its icon, manifest and headers by path and CMake does not scan them: without this, changing
# launcher.ico (or the manifest) leaves "ninja: no work to do" and the exe keeps the old icon.
set_source_files_properties(res/launcher.rc PROPERTIES OBJECT_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/res/launcher.ico;${CMAKE_CURRENT_SOURCE_DIR}/res/launcher.manifest;${CMAKE_CURRENT_SOURCE_DIR}/res/resource.h;${CMAKE_CURRENT_SOURCE_DIR}/res/rcdefs.h")
target_compile_definitions(tsukihime_launcher PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00)
target_link_libraries(tsukihime_launcher PRIVATE comctl32 shell32 uxtheme user32 gdi32 bcrypt ole32)

# Release tag the exe downloads the port from (tools\dev\build_launcher.ps1 -Tag v1.2.3). Empty = the latest release.
set(LAUNCHER_TAG "" CACHE STRING "Release tag of the port zip that the launcher downloads when the port is missing")
configure_file(src/launcher_tag.h.in ${CMAKE_CURRENT_BINARY_DIR}/launcher_tag.h @ONLY)
target_include_directories(tsukihime_launcher PRIVATE ${CMAKE_CURRENT_BINARY_DIR})

if(MSVC)
    # Sources are ASCII (the Spanish text uses \u escapes); the manifest comes from launcher.rc, not from the linker.
    target_compile_options(tsukihime_launcher PRIVATE /utf-8 /W3)
    target_compile_definitions(tsukihime_launcher PRIVATE _CRT_SECURE_NO_WARNINGS)   # _vsnwprintf / swscanf are fine here
    target_link_options(tsukihime_launcher PRIVATE /MANIFEST:NO)
else()
    # MinGW (only for quick test builds): wWinMain, static runtime.
    target_link_options(tsukihime_launcher PRIVATE -municode -static -static-libgcc -static-libstdc++)
endif()
