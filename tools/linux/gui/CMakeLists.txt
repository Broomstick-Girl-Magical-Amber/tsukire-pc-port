# tools/linux/gui/CMakeLists.txt - tsukihime_launcher: Dear ImGui + SDL3 front end of tools/linux/launcher.sh.
# Built by tools/linux/build_gui.sh (it fetches Dear ImGui, pinned, and finds the SDL3 of vcpkg that the game uses).
# It contains no code or data of the game.
cmake_minimum_required(VERSION 3.20)
project(tsukihime_launcher LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

option(NX_STATIC_RUNTIME "Link libstdc++ and libgcc statically (portable release binary: only glibc is needed)" OFF)
set(NX_IMGUI_DIR "" CACHE PATH "Dear ImGui source folder (the one with imgui.h)")
if(NOT EXISTS "${NX_IMGUI_DIR}/imgui.h")
    message(FATAL_ERROR "NX_IMGUI_DIR is not set or has no imgui.h (tools/linux/build_gui.sh fetches it)")
endif()

find_package(SDL3 REQUIRED CONFIG)
find_package(Threads REQUIRED)

add_executable(tsukihime_launcher
    launcher_gui.cpp
    ${NX_IMGUI_DIR}/imgui.cpp
    ${NX_IMGUI_DIR}/imgui_draw.cpp
    ${NX_IMGUI_DIR}/imgui_tables.cpp
    ${NX_IMGUI_DIR}/imgui_widgets.cpp
    ${NX_IMGUI_DIR}/backends/imgui_impl_sdl3.cpp
    ${NX_IMGUI_DIR}/backends/imgui_impl_sdlrenderer3.cpp
)
target_include_directories(tsukihime_launcher PRIVATE ${NX_IMGUI_DIR} ${NX_IMGUI_DIR}/backends)
target_compile_options(tsukihime_launcher PRIVATE -O2 -Wall -Wextra -Wno-unused-parameter)
target_link_libraries(tsukihime_launcher PRIVATE SDL3::SDL3 Threads::Threads ${CMAKE_DL_LIBS})
set_target_properties(tsukihime_launcher PROPERTIES BUILD_WITH_INSTALL_RPATH ON INSTALL_RPATH "$ORIGIN")
if(NX_STATIC_RUNTIME)
    target_link_options(tsukihime_launcher PRIVATE -static-libstdc++ -static-libgcc)
endif()
