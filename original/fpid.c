#include <stdio.h>
#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <dirent.h>

int main(int argc, char *argv[]) {
    int q_flag = 0;
    int s_flag = 0;
    int opt;

    while ((opt = getopt(argc, argv, "qhs")) != -1) {
        switch (opt) {
            case 'q':
                q_flag = 1;
                break;
            case 's':
                s_flag = 1;
                break;
            case 'h':
                printf("Usage: %s [-q] [-s] [-h] <program name or path>\n", argv[0]);
                printf("Options:\n");
                printf("  -q    Quiet mode: suppress output, exit 0 if found\n");
                printf("  -s    Single shot: exit after first match\n");
                printf("  -h    Show this help\n");
                return 0;
            default:
                fprintf(stderr, "Unknown option '%c'(see \"%s -h\")\n", optopt, argv[0]);
                return 1;
        }
    }

    if (optind != argc - 1) {
        fprintf(stderr, "Error: Missing program name or path\n");
        fprintf(stderr, "Usage: %s [-qhs] <program name or path>\n", argv[0]);
        return 1;
    }

    const char *prog_name = argv[optind];
    const int is_path = strchr(prog_name, '/') != NULL;
    const size_t prog_len = strlen(prog_name);

    DIR *proc_dir = opendir("/proc");
    if (!proc_dir) {
        perror("open dir /proc failed");
        return 1;
    }

    struct dirent *entry;
    int found = 0;

    if (is_path) {
        while ((entry = readdir(proc_dir))) {
            if (entry->d_name[0] < '0' || entry->d_name[0] > '9') continue;
            const char *name = entry->d_name;
            size_t name_len = strlen(name);

            char exe_file[32];
            size_t needed = 11 + name_len;
            if (needed > sizeof(exe_file)) continue;
            char *p = exe_file;
            memcpy(p, "/proc/", 6);
            p += 6;
            memcpy(p, name, name_len);
            p += name_len;
            memcpy(p, "/exe", 4);
            p += 4;
            *p = '\0';

            char exe_path[4096];
            ssize_t len = readlink(exe_file, exe_path, sizeof(exe_path) - 1);
            if (len == -1) continue;
            exe_path[len] = '\0';
            if ((size_t)len == prog_len && memcmp(exe_path, prog_name, prog_len) == 0) {
                found = 1;
                if (!q_flag) printf("%s\n", name);
                if (s_flag) {
                    closedir(proc_dir);
                    return 0;
                }
            }
        }
    } else {
        while ((entry = readdir(proc_dir))) {
            if (entry->d_name[0] < '0' || entry->d_name[0] > '9') continue;
            const char *name = entry->d_name;
            size_t name_len = strlen(name);

            char cmdline_path[32];
            size_t needed = 15 + name_len;
            if (needed > sizeof(cmdline_path)) continue;
            char *p = cmdline_path;
            memcpy(p, "/proc/", 6);
            p += 6;
            memcpy(p, name, name_len);
            p += name_len;
            memcpy(p, "/cmdline", 8);
            p += 8;
            *p = '\0';

            int cmd_fd = open(cmdline_path, O_RDONLY);
            if (cmd_fd < 0) continue;

            char cmdline[4096];
            ssize_t bytes_read = read(cmd_fd, cmdline, sizeof(cmdline) - 1);
            close(cmd_fd);
            if (bytes_read <= 0) continue;
            cmdline[bytes_read] = '\0';
            const char *slash = strrchr(cmdline, '/');
            const char *proc_name = slash ? slash + 1 : cmdline;
            size_t base_len = strlen(proc_name);

            if (base_len == prog_len && memcmp(proc_name, prog_name, prog_len) == 0) {
                found = 1;
                if (!q_flag) printf("%s\n", name);
                if (s_flag) {
                    closedir(proc_dir);
                    return 0;
                }
            }
        }
    }

    closedir(proc_dir);
    return found ? 0 : 1;
}