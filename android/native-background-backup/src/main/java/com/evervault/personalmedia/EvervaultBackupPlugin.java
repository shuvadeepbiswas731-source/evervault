package com.evervault.personalmedia;

import android.Manifest;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import androidx.work.Constraints;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;
import com.getcapacitor.*;
import com.getcapacitor.annotation.*;
import org.json.JSONObject;
import java.util.concurrent.TimeUnit;

@CapacitorPlugin(
    name = "EvervaultBackup",
    permissions = {
        @Permission(alias = "mediaImages", strings = { Manifest.permission.READ_MEDIA_IMAGES }),
        @Permission(alias = "mediaVideo", strings = { Manifest.permission.READ_MEDIA_VIDEO }),
        @Permission(alias = "legacyMedia", strings = { Manifest.permission.READ_EXTERNAL_STORAGE })
    }
)
public class EvervaultBackupPlugin extends Plugin {
    private static final String PREFS = "evervault_backup";
    private static final String WORK_NAME = "evervault-periodic-media-backup";

    private SharedPreferences prefs() {
        return getContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    @PluginMethod
    public void configure(PluginCall call) {
        String url = call.getString("serverUrl", "").trim();
        String token = call.getString("token", "").trim();
        String network = call.getString("network", "unmetered");
        if (!url.startsWith("https://") || token.isEmpty()) {
            call.reject("A valid HTTPS server address and active sign-in are required.");
            return;
        }
        prefs().edit().putString("serverUrl", url.replaceAll("/+$", ""))
            .putString("token", token)
            .putString("network", network.equals("connected") ? "connected" : "unmetered")
            .apply();
        JSObject result = new JSObject();
        result.put("saved", true);
        call.resolve(result);
    }

    @PluginMethod
    public void enable(PluginCall call) {
        if (Build.VERSION.SDK_INT >= 33) {
            requestPermissionForAliases(new String[]{"mediaImages", "mediaVideo"}, call, "mediaPermissionResult");
        } else {
            requestPermissionForAlias("legacyMedia", call, "mediaPermissionResult");
        }
    }

    @PermissionCallback
    private void mediaPermissionResult(PluginCall call) {
        if (Build.VERSION.SDK_INT >= 33) {
            if (getPermissionState("mediaImages") != PermissionState.GRANTED ||
                getPermissionState("mediaVideo") != PermissionState.GRANTED) {
                call.reject("Allow access to photos and videos in Android settings to enable backup.");
                return;
            }
        } else if (getPermissionState("legacyMedia") != PermissionState.GRANTED) {
            call.reject("Allow media access to enable backup.");
            return;
        }
        String url = prefs().getString("serverUrl", "");
        String token = prefs().getString("token", "");
        if (url.isEmpty() || token.isEmpty()) {
            call.reject("Sign in and configure the server address first.");
            return;
        }
        boolean unmetered = !"connected".equals(prefs().getString("network", "unmetered"));
        Constraints constraints = new Constraints.Builder()
            .setRequiresCharging(true)
            .setRequiredNetworkType(unmetered ? NetworkType.UNMETERED : NetworkType.CONNECTED)
            .build();
        PeriodicWorkRequest request = new PeriodicWorkRequest.Builder(
            EvervaultBackupWorker.class, 15, TimeUnit.MINUTES)
            .setConstraints(constraints)
            .addTag(WORK_NAME)
            .build();
        WorkManager.getInstance(getContext()).enqueueUniquePeriodicWork(
            WORK_NAME, ExistingPeriodicWorkPolicy.UPDATE, request);
        prefs().edit().putBoolean("enabled", true).apply();
        JSObject result = new JSObject();
        result.put("enabled", true);
        result.put("message", "Automatic backup scheduled. Android runs it periodically while charging.");
        call.resolve(result);
    }

    @PluginMethod
    public void disable(PluginCall call) {
        WorkManager.getInstance(getContext()).cancelUniqueWork(WORK_NAME);
        prefs().edit().putBoolean("enabled", false).apply();
        JSObject result = new JSObject();
        result.put("enabled", false);
        call.resolve(result);
    }

    @PluginMethod
    public void getStatus(PluginCall call) {
        JSObject result = new JSObject();
        result.put("enabled", prefs().getBoolean("enabled", false));
        result.put("network", prefs().getString("network", "unmetered"));
        result.put("lastRun", prefs().getString("lastRun", "not run yet"));
        call.resolve(result);
    }
}
