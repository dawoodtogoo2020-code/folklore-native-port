package org.folklore.port;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.NoSuchFileException;
import java.nio.file.attribute.BasicFileAttributes;

/** Writable volumes use internal storage; existing external data needs migration.
 * This validates trusted app paths, not concurrent hostile path substitution. */
final class FolkloreWritablePaths {
    private final File base, external;
    FolkloreWritablePaths(File internalBase, File externalTaskRoot) {
        base=internalBase; external=externalTaskRoot;
    }
    String rootPathForDisplay() {
        return base==null?null:new File(base,"Folklore").getAbsolutePath();
    }
    File root(boolean create) throws IOException {
        if(base==null)throw new IOException("App-private storage is unavailable");
        File canonicalBase=base.getCanonicalFile();
        if(!canonicalBase.isDirectory())throw new IOException("App-private files directory is missing");
        if(external!=null) {
            if(Files.isSymbolicLink(external.toPath()))throw new IOException("External task root must not be a symbolic link");
            File legacy=new File(external,"volumes");
            try {
                Files.readAttributes(legacy.toPath(),BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS);
                checkEmptyLegacy(legacy,0);
            } catch(NoSuchFileException absent) {
                // Only genuine absence is harmless. Permission/I/O errors fail.
            }
        }
        return directory(canonicalBase,"Folklore",create);
    }
    private static void checkEmptyLegacy(File dir,int depth) throws IOException {
        final String error="Existing external volumes require migration; files were preserved";
        if(depth>64)throw new IOException(error);
        BasicFileAttributes attr;
        try {attr=Files.readAttributes(dir.toPath(),BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS);}
        catch(IOException e) {throw new IOException(error,e);}
        if(!attr.isDirectory()||attr.isSymbolicLink())throw new IOException(error);
        File[] children=dir.listFiles();
        if(children==null)throw new IOException(error);
        for(File child:children)checkEmptyLegacy(child,depth+1);
    }
    static File directory(File root,String relative,boolean create) throws IOException {
        if(root==null||relative==null||relative.isEmpty()||new File(relative).isAbsolute())
            throw new IOException("Invalid writable path");
        if(Files.isSymbolicLink(root.toPath())||!root.isDirectory())throw new IOException("Invalid writable root");
        File canonicalRoot=root.getCanonicalFile(),current=canonicalRoot;
        for(String component:relative.split("/",-1)) {
            if(component.isEmpty()||component.equals(".")||component.equals("..")||component.indexOf('\\')>=0)
                throw new IOException("Invalid writable path component");
            File next=new File(current,component);
            if(Files.isSymbolicLink(next.toPath()))throw new IOException("Writable symbolic links are not allowed");
            File canonical=next.getCanonicalFile();
            if(!inside(canonicalRoot,canonical))throw new IOException("Writable path escapes app storage");
            if(create&&!next.exists()&&!next.mkdir()&&!next.isDirectory())throw new IOException("Cannot create writable directory");
            if(!next.isDirectory()||!next.canWrite())throw new IOException("Writable directory is unavailable");
            current=canonical;
        }
        return current;
    }
    static File settings(File root,boolean requireParent) throws IOException {
        // Always require an existing checked parent. Validation prepares it first.
        File parent=directory(root,"volumes",false),file=new File(parent,"graphics.ini");
        if(Files.isSymbolicLink(file.toPath()))throw new IOException("Settings must not be a symbolic link");
        File canonical=file.getCanonicalFile();
        if(!inside(root.getCanonicalFile(),canonical)||(file.exists()&&!file.isFile()))
            throw new IOException("Invalid settings file");
        return canonical;
    }
    private static boolean inside(File root,File child) {
        String prefix=root.getPath();if(!prefix.endsWith(File.separator))prefix+=File.separator;
        return child.getPath().startsWith(prefix)&&child.getPath().length()>prefix.length();
    }
}
