// libtsuki_boot.so - the only native code of the published APK (phase 2 of the Android port).
// The game itself (the runtime + the code recompiled from the player's dump, one lib<game>.so built on the player's PC)
// is not in the APK: the home screen (Home.java) copies it from the port folder to the app's own files folder, where
// Android allows loading it, and writes its path to <files>/game_lib.txt. NativeActivity loads this library and calls
// ANativeActivity_onCreate: it loads the game's library from that path and hands the call over to its
// ANativeActivity_onCreate (platform_android.cpp). If it cannot, the reason goes to logcat (tag tsuki_boot) and to
// <files>/boot_error.txt, which the home screen shows the next time it opens, and the activity ends.
#include <android/log.h>
#include <android/native_activity.h>
#include <dlfcn.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#define TAG "tsuki_boot"

typedef void (*OnCreate)(ANativeActivity*, void*, size_t);

static void fail(ANativeActivity* activity, const char* fmt, ...) {
    char msg[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);
    __android_log_print(ANDROID_LOG_ERROR, TAG, "%s", msg);
    char path[1024];
    snprintf(path, sizeof path, "%s/boot_error.txt", activity->internalDataPath);
    FILE* f = fopen(path, "w");
    if (f) {
        fputs(msg, f);
        fclose(f);
    }
    ANativeActivity_finish(activity);
}

__attribute__((visibility("default"))) void ANativeActivity_onCreate(ANativeActivity* activity, void* saved, size_t size) {
    char path[1024], lib[1024] = "";
    snprintf(path, sizeof path, "%s/game_lib.txt", activity->internalDataPath);
    FILE* f = fopen(path, "r");
    if (f) {
        if (!fgets(lib, sizeof lib, f)) lib[0] = 0;
        fclose(f);
    }
    lib[strcspn(lib, "\r\n")] = 0;
    if (!lib[0]) {
        fail(activity, "no game library chosen (%s missing or empty): start the game from the home screen", path);
        return;
    }
    void* h = dlopen(lib, RTLD_NOW | RTLD_LOCAL);
    if (!h) {
        const char* e = dlerror();
        fail(activity, "dlopen(%s) failed: %s", lib, e ? e : "(no reason given)");
        return;
    }
    OnCreate create = (OnCreate)dlsym(h, "ANativeActivity_onCreate");
    if (!create) {
        fail(activity, "%s has no ANativeActivity_onCreate", lib);
        return;
    }
    __android_log_print(ANDROID_LOG_INFO, TAG, "game library %s loaded", lib);
    create(activity, saved, size);
}
