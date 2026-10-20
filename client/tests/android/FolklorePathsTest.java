package org.folklore.port;

import java.io.File;
import java.io.IOException;
import java.io.InterruptedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

/** Local JVM path-policy checks. The real user ELF is read locally, never sent to a provider. */
public final class FolklorePathsTest {
    private static int checks;
    private static void check(boolean value, String description) {
        ++checks;
        if (!value) throw new AssertionError(description);
    }
    private static void rejectArguments(FolklorePaths paths) {
        try { paths.buildArguments(); throw new AssertionError("unsafe arguments accepted"); }
        catch (IOException expected) { ++checks; }
    }
    public static void main(String[] args) throws Exception {
        if (args.length != 2) throw new IllegalArgumentException("fresh fixture root and original ELF path required");
        Path fixture=Paths.get(args[0]).toAbsolutePath();
        check(!Files.exists(fixture),"fixture must be fresh");
        Files.createDirectory(fixture);
        Path base=Files.createDirectory(fixture.resolve("app-files"));
        Path internal=Files.createDirectory(fixture.resolve("internal-files"));
        Path writable=internal.resolve("Folklore");
        Path outside=Files.createDirectory(fixture.resolve("outside"));
        Files.write(outside.resolve("preserve"),new byte[]{1,2,3});
        FolklorePaths paths=new FolklorePaths(base.toFile(),internal.toFile());
        check(!new FolklorePaths(null,internal.toFile()).validateAndPrepare().ok,"unavailable storage fails");
        check(!paths.validateAndPrepare().ok,"missing inputs fail");
        Path root=base.resolve("Folklore");
        check(Files.isDirectory(writable.resolve("volumes/dev_hdd1")),"writable volume prepared");
        check(!Files.exists(root.resolve("game")),"no invented game input");
        rejectArguments(paths);
        Path elf=root.resolve(FolklorePaths.ELF_RELATIVE);
        Files.createDirectories(elf.getParent());
        Path sfo=root.resolve(FolklorePaths.SFO_RELATIVE);
        Files.write(sfo,"nonempty SFO fixture; native title validation is separate".getBytes(StandardCharsets.UTF_8));
        Files.createDirectories(root.resolve("dev_flash/sys/external"));
        Files.write(elf,new byte[]{1,2,3});
        check(!paths.validateAndPrepare().ok,"wrong ELF rejected");
        Files.delete(elf);
        Files.copy(Paths.get(args[1]),elf);
        Path save=writable.resolve(FolklorePaths.SAVEDATA_RELATIVE).resolve("keep-save.bin");
        Files.write(save,new byte[]{9,8,7});
        check(paths.validateAndPrepare().ok,"correct local ELF passes preflight");
        check(Arrays.equals(Files.readAllBytes(save),new byte[]{9,8,7}),"existing save preserved");
        String[] arguments=paths.buildArguments();
        check(arguments.length==7,"exact argument count");
        check(arguments[0].equals(elf.toRealPath().toString()),"canonical ELF argument");
        check(arguments[1].equals(root.resolve("game").toRealPath().toString()),"VFS argument");
        check(arguments[2].equals(writable.resolve("volumes/dev_hdd0").toRealPath().toString()) && arguments[3].endsWith("/dev_flash"),"volume and firmware arguments");
        check(arguments[4].equals("--contained-writes") && arguments[5].equals("--vulkan"),"native mode flags");
        check(arguments[6].equals("--settings="+writable.resolve("volumes/graphics.ini")),"contained settings path");
        check(!FolklorePaths.isStrictlyInside(root.toFile(),base.resolve("FolkloreSibling/input").toFile()),"prefix sibling rejected");
        Path preserved=elf.resolveSibling("ELF-preserved");
        Files.move(elf,preserved);Files.createSymbolicLink(elf,outside.resolve("preserve"));
        check(!paths.validateAndPrepare().ok,"outside ELF link rejected");rejectArguments(paths);
        Files.delete(elf);Files.move(preserved,elf);
        Path settings=writable.resolve("volumes/graphics.ini");
        Files.createSymbolicLink(settings,outside.resolve("not-created.ini"));
        check(!paths.validateAndPrepare().ok,"dangling settings escape rejected");rejectArguments(paths);
        check(!Files.exists(outside.resolve("not-created.ini")),"outside settings untouched");
        Files.delete(settings);
        Path volumes=writable.resolve("volumes"), held=writable.resolve("volumes-preserved");
        Files.move(volumes,held);Files.createSymbolicLink(volumes,outside);
        check(!paths.validateAndPrepare().ok,"outside volume link rejected");
        check(!Files.exists(outside.resolve("dev_hdd0")),"no directories created outside root");
        Files.delete(volumes);Files.move(held,volumes);
        Thread.currentThread().interrupt();
        try { paths.validateAndPrepare(); throw new AssertionError("cancelled hash succeeded"); }
        catch (InterruptedIOException expected) { ++checks; }
        finally { Thread.interrupted(); }
        check(paths.validateAndPrepare().ok,"retry after cancellation succeeds");
        check(Arrays.equals(Files.readAllBytes(outside.resolve("preserve")),new byte[]{1,2,3}),"outside sentinel preserved");
        System.out.println("Android path policy: "+checks+" checks passed; fixture retained at "+fixture);
    }
}
