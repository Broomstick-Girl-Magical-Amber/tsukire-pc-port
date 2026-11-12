// Home screen of the Android port: the player picks the port folder on the phone
// (the same layout as the port on the PC: <folder>/data/<edition>, the .xci/.nsp in <folder>, mods in
// <folder>/mods/<edition>) and, if it is elsewhere, the dump, both with Android's own file picker (the Storage Access
// Framework); the launcher turns the choice into a plain path, checks the data and finds the dump, and Play starts the
// game on it. The game's own library (lib<game>.so: the runtime + the code recompiled from the player's dump, built on
// the PC) is not in the APK: it comes in the port folder (<data root>/android/lib<game>.so) and is copied here to the
// app's own files folder, from where libtsuki_boot.so (android/boot/boot.c, the library of the NativeActivity) loads
// it. One app for both editions: the one whose data is in the folder (a button switches if both are).
// The choice goes to <files>/data_root.txt, which android_main reads (platform_android.cpp). A dump outside the folder goes to NX_ROMFS_DUMP in <data root>/config.txt
// (romfs_direct.cpp). Needs "all files access" (MANAGE_EXTERNAL_STORAGE): the game reads the folder and the dump with
// plain paths, so only folders of the phone's storage or an SD card work (not Drive or other apps).
// Portrait (the manifest); the game itself is landscape. The interface is built in code (no layouts); texts in
// Spanish or English by the phone's language.
package io.github.tsukire.launcher;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.provider.DocumentsContract;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.RandomAccessFile;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;

public class Home extends Activity {
    static final String STORAGE = "/storage/emulated/0";
    static final String EXTERNAL_DOCS = "com.android.externalstorage.documents";  // the phone's storage and SD cards
    static final String DOWNLOAD_DOCS = "com.android.providers.downloads.documents";
    static final int REQ_FOLDER = 1, REQ_DUMP = 2;
    static final String DUMP_LINE = "NX_ROMFS_DUMP=";
    static final String DUMP_NOTE = "# NX_ROMFS_DUMP: written by the Android launcher (the dump is not in the port folder)";

    // The edition to play (japan | global): the one whose data is in the port folder; the player's choice if both are.
    String edition = "global";
    boolean installing;
    boolean es;

    TextView title, status;
    Button play, pickFolder, pickDump, access, log, cancel, switchEdition;
    ScrollView statusView, logView;
    TextView logText;

    enum Mode { HOME, LOG }
    Mode mode = Mode.HOME;

    // ---- lifecycle --------------------------------------------------------------------------------------------------

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        es = Locale.getDefault().getLanguage().equals("es");
        edition = prefs().getString("edition", "global");
        buildViews();
        refresh();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (status != null && mode == Mode.HOME) refresh();  // back from the permission page or the file picker
    }

    @Override
    public void onBackPressed() {
        if (mode != Mode.HOME) {
            setMode(Mode.HOME);
            refresh();
            return;
        }
        super.onBackPressed();
    }

    // ---- texts ------------------------------------------------------------------------------------------------------

    String t(String spanish, String english) { return es ? spanish : english; }

    String editionName() {
        return edition.equals("global") ? t("Edici\u00f3n global", "Global edition")
                                        : t("Edici\u00f3n japonesa (cartucho)", "Japanese edition (cartridge)");
    }

    // ---- paths ------------------------------------------------------------------------------------------------------

    String dataSub() { return edition.equals("global") ? "data/tsuki_global" : "data/tsuki"; }
    String modsSub() { return edition.equals("global") ? "mods/global" : "mods/japan"; }
    long programId() { return edition.equals("global") ? 0x010057A01E4D4000L : 0x01001DC01486A000L; }
    String libName() { return edition.equals("global") ? "libtsukihime_global.so" : "libtsukihime.so"; }
    File libSource(String f) { return new File(dataRoot(f), "android/" + libName()); }   // as copied from the PC
    File libInstalled() { return new File(new File(getFilesDir(), "game"), libName()); }  // where it can be loaded from
    String libStamp(File src) { return src.length() + ":" + src.lastModified(); }

    static boolean hasEdition(String f, String ed) {
        return new File(f, (ed.equals("global") ? "data/tsuki_global" : "data/tsuki") + "/romfs_source.bin").isFile();
    }

    SharedPreferences prefs() { return getSharedPreferences("launcher", MODE_PRIVATE); }
    String folder() { return prefs().getString("folder", null); }
    String chosenDump() { return prefs().getString("dump", null); }
    File dataRoot(String f) { return new File(f, dataSub()); }

    boolean hasAccess() { return Environment.isExternalStorageManager(); }

    // ---- romfs_source.bin (tools/internal/romfs_source.py; read the same way as romfs_direct.cpp) ----------------------

    static class Source {
        long programId, ncaOff, ncaSize;
        byte[] sig = new byte[0x100];  // the first 0x100 bytes of the NCA in the dump
        String hint = "";              // the name the dump had on the PC
    }

    static Source readSource(File f) {
        try (RandomAccessFile r = new RandomAccessFile(f, "r")) {
            byte[] head = new byte[340];
            r.readFully(head);
            if (!new String(head, 0, 7, StandardCharsets.US_ASCII).equals("NXRSRC1")) return null;
            ByteBuffer b = ByteBuffer.wrap(head).order(ByteOrder.LITTLE_ENDIAN);
            Source s = new Source();
            s.programId = b.getLong(16);
            s.ncaOff = b.getLong(24);
            s.ncaSize = b.getLong(32);
            System.arraycopy(head, 80, s.sig, 0, 0x100);
            int n = b.getInt(336);
            if (n > 0 && n < 4096) {
                byte[] name = new byte[n];
                r.readFully(name);
                s.hint = new String(name, StandardCharsets.UTF_8);
            }
            return s;
        } catch (Exception e) {
            return null;
        }
    }

    static boolean isTheDump(File p, Source s) {
        if (s == null || !p.isFile() || p.length() < s.ncaOff + s.ncaSize) return false;
        try (RandomAccessFile r = new RandomAccessFile(p, "r")) {
            byte[] buf = new byte[0x100];
            r.seek(s.ncaOff);
            r.readFully(buf);
            return Arrays.equals(buf, s.sig);
        } catch (Exception e) {
            return false;
        }
    }

    static boolean isDumpName(String n) {
        String l = n.toLowerCase(Locale.ROOT);
        return l.endsWith(".xci") || l.endsWith(".nsp");
    }

    // The dump of this table in the port folder (the name it had first, then any .xci/.nsp), or null.
    static File dumpInFolder(File folder, Source s) {
        if (s == null) return null;
        if (!s.hint.isEmpty() && isTheDump(new File(folder, s.hint), s)) return new File(folder, s.hint);
        File[] kids = folder.listFiles();
        if (kids != null)
            for (File k : kids)
                if (isDumpName(k.getName()) && isTheDump(k, s)) return k;
        return null;
    }

    // ---- config.txt: the NX_ROMFS_DUMP line (only that one; every other line is kept) -----------------------------------

    void writeDumpLine(File root, String dumpPath) throws Exception {
        File cfg = new File(root, "config.txt");
        List<String> lines = new ArrayList<>();
        if (cfg.isFile()) {
            try (BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(cfg), StandardCharsets.UTF_8))) {
                String line;
                while ((line = r.readLine()) != null)
                    if (!line.startsWith(DUMP_LINE) && !line.equals(DUMP_NOTE)) lines.add(line);
            }
        }
        if (dumpPath != null) {
            lines.add(DUMP_NOTE);
            lines.add(DUMP_LINE + dumpPath);
        }
        if (!cfg.isFile() && dumpPath == null) return;
        StringBuilder sb = new StringBuilder();
        for (String l : lines) sb.append(l).append('\n');
        try (FileOutputStream o = new FileOutputStream(cfg)) {
            o.write(sb.toString().getBytes(StandardCharsets.UTF_8));
        }
    }

    // ---- state ------------------------------------------------------------------------------------------------------

    String check(boolean ok, String what) { return (ok ? "  ok      " : t("  falta   ", "  missing ")) + what + "\n"; }

    // Checks everything and shows it; returns the dump to use (null: cannot play).
    File refresh() {
        title.setText("Tsukihime -A piece of blue glass moon-\n" + editionName());
        if (mode == Mode.LOG) return null;
        access.setVisibility(hasAccess() ? View.GONE : View.VISIBLE);
        play.setEnabled(false);
        pickDump.setEnabled(false);
        log.setEnabled(false);
        switchEdition.setVisibility(View.GONE);
        if (!hasAccess()) {
            say(t("El juego necesita \"acceso a todos los archivos\" para leer la carpeta del port y el dump.\n"
                + "Toca \"Dar permiso\" y act\u00edvalo para esta app.",
                  "The game needs \"all files access\" to read the port folder and the dump.\n"
                + "Tap \"Grant access\" and turn it on for this app."), true);
            return null;
        }
        String f = folder();
        if (f == null || !new File(f).isDirectory()) {
            say(t("Elige la carpeta del port: la que copiaste del PC, con data/" + dataSub().substring(5) + " adentro.\n"
                + "(tools/android/phone_data.ps1 la prepara en el PC.)",
                  "Choose the port folder: the one copied from the PC, with data/" + dataSub().substring(5) + " inside.\n"
                + "(tools/android/phone_data.ps1 makes it on the PC.)"), true);
            return null;
        }
        boolean jp = hasEdition(f, "japan"), gl = hasEdition(f, "global");
        if (jp != gl) edition = gl ? "global" : "japan";  // only one in the folder: that one
        switchEdition.setVisibility(jp && gl ? View.VISIBLE : View.GONE);
        title.setText("Tsukihime -A piece of blue glass moon-\n" + editionName());
        File root = dataRoot(f);
        log.setEnabled(new File(root, "nx.log").isFile());
        StringBuilder s = new StringBuilder();
        File bootError = new File(getFilesDir(), "boot_error.txt");
        if (bootError.isFile()) {
            try (BufferedReader br = new BufferedReader(new InputStreamReader(new FileInputStream(bootError), StandardCharsets.UTF_8))) {
                s.append(t("El juego no pudo iniciar la \u00faltima vez:\n", "The game could not start last time:\n")).append(br.readLine()).append("\n\n");
            } catch (Exception e) {
                // unreadable: nothing to show
            }
        }
        s.append(t("Carpeta: ", "Folder: ")).append(f).append("\n\n");
        boolean main = new File(root, "analysis/main.bin").isFile(), sdk = new File(root, "analysis/sdk.bin").isFile();
        boolean spirv = new File(root, "spirv/results.tsv").isFile(), font = new File(root, "fonts/shared_0.ttf").isFile();
        File table = new File(root, "romfs_source.bin");
        Source src = readSource(table);
        File libSrc = libSource(f);
        boolean lib = libSrc.isFile() || libInstalled().isFile();
        boolean data = main && sdk && spirv && font && src != null && lib;
        s.append(check(lib, t("c\u00f3digo del juego (android/" + libName() + ")", "game code (android/" + libName() + ")")));
        s.append(check(main && sdk, t("m\u00f3dulos del juego (analysis)", "game modules (analysis)")));
        s.append(check(src != null, t("tabla del RomFS (romfs_source.bin)", "RomFS table (romfs_source.bin)")));
        s.append(check(spirv, t("shaders (spirv)", "shaders (spirv)")));
        s.append(check(font, t("fuente (fonts/shared_0.ttf)", "font (fonts/shared_0.ttf)")));
        s.append(check(new File(root, "hud/manifest.txt").isFile(), t("iconos del HUD (opcional)", "HUD icons (optional)")));
        if (src != null && src.programId != programId()) {
            s.append(t("\nLa tabla (romfs_source.bin) es de otra edici\u00f3n, no de la ", "\nThe table (romfs_source.bin) is from another edition, not the ")
                     + editionName() + ".\n");
            data = false;
        }
        File dump = null;
        if (src != null) {
            pickDump.setEnabled(true);
            String chosen = chosenDump();
            if (chosen != null && isTheDump(new File(chosen), src)) dump = new File(chosen);
            if (dump == null) dump = dumpInFolder(new File(f), src);
            s.append("\n").append(t("Dump: ", "Dump: "));
            if (dump != null) s.append(dump.getPath()).append(String.format(Locale.ROOT, " (%.1f GB)\n", dump.length() / 1e9));
            else s.append(t("no est\u00e1 (" + src.hint + "): ponlo en la carpeta o elige uno con \"Elegir dump\".\n",
                            "not found (" + src.hint + "): put it in the folder or choose one with \"Choose dump\".\n"));
        }
        File saves = new File(root, "user/save");
        String[] savesList = saves.list();
        s.append(t("Partidas: ", "Saves: ")).append(savesList == null || savesList.length == 0
                ? t("ninguna todav\u00eda", "none yet") : t("s\u00ed (user/save)", "yes (user/save)")).append("\n");
        File mods = new File(f, modsSub());
        int layers = 0;
        File[] kids = mods.listFiles();
        if (kids != null)
            for (File k : kids)
                if (k.isDirectory() && !k.getName().startsWith("_") && !k.getName().startsWith(".")) layers++;
        s.append(t("Mods activos: ", "Active mods: ")).append(layers).append(" (").append(modsSub()).append(")\n");
        boolean ready = data && dump != null;
        if (!data) s.append(t("\nFaltan datos: vuelve a copiar la carpeta desde el PC (tools/android/phone_data.ps1).",
                              "\nData missing: copy the folder from the PC again (tools/android/phone_data.ps1)."));
        say(s.toString(), !ready);
        play.setEnabled(ready && !installing);
        return ready ? dump : null;
    }

    void say(String text, boolean warn) {
        status.setText(text);
        status.setTextColor(warn ? Color.rgb(255, 170, 120) : Color.WHITE);
    }

    // ---- actions ----------------------------------------------------------------------------------------------------

    void start() {
        final File dump = refresh();
        final String f = folder();
        if (dump == null || f == null || installing) return;
        final File root = dataRoot(f), src = libSource(f), dst = libInstalled();
        final String stamp = src.isFile() ? libStamp(src) : null;
        boolean fresh = dst.isFile() && (stamp == null || stamp.equals(prefs().getString("lib_" + edition, "")));
        if (fresh) {
            launch(root, dump, dst);
            return;
        }
        // The library of the port folder is new or changed: copy it to the app's own folder (Android only loads code
        // from there), off the UI thread. Read-only once copied.
        installing = true;
        play.setEnabled(false);
        say(t("Instalando el c\u00f3digo del juego (", "Installing the game code (") + (src.length() >> 20) + " MB)...", false);
        new Thread(new Runnable() { public void run() {
            String error = null;
            File tmp = new File(dst.getPath() + ".tmp");
            try {
                dst.getParentFile().mkdirs();
                try (FileInputStream in = new FileInputStream(src); FileOutputStream out = new FileOutputStream(tmp)) {
                    byte[] buf = new byte[1 << 20];
                    for (int n; (n = in.read(buf)) > 0; ) out.write(buf, 0, n);
                }
                if (dst.exists() && !dst.delete()) throw new java.io.IOException("cannot replace " + dst);
                if (!tmp.renameTo(dst)) throw new java.io.IOException("cannot rename to " + dst);
                dst.setReadOnly();
                prefs().edit().putString("lib_" + edition, stamp).apply();
            } catch (Exception e) {
                error = e.toString();
                tmp.delete();
            }
            final String err = error;
            runOnUiThread(new Runnable() { public void run() {
                installing = false;
                if (err != null) {
                    refresh();
                    say(t("No se pudo instalar el c\u00f3digo del juego: ", "Could not install the game code: ") + err, true);
                } else {
                    launch(root, dump, dst);
                }
            } });
        } }).start();
    }

    void launch(File root, File dump, File lib) {
        try {
            // NX_ROMFS_DUMP only when the dump is not in the port folder (romfs_direct.cpp finds it there by itself)
            boolean inFolder = dump.getParentFile() != null
                    && dump.getParentFile().getCanonicalPath().equals(new File(folder()).getCanonicalPath());
            writeDumpLine(root, inFolder ? null : dump.getPath());
            writeText(new File(getFilesDir(), "data_root.txt"), root.getPath());
            writeText(new File(getFilesDir(), "game_lib.txt"), lib.getPath());  // read by libtsuki_boot.so
            new File(getFilesDir(), "boot_error.txt").delete();
            prefs().edit().putString("edition", edition).apply();
        } catch (Exception e) {
            say(t("No se pudo guardar la elecci\u00f3n: ", "Could not save the choice: ") + e, true);
            return;
        }
        Intent i = new Intent();
        i.setClassName(this, "android.app.NativeActivity");
        startActivity(i);
        finish();
    }

    static void writeText(File f, String text) throws Exception {
        try (FileOutputStream o = new FileOutputStream(f)) {
            o.write(text.getBytes(StandardCharsets.UTF_8));
        }
    }

    void askAccess() {
        try {
            startActivity(new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:" + getPackageName())));
        } catch (Exception e) {
            startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
        }
    }

    void showLog() {
        String f = folder();
        if (f == null) return;
        File nx = new File(dataRoot(f), "nx.log");
        StringBuilder sb = new StringBuilder();
        try (RandomAccessFile r = new RandomAccessFile(nx, "r")) {
            long len = r.length(), from = Math.max(0, len - 48 * 1024);  // the end of the log
            byte[] b = new byte[(int) (len - from)];
            r.seek(from);
            r.readFully(b);
            sb.append(nx.getPath()).append(from > 0 ? t(" (el final)\n\n", " (the end)\n\n") : "\n\n");
            sb.append(new String(b, StandardCharsets.UTF_8));
        } catch (Exception e) {
            sb.append(e.toString());
        }
        logText.setText(sb.toString());
        setMode(Mode.LOG);
        logView.post(new Runnable() { public void run() { logView.fullScroll(View.FOCUS_DOWN); } });
    }

    // ---- Android's file picker (Storage Access Framework) -> a plain path ------------------------------------------

    // A document id of the phone's storage ("primary:Tsukihime/x.nsp") or of an SD card ("1A2B-3C4D:dir") as a path.
    static String idToPath(String id) {
        int c = id.indexOf(':');
        if (c < 0) return null;
        String vol = id.substring(0, c), rel = id.substring(c + 1);
        String base = vol.equalsIgnoreCase("primary") ? STORAGE : "/storage/" + vol;
        return rel.isEmpty() ? base : base + "/" + rel;
    }

    static String uriToPath(Uri uri, boolean tree) {
        try {
            String auth = uri.getAuthority();
            String id = tree ? DocumentsContract.getTreeDocumentId(uri) : DocumentsContract.getDocumentId(uri);
            if (EXTERNAL_DOCS.equals(auth)) return idToPath(id);
            if (DOWNLOAD_DOCS.equals(auth) && id.startsWith("raw:")) return id.substring(4);  // Downloads, some phones
        } catch (Exception e) {
            // not a document of a storage provider
        }
        return null;
    }

    // Where the picker opens: the folder chosen before (or the internal storage).
    Uri initialUri(String path) {
        String rel = null;
        if (path != null && path.startsWith(STORAGE)) rel = "primary:" + path.substring(Math.min(path.length(), STORAGE.length() + 1));
        return DocumentsContract.buildDocumentUri(EXTERNAL_DOCS, rel != null ? rel : "primary:");
    }

    void pickFolderSaf() {
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
        i.putExtra(DocumentsContract.EXTRA_INITIAL_URI, initialUri(folder()));
        try {
            startActivityForResult(i, REQ_FOLDER);
        } catch (Exception e) {
            say(t("No se pudo abrir el selector de carpetas: ", "Could not open the folder picker: ") + e, true);
        }
    }

    void pickDumpSaf() {
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        i.addCategory(Intent.CATEGORY_OPENABLE);
        i.setType("*/*");  // .xci / .nsp have no MIME type of their own
        String from = chosenDump() != null ? new File(chosenDump()).getParent() : folder();
        i.putExtra(DocumentsContract.EXTRA_INITIAL_URI, initialUri(from));
        try {
            startActivityForResult(i, REQ_DUMP);
        } catch (Exception e) {
            say(t("No se pudo abrir el selector de archivos: ", "Could not open the file picker: ") + e, true);
        }
    }

    @Override
    protected void onActivityResult(int req, int result, Intent data) {
        super.onActivityResult(req, result, data);
        if (result != RESULT_OK || data == null || data.getData() == null) return;
        String path = uriToPath(data.getData(), req == REQ_FOLDER);
        if (path == null) {
            say(t("Eso no es una carpeta del almacenamiento del tel\u00e9fono o de la tarjeta SD (el juego lee rutas normales).\n"
                + "En el selector, entra por \"Almacenamiento interno\" o por la tarjeta.",
                  "That is not a folder of the phone's storage or of the SD card (the game reads plain paths).\n"
                + "In the picker, go in through \"Internal storage\" or the card."), true);
            return;
        }
        if (req == REQ_FOLDER) {
            if (!new File(path).isDirectory()) {
                say(t("No se puede leer " + path + ": falta el permiso de acceso a todos los archivos.",
                      "Cannot read " + path + ": all files access is missing."), true);
                return;
            }
            prefs().edit().putString("folder", path).remove("dump").apply();
            refresh();
            return;
        }
        // a dump: it must be the one of romfs_source.bin
        String f = folder();
        Source src = f == null ? null : readSource(new File(dataRoot(f), "romfs_source.bin"));
        if (!isDumpName(path) || !isTheDump(new File(path), src)) {
            refresh();
            say(t("Ese archivo no es el dump con el que se recompil\u00f3 el juego", "That file is not the dump the game was recompiled from")
                + (src != null ? " (" + src.hint + ")" : "") + ":\n" + path, true);
            return;
        }
        prefs().edit().putString("dump", path).apply();
        refresh();
    }

    // ---- views ------------------------------------------------------------------------------------------------------

    void setMode(Mode m) {
        mode = m;
        boolean home = m == Mode.HOME;
        statusView.setVisibility(home ? View.VISIBLE : View.GONE);
        logView.setVisibility(home ? View.GONE : View.VISIBLE);
        cancel.setVisibility(home ? View.GONE : View.VISIBLE);
        for (Button b : new Button[] {play, pickFolder, pickDump, log}) b.setVisibility(home ? View.VISIBLE : View.GONE);
        if (!home) {
            access.setVisibility(View.GONE);
            switchEdition.setVisibility(View.GONE);
        }
    }

    // Portrait: the title, the buttons one under the other, and the state (or the log) below.
    void buildViews() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Color.rgb(16, 20, 34));
        page.setPadding(dp(20), dp(28), dp(20), dp(16));

        title = text("", 20, Color.WHITE);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setPadding(0, 0, 0, dp(12));
        page.addView(title);

        play = button(t("Jugar", "Play"), new View.OnClickListener() { public void onClick(View v) { start(); } });
        access = button(t("Dar permiso", "Grant access"), new View.OnClickListener() { public void onClick(View v) { askAccess(); } });
        pickFolder = button(t("Elegir carpeta del port", "Choose port folder"), new View.OnClickListener() { public void onClick(View v) { pickFolderSaf(); } });
        pickDump = button(t("Elegir dump (.xci / .nsp)", "Choose dump (.xci / .nsp)"), new View.OnClickListener() { public void onClick(View v) { pickDumpSaf(); } });
        log = button(t("Ver nx.log", "Show nx.log"), new View.OnClickListener() { public void onClick(View v) { showLog(); } });
        cancel = button(t("Volver", "Back"), new View.OnClickListener() { public void onClick(View v) { setMode(Mode.HOME); refresh(); } });
        switchEdition = button(t("Cambiar de edici\u00f3n", "Switch edition"), new View.OnClickListener() { public void onClick(View v) {
            edition = edition.equals("global") ? "japan" : "global";
            prefs().edit().putString("edition", edition).remove("dump").apply();
            refresh();
        } });
        switchEdition.setVisibility(View.GONE);
        for (Button b : new Button[] {play, access, pickFolder, switchEdition, pickDump, log, cancel}) page.addView(b);

        status = text("", 14, Color.WHITE);
        status.setTypeface(Typeface.MONOSPACE);
        status.setPadding(0, dp(12), 0, 0);
        statusView = new ScrollView(this);
        statusView.addView(status);
        page.addView(statusView, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        logView = new ScrollView(this);
        logText = text("", 11, Color.rgb(200, 206, 220));
        logText.setTypeface(Typeface.MONOSPACE);
        logText.setTextIsSelectable(true);
        logView.addView(logText);
        page.addView(logView, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        setContentView(page);
        setMode(Mode.HOME);
    }

    int dp(int v) { return (int) (v * getResources().getDisplayMetrics().density + 0.5f); }

    TextView text(String s, int sp, int color) {
        TextView tv = new TextView(this);
        tv.setText(s);
        tv.setTextSize(sp);
        tv.setTextColor(color);
        return tv;
    }

    Button button(String label, View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(label);
        b.setAllCaps(false);
        b.setOnClickListener(l);
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.setMargins(0, 0, 0, dp(6));
        b.setLayoutParams(p);
        b.setGravity(Gravity.CENTER);
        return b;
    }
}
