// The app's home screen (docs/nvn/ANDROID-PORT.md, "Home screen"): finds or lets the player pick the folder with the
// game's data, extracted from any of our data zips (the phone's, the PC's, the portable one), and starts the game
// (android.app.NativeActivity, libwonder.so) on it. The choice goes to <files>/data_root.txt, which android_main
// reads (platform_android.cpp). The interface is built in code: no layouts, no resources but the app's own.
package io.github.belmantegu.wonderrecomp;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.TextView;

import java.io.File;
import java.io.FileOutputStream;
import java.io.FileReader;
import java.io.BufferedReader;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

public class Home extends Activity {
    // Where a data root may sit inside the folder the player picks: the phone zip, the portable zip, the PC data zip
    // (extracted at the repository root or on its own), or the folder itself.
    static final String[] INSIDE = {"", "Super Mario Bros. Wonder", "data", "WonderPC/data", "private/wonder",
                                    "SwitchGamesRecomp/private/wonder", "wonder"};
    static final String STORAGE = "/storage/emulated/0";

    TextView status;
    TextView browsing;
    ListView list;
    Button play;
    File current = new File(STORAGE);
    String root;  // the data root chosen or found, or null

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Color.rgb(24, 26, 32));
        page.setPadding(dp(24), dp(16), dp(24), dp(16));

        TextView title = text("Super Mario Bros. Wonder", 24, Color.WHITE);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        page.addView(title);
        page.addView(text("Switch recomp - dados do seu cartucho", 13, Color.rgb(160, 168, 184)));

        status = text("", 15, Color.WHITE);
        status.setPadding(0, dp(12), 0, dp(12));
        page.addView(status);

        LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(LinearLayout.HORIZONTAL);
        play = button("Jogar", new View.OnClickListener() { public void onClick(View v) { start(); } });
        buttons.addView(play);
        buttons.addView(button("Usar esta pasta", new View.OnClickListener() { public void onClick(View v) { pick(current); } }));
        buttons.addView(button("Voltar pasta", new View.OnClickListener() { public void onClick(View v) {
            File up = current.getParentFile();
            if (up != null && up.getPath().startsWith(STORAGE)) browse(up);
        } }));
        buttons.addView(button("Permissao", new View.OnClickListener() { public void onClick(View v) { askAccess(); } }));
        page.addView(buttons);

        browsing = text("", 13, Color.rgb(160, 168, 184));
        browsing.setPadding(0, dp(10), 0, dp(4));
        page.addView(browsing);
        list = new ListView(this);
        list.setOnItemClickListener(new android.widget.AdapterView.OnItemClickListener() {
            public void onItemClick(android.widget.AdapterView<?> parent, View view, int pos, long id) {
                browse(new File(current, (String) parent.getItemAtPosition(pos)));
            }
        });
        page.addView(list, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        setContentView(page);

        root = load();
        if (root == null || !valid(new File(root))) root = autodetect();
        browse(root != null ? new File(root).getParentFile() : new File(STORAGE));
        refresh();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (list != null) browse(current);  // back from the permission page
        if (status != null) refresh();
    }

    // ---- data folder ------------------------------------------------------------------------------------------------

    static boolean valid(File r) { return new File(r, "analysis/main.bin").isFile() && new File(r, "extract/romfs").isDirectory(); }

    // The data root inside `f` (see INSIDE), or null.
    static File resolve(File f) {
        for (String in : INSIDE) {
            File r = in.isEmpty() ? f : new File(f, in);
            if (valid(r)) return r;
        }
        return null;
    }

    String autodetect() {
        List<File> places = new ArrayList<>();
        places.add(new File(STORAGE));
        places.add(new File(STORAGE, "Download"));
        places.add(new File(STORAGE, "Documents"));
        File[] top = new File(STORAGE).listFiles();
        if (top != null) places.addAll(Arrays.asList(top));
        for (File p : places) {
            if (!p.isDirectory()) continue;
            File r = resolve(p);
            if (r != null) return r.getPath();
        }
        return null;
    }

    void pick(File f) {
        File r = resolve(f);
        if (r == null) {
            status.setText("Nao encontrei os dados em\n" + f.getPath() + "\n\nEscolha a pasta extraida do zip (a que tem "
                           + "analysis, extract e spirv, ou uma pasta acima dela).");
            status.setTextColor(Color.rgb(255, 170, 120));
            play.setEnabled(false);
            return;
        }
        root = r.getPath();
        save(root);
        refresh();
    }

    void refresh() {
        if (!hasAccess()) {
            status.setText("Precisa de acesso a todos os arquivos para ler a pasta do jogo.\nToque em \"Permissao\".");
            status.setTextColor(Color.rgb(255, 170, 120));
            play.setEnabled(false);
            return;
        }
        if (root == null) {
            status.setText("Nenhuma pasta de dados encontrada.\nNavegue ate a pasta extraida e toque em \"Usar esta pasta\".");
            status.setTextColor(Color.rgb(255, 170, 120));
            play.setEnabled(false);
            return;
        }
        File r = new File(root);
        StringBuilder s = new StringBuilder("Dados: " + root + "\n");
        s.append(check(r, "analysis/main.bin", "modulos")).append(check(r, "extract/romfs", "RomFS"));
        s.append(check(r, "spirv/results.tsv", "shaders (results.tsv)")).append(check(r, "fonts/shared_5.ttf", "icones dos botoes"));
        status.setText(s.toString());
        status.setTextColor(Color.WHITE);
        play.setEnabled(valid(r));
    }

    static String check(File r, String path, String what) {
        return (new File(r, path).exists() ? "  ok   " : "  falta ") + what + "\n";
    }

    void start() {
        if (root == null || !valid(new File(root))) return;
        save(root);
        Intent i = new Intent();
        i.setClassName(this, "android.app.NativeActivity");
        startActivity(i);
        finish();
    }

    // ---- folder browser ---------------------------------------------------------------------------------------------

    void browse(File dir) {
        if (dir == null || !dir.isDirectory()) dir = new File(STORAGE);
        current = dir;
        List<String> names = new ArrayList<>();
        File[] kids = dir.listFiles();
        if (kids != null)
            for (File k : kids)
                if (k.isDirectory() && !k.getName().startsWith(".")) names.add(k.getName());
        java.util.Collections.sort(names, String.CASE_INSENSITIVE_ORDER);
        browsing.setText("Pasta: " + dir.getPath() + (resolve(dir) != null ? "   (tem os dados)" : ""));
        list.setAdapter(new ArrayAdapter<String>(this, android.R.layout.simple_list_item_1, names) {
            @Override
            public View getView(int pos, View v, ViewGroup parent) {
                TextView t = (TextView) super.getView(pos, v, parent);
                t.setTextColor(Color.rgb(220, 224, 232));
                return t;
            }
        });
    }

    // ---- storage and settings ---------------------------------------------------------------------------------------

    boolean hasAccess() { return Environment.isExternalStorageManager(); }

    void askAccess() {
        Intent i = new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:" + getPackageName()));
        startActivity(i);
    }

    File choiceFile() { return new File(getFilesDir(), "data_root.txt"); }

    void save(String path) {
        try (FileOutputStream o = new FileOutputStream(choiceFile())) {
            o.write(path.getBytes("UTF-8"));
        } catch (Exception e) {
            status.setText("Nao consegui gravar a escolha: " + e);
        }
    }

    String load() {
        try (BufferedReader r = new BufferedReader(new FileReader(choiceFile()))) {
            String line = r.readLine();
            return line == null || line.trim().isEmpty() ? null : line.trim();
        } catch (Exception e) {
            return null;
        }
    }

    // ---- views ------------------------------------------------------------------------------------------------------

    int dp(int v) { return (int) (v * getResources().getDisplayMetrics().density + 0.5f); }

    TextView text(String s, int sp, int color) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(sp);
        t.setTextColor(color);
        return t;
    }

    Button button(String label, View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(label);
        b.setAllCaps(false);
        b.setOnClickListener(l);
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        p.setMargins(0, 0, dp(8), 0);
        b.setLayoutParams(p);
        b.setGravity(Gravity.CENTER);
        return b;
    }
}
