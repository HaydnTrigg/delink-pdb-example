
#ifdef DECOMP
extern "C" int __cdecl printf(char const* const format, ...);
#else
#include <stdio.h>
#endif

int main()
{
    printf("hello world");
    return 0;
}
