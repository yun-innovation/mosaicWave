#include <unistd.h>

int main(void)
{
    char *argv[] = {"open", "http://127.0.0.1:8090/", 0};
    execv("/usr/bin/open", argv);
    return 127;
}
