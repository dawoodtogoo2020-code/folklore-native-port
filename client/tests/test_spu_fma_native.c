/* Exact runtime FMA regression. Compile with and without SPU_NATIVE_FMA. */
#include "../native/spu_fma_native.h"
#include <immintrin.h>
#include <cpuid.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
#include <fenv.h>
#pragma STDC FENV_ACCESS ON
typedef union V { float f[4]; uint32_t u[4]; } V;
__attribute__((target("xsave"))) static uint64_t xcr0(void) { return _xgetbv(0); }
static int capable(void) {
    unsigned a,b,c,d;
    if(!__get_cpuid(1,&a,&b,&c,&d)) return 0;
    const unsigned needed=(1u<<12)|(1u<<27)|(1u<<28);
    return (c&needed)==needed && (xcr0()&6)==6;
}
__attribute__((noinline)) static V scalar(V a,V b,V c,int op) {
    u128 av,bv,cv,r;
    memcpy(&av,a.u,16);memcpy(&bv,b.u,16);memcpy(&cv,c.u,16);
    r=op==0?spu_scalar_fma(av,bv,cv):op==1?spu_scalar_fms(av,bv,cv):spu_scalar_fnms(av,bv,cv);
    V out;memcpy(out.u,&r,16);return out;
}
static V native(V a,V b,V c,int op) {
    u128 av,bv,cv,r;
    memcpy(&av,a.u,16);memcpy(&bv,b.u,16);memcpy(&cv,c.u,16);
    r=op==0?spu_fma(av,bv,cv):op==1?spu_fms(av,bv,cv):spu_fnms(av,bv,cv);
    V out;memcpy(out.u,&r,16);return out;
}
static uint32_t rng=0x6f932ea1;
static uint32_t next(void) { rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng; }
static const uint32_t edge[]={0,0x80000000,1,0x007fffff,0x00800000,0x80800000,
    0x3f800000,0xbf800000,0x3f800001,0x7f7fffff,0xff7fffff,0x7f800000,
    0xff800000,0x7fc12345,0xffc23456,0x7f812345,0xff812345};
int main(int argc,char**argv) {
    const int available=capable();
    printf("cpu_os_fma=%d\n",available);
    const int forced=argc>1&&!strcmp(argv[1],"--force-fallback");
    if(forced) _putenv_s("SPU_NATIVE_FMA_OFF","1");
    unsigned failures=0;
    fenv_t original; fegetenv(&original); unsigned original_mx=_mm_getcsr();
    u128 one={0},probe;
    for(int i=0;i<4;i++) one._f32[i]=1.0f;
    _mm_setcsr((original_mx&~0x3fu)|0x1f80u);
    int expected=0;
#if defined(SPU_NATIVE_FMA)
    expected=!forced;
    if(expected && (!available || !spu_native_fma_enabled())) {
        fprintf(stderr,"Native qualification requires working CPU/OS FMA and an enabled path\n");
        return 2;
    }
    if(forced && spu_native_fma_enabled()) failures++;
#endif
    const int used=spu_native_fma_try(one,one,one,0,&probe);
    failures+=used!=expected;
    printf("packed_path_expected=%d packed_path_observed=%d\n",expected,used);
    /* Clear sticky flags before unmasking. The guard must decline without FP work. */
    for(unsigned mask=0x80;mask<=0x1000;mask<<=1) {
        _mm_setcsr(((original_mx&~0x3fu)|0x1f80u)&~mask);
        failures+=spu_native_fma_try(one,one,one,0,&probe)!=0;
    }
    _mm_setcsr((original_mx&~0x3fu)|0x1f80u);
    printf("unmasked_exception_guard_checks=6 failures=%u\n",failures);
    const int rounds[]={FE_TONEAREST,FE_DOWNWARD,FE_UPWARD,FE_TOWARDZERO};
    for(int rm=0;rm<4;rm++) for(int flush=0;flush<4;flush++) for(int kind=0;kind<3;kind++) {
        unsigned bits=0,flags=0,mxflags=0,cases=0;
        fesetround(rounds[rm]); unsigned control=(_mm_getcsr()&~(0x8040u|0x3fu))|0x1f80u;
        if(flush&1) control|=0x8000;
        if(flush&2) control|=0x40;
        for(int n=0;n<10000;n++) {
            V a,b,c;
            for(int l=0;l<4;l++) {
                uint32_t* dest[]={&a.u[l],&b.u[l],&c.u[l]};
                for(int j=0;j<3;j++) {
                    uint32_t u=next();
                    if(kind==0) u=(u&0x807fffff)|((112u+((u>>24)%30u))<<23);
                    else if(kind==1) u=edge[u%(sizeof edge/sizeof edge[0])];
                    *dest[j]=u;
                }
            }
            for(int op=0;op<3;op++) {
                feclearexcept(FE_ALL_EXCEPT);_mm_setcsr(control);
                V s=scalar(a,b,c,op);int sf=fetestexcept(FE_ALL_EXCEPT);unsigned sm=_mm_getcsr()&0x3f;
                feclearexcept(FE_ALL_EXCEPT);_mm_setcsr(control);
                V h=native(a,b,c,op);int hf=fetestexcept(FE_ALL_EXCEPT);unsigned hm=_mm_getcsr()&0x3f;
                cases++; flags+=sf!=hf;mxflags+=sm!=hm;
                for(int l=0;l<4;l++) if(s.u[l]!=h.u[l]) {
                    if(bits==0 && rm==0 && flush==0) printf("first kind=%d op=%d a=%08x b=%08x c=%08x scalar=%08x native=%08x sf=%x hf=%x sm=%x hm=%x\n",kind,op,a.u[l],b.u[l],c.u[l],s.u[l],h.u[l],sf,hf,sm,hm);
                    bits++;
                }
            }
        }
        failures+=bits+flags+mxflags;
        printf("rm=%d flush=%d kind=%d vectors=%u bits=%u flags=%u mxflags=%u\n",rm,flush,kind,cases,bits,flags,mxflags);
    }
    fesetenv(&original);_mm_setcsr(original_mx); printf("mismatches=%u\n",failures); return failures?1:0;
}
