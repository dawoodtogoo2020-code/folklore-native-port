package org.folklore.port;
import java.io.IOException;
import java.nio.file.*;
import java.util.Arrays;
public final class FolkloreWritablePathsTest {
    private static int checks;
    private interface Attempt {void run() throws Exception;}
    private static void reject(Attempt a) throws Exception {
        try {a.run();throw new AssertionError("unsafe path accepted");}catch(IOException expected){++checks;}
    }
    private static void check(boolean ok) {++checks;if(!ok)throw new AssertionError("check "+checks);}
    public static void main(String[] args) throws Exception {
        Path fixture=Files.createTempDirectory("folklore-storage-");
        Path base=Files.createDirectory(fixture.resolve("internal")),ext=Files.createDirectory(fixture.resolve("external"));
        Path outside=Files.createDirectory(fixture.resolve("outside"));
        Path sentinel=Files.write(outside.resolve("keep"),new byte[]{1,2,3});
        FolkloreWritablePaths policy=new FolkloreWritablePaths(base.toFile(),ext.toFile());
        reject(()->new FolkloreWritablePaths(null,ext.toFile()).root(true));
        reject(()->new FolkloreWritablePaths(base.resolve("missing").toFile(),ext.toFile()).root(true));
        reject(()->policy.root(false));
        Path root=policy.root(true).toPath();check(root.equals(base.resolve("Folklore").toRealPath()));
        for(String bad:new String[]{"","/outside",".","..","a/../b","a//b","a/","a\\b"})
            reject(()->FolkloreWritablePaths.directory(root.toFile(),bad,true));
        reject(()->FolkloreWritablePaths.directory(root.toFile(),"missing",false));
        Path volumes=FolkloreWritablePaths.directory(root.toFile(),"volumes/dev_hdd0",true).toPath().getParent();
        Path settings=FolkloreWritablePaths.settings(root.toFile(),true).toPath();check(!Files.exists(settings));
        Files.write(settings,new byte[]{5});check(FolkloreWritablePaths.settings(root.toFile(),true).equals(settings.toFile()));
        Files.delete(settings);Files.createDirectory(settings);reject(()->FolkloreWritablePaths.settings(root.toFile(),true));Files.delete(settings);
        Files.createSymbolicLink(settings,sentinel);reject(()->FolkloreWritablePaths.settings(root.toFile(),true));Files.delete(settings);
        Path link=root.resolve("link");
        for(Path target:new Path[]{outside,volumes,base.resolve("FolkloreSibling")}) {
            Files.createSymbolicLink(link,target);reject(()->FolkloreWritablePaths.directory(root.toFile(),"link/nested",true));Files.delete(link);
        }
        Path file=Files.write(root.resolve("file"),new byte[0]);reject(()->FolkloreWritablePaths.directory(root.toFile(),"file/nested",true));Files.delete(file);
        Path legacy=Files.createDirectories(ext.resolve("volumes/empty/nested")).getParent().getParent();check(policy.root(false).equals(root.toFile()));
        Path old=Files.write(legacy.resolve("old-save"),new byte[]{9});reject(()->policy.root(true));check(Arrays.equals(Files.readAllBytes(old),new byte[]{9}));Files.delete(old);
        Files.write(old,new byte[0]);reject(()->policy.root(false));Files.delete(old);
        for(Path target:new Path[]{outside,legacy.resolve("empty"),outside.resolve("missing")}) {
            Files.createSymbolicLink(old,target);reject(()->policy.root(false));Files.delete(old);
        }
        Path held=base.resolve("held");Files.move(root,held);Files.createSymbolicLink(root,held);reject(()->policy.root(true));Files.delete(root);Files.move(held,root);
        check(Arrays.equals(Files.readAllBytes(sentinel),new byte[]{1,2,3}));
        // Retain bounded owned fixtures for inspection. Never recursively delete
        // user-supplied paths or follow the test symlinks during cleanup.
        System.out.println("Writable storage: "+checks+" checks passed; fixture "+fixture);
    }
}
