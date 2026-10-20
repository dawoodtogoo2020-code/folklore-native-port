/* Opt-in packed implementation of the existing scalar fmaf contract.
 * Not SPU ISA emulation changes. Nonfinite operands retain library NaN rules.
 * The caller enables this only for separately qualified generated images. */
#ifndef SPU_FMA_NATIVE_H
#define SPU_FMA_NATIVE_H
#define spu_fma spu_scalar_fma
#define spu_fms spu_scalar_fms
#define spu_fnms spu_scalar_fnms
#include "spu_helpers.h"
#undef spu_fma
#undef spu_fms
#undef spu_fnms
#include <stdlib.h>
#if defined(SPU_NATIVE_FMA) && (defined(__GNUC__) || defined(__clang__)) && \
    (defined(__x86_64__) || defined(__i386__))
#include <immintrin.h>

static inline int spu_native_fma_enabled(void)
{
    static int enabled;
    int value=__atomic_load_n(&enabled,__ATOMIC_ACQUIRE);
    if (!value) {
        const char* off=getenv("SPU_NATIVE_FMA_OFF");
        /* Compiler CPU discovery includes OS AVX save/restore support. */
        value=(!off || off[0]!='1') && __builtin_cpu_supports("avx") &&
              __builtin_cpu_supports("fma") ? 1 : -1;
        __atomic_store_n(&enabled,value,__ATOMIC_RELEASE);
    }
    return value>0;
}

__attribute__((target("avx,fma"),noinline))
static u128 spu_native_fma_eval(u128 a,u128 b,u128 c,int op)
{
    __m128 av=_mm_loadu_ps(a._f32),bv=_mm_loadu_ps(b._f32),cv=_mm_loadu_ps(c._f32);
    const __m128 sign=_mm_castsi128_ps(_mm_set1_epi32((int)0x80000000u));
    if(op==1) cv=_mm_xor_ps(cv,sign);
    if(op==2) bv=_mm_xor_ps(bv,sign);
    u128 result;_mm_storeu_ps(result._f32,_mm_fmadd_ps(av,bv,cv));return result;
}

static inline int spu_native_fma_try(u128 a,u128 b,u128 c,int op,u128* result)
{
    if(!spu_native_fma_enabled()) return 0;
    /* A packed instruction could trap in a different lane order. */
    if((_mm_getcsr()&0x1f80u)!=0x1f80u) return 0;
    for(int i=0;i<4;i++)
        if((a._u32[i]&0x7f800000u)==0x7f800000u ||
           (b._u32[i]&0x7f800000u)==0x7f800000u ||
           (c._u32[i]&0x7f800000u)==0x7f800000u) return 0;
    *result=spu_native_fma_eval(a,b,c,op);
    return 1;
}
#else
static inline int spu_native_fma_try(u128 a,u128 b,u128 c,int op,u128* result)
{
    (void)a;(void)b;(void)c;(void)op;(void)result;return 0;
}
#endif
static inline u128 spu_fma(u128 a,u128 b,u128 c)
{ u128 r;return spu_native_fma_try(a,b,c,0,&r)?r:spu_scalar_fma(a,b,c); }
static inline u128 spu_fms(u128 a,u128 b,u128 c)
{ u128 r;return spu_native_fma_try(a,b,c,1,&r)?r:spu_scalar_fms(a,b,c); }
static inline u128 spu_fnms(u128 a,u128 b,u128 c)
{ u128 r;return spu_native_fma_try(a,b,c,2,&r)?r:spu_scalar_fnms(a,b,c); }
#endif
