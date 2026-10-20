#pragma once
#include <cstdint>
// Private diagnostic only: records an unhandled host exception, then preserves
// normal failure handling. It never handles or repairs the fault.
void folklore_install_fault_capture(void* guest_base,std::uint32_t guest_limit);
int folklore_check_fault_capture();
