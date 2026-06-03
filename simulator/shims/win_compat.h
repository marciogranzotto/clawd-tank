/* Windows compatibility shims for POSIX functions */
#pragma once

#ifdef _WIN32

#include <time.h>

/* localtime_r is POSIX; Windows has localtime_s with swapped args.
 * Accept const void* to handle both time_t* and long* (MinGW timeval.tv_sec). */
#ifndef localtime_r
static inline struct tm *localtime_r(const void *timep, struct tm *result) {
    time_t t = (time_t)(*(const long *)timep);
    if (localtime_s(result, &t) == 0) return result;
    return NULL;
}
#define localtime_r localtime_r
#endif

/* settimeofday not available on Windows — no-op for simulator */
#ifndef settimeofday
#define settimeofday(tv, tz) (0)
#endif

#endif /* _WIN32 */
