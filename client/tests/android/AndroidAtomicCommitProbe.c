#include <jni.h>
#include "ps3emu/strict_fs.h"

JNIEXPORT jstring JNICALL Java_org_folklore_storagecheck_StorageProbe_nativeCommit
  (JNIEnv* env,jclass type,jstring root) {
    (void)type;
    const char* base=(*env)->GetStringUTFChars(env,root,NULL);
    char dir[4096],source[4096],dest[4096],result[256];
    int checks=0;
    if(snprintf(dir,sizeof dir,"%s/commit-native-XXXXXX",base)>=(int)sizeof dir)return NULL;
    (*env)->ReleaseStringUTFChars(env,root,base);
    if(!mkdtemp(dir))return (*env)->NewStringUTF(env,"mkdtemp failed");
    snprintf(source,sizeof source,"%s/source",dir);
    snprintf(dest,sizeof dest,"%s/destination",dir);
    setenv("PS3_STRICT_FS_WRITE_ROOT",dir,1);
    setenv("PS3_STRICT_FS_READONLY","0",1);
#define CHECK(c) do { if(!(c)){snprintf(result,sizeof result,"FAIL check=%d errno=%d",checks+1,errno);goto done;} ++checks;} while(0)
    FILE* f=ps3_strict_fs_fopen(source,"wbx"); CHECK(f!=NULL);
    size_t n=ps3_strict_fs_fwrite("first",1,5,f);int closed=fclose(f);CHECK(n==5&&closed==0);
    CHECK(ps3_strict_fs_commit_new_file(source,dest)==0);
    struct stat st;CHECK(lstat(source,&st)==-1&&errno==ENOENT);
    CHECK(lstat(dest,&st)==0&&st.st_nlink==1&&st.st_size==5);
    f=ps3_strict_fs_fopen(source,"wbx");CHECK(f!=NULL);
    n=ps3_strict_fs_fwrite("second",1,6,f);closed=fclose(f);CHECK(n==6&&closed==0);
    CHECK(ps3_strict_fs_commit_new_file(source,dest)==-1&&errno==EEXIST);
    CHECK(lstat(source,&st)==0&&st.st_size==6);
    f=fopen(dest,"rb");CHECK(f!=NULL);char contents[8]={0};n=fread(contents,1,7,f);fclose(f);
    CHECK(n==5&&!strcmp(contents,"first"));
    CHECK(ps3_strict_fs_replace_file(source,dest)==0);
    f=fopen(dest,"rb");CHECK(f!=NULL);memset(contents,0,sizeof contents);n=fread(contents,1,7,f);fclose(f);
    CHECK(n==6&&!strcmp(contents,"second"));
    snprintf(result,sizeof result,"PASS %d checks: atomic new commit, no overwrite, source preservation, replacement",checks);
done:
    if(unlink(source)&&errno!=ENOENT)snprintf(result,sizeof result,"FAIL source cleanup");
    if(unlink(dest)&&errno!=ENOENT)snprintf(result,sizeof result,"FAIL destination cleanup");
    if(rmdir(dir))snprintf(result,sizeof result,"FAIL directory cleanup");
    unsetenv("PS3_STRICT_FS_WRITE_ROOT");
    return (*env)->NewStringUTF(env,result);
}
