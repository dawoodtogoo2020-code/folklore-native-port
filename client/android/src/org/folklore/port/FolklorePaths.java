package org.folklore.port;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InterruptedIOException;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Path policy for the Folklore task root. Android-free so it can be exercised by
 * plain JVM tests. Inputs live in external app storage; writable volumes live
 * in internal app storage so descriptor identity is stable on Android.
 */
final class FolklorePaths {
    static final String ROOT_NAME = "Folklore";
    static final String EXPECTED_ELF_SHA256 =
            "51a9eb16ceca3edfb7454b37a9e895f5a4e35bd515a40ae5b918f97c3a1b6c0e";

    static final String GAME_RELATIVE = "game";
    static final String ELF_RELATIVE = "game/PS3_GAME/USRDIR/EBOOT.elf";
    static final String SFO_RELATIVE = "game/PS3_GAME/PARAM.SFO";
    static final String DEV_FLASH_RELATIVE = "dev_flash";
    static final String DEV_FLASH_EXTERNAL_RELATIVE = "dev_flash/sys/external";
    static final String VOLUMES_RELATIVE = "volumes";
    static final String DEV_HDD0_RELATIVE = "volumes/dev_hdd0";
    static final String SAVEDATA_RELATIVE = "volumes/dev_hdd0/home/00000001/savedata";
    static final String DEV_HDD1_RELATIVE = "volumes/dev_hdd1";
    static final String SETTINGS_NAME = "graphics.ini";
    static final String SETTINGS_RELATIVE = "volumes/graphics.ini";

    private static final int HASH_BUFFER_BYTES = 64 * 1024;
    private static final char[] HEX = "0123456789abcdef".toCharArray();

    private final File baseDir;
    private final File root;
    private final FolkloreWritablePaths writable;

    FolklorePaths(File appExternalFilesDir, File appInternalFilesDir) {
        this.baseDir = appExternalFilesDir;
        this.root = appExternalFilesDir == null ? null : new File(appExternalFilesDir, ROOT_NAME);
        this.writable = new FolkloreWritablePaths(appInternalFilesDir, root);
    }

    /** Absolute (not canonicalized) task root for display, or null if storage is unavailable. */
    String rootPathForDisplay() {
        return root == null ? null : root.getAbsolutePath();
    }

    /**
     * Validates user-supplied inputs and prepares the app-owned writable volumes.
     * Performs disk IO and hashing; call off the UI thread. Never creates inputs
     * and never deletes or overwrites existing files.
     *
     * @throws InterruptedIOException if the calling thread was interrupted while hashing
     */
    Report validateAndPrepare() throws InterruptedIOException {
        List<String> problems = new ArrayList<String>();
        List<String> missing = new ArrayList<String>();
        if (root == null) {
            problems.add("App-specific external storage is unavailable. Make sure shared storage is mounted, then check again.");
            return new Report(false, null, problems, missing);
        }

        File canonicalRoot;
        try {
            canonicalRoot = canonicalRoot(true);
        } catch (IOException e) {
            problems.add("Task root is unusable: " + describe(e));
            return new Report(false, root.getAbsolutePath(), problems, missing);
        }

        try {
            File writableRoot = writable.root(true);
            FolkloreWritablePaths.directory(writableRoot, SAVEDATA_RELATIVE, true);
            FolkloreWritablePaths.directory(writableRoot, DEV_HDD1_RELATIVE, true);
            FolkloreWritablePaths.settings(writableRoot, true);
        } catch (IOException e) {
            problems.add("Writable volume setup failed: " + describe(e));
        }

        checkOptionalDirectory(canonicalRoot, GAME_RELATIVE, problems);
        File elf = checkInputFile(canonicalRoot, ELF_RELATIVE, "EBOOT.elf", problems, missing);
        if (elf != null) {
            checkElfHash(elf, problems);
        }
        File sfo = checkInputFile(canonicalRoot, SFO_RELATIVE, "PARAM.SFO", problems, missing);
        if (sfo != null) {
            checkReadable(sfo, "PARAM.SFO", problems);
        }
        checkInputDirectoryChain(canonicalRoot, DEV_FLASH_EXTERNAL_RELATIVE, problems, missing);

        return new Report(problems.isEmpty(), canonicalRoot.getPath(), problems, missing);
    }

    /**
     * Derives native arguments from the same canonical paths without hashing or
     * creating anything. Returns arguments after the executable name.
     *
     * @throws IOException if storage is unavailable or any path is missing or escapes the root
     */
    String[] buildArguments() throws IOException {
        File canonicalRoot = canonicalRoot(false);
        File writableRoot = writable.root(false);
        File elf = existingFile(canonicalRoot, ELF_RELATIVE);
        File game = existingDirectory(canonicalRoot, GAME_RELATIVE);
        File hdd0 = FolkloreWritablePaths.directory(writableRoot, DEV_HDD0_RELATIVE, false);
        File flash = existingDirectory(canonicalRoot, DEV_FLASH_RELATIVE);
        File settings = FolkloreWritablePaths.settings(writableRoot, true);
        return new String[] {
            elf.getPath(),
            game.getPath(),
            hdd0.getPath(),
            flash.getPath(),
            "--contained-writes",
            "--vulkan",
            "--settings=" + settings.getAbsolutePath()
        };
    }

    /** True if canonicalChild is strictly below canonicalParent (separator-aware, not a raw prefix). */
    static boolean isStrictlyInside(File canonicalParent, File canonicalChild) {
        String parent = canonicalParent.getPath();
        String child = canonicalChild.getPath();
        if (!parent.endsWith(File.separator)) {
            parent = parent + File.separator;
        }
        return child.length() > parent.length() && child.startsWith(parent);
    }

    static String sha256Hex(File file) throws IOException {
        MessageDigest digest;
        try {
            digest = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            throw new IOException("SHA-256 is unavailable", e);
        }
        byte[] buffer = new byte[HASH_BUFFER_BYTES];
        InputStream in = new FileInputStream(file);
        try {
            int read;
            while ((read = in.read(buffer)) != -1) {
                if (Thread.currentThread().isInterrupted()) {
                    throw new InterruptedIOException("File check cancelled");
                }
                digest.update(buffer, 0, read);
            }
        } finally {
            in.close();
        }
        byte[] hash = digest.digest();
        char[] out = new char[hash.length * 2];
        for (int i = 0; i < hash.length; i++) {
            out[i * 2] = HEX[(hash[i] >> 4) & 0xF];
            out[i * 2 + 1] = HEX[hash[i] & 0xF];
        }
        return new String(out);
    }

    private File canonicalRoot(boolean create) throws IOException {
        if (root == null) {
            throw new IOException("App-specific external storage is unavailable");
        }
        File canonicalBase = baseDir.getCanonicalFile();
        if (!canonicalBase.isDirectory()) {
            throw new IOException("App external files directory does not exist: " + baseDir.getAbsolutePath());
        }
        if (create && !root.exists() && !root.mkdir() && !root.isDirectory()) {
            throw new IOException("Cannot create " + root.getAbsolutePath());
        }
        File canonical = root.getCanonicalFile();
        if (!isStrictlyInside(canonicalBase, canonical)) {
            throw new IOException("Task root resolves outside app storage: " + canonical.getPath());
        }
        if (!canonical.isDirectory()) {
            throw new IOException("Task root is not a directory: " + canonical.getPath());
        }
        return canonical;
    }

    private static File resolve(File base, String relative) {
        File file = base;
        for (String part : relative.split("/")) {
            file = new File(file, part);
        }
        return file;
    }

    private static File requireInside(File canonicalRoot, File candidate, String relative) throws IOException {
        File canonical = candidate.getCanonicalFile();
        if (!isStrictlyInside(canonicalRoot, canonical)) {
            throw new IOException(relative + " resolves outside the task root (" + canonical.getPath() + ")");
        }
        return canonical;
    }

    private static void prepareWritableDirectory(File canonicalRoot, String relative) throws IOException {
        File current = canonicalRoot;
        StringBuilder walked = new StringBuilder();
        for (String part : relative.split("/")) {
            if (walked.length() > 0) {
                walked.append('/');
            }
            walked.append(part);
            File next = new File(current, part);
            if (!next.exists() && !next.mkdir() && !next.isDirectory()) {
                throw new IOException("Cannot create " + walked);
            }
            File canonical = requireInside(canonicalRoot, next, walked.toString());
            if (!canonical.isDirectory()) {
                throw new IOException(walked + " exists but is not a directory");
            }
            current = canonical;
        }
        if (!current.canWrite()) {
            throw new IOException(relative + " is not writable");
        }
    }

    private static void checkSettings(File canonicalRoot, List<String> problems) {
        File settings = resolve(canonicalRoot, SETTINGS_RELATIVE);
        if (Files.isSymbolicLink(settings.toPath())) {
            problems.add(SETTINGS_RELATIVE + " must not be a symbolic link.");
            return;
        }
        if (!settings.exists()) {
            return;
        }
        try {
            File canonical = requireInside(canonicalRoot, settings, SETTINGS_RELATIVE);
            if (!canonical.isFile()) {
                problems.add(SETTINGS_RELATIVE + " exists but is not a regular file.");
            }
        } catch (IOException e) {
            problems.add(describe(e));
        }
    }

    private static void checkOptionalDirectory(File canonicalRoot, String relative, List<String> problems) {
        File dir = resolve(canonicalRoot, relative);
        if (!dir.exists()) {
            return;
        }
        try {
            File canonical = requireInside(canonicalRoot, dir, relative);
            if (!canonical.isDirectory()) {
                problems.add(relative + " exists but is not a directory.");
            }
        } catch (IOException e) {
            problems.add(describe(e));
        }
    }

    private static File checkInputFile(File canonicalRoot, String relative, String label,
            List<String> problems, List<String> missing) {
        File file = resolve(canonicalRoot, relative);
        if (!file.exists()) {
            missing.add(relative);
            problems.add(label + " is missing: " + relative);
            return null;
        }
        File canonical;
        try {
            canonical = requireInside(canonicalRoot, file, relative);
        } catch (IOException e) {
            problems.add(describe(e));
            return null;
        }
        if (!canonical.isFile()) {
            problems.add(label + " is not a regular file: " + relative);
            return null;
        }
        if (!canonical.canRead()) {
            problems.add(label + " is not readable: " + relative);
            return null;
        }
        return canonical;
    }

    private static void checkElfHash(File elf, List<String> problems) throws InterruptedIOException {
        try {
            String actual = sha256Hex(elf);
            if (!EXPECTED_ELF_SHA256.equals(actual)) {
                problems.add("EBOOT.elf does not match the expected BCUS98147 1.00 executable (SHA-256 " + actual + ").");
            }
        } catch (InterruptedIOException e) {
            throw e;
        } catch (IOException e) {
            problems.add("EBOOT.elf could not be read: " + describe(e));
        }
    }

    private static void checkReadable(File file, String label, List<String> problems) {
        try {
            InputStream in = new FileInputStream(file);
            try {
                if (in.read() == -1) {
                    problems.add(label + " is empty.");
                }
            } finally {
                in.close();
            }
        } catch (IOException e) {
            problems.add(label + " could not be read: " + describe(e));
        }
    }

    private static void checkInputDirectoryChain(File canonicalRoot, String relative,
            List<String> problems, List<String> missing) {
        File current = canonicalRoot;
        StringBuilder walked = new StringBuilder();
        for (String part : relative.split("/")) {
            if (walked.length() > 0) {
                walked.append('/');
            }
            walked.append(part);
            String path = walked.toString();
            File next = new File(current, part);
            if (!next.exists()) {
                missing.add(path + "/");
                problems.add("Required directory is missing: " + path + "/");
                return;
            }
            File canonical;
            try {
                canonical = requireInside(canonicalRoot, next, path);
            } catch (IOException e) {
                problems.add(describe(e));
                return;
            }
            if (!canonical.isDirectory()) {
                problems.add(path + " is not a directory.");
                return;
            }
            if (!canonical.canRead()) {
                problems.add(path + " is not readable.");
                return;
            }
            current = canonical;
        }
    }

    private static File existingFile(File canonicalRoot, String relative) throws IOException {
        File canonical = requireInside(canonicalRoot, resolve(canonicalRoot, relative), relative);
        if (!canonical.isFile()) {
            throw new IOException(relative + " is missing or not a regular file");
        }
        return canonical;
    }

    private static File existingDirectory(File canonicalRoot, String relative) throws IOException {
        File canonical = requireInside(canonicalRoot, resolve(canonicalRoot, relative), relative);
        if (!canonical.isDirectory()) {
            throw new IOException(relative + " is missing or not a directory");
        }
        return canonical;
    }

    private static String describe(Throwable e) {
        String message = e.getMessage();
        return message != null ? message : e.getClass().getSimpleName();
    }

    static final class Report {
        final boolean ok;
        final String rootPath;
        final List<String> problems;
        final List<String> missingInputs;

        Report(boolean ok, String rootPath, List<String> problems, List<String> missingInputs) {
            this.ok = ok;
            this.rootPath = rootPath;
            this.problems = Collections.unmodifiableList(new ArrayList<String>(problems));
            this.missingInputs = Collections.unmodifiableList(new ArrayList<String>(missingInputs));
        }

        static Report failure(String rootPath, String problem) {
            List<String> problems = new ArrayList<String>();
            problems.add(problem);
            return new Report(false, rootPath, problems, new ArrayList<String>());
        }
    }
}
