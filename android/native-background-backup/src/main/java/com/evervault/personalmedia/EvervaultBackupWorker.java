package com.evervault.personalmedia;

import android.content.ContentResolver;
import android.content.Context;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.net.Uri;
import android.provider.MediaStore;
import androidx.annotation.NonNull;
import androidx.work.Worker;
import androidx.work.WorkerParameters;
import org.json.JSONObject;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.HashSet;
import java.util.Set;

public class EvervaultBackupWorker extends Worker {
    private static final String PREFS = "evervault_backup";
    private final SharedPreferences prefs;
    private String serverUrl;
    private String token;

    public EvervaultBackupWorker(@NonNull Context context, @NonNull WorkerParameters params) {
        super(context, params);
        prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    @NonNull @Override
    public Result doWork() {
        if (!prefs.getBoolean("enabled", false)) return Result.success();
        serverUrl = prefs.getString("serverUrl", "");
        token = prefs.getString("token", "");
        if (serverUrl.isEmpty() || token.isEmpty()) return Result.failure();
        try {
            backupCollection(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, "I");
            backupCollection(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, "V");
            prefs.edit().putString("lastRun", new SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault()).format(new Date())).apply();
            return Result.success();
        } catch (AuthException e) {
            // Token expired. Stop repeated attempts until the owner signs in again.
            prefs.edit().putBoolean("enabled", false).apply();
            return Result.failure();
        } catch (Exception e) {
            return Result.retry();
        }
    }

    private void backupCollection(Uri collection, String prefix) throws Exception {
        ContentResolver resolver = getApplicationContext().getContentResolver();
        String[] projection = {
            MediaStore.MediaColumns._ID, MediaStore.MediaColumns.DISPLAY_NAME,
            MediaStore.MediaColumns.SIZE, MediaStore.MediaColumns.MIME_TYPE
        };
        String sort = MediaStore.MediaColumns.DATE_ADDED + " ASC";
        try (Cursor cursor = resolver.query(collection, projection, null, null, sort)) {
            if (cursor == null) return;
            int idCol = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns._ID);
            int nameCol = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DISPLAY_NAME);
            int sizeCol = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.SIZE);
            int mimeCol = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.MIME_TYPE);
            while (cursor.moveToNext()) {
                if (isStopped()) throw new InterruptedException("Work stopped");
                long id = cursor.getLong(idCol);
                String key = prefix + ":" + id;
                if (isUploaded(key)) continue;
                String name = cursor.getString(nameCol);
                long size = cursor.getLong(sizeCol);
                String mime = cursor.isNull(mimeCol) ? "application/octet-stream" : cursor.getString(mimeCol);
                if (size <= 0 || name == null) continue;
                Uri item = Uri.withAppendedPath(collection, Long.toString(id));
                upload(resolver, item, name, mime, size);
                markUploaded(key);
            }
        }
    }

    private boolean isUploaded(String key) {
        return prefs.getStringSet("uploaded", new HashSet<String>()).contains(key);
    }
    private void markUploaded(String key) {
        Set<String> keys = new HashSet<>(prefs.getStringSet("uploaded", new HashSet<String>()));
        keys.add(key);
        prefs.edit().putStringSet("uploaded", keys).apply();
    }

    private void upload(ContentResolver resolver, Uri uri, String name, String mime, long size) throws Exception {
        JSONObject start = new JSONObject();
        start.put("name", name); start.put("size", size); start.put("mime", mime);
        JSONObject session = requestJson("POST", "/api/uploads", start.toString(), null);
        String uploadId = session.getString("upload_id");
        long offset = session.optLong("offset", 0);
        byte[] buffer = new byte[1024 * 1024];
        try (InputStream in = resolver.openInputStream(uri)) {
            if (in == null) throw new Exception("Cannot read media");
            // The server's chunk protocol is sequential; new sessions start at offset zero.
            while (offset < size) {
                int wanted = (int)Math.min(buffer.length, size - offset);
                int count = 0;
                while (count < wanted) {
                    int n = in.read(buffer, count, wanted - count);
                    if (n < 0) break;
                    count += n;
                }
                if (count <= 0) throw new Exception("Media read ended early");
                byte[] chunk = new byte[count];
                System.arraycopy(buffer, 0, chunk, 0, count);
                JSONObject ack = requestJson("PUT", "/api/uploads/" + uploadId + "?offset=" + offset, null, chunk);
                offset = ack.getLong("offset");
            }
        }
        requestJson("POST", "/api/uploads/" + uploadId + "/finish", "{}", null);
    }

    private JSONObject requestJson(String method, String path, String json, byte[] raw) throws Exception {
        HttpURLConnection c = (HttpURLConnection)new URL(serverUrl + path).openConnection();
        c.setRequestMethod(method);
        c.setConnectTimeout(30000); c.setReadTimeout(120000);
        c.setRequestProperty("Authorization", "Bearer " + token);
        if (raw != null) {
            c.setDoOutput(true); c.setRequestProperty("Content-Type", "application/octet-stream");
            c.setFixedLengthStreamingMode(raw.length);
            try (OutputStream out = c.getOutputStream()) { out.write(raw); }
        } else if (json != null) {
            c.setDoOutput(true); c.setRequestProperty("Content-Type", "application/json");
            byte[] bytes = json.getBytes("UTF-8");
            try (OutputStream out = c.getOutputStream()) { out.write(bytes); }
        }
        int status = c.getResponseCode();
        InputStream response = status < 400 ? c.getInputStream() : c.getErrorStream();
        ByteArrayOutputStream responseBuffer = new ByteArrayOutputStream();\n        byte[] responseChunk = new byte[4096];\n        int responseCount;\n        while ((responseCount = response.read(responseChunk)) != -1) responseBuffer.write(responseChunk, 0, responseCount);\n        byte[] bytes = responseBuffer.toByteArray();
        c.disconnect();
        if (status == 401) throw new AuthException();
        if (status < 200 || status >= 300) throw new Exception("Server returned " + status + ": " + new String(bytes, "UTF-8"));
        return new JSONObject(new String(bytes, "UTF-8"));
    }

    private static class AuthException extends Exception {}
}
