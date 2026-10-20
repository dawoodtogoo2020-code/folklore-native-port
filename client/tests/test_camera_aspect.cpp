#include "../native/camera_aspect.h"
#include <cstdio>
#include <limits>
#include <initializer_list>
int main()
{
    unsigned checks=0,failures=0;
    auto check=[&](bool ok){++checks;if(!ok){++failures;std::fprintf(stderr,"camera aspect check %u failed\n",checks);}};
    const double original=static_cast<float>(16.0/9.0),wide=3440.0/1440.0;
    check(folklore_camera_aspect_value("2.388888888888889")==wide);
    for(const char* text:{"", "nan", "inf", " 2.4", "2.4 ", "-2.4", "1.6", "4", "2.4x"})
        check(folklore_camera_aspect_value(text)==0);
    check(folklore_camera_aspect_value(nullptr)==0);
    check(folklore_camera_aspect_apply(original,0)==original);
    check(folklore_camera_aspect_apply(original,wide)==wide);
    check(folklore_camera_aspect_apply(1.6,wide)==1.6);
    check(folklore_camera_aspect_apply(original,4)==original);
    check(folklore_camera_aspect_apply(original,std::numeric_limits<double>::infinity())==original);
    check(std::isnan(folklore_camera_aspect_apply(std::numeric_limits<double>::quiet_NaN(),wide)));
    check(std::fabs((3.07768321/wide)/(3.07768321/original)-original/wide)<1e-12);
    std::printf("Camera aspect: %u checks, %u failures\n",checks,failures);
    return failures?1:0;
}
