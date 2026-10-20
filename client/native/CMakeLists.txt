cmake_minimum_required(VERSION 3.20)
project(FolkloreBoot LANGUAGES C CXX)
set(CMAKE_C_STANDARD 17)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT DEFINED PS3RECOMP_DIR OR NOT DEFINED RECOMP_DIR)
    message(FATAL_ERROR "Pass pinned PS3RECOMP_DIR and private RECOMP_DIR")
endif()
file(TO_CMAKE_PATH "${PS3RECOMP_DIR}" PS3RECOMP_DIR)
file(TO_CMAKE_PATH "${RECOMP_DIR}" RECOMP_DIR)
if(NOT CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    message(FATAL_ERROR "This initial diagnostic target requires Clang")
endif()
find_package(Python3 COMPONENTS Interpreter REQUIRED)
if(ANDROID)
    # Imported targets found only inside runtime/ are not visible to this target.
    find_package(SDL2 2.26 REQUIRED CONFIG)
endif()
set(PS3RECOMP_BUILD_TESTS OFF CACHE BOOL "Separate upstream tests" FORCE)
add_subdirectory("${PS3RECOMP_DIR}" "${CMAKE_BINARY_DIR}/runtime" EXCLUDE_FROM_ALL)
file(GLOB LIFTED CONFIGURE_DEPENDS "${RECOMP_DIR}/ppu_recomp_*.cpp")
if(NOT LIFTED OR NOT EXISTS "${RECOMP_DIR}/ppu_recomp.h")
    message(FATAL_ERROR "Missing private PPU output")
endif()
option(FOLKLORE_CAMERA_ASPECT_BRIDGE "Enable the exact-source guarded world camera bridge" OFF)
if(FOLKLORE_CAMERA_ASPECT_BRIDGE)
    foreach(CAMERA_UNIT ppu_recomp_005.cpp ppu_recomp_006.cpp)
    set(CAMERA_BRIDGE "${CMAKE_CURRENT_BINARY_DIR}/camera-bridge-v2/${CAMERA_UNIT}")
    execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/prepare_camera_aspect.py"
        --source "${RECOMP_DIR}/${CAMERA_UNIT}" --output "${CAMERA_BRIDGE}"
        RESULT_VARIABLE CAMERA_BRIDGE_RESULT OUTPUT_QUIET ERROR_VARIABLE CAMERA_BRIDGE_ERROR)
    if(NOT CAMERA_BRIDGE_RESULT EQUAL 0)
        message(FATAL_ERROR "Camera aspect bridge verification failed: ${CAMERA_BRIDGE_ERROR}")
    endif()
    list(REMOVE_ITEM LIFTED "${RECOMP_DIR}/${CAMERA_UNIT}")
    list(APPEND LIFTED "${CAMERA_BRIDGE}")
    endforeach()
endif()
# Main-loop frame floor (scripts/prepare_frame_limiter.py, native/frame_limiter.h).
# Default behaviour is the original 30 FPS floor; FOLKLORE_FRAME_RATE=60 is experimental.
option(FOLKLORE_FRAME_LIMITER_BRIDGE "Enable the exact-source guarded main-loop frame floor" OFF)
if(FOLKLORE_FRAME_LIMITER_BRIDGE)
    set(LIMITER_BRIDGE "${CMAKE_CURRENT_BINARY_DIR}/frame-limiter-v1/ppu_recomp_007.cpp")
    execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/prepare_frame_limiter.py"
        --source "${RECOMP_DIR}/ppu_recomp_007.cpp" --output "${LIMITER_BRIDGE}"
        RESULT_VARIABLE LIMITER_BRIDGE_RESULT OUTPUT_QUIET ERROR_VARIABLE LIMITER_BRIDGE_ERROR)
    if(NOT LIMITER_BRIDGE_RESULT EQUAL 0)
        message(FATAL_ERROR "Frame limiter bridge verification failed: ${LIMITER_BRIDGE_ERROR}")
    endif()
    list(REMOVE_ITEM LIFTED "${RECOMP_DIR}/ppu_recomp_007.cpp")
    list(APPEND LIFTED "${LIMITER_BRIDGE}")
endif()
set(FOLKLORE_AUDIO_DIAGNOSTICS_DEFAULT OFF)
if(WIN32 AND FOLKLORE_CAMERA_ASPECT_BRIDGE AND FOLKLORE_FRAME_LIMITER_BRIDGE)
    set(FOLKLORE_AUDIO_DIAGNOSTICS_DEFAULT ON)
endif()
# Guarded observation only; runtime CSV paths must opt in to guest PCM reads.
option(FOLKLORE_AUDIO_DIAGNOSTICS "Prepare guarded audio producer PCM traces" ${FOLKLORE_AUDIO_DIAGNOSTICS_DEFAULT})
if(FOLKLORE_AUDIO_DIAGNOSTICS)
    if(NOT WIN32 OR NOT FOLKLORE_CAMERA_ASPECT_BRIDGE OR NOT FOLKLORE_FRAME_LIMITER_BRIDGE)
        message(FATAL_ERROR "Audio diagnostics require Windows, camera and frame-limiter bridges")
    endif()
    set(AUDIO_TRACE_DIR "${CMAKE_CURRENT_BINARY_DIR}/audio-producer-trace-v4-nested")
    execute_process(COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/prepare_audio_producer_trace.py"
        --source "${RECOMP_DIR}/ppu_recomp_012.cpp"
        --output-dir "${AUDIO_TRACE_DIR}" --prepare
        RESULT_VARIABLE AUDIO_TRACE_RESULT OUTPUT_QUIET ERROR_VARIABLE AUDIO_TRACE_ERROR)
    if(NOT AUDIO_TRACE_RESULT EQUAL 0)
        message(FATAL_ERROR "Audio producer trace preparation failed: ${AUDIO_TRACE_ERROR}")
    endif()
    list(REMOVE_ITEM LIFTED "${RECOMP_DIR}/ppu_recomp_012.cpp")
    list(APPEND LIFTED "${AUDIO_TRACE_DIR}/ppu_recomp_012.cpp")
endif()
# Observation only. These prepared copies retain the exact input instructions.
option(FOLKLORE_DRAW_PRODUCER_DIAGNOSTICS "Prepare bounded draw producer register traces" OFF)
if(FOLKLORE_DRAW_PRODUCER_DIAGNOSTICS)
    set(DRAW_TRACE_DIR "${CMAKE_CURRENT_BINARY_DIR}/draw-producer-trace-v1")
    file(MAKE_DIRECTORY "${DRAW_TRACE_DIR}")
    foreach(DRAW_UNIT ppu_recomp_008.cpp ppu_recomp_011.cpp)
        execute_process(COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/prepare_draw_producer_trace.py"
            --source "${RECOMP_DIR}/${DRAW_UNIT}" --output "${DRAW_TRACE_DIR}/${DRAW_UNIT}"
            RESULT_VARIABLE DRAW_TRACE_RESULT OUTPUT_QUIET ERROR_VARIABLE DRAW_TRACE_ERROR)
        if(NOT DRAW_TRACE_RESULT EQUAL 0)
            message(FATAL_ERROR "Draw producer trace preparation failed: ${DRAW_TRACE_ERROR}")
        endif()
        list(REMOVE_ITEM LIFTED "${RECOMP_DIR}/${DRAW_UNIT}")
        list(APPEND LIFTED "${DRAW_TRACE_DIR}/${DRAW_UNIT}")
        set_property(SOURCE "${DRAW_TRACE_DIR}/${DRAW_UNIT}" APPEND PROPERTY
            COMPILE_DEFINITIONS FOLKLORE_DRAW_PRODUCER_TRACE=1)
        set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${RECOMP_DIR}/${DRAW_UNIT}")
    endforeach()
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
        "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/prepare_draw_producer_trace.py")
endif()
set(NIDS "${CMAKE_CURRENT_BINARY_DIR}/ppu_hle_nids.cpp")
file(GLOB_RECURSE HLE_MODULE_SOURCES CONFIGURE_DEPENDS "${PS3RECOMP_DIR}/libs/*.c")
add_custom_command(OUTPUT "${NIDS}"
    COMMAND "${Python3_EXECUTABLE}" "${PS3RECOMP_DIR}/tools/gen_hle_nids.py" --out "${NIDS}" --all
    DEPENDS "${PS3RECOMP_DIR}/tools/gen_hle_nids.py" "${PS3RECOMP_DIR}/tools/nid_database.py" ${HLE_MODULE_SOURCES})
if(ANDROID)
    # SDLActivity loads this library and calls its SDL_main entry point.
    add_library(FolkloreBoot SHARED)
    target_link_options(FolkloreBoot PRIVATE "-Wl,--no-undefined")
else()
    add_executable(FolkloreBoot)
endif()
target_sources(FolkloreBoot PRIVATE boot_main.cpp graphics_settings.cpp fs_stat_probe.cpp fs_open_probe.cpp fs_seek_probe.cpp fs_unlink_probe.cpp fs_aio_probe.cpp "${NIDS}" ${LIFTED}
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_loader.cpp"
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_hle.cpp"
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_fs.cpp"
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_fs_aio_engine.cpp"
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_sysprx.cpp")
if(FOLKLORE_AUDIO_DIAGNOSTICS)
    set_property(SOURCE "${AUDIO_TRACE_DIR}/ppu_recomp_012.cpp" APPEND PROPERTY
        COMPILE_DEFINITIONS FOLKLORE_AUDIO_PRODUCER_TRACE=1)
endif()
if(FOLKLORE_CAMERA_ASPECT_BRIDGE)
    target_compile_definitions(FolkloreBoot PRIVATE FOLKLORE_CAMERA_ASPECT_BRIDGE=1)
endif()
target_include_directories(FolkloreBoot PRIVATE "${RECOMP_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}"
    "${PS3RECOMP_DIR}/include" "${PS3RECOMP_DIR}/runtime/ppu"
    "${PS3RECOMP_DIR}/runtime/spu" "${PS3RECOMP_DIR}/runtime/memory"
    "${PS3RECOMP_DIR}/runtime/syscalls" "${PS3RECOMP_DIR}/runtime/platform"
    "${PS3RECOMP_DIR}/libs/video")
target_include_directories(FolkloreBoot PRIVATE "${PS3RECOMP_DIR}/libs/system")
target_sources(FolkloreBoot PRIVATE "${PS3RECOMP_DIR}/tests/test_spurs_job_snapshot.c")
set_source_files_properties("${PS3RECOMP_DIR}/tests/test_spurs_job_snapshot.c"
    PROPERTIES COMPILE_DEFINITIONS SPURS_SNAPSHOT_NATIVE)
target_compile_options(FolkloreBoot PRIVATE -fno-strict-aliasing -ffp-contract=off)
# Optimization level for the lifted PPU sources. -O1 builds as fast as -O0
# here (about a minute for the whole game) and the guest runs much faster;
# -O2 measured the same as -O1. -fno-strict-aliasing and -ffp-contract=off
# above still apply, so optimizing does not change aliasing or FP rounding.
set(FOLKLORE_LIFTED_OPT "-O1" CACHE STRING "Optimization flag for lifted PPU sources")
set_source_files_properties(${LIFTED} PROPERTIES COMPILE_OPTIONS "-w;${FOLKLORE_LIFTED_OPT}")
target_link_libraries(FolkloreBoot PRIVATE ps3recomp_runtime)
if(PS3RECOMP_RSX_CACAO)
    target_compile_definitions(FolkloreBoot PRIVATE FOLKLORE_HAS_CACAO=1)
endif()
set(FOLKLORE_PRIVATE_NATIVE_JOB_SOURCE "" CACHE FILEPATH "Reviewed private native job registration source")
option(FOLKLORE_AUDIO_NATIVE_FMA "Qualify packed finite FMA for the measured bc31db8876d7 audio image" OFF)
if(NOT FOLKLORE_PRIVATE_NATIVE_JOB_SOURCE STREQUAL "")
    if(NOT IS_ABSOLUTE "${FOLKLORE_PRIVATE_NATIVE_JOB_SOURCE}" OR
       NOT EXISTS "${FOLKLORE_PRIVATE_NATIVE_JOB_SOURCE}" OR
       IS_DIRECTORY "${FOLKLORE_PRIVATE_NATIVE_JOB_SOURCE}")
        message(FATAL_ERROR "Invalid private native job registration source")
    endif()
    target_sources(FolkloreBoot PRIVATE "${FOLKLORE_PRIVATE_NATIVE_JOB_SOURCE}")
    set(FOLKLORE_PRIVATE_NATIVE_EXTRA_SOURCES "" CACHE STRING "Additional private AOT translation units")
    foreach(private_source IN LISTS FOLKLORE_PRIVATE_NATIVE_EXTRA_SOURCES)
        if(NOT IS_ABSOLUTE "${private_source}" OR NOT EXISTS "${private_source}" OR IS_DIRECTORY "${private_source}")
            message(FATAL_ERROR "Invalid additional private AOT translation unit")
        endif()
        target_sources(FolkloreBoot PRIVATE "${private_source}")
        # The immutable generated image keeps its recorded hash. Include the
        # current SDK helpers first, using their existing include guard, only
        # for this independently measured and parity-tested image.
        file(TO_CMAKE_PATH "${private_source}" private_source_normalized)
        if(FOLKLORE_AUDIO_NATIVE_FMA AND private_source_normalized MATCHES "/image_bc31db8876d7/private_extra_jobs\\.c$")
            get_filename_component(fma_image_dir "${private_source}" DIRECTORY)
            set(fma_files "${private_source}" "${fma_image_dir}/image03_generated.c" "${fma_image_dir}/image03_generated.h")
            set(fma_hashes
                "6c0c1a8c05a2ccc35baa7181de7f0c97c3aba94f2084a0108299fa28b67e0e02"
                "b16366b1cc65a9992e36677dd187d49b2a3b11183fb852e394f5bf72b0d737d1"
                "1c1761c1a41f1f74ce7d492fa605fe5808fa44fb5e6ff6e7a728149d8c7ad425")
            foreach(fma_index RANGE 0 2)
                list(GET fma_files ${fma_index} fma_file)
                list(GET fma_hashes ${fma_index} fma_expected)
                file(SHA256 "${fma_file}" fma_actual)
                if(NOT fma_actual STREQUAL fma_expected)
                    message(FATAL_ERROR "Native FMA image differs from qualified source: ${fma_file}")
                endif()
            endforeach()
            set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${fma_files})
            set_property(SOURCE "${private_source}" APPEND PROPERTY COMPILE_DEFINITIONS SPU_NATIVE_FMA=1)
            set_property(SOURCE "${private_source}" APPEND PROPERTY COMPILE_OPTIONS
                "-include" "${CMAKE_CURRENT_SOURCE_DIR}/spu_fma_native.h")
            message(STATUS "Finite native FMA enabled for measured audio image bc31db8876d7 only")
        endif()
    endforeach()
    set_property(SOURCE boot_main.cpp APPEND PROPERTY COMPILE_DEFINITIONS FOLKLORE_PRIVATE_NATIVE_JOBS=1)
endif()
if(WIN32)
    target_sources(FolkloreBoot PRIVATE msgdialog_host_win32.cpp msgdialog_host_probe_win32.cpp native_fault_win32.cpp)
    target_compile_options(FolkloreBoot PRIVATE -fms-extensions)
    target_link_libraries(FolkloreBoot PRIVATE dbghelp user32 gdi32 winmm d3d12 dxgi d3dcompiler dxguid)
    if(NOT MSVC)
        target_link_options(FolkloreBoot PRIVATE "-Wl,--stack,33554432")
    endif()
elseif(ANDROID)
    target_sources(FolkloreBoot PRIVATE msgdialog_host_android.cpp android_touch_jni.cpp)
    target_link_libraries(FolkloreBoot PRIVATE SDL2::SDL2 ${CMAKE_DL_LIBS} log android)
else()
    find_package(Threads REQUIRED)
    target_sources(FolkloreBoot PRIVATE msgdialog_host_linux.cpp)
    target_link_libraries(FolkloreBoot PRIVATE ${CMAKE_DL_LIBS})
    add_executable(FolkloreDialogProbe EXCLUDE_FROM_ALL msgdialog_host_linux.cpp msgdialog_host_linux_probe.cpp)
    target_include_directories(FolkloreDialogProbe PRIVATE "${PS3RECOMP_DIR}/include" "${PS3RECOMP_DIR}/libs/system")
    target_compile_features(FolkloreDialogProbe PRIVATE cxx_std_17)
    target_link_libraries(FolkloreDialogProbe PRIVATE ${CMAKE_DL_LIBS} Threads::Threads)
endif()
